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

from greeter.config import load_layered_config, load_json_file
from greeter.directory import resolve_directory_path
from greeter.flow import Employee, load_employees
from greeter import m365
from webui import system_info
from webui.settings_store import (
    atomic_write_json, save_settings, set_webui_password, verify_password,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
SECRETS_PATH = ROOT / "secrets.json"
EMPLOYEES_PATH = ROOT / "employees.json"

HOST_CHANNEL_PREFIXES = ("email", "teams", "slack")


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
        )

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
