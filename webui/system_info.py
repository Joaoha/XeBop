"""Best-effort host introspection for the settings UI (audio devices, restart).

Everything here is defensive: on a non-Pi dev box (no sounddevice, no aplay,
no systemd) the functions return empty lists / structured failures rather than
raising, so the settings page always renders.
"""

from __future__ import annotations

import subprocess

AGENT_SERVICE = "xebop-agent.service"


def list_audio_devices() -> dict:
    """Return {"input": [...], "output": [...]} from sounddevice, or empties."""
    try:
        import sounddevice as sd  # imported lazily; not present on all dev boxes
    except Exception:
        return {"input": [], "output": []}
    try:
        devices = sd.query_devices()
    except Exception:
        return {"input": [], "output": []}
    inputs, outputs = [], []
    for idx, dev in enumerate(devices):
        name = dev.get("name", f"device {idx}")
        if dev.get("max_input_channels", 0) > 0:
            inputs.append({"index": idx, "name": name})
        if dev.get("max_output_channels", 0) > 0:
            outputs.append({"index": idx, "name": name})
    return {"input": inputs, "output": outputs}


def list_aplay_devices() -> list[str]:
    """Return ALSA PCM names from `aplay -L` (the `plughw:...` strings), or []."""
    try:
        out = subprocess.run(
            ["aplay", "-L"], capture_output=True, text=True, timeout=5, check=False
        )
    except Exception:
        return []
    names = []
    for line in out.stdout.splitlines():
        # PCM names are the non-indented lines; descriptions are indented.
        if line and not line[0].isspace():
            names.append(line.strip())
    return names


def restart_agent() -> dict:
    """Restart the agent via a narrowly-scoped passwordless sudo rule.

    Requires a sudoers entry limiting exactly this command (see setup.sh).
    Never runs the web UI itself as root. Returns {"ok", "message"}.
    """
    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", AGENT_SERVICE],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception as exc:
        return {"ok": False, "message": f"restart failed: {exc}"}
    if result.returncode == 0:
        return {"ok": True, "message": f"Restarted {AGENT_SERVICE}."}
    detail = (result.stderr or result.stdout or "").strip()
    return {"ok": False, "message": f"restart failed (exit {result.returncode}): {detail[:200]}"}
