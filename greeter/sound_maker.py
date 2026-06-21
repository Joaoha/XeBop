"""Generate / list / delete greeter sound clips.

Shared by the CLI (scripts/make_sound.py) and the settings web UI. Clips are
synthesized in the greeter's own Piper voice and stored under
sounds/<category>_sounds/ where the agent already picks them up.

Pure helpers (safe_name, clip_path, list_clips, delete_clip) are stdlib-only
and unit-tested; synthesize() shells out to Piper and only runs on a box that
has it (the Pi).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

CATEGORIES = ("greeting", "thinking", "ack", "error")


def safe_name(name: str) -> str:
    """A filesystem-safe clip name: lowercase, [a-z0-9_-] only (no traversal)."""
    s = re.sub(r"[^a-z0-9_-]", "", (name or "").strip().lower().replace(" ", "_"))
    return s.strip("-_")


def clip_path(root: str | Path, category: str, name: str) -> Path:
    """Resolved path for a clip; raises ValueError on a bad category/name."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category: {category}")
    sn = safe_name(name)
    if not sn:
        raise ValueError("invalid name")
    return Path(root) / "sounds" / f"{category}_sounds" / f"{sn}.wav"


def list_clips(root: str | Path) -> dict[str, list[str]]:
    """{category: [clip names without extension]} for all categories."""
    out: dict[str, list[str]] = {}
    for cat in CATEGORIES:
        d = Path(root) / "sounds" / f"{cat}_sounds"
        out[cat] = sorted(p.stem for p in d.glob("*.wav")) if d.exists() else []
    return out


def delete_clip(root: str | Path, category: str, name: str) -> bool:
    try:
        p = clip_path(root, category, name)
    except ValueError:
        return False
    sounds_root = (Path(root) / "sounds").resolve()
    rp = p.resolve()
    if str(rp).startswith(str(sounds_root)) and rp.exists():
        try:
            rp.unlink()
            return True
        except OSError:
            return False
    return False


def synthesize(root: str | Path, category: str, name: str, text: str,
               voice_model: str) -> tuple[bool, str]:
    """Render `text` to sounds/<category>_sounds/<name>.wav via Piper.

    Returns (ok, message). Requires Piper + the voice model (i.e. runs on the Pi).
    """
    text = (text or "").strip()
    if not text:
        return False, "No text given."
    try:
        out = clip_path(root, category, name)
    except ValueError as e:
        return False, str(e)

    voice = voice_model or "piper/en_GB-semaine-medium.onnx"
    if not Path(voice).is_absolute():
        voice = str(Path(root) / voice)
    piper = "piper" if shutil.which("piper") else str(Path(root) / "piper" / "piper")

    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        res = subprocess.run(
            [piper, "--model", voice, "--output_file", str(out)],
            input=text, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
    except FileNotFoundError:
        return False, "Piper not found on this device."
    except Exception as e:  # pragma: no cover - defensive
        return False, f"synthesis error: {e}"
    if res.returncode == 0 and out.exists():
        return True, f"Created {out.name}"
    return False, (res.stderr or "Piper failed").strip()[:200]
