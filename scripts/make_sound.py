#!/usr/bin/env python3
"""Synthesize a new greeter sound clip in the configured Piper voice.

Generates a .wav in the greeter's own voice (the same Piper model TTS uses),
so startup greetings / thinking hums match how XeBop speaks.

Usage:
    python3 scripts/make_sound.py <category> <name> "<text to say>"

  <category>  one of: greeting, thinking, ack, error
  <name>      file name (no extension), e.g. all_systems_go
  <text>      what the greeter should say

Examples:
    python3 scripts/make_sound.py greeting all_systems_go "All systems go."
    python3 scripts/make_sound.py thinking hmm "Hmm, let me check on that."

Run on the Pi (needs Piper + the voice model installed by setup.sh). The clip
lands in sounds/<category>_sounds/ and is picked up automatically — greeting
clips play at startup, thinking clips loop while transcribing.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from greeter.config import load_layered_config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = {"greeting", "thinking", "ack", "error"}


def main() -> None:
    if len(sys.argv) < 4 or sys.argv[1] not in CATEGORIES:
        print(__doc__)
        sys.exit(1)

    category, name = sys.argv[1], sys.argv[2]
    text = " ".join(sys.argv[3:]).strip()
    if not text:
        print("No text given.")
        sys.exit(1)

    cfg = load_layered_config({}, ROOT / "config.json", ROOT / "secrets.json")
    voice = (
        (cfg.get("branding") or {}).get("voice_model")
        or cfg.get("voice_model")
        or "piper/en_GB-semaine-medium.onnx"
    )
    piper = "piper" if shutil.which("piper") else str(ROOT / "piper" / "piper")

    out_dir = ROOT / "sounds" / f"{category}_sounds"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.wav"

    print(f"Synthesizing '{text}'\n  voice: {voice}\n  -> {out}")
    result = subprocess.run(
        [piper, "--model", str(voice), "--output_file", str(out)],
        input=text, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        print(f"piper failed: {result.stderr.strip()}")
        sys.exit(1)
    print("Done. It'll be used automatically next time the agent runs.")


if __name__ == "__main__":
    main()
