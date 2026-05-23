#!/usr/bin/env python3
"""Bundle the generic face frames into one animated GIF per state.

Lets reviewers eyeball the animations without running the full Tk agent.

Usage:
    python3 scripts/preview_faces.py            # writes preview_faces/<state>.gif
    python3 scripts/preview_faces.py --open     # also opens the output dir
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FACES = ROOT / "faces"
OUT = ROOT / "preview_faces"

FRAME_MS = {
    "idle": 120,
    "listening": 80,
    "thinking": 70,
    "speaking": 50,
    "capturing": 90,
    "warmup": 150,
    "error": 120,
}


def build(state: str) -> Path | None:
    src = FACES / state
    frames = sorted(src.glob("*.png"))
    if not frames:
        print(f"  skip {state}: no frames", file=sys.stderr)
        return None
    OUT.mkdir(exist_ok=True)
    imgs = [Image.open(p).convert("RGB") for p in frames]
    dst = OUT / f"{state}.gif"
    imgs[0].save(
        dst,
        save_all=True,
        append_images=imgs[1:],
        duration=FRAME_MS.get(state, 100),
        loop=0,
        optimize=True,
    )
    print(f"  {state}: {len(imgs)} frames -> {dst.relative_to(ROOT)}")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="open output dir (macOS)")
    args = ap.parse_args()

    states = sorted(p.name for p in FACES.iterdir() if p.is_dir())
    print(f"Building previews for {len(states)} states ->")
    for s in states:
        build(s)
    if args.open and sys.platform == "darwin":
        subprocess.run(["open", str(OUT)], check=False)
    print(f"\nDone. Open {OUT.relative_to(ROOT)}/ to view.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
