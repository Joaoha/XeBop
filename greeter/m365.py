"""Microsoft 365 (Microsoft Graph) directory sync.

App-only (client-credentials) read of the org directory, mapped to the same
``Employee`` shape the greeter already uses and written to a local cache file
byte-compatible with ``employees.json``. The live agent never imports or calls
this module — only the settings web UI does, on an explicit Sync. That keeps
visitor conversations fully offline and immune to Graph latency/outages.

Stdlib only (urllib), mirroring greeter.notify's HTTP style — no msal/requests.

Azure setup (one-time, by an admin): register an app, grant the
``User.Read.All`` *application* permission, and grant admin consent. Then put
the tenant id + client id in config.json and the client secret in secrets.json.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping

from .flow import Employee, _normalize

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_LOGIN_BASE = "https://login.microsoftonline.com"
_USER_SELECT = "displayName,jobTitle,givenName,surname,mail,userPrincipalName,mailNickname,accountEnabled,userType"
CACHE_SCHEMA_VERSION = 1


class GraphError(Exception):
    """Raised when a Graph/token call fails."""


# --------------------------------------------------------------------------
# Low-level HTTP (patchable in tests via urllib.request.urlopen)
# --------------------------------------------------------------------------

def _post_form(url: str, fields: Mapping[str, str], timeout_s: float) -> dict:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise GraphError(f"token request failed: HTTP {exc.code} {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise GraphError(f"token request failed: {exc}") from exc


def _get_json(url: str, token: str, timeout_s: float) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise GraphError(f"Graph GET failed: HTTP {exc.code} {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise GraphError(f"Graph GET failed: {exc}") from exc


# --------------------------------------------------------------------------
# Auth + fetch
# --------------------------------------------------------------------------

def fetch_token(
    tenant_id: str, client_id: str, client_secret: str, timeout_s: float = 10.0
) -> str:
    """Acquire an app-only access token via the client-credentials flow."""
    if not (tenant_id and client_id and client_secret):
        raise GraphError("tenant_id, client_id and client_secret are all required")
    url = f"{_LOGIN_BASE}/{tenant_id}/oauth2/v2.0/token"
    payload = _post_form(
        url,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout_s,
    )
    token = payload.get("access_token")
    if not token:
        raise GraphError(f"token response had no access_token: {payload}")
    return token


def list_users(token: str, timeout_s: float = 15.0, max_pages: int = 100) -> list[dict]:
    """Return raw Graph user objects, following @odata.nextLink paging."""
    url = f"{GRAPH_BASE}/users?$select={_USER_SELECT}&$top=999"
    users: list[dict] = []
    pages = 0
    while url and pages < max_pages:
        payload = _get_json(url, token, timeout_s)
        users.extend(payload.get("value", []))
        url = payload.get("@odata.nextLink", "")
        pages += 1
    return users


# --------------------------------------------------------------------------
# Mapping Graph user -> Employee
# --------------------------------------------------------------------------

def user_to_employee(user: Mapping[str, Any], host_channel: str = "email") -> Employee | None:
    """Map one Graph user to an Employee, or None if not a greetable host.

    Skips disabled accounts and guests. For email routing, skips users with no
    mailbox (the notifier couldn't reach them anyway). alt_names are kept
    deliberately sparse (mailNickname only) — given/surname are already matched
    as fragments of the canonical name, and adding them only multiplies the
    name collisions that make find_employee return None.
    """
    if user.get("accountEnabled") is False:
        return None
    if str(user.get("userType", "Member")).lower() == "guest":
        return None

    name = (user.get("displayName") or "").strip()
    if not name:
        return None

    if host_channel == "teams":
        upn = (user.get("userPrincipalName") or "").strip()
        if not upn:
            return None
        channel_id = f"teams:{upn}"
    else:  # email (default)
        mail = (user.get("mail") or "").strip()
        if not mail:
            return None
        channel_id = f"email:{mail}"

    alt: list[str] = []
    nick = (user.get("mailNickname") or "").strip()
    if nick and _normalize(nick) and _normalize(nick) not in {_normalize(p) for p in name.split()}:
        alt.append(nick)

    return Employee(
        name=name,
        role=(user.get("jobTitle") or "").strip(),
        alt_names=tuple(alt),
        host_channel_id=channel_id,
    )


def fetch_directory(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    host_channel: str = "email",
    timeout_s: float = 15.0,
) -> list[Employee]:
    """Full sync: token -> list users -> map to greetable Employees."""
    token = fetch_token(tenant_id, client_id, client_secret, timeout_s)
    raw = list_users(token, timeout_s)
    out: list[Employee] = []
    for u in raw:
        emp = user_to_employee(u, host_channel)
        if emp is not None:
            out.append(emp)
    out.sort(key=lambda e: e.name.lower())
    return out


# --------------------------------------------------------------------------
# Collision detection + cache writing
# --------------------------------------------------------------------------

def _match_tokens(emp: Employee) -> set[str]:
    """Normalized tokens that would route a query to this employee."""
    tokens = {_normalize(emp.name)}
    tokens.update(_normalize(p) for p in emp.name.split())
    tokens.update(_normalize(a) for a in emp.alt_names)
    return {t for t in tokens if t}


def detect_collisions(employees: Iterable[Employee]) -> list[dict]:
    """Find match tokens shared by >1 employee.

    find_employee returns None when a query matches more than one person, so
    these are exactly the people a visitor could not be routed to reliably.
    Returns ``[{"token": str, "names": [str, ...]}, ...]``.
    """
    emps = list(employees)
    by_token: dict[str, list[str]] = {}
    for emp in emps:
        for tok in _match_tokens(emp):
            by_token.setdefault(tok, [])
            if emp.name not in by_token[tok]:
                by_token[tok].append(emp.name)
    collisions = [
        {"token": tok, "names": names}
        for tok, names in sorted(by_token.items())
        if len(names) > 1
    ]
    return collisions


def employees_to_cache(employees: Iterable[Employee]) -> dict:
    """Serialize to the same shape as employees.json so load_employees reads it."""
    return {
        "$schema_version": CACHE_SCHEMA_VERSION,
        "_generated_by": "m365_sync",
        "_generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "employees": [
            {
                "name": e.name,
                "role": e.role,
                "alt_names": list(e.alt_names),
                "host_channel_id": e.host_channel_id,
            }
            for e in employees
        ],
    }


def write_cache(path: str | Path, employees: Iterable[Employee]) -> None:
    """Atomically write the curated subset to the cache file.

    Same pattern as VisitorLog._atomic_rewrite: same-dir mkstemp + os.replace,
    so a reader never sees a half-written file.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(employees_to_cache(employees), indent=2)
    fd, tmp = tempfile.mkstemp(prefix=".m365_directory.", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload + "\n")
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def test_connection(
    tenant_id: str, client_id: str, client_secret: str, timeout_s: float = 10.0
) -> dict:
    """Probe credentials with a cheap call. Never raises.

    Returns ``{"ok": bool, "message": str}``.
    """
    try:
        token = fetch_token(tenant_id, client_id, client_secret, timeout_s)
        _get_json(f"{GRAPH_BASE}/users?$top=1&$select=displayName", token, timeout_s)
        return {"ok": True, "message": "Connected to Microsoft Graph."}
    except GraphError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:  # belt + suspenders: UI must always get a verdict
        return {"ok": False, "message": f"unexpected error: {exc}"}
