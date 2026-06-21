"""XeBop settings web UI — a small Flask app, run as its own service.

Runs as the agent's user (never root), binds to the LAN, and is protected by a
password (stored as a salted PBKDF2 hash in secrets.json). It edits config.json
/ secrets.json / employees.json / the M365 cache, then offers a Restart button
so the agent picks up changes (the agent reads config once at startup).

Config takes effect on agent restart, not live — see the plan's non-goals.
"""

from __future__ import annotations

import json
import os
import secrets as pysecrets
from pathlib import Path

from flask import (
    Flask, flash, jsonify, redirect, render_template, request, session, url_for
)

from flask import abort, send_file

from greeter.config import load_layered_config, load_json_file
from greeter.directory import resolve_directory_path
from greeter.flow import DEFAULT_PHRASES, Employee, load_employees, resolve_phrase
from greeter.notify import make_notifier
from greeter.visitor_log import VisitorLog
from greeter import m365, sound_maker
from webui import system_info
from webui.settings_store import (
    atomic_write_json, save_settings, set_webui_password, verify_password,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
SECRETS_PATH = ROOT / "secrets.json"
EMPLOYEES_PATH = ROOT / "employees.json"

HOST_CHANNEL_PREFIXES = ("email", "teams", "slack")

# Greeter phrase slots shown on the Phrases tab: (config key, label, placeholders).
PHRASE_META = [
    ("didnt_catch_name", "Didn't catch the visitor's name", []),
    ("visitor_name_confirm", "Confirm the heard name", ["name"]),
    ("spell_name", "Ask the visitor to spell their name", []),
    ("returning_visitor", "Already checked in (returning)", ["name", "host"]),
    ("ask_host", "Ask who they're visiting", ["name"]),
    ("host_unknown_retry", "Host not found — ask again", []),
    ("host_unknown_giveup", "Host not found — give up", []),
    ("confirm_host", "Confirm visitor and host", ["visitor", "host"]),
    ("notified_host", "Host has been notified", ["host"]),
    ("confirm_no", "Visitor said the host was wrong", []),
    ("confirm_unclear", "Yes/no answer not understood", []),
    ("already_on_way", "Repeat / already on the way", []),
    ("didnt_catch", "Didn't catch speech (general)", []),
    ("ack", "Acknowledgement while thinking", []),
    ("hold_still", "Before taking the check-in photo", []),
    ("stopped", "Visitor said stop / cancel", []),
    ("going_to_sleep", "Visitor said go to sleep", []),
]


def _phrase_value_to_text(value):
    """A phrase value (str or list of variants) -> textarea text, one per line."""
    if isinstance(value, (list, tuple)):
        return "\n".join(str(v) for v in value)
    return str(value or "")


def _visitor_log():
    cfg = merged_config().get("visitor_log") or {}
    return VisitorLog(
        path=ROOT / cfg.get("path", "visitor_log.jsonl"),
        mode=cfg.get("mode", "minimal"),
        retention_days=int(cfg.get("retention_days", 7)),
        salt=cfg.get("salt", ""),
    )


def _humanize_duration(seconds):
    if seconds is None:
        return ""
    minutes = max(0, seconds // 60)
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _display_visitor(encoded):
    """Hashed names (minimal mode) aren't human-readable — label them."""
    if isinstance(encoded, str) and encoded.startswith("sha256:"):
        return "(name hidden)"
    return encoded or "(unknown)"


def _visit_photo_path(visit_id):
    """Absolute photo path for a visit_id, only if it lives under photo_dir."""
    cfg = merged_config().get("visitor_log") or {}
    photo_dir = (ROOT / cfg.get("photo_dir", "visitor_photos")).resolve()
    for e in _visitor_log().entries():
        if e.get("visit_id") == visit_id and e.get("kind") == "check_in" and e.get("photo"):
            p = (ROOT / e["photo"]).resolve()
            # guard against path traversal via a crafted log/visit_id
            if str(p).startswith(str(photo_dir)) and p.exists():
                return p
    return None


def _photo_entries():
    """Check-in records whose photo file is still on disk, newest first."""
    log = _visitor_log()
    out = []
    for e in log.entries():
        if e.get("kind") == "check_in" and e.get("visit_id") and _visit_photo_path(e["visit_id"]):
            out.append({
                "visit_id": e["visit_id"],
                "name": _display_visitor(e.get("visitor")),
                "ts": e.get("ts", ""),
            })
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


def _phrase_fields(cfg):
    effective = {**DEFAULT_PHRASES, **(cfg.get("phrases") or {})}
    return [
        {
            "key": key,
            "label": label,
            "placeholders": placeholders,
            "text": _phrase_value_to_text(effective.get(key, DEFAULT_PHRASES[key])),
            "default": DEFAULT_PHRASES[key],
        }
        for key, label, placeholders in PHRASE_META
    ]


def merged_config() -> dict:
    return load_layered_config({}, CONFIG_PATH, SECRETS_PATH)


def _password_configured() -> bool:
    return bool((merged_config().get("webui") or {}).get("password_hash"))


def _flask_secret() -> str:
    """Stable session key persisted in secrets.json (keeps logins across restarts)."""
    sec = load_json_file(SECRETS_PATH)
    key = (sec.get("webui") or {}).get("flask_secret")
    if not key:
        key = pysecrets.token_hex(32)
        save_settings({"webui": {"flask_secret": key}}, CONFIG_PATH, SECRETS_PATH)
    return key


def _logged_in() -> bool:
    return bool(session.get("authed"))


def _form_bool(name: str) -> bool:
    return request.form.get(name) in ("on", "true", "1", "yes")


def _clean(value: str | None) -> str:
    return (value or "").strip()


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = _flask_secret()

    @app.before_request
    def _gate():
        # Public endpoints: login + static. Everything else needs a session.
        if request.endpoint in (None, "login", "static"):
            return None
        if not _password_configured():
            return redirect(url_for("login"))
        if not _logged_in():
            return redirect(url_for("login"))
        return None

    # ---- auth -----------------------------------------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        configured = _password_configured()
        if request.method == "POST":
            if not configured:
                # First-run: set the admin password.
                pw = request.form.get("password", "")
                confirm = request.form.get("confirm", "")
                if len(pw) < 6:
                    flash("Password must be at least 6 characters.", "error")
                elif pw != confirm:
                    flash("Passwords do not match.", "error")
                else:
                    set_webui_password(pw, CONFIG_PATH, SECRETS_PATH)
                    session["authed"] = True
                    flash("Password set. You're logged in.", "ok")
                    return redirect(url_for("index"))
            else:
                cfg = merged_config().get("webui") or {}
                if verify_password(
                    request.form.get("password", ""),
                    cfg.get("password_hash", ""),
                    cfg.get("salt", ""),
                ):
                    session["authed"] = True
                    return redirect(url_for("index"))
                flash("Incorrect password.", "error")
        return render_template("login.html", configured=configured)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/password", methods=["POST"])
    def change_password():
        pw = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if len(pw) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif pw != confirm:
            flash("Passwords do not match.", "error")
        else:
            set_webui_password(pw, CONFIG_PATH, SECRETS_PATH)
            flash("Password updated.", "ok")
        return redirect(url_for("index"))

    # ---- main page ------------------------------------------------------
    @app.route("/")
    def index():
        cfg = merged_config()
        directory_cfg = cfg.get("directory") or {}
        m365_cfg = directory_cfg.get("m365") or {}
        try:
            local_employees = load_employees(EMPLOYEES_PATH)
        except Exception:
            local_employees = []
        secrets_set = {
            "smtp_password": bool(((cfg.get("notify") or {}).get("email") or {}).get("password")),
            "m365_client_secret": bool(m365_cfg.get("client_secret")),
        }
        log = _visitor_log()
        on_site = [
            {
                "visit_id": v.get("visit_id"),
                "name": _display_visitor(v.get("visitor")),
                "host": v.get("host") or "—",
                "since": _humanize_duration(v.get("duration_seconds")),
                "has_photo": bool(_visit_photo_path(v.get("visit_id"))),
            }
            for v in log.open_visits()
        ]
        history = [
            {
                "ts": e.get("ts", ""),
                "name": _display_visitor(e.get("visitor")),
                "host": e.get("host") or "—",
                "what": e.get("kind") or e.get("outcome") or "—",
            }
            for e in list(log.entries())[-25:][::-1]
        ]
        return render_template(
            "settings.html",
            cfg=cfg,
            branding=cfg.get("branding") or {},
            notify=cfg.get("notify") or {},
            directory=directory_cfg,
            m365=m365_cfg,
            source=directory_cfg.get("source", "local"),
            local_employees=[e.__dict__ for e in local_employees],
            audio=system_info.list_audio_devices(),
            aplay_devices=system_info.list_aplay_devices(),
            secrets_set=secrets_set,
            phrase_fields=_phrase_fields(cfg),
            sound_clips=sound_maker.list_clips(ROOT),
            sound_categories=sound_maker.CATEGORIES,
            on_site=on_site,
            history=history,
            photos=_photo_entries(),
            vlog=cfg.get("visitor_log") or {},
        )

    # ---- visitors -------------------------------------------------------
    @app.route("/visitors/checkout/<visit_id>", methods=["POST"])
    def visitor_checkout(visit_id):
        log = _visitor_log()
        visit = next((v for v in log.open_visits() if v.get("visit_id") == visit_id), None)
        if visit is None:
            flash("That visit is already closed or unknown.", "error")
            return redirect(url_for("index", _anchor="visitors"))
        log.check_out(visit_id)
        host_name = visit.get("host")
        host_channel = visit.get("host_channel_id")
        if host_name and host_channel:
            stored = visit.get("visitor")
            visitor_label = "Your visitor" if (not stored or str(stored).startswith("sha256:")) else stored
            msg = resolve_phrase(
                merged_config().get("phrases") or {}, "exit_notice",
                visitor=visitor_label, host=host_name,
            )
            try:
                make_notifier(merged_config())(Employee(host_name, "", (), host_channel), msg)
            except Exception as exc:
                flash(f"Signed out, but host notify failed: {exc}", "error")
                return redirect(url_for("index", _anchor="visitors"))
        flash("Signed out.", "ok")
        return redirect(url_for("index", _anchor="visitors"))

    @app.route("/visitors/photo/<visit_id>")
    def visitor_photo(visit_id):
        path = _visit_photo_path(visit_id)
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/jpeg")

    # ---- sound maker ----------------------------------------------------
    @app.route("/sounds/make", methods=["POST"])
    def sound_make():
        cfg = merged_config()
        voice = (
            (cfg.get("branding") or {}).get("voice_model")
            or cfg.get("voice_model")
            or "piper/en_GB-semaine-medium.onnx"
        )
        ok, msg = sound_maker.synthesize(
            ROOT,
            request.form.get("category", ""),
            request.form.get("name", ""),
            request.form.get("text", ""),
            voice,
        )
        flash(msg + (" Restart the agent to use it." if ok else ""), "ok" if ok else "error")
        return redirect(url_for("index", _anchor="sounds"))

    @app.route("/sounds/delete", methods=["POST"])
    def sound_delete():
        if sound_maker.delete_clip(ROOT, request.form.get("category", ""), request.form.get("name", "")):
            flash("Sound deleted.", "ok")
        else:
            flash("Could not delete sound.", "error")
        return redirect(url_for("index", _anchor="sounds"))

    @app.route("/sounds/play/<category>/<name>")
    def sound_play(category, name):
        try:
            p = sound_maker.clip_path(ROOT, category, name)
        except ValueError:
            abort(404)
        if not p.exists():
            abort(404)
        return send_file(p, mimetype="audio/wav")

    # ---- visitor-log / privacy settings --------------------------------
    @app.route("/save/visitorlog", methods=["POST"])
    def save_visitorlog():
        mode = request.form.get("mode", "standard")
        if mode not in ("standard", "minimal"):
            mode = "standard"
        rd = _clean(request.form.get("retention_days"))
        ps = _clean(request.form.get("preview_seconds"))
        save_settings({"visitor_log": {
            "mode": mode,
            "retention_days": int(rd) if rd.isdigit() else 7,
            "capture_photo": _form_bool("capture_photo"),
            "preview_seconds": int(ps) if ps.isdigit() else 3,
        }}, CONFIG_PATH, SECRETS_PATH)
        flash("Visitor-log settings saved. Restart the agent to apply.", "ok")
        return redirect(url_for("index", _anchor="photos"))

    # ---- photo management ----------------------------------------------
    @app.route("/photos/delete/<visit_id>", methods=["POST"])
    def photo_delete(visit_id):
        path = _visit_photo_path(visit_id)
        if path is not None:
            try:
                path.unlink()
                flash("Photo deleted.", "ok")
            except OSError as exc:
                flash(f"Could not delete photo: {exc}", "error")
        else:
            flash("Photo not found.", "error")
        return redirect(url_for("index", _anchor="photos"))

    @app.route("/photos/delete_all", methods=["POST"])
    def photos_delete_all():
        cfg = merged_config().get("visitor_log") or {}
        photo_dir = (ROOT / cfg.get("photo_dir", "visitor_photos")).resolve()
        n = 0
        if photo_dir.exists():
            for p in photo_dir.glob("*.jpg"):
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
        flash(f"Deleted {n} photo(s).", "ok")
        return redirect(url_for("index", _anchor="photos"))

    # ---- saves ----------------------------------------------------------
    @app.route("/save/audio", methods=["POST"])
    def save_audio():
        def _opt(name):
            v = _clean(request.form.get(name))
            return v or None
        sr = _clean(request.form.get("input_sample_rate"))
        save_settings({
            "input_device": _opt("input_device"),
            "input_sample_rate": int(sr) if sr.isdigit() else None,
            "output_device": _opt("output_device"),
            "aplay_device": _opt("aplay_device"),
        }, CONFIG_PATH, SECRETS_PATH)
        flash("Audio settings saved. Restart the agent to apply.", "ok")
        return redirect(url_for("index", _anchor="audio"))

    @app.route("/save/branding", methods=["POST"])
    def save_branding():
        thr = _clean(request.form.get("wake_threshold"))
        try:
            threshold = float(thr) if thr else 0.5
        except ValueError:
            threshold = 0.5
        save_settings({"branding": {
            "agent_name": _clean(request.form.get("agent_name")),
            "company_name": _clean(request.form.get("company_name")),
            "opening_line": _clean(request.form.get("opening_line")),
            "wake_word": {
                "phrase": _clean(request.form.get("wake_phrase")),
                "threshold": threshold,
            },
        }}, CONFIG_PATH, SECRETS_PATH)
        flash("Branding saved. Restart the agent to apply.", "ok")
        return redirect(url_for("index", _anchor="branding"))

    @app.route("/save/notify", methods=["POST"])
    def save_notify():
        port = _clean(request.form.get("smtp_port"))
        email = {
            "host": _clean(request.form.get("smtp_host")),
            "port": int(port) if port.isdigit() else 587,
            "username": _clean(request.form.get("smtp_username")),
            "from": _clean(request.form.get("smtp_from")),
            "subject": _clean(request.form.get("smtp_subject")) or "Visitor at the front desk",
            "use_starttls": _form_bool("smtp_starttls"),
            "use_ssl": _form_bool("smtp_ssl"),
        }
        # Only overwrite the password when a new one is typed (blank = keep).
        new_pw = request.form.get("smtp_password", "")
        if new_pw:
            email["password"] = new_pw
        save_settings({"notify": {
            "email": email,
            "teams_webhook_url": _clean(request.form.get("teams_webhook_url")) or None,
            "slack_webhook_url": _clean(request.form.get("slack_webhook_url")) or None,
        }}, CONFIG_PATH, SECRETS_PATH)
        flash("Notification settings saved. Restart the agent to apply.", "ok")
        return redirect(url_for("index", _anchor="notify"))

    @app.route("/save/directory", methods=["POST"])
    def save_directory():
        source = request.form.get("source", "local")
        if source not in ("local", "m365"):
            source = "local"
        m365_updates = {
            "tenant_id": _clean(request.form.get("tenant_id")),
            "client_id": _clean(request.form.get("client_id")),
            "host_channel": request.form.get("host_channel", "email"),
        }
        new_secret = request.form.get("client_secret", "")
        if new_secret:
            m365_updates["client_secret"] = new_secret
        save_settings({"directory": {"source": source, "m365": m365_updates}},
                      CONFIG_PATH, SECRETS_PATH)
        flash("Directory settings saved. Restart the agent to apply.", "ok")
        return redirect(url_for("index", _anchor="addressbook"))

    # ---- greeter phrases ------------------------------------------------
    @app.route("/save/phrases", methods=["POST"])
    def save_phrases():
        # Build the whole phrases block from the form so clearing a field
        # reverts that line to its built-in default (a blank field = omitted).
        phrases = {}
        for key, _label, _ph in PHRASE_META:
            variants = [
                ln.strip() for ln in request.form.get(key, "").splitlines() if ln.strip()
            ]
            if len(variants) == 1:
                phrases[key] = variants[0]
            elif len(variants) > 1:
                phrases[key] = variants
            # zero non-blank lines -> leave it out, default applies
        cfg = load_json_file(CONFIG_PATH)
        cfg["phrases"] = phrases
        atomic_write_json(CONFIG_PATH, cfg)
        flash("Phrases saved. Restart the agent to apply.", "ok")
        return redirect(url_for("index", _anchor="phrases"))

    # ---- local address book CRUD ---------------------------------------
    @app.route("/addressbook/save", methods=["POST"])
    def save_local_addressbook():
        try:
            employees = json.loads(request.form.get("employees_json", "[]"))
            assert isinstance(employees, list)
        except Exception:
            flash("Could not parse the employee list.", "error")
            return redirect(url_for("index", _anchor="addressbook"))
        cleaned = []
        for e in employees:
            name = _clean(e.get("name"))
            channel = _clean(e.get("host_channel_id"))
            if not name or not channel:
                continue
            cleaned.append({
                "name": name,
                "role": _clean(e.get("role")),
                "alt_names": [a.strip() for a in (e.get("alt_names") or []) if a.strip()],
                "host_channel_id": channel,
            })
        atomic_write_json(EMPLOYEES_PATH, {"$schema_version": 1, "employees": cleaned})
        flash(f"Saved {len(cleaned)} employees. Restart the agent to apply.", "ok")
        return redirect(url_for("index", _anchor="addressbook"))

    # ---- M365 actions (AJAX) -------------------------------------------
    @app.route("/m365/test", methods=["POST"])
    def m365_test():
        cfg = (merged_config().get("directory") or {}).get("m365") or {}
        return jsonify(m365.test_connection(
            cfg.get("tenant_id", ""), cfg.get("client_id", ""), cfg.get("client_secret", "")
        ))

    @app.route("/m365/sync", methods=["POST"])
    def m365_sync():
        cfg = (merged_config().get("directory") or {}).get("m365") or {}
        try:
            emps = m365.fetch_directory(
                cfg.get("tenant_id", ""), cfg.get("client_id", ""),
                cfg.get("client_secret", ""), cfg.get("host_channel", "email"),
            )
        except m365.GraphError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 200
        return jsonify({"ok": True, "candidates": [e.__dict__ for e in emps]})

    @app.route("/m365/curate", methods=["POST"])
    def m365_curate():
        data = request.get_json(silent=True) or {}
        rows = data.get("employees") or []
        emps = [
            Employee(
                name=_clean(r.get("name")),
                role=_clean(r.get("role")),
                alt_names=tuple(a.strip() for a in (r.get("alt_names") or []) if a.strip()),
                host_channel_id=_clean(r.get("host_channel_id")),
            )
            for r in rows if _clean(r.get("name")) and _clean(r.get("host_channel_id"))
        ]
        cache_path = ROOT / ((merged_config().get("directory") or {}).get("m365") or {}).get(
            "cache_path", "m365_directory.json"
        )
        m365.write_cache(cache_path, emps)
        return jsonify({
            "ok": True,
            "count": len(emps),
            "collisions": m365.detect_collisions(emps),
        })

    # ---- restart --------------------------------------------------------
    @app.route("/restart", methods=["POST"])
    def restart():
        result = system_info.restart_agent()
        flash(result["message"], "ok" if result["ok"] else "error")
        return redirect(url_for("index"))

    return app


def main():
    cfg = merged_config().get("webui") or {}
    host = cfg.get("host", "0.0.0.0")
    port = int(cfg.get("port", 8080))
    create_app().run(host=host, port=port)


if __name__ == "__main__":
    main()
