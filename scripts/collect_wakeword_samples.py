#!/usr/bin/env python3
"""Record office-acoustic voice samples of the wake phrase for OpenWakeWord training.

Usage:
    python scripts/collect_wakeword_samples.py --speaker alice --count 30

Each sample is a 2-second 16 kHz mono WAV in `wakeword_samples/<speaker>/`.
Aim for 50-100 samples total across at least 3-5 speakers, recorded near the
greeter's actual mic position so the model learns the room acoustics.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from scipy.io import wavfile

SAMPLE_RATE = 16000
DURATION_S = 2.0


def record_one(out_path: Path) -> None:
    print(f"  [{out_path.name}] speak in 1s...", end="", flush=True)
    time.sleep(1.0)
    print(" recording", end="", flush=True)
    audio = sd.rec(int(DURATION_S * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16")
    sd.wait()
    print(" done")
    wavfile.write(out_path, SAMPLE_RATE, audio)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--speaker", required=True, help="speaker id, e.g. alice")
    ap.add_argument("--count", type=int, default=20, help="number of samples to record")
    ap.add_argument("--phrase", default="Hey XeBop", help="wake phrase the speaker should say")
    ap.add_argument("--out-dir", default="wakeword_samples")
    args = ap.parse_args()

    out_dir = Path(args.out_dir) / args.speaker
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(out_dir.glob("*.wav"))
    start_idx = len(existing)

    print(f"Recording {args.count} samples of '{args.phrase}' for speaker '{args.speaker}'.")
    print(f"Saving to {out_dir} (already have {start_idx} samples there).")
    print("Speak naturally, vary tone/distance slightly between takes.\n")

    for i in range(args.count):
        idx = start_idx + i + 1
        path = out_dir / f"{args.speaker}_{idx:03d}.wav"
        try:
            record_one(path)
        except KeyboardInterrupt:
            print("\nstopped by user")
            return 0
    print(f"\nDone. {out_dir} now has {len(list(out_dir.glob('*.wav')))} samples.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
