"""Address-book source selection.

XeBop can read its greetable directory from two sources:

- ``local``  — the hand-edited ``employees.json``.
- ``m365``   — a curated subset synced from Microsoft 365 (Microsoft Graph),
  written to a local cache file in the *same shape* as ``employees.json``.

Because the M365 cache is byte-compatible with ``employees.json``, the runtime
read path is identical for both: ``load_employees(resolve_directory_path(cfg))``.
The agent therefore never calls Graph during a live conversation — a network
hiccup can't stall a visitor mid-greeting. Only the web UI's Sync hits Graph.

This is intentionally a plain function (not a provider class hierarchy) to match
the codebase style (cf. ``greeter.notify.make_notifier``).
"""

from __future__ import annotations

from typing import Any, Mapping

DEFAULT_LOCAL_PATH = "employees.json"
DEFAULT_M365_CACHE_PATH = "m365_directory.json"


def resolve_directory_path(
    config: Mapping[str, Any] | None,
    local_path: str = DEFAULT_LOCAL_PATH,
) -> str:
    """Return the file the runtime directory should be loaded from.

    Falls back to the local path for any unknown/missing source so a bad
    config value can never leave the agent without a directory to read.
    """
    directory = (config or {}).get("directory") or {}
    source = directory.get("source", "local")
    if source == "m365":
        m365 = directory.get("m365") or {}
        return m365.get("cache_path") or DEFAULT_M365_CACHE_PATH
    return local_path
