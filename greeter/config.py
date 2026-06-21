"""Layered configuration loading for XeBop.

The runtime config is built from three layers, lowest to highest precedence:

    DEFAULT_CONFIG  <  config.json  <  secrets.json

`config.json` is git-tracked and holds non-secret settings. `secrets.json` is
gitignored and mirrors the config tree, so secrets (SMTP password, M365 client
secret, web UI password hash) never land in the tracked file.

Merging is recursive: an overlay can set a single leaf (e.g.
`notify.email.password`) without dropping its siblings. A plain `dict.update`
would clobber whole subtrees, which is wrong for this layered model.

Kept dependency-free (stdlib only) so both the agent and the web UI can import
it without pulling in audio/GUI libraries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict:
    """Recursively merge ``override`` onto ``base``, returning a new dict.

    dict-into-dict merges key-by-key; anything else in ``override`` replaces
    the base value. Neither input is mutated.
    """
    merged: dict[str, Any] = {}
    for k, v in (base or {}).items():
        merged[k] = deep_merge(v, {}) if isinstance(v, dict) else v
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_json_file(path: str | Path) -> dict:
    """Return parsed JSON object at ``path``, or ``{}`` if missing/invalid."""
    p = Path(path)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                return data
            print(f"Config Error: {path} is not a JSON object. Ignoring it.")
        except Exception as e:
            print(f"Config Error reading {path}: {e}. Ignoring it.")
    return {}


def load_layered_config(
    default_config: Mapping[str, Any],
    config_path: str | Path,
    secrets_path: str | Path,
) -> dict:
    """Build the runtime config: defaults < config.json < secrets.json."""
    config = deep_merge(default_config, load_json_file(config_path))
    config = deep_merge(config, load_json_file(secrets_path))
    return config
