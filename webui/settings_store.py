"""Read/write XeBop settings, keeping secrets out of the tracked config.

The web UI edits a merged view of the config but must persist each value to
the right file: secret leaves go to the gitignored ``secrets.json``, everything
else to the tracked ``config.json``. This module owns that split, the atomic
writes, and the web-UI password hashing.

Stdlib only — reuses ``greeter.config`` for the merge/read primitives.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from greeter.config import deep_merge, load_json_file

# Leaves (by path) that must live in secrets.json, never config.json.
SECRET_PATHS: tuple[tuple[str, ...], ...] = (
    ("notify", "email", "password"),
    ("directory", "m365", "client_secret"),
    ("webui", "password_hash"),
    ("webui", "salt"),
)

_PBKDF2_ITERATIONS = 200_000


def is_secret_path(path: tuple[str, ...]) -> bool:
    return path in SECRET_PATHS


def split_settings(updates: Mapping[str, Any]) -> tuple[dict, dict]:
    """Split a nested updates dict into (config_part, secret_part).

    Each leaf is routed by its full path: secret paths land in secret_part,
    everything else in config_part, both rebuilt as nested dicts.
    """
    config_part: dict[str, Any] = {}
    secret_part: dict[str, Any] = {}

    def walk(node: Mapping[str, Any], path: tuple[str, ...]) -> None:
        for key, value in node.items():
            here = path + (key,)
            if isinstance(value, dict):
                walk(value, here)
            else:
                target = secret_part if is_secret_path(here) else config_part
                cursor = target
                for part in here[:-1]:
                    cursor = cursor.setdefault(part, {})
                cursor[here[-1]] = value

    walk(updates, ())
    return config_part, secret_part


def atomic_write_json(path: str | Path, obj: Any) -> None:
    """Write ``obj`` as pretty JSON, atomically (same-dir temp + os.replace)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, indent=2)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", dir=str(p.parent))
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


def save_settings(
    updates: Mapping[str, Any], config_path: str | Path, secrets_path: str | Path
) -> None:
    """Persist ``updates``: secret leaves to secrets.json, the rest to config.json.

    Both files are read-modify-written so unrelated keys are preserved (a
    deep-merge of the new values onto the existing file).
    """
    config_part, secret_part = split_settings(updates)
    if config_part:
        merged = deep_merge(load_json_file(config_path), config_part)
        atomic_write_json(config_path, merged)
    if secret_part:
        merged = deep_merge(load_json_file(secrets_path), secret_part)
        atomic_write_json(secrets_path, merged)


# --------------------------------------------------------------------------
# Web-UI password (stored as a salted PBKDF2 hash, never plaintext)
# --------------------------------------------------------------------------

def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    """Return (hash_hex, salt_hex) for ``password``. Generates a salt if none given."""
    if salt_hex:
        salt = bytes.fromhex(salt_hex)
    else:
        salt = os.urandom(16)
        salt_hex = salt.hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return digest.hex(), salt_hex


def verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
    """Constant-time check of ``password`` against a stored hash+salt."""
    if not (hash_hex and salt_hex):
        return False
    try:
        candidate, _ = hash_password(password, salt_hex)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, hash_hex)


def set_webui_password(password: str, config_path: str | Path, secrets_path: str | Path) -> None:
    """Hash ``password`` and store the hash+salt in secrets.json."""
    hash_hex, salt_hex = hash_password(password)
    save_settings(
        {"webui": {"password_hash": hash_hex, "salt": salt_hex}},
        config_path,
        secrets_path,
    )
