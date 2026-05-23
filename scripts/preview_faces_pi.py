#!/usr/bin/env python3
"""Open the greeter Tk window on the Pi and cycle every face state.

No mic, camera, LLM, or wake-word needed. Use this to visually sign off the
face animations on the actual LCD before running the full agent.

Usage (on the Pi, from the repo root):

    source venv/bin/activate
    python3 scripts/preview_faces_pi.py            # 5 s per state, all 7 states, then quit
    python3 scripts/preview_faces_pi.py --hold 3   # 3 s per state
    python3 scripts/preview_faces_pi.py --loop     # cycle forever (Esc to exit fullscreen, Ctrl-C to quit)

Frame cadence matches agent.py (50 ms for speaking, 500 ms otherwise).
"""
from __future__ import annotations

import argparse
import os
import sys
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageTk

STATES = ["idle", "listening", "thinking", "speaking", "capturing", "warmup", "error"]


def _fit_size(src_w: int, src_h: int, dst_w: int, dst_h: int) -> tuple[int, int]:
    scale = min(dst_w / src_w, dst_h / src_h)
    return max(1, int(src_w * scale)), max(1, int(src_h * scale))


class FacePreview:
    def __init__(self, master: tk.Tk, faces_dir: Path, hold_seconds: float, loop: bool):
        self.master = master
        self.faces_dir = faces_dir
        self.hold_ms = int(hold_seconds * 1000)
        self.loop = loop

        master.title("XeBop face preview")
        master.attributes("-fullscreen", True)
        master.configure(bg="black")
        master.bind("<Escape>", self._exit_fullscreen)
        master.bind("q", lambda _e: master.destroy())

        master.update_idletasks()
        self.screen_w = master.winfo_screenwidth()
        self.screen_h = master.winfo_screenheight()

        self.label = tk.Label(master, bg="black")
        self.label.place(x=0, y=0, width=self.screen_w, height=self.screen_h)

        self.caption = tk.Label(master, fg="white", bg="black", font=("Helvetica", 24))
        self.caption.place(x=20, y=self.screen_h - 60)

        self.animations: dict[str, list[ImageTk.PhotoImage]] = {}
        self._load()

        self.state_idx = 0
        self.frame_idx = 0
        self.state_started_ms = 0
        self.master.after(0, self._tick)

    def _exit_fullscreen(self, _event=None):
        self.master.attributes("-fullscreen", False)

    def _load(self) -> None:
        sw, sh = self.screen_w, self.screen_h
        for state in STATES:
            folder = self.faces_dir / state
            frames: list[ImageTk.PhotoImage] = []
            if folder.exists():
                for f in sorted(folder.glob("*.png")):
                    src = Image.open(f)
                    fw, fh = _fit_size(src.width, src.height, sw, sh)
                    canvas = Image.new("RGB", (sw, sh), color="black")
                    fitted = src.resize((fw, fh))
                    canvas.paste(fitted, ((sw - fw) // 2, (sh - fh) // 2))
                    frames.append(ImageTk.PhotoImage(canvas))
            if not frames:
                blank = Image.new("RGB", (sw, sh), color="#0000FF")
                frames.append(ImageTk.PhotoImage(blank))
            self.animations[state] = frames

    def _tick(self) -> None:
        state = STATES[self.state_idx]
        frames = self.animations[state]
        self.frame_idx = (self.frame_idx + 1) % len(frames)
        self.label.config(image=frames[self.frame_idx])
        self.caption.config(text=f"{state}  ({self.frame_idx + 1}/{len(frames)})")

        speed = 50 if state == "speaking" else 500
        self.state_started_ms += speed
        if self.state_started_ms >= self.hold_ms:
            self.state_started_ms = 0
            self.frame_idx = 0
            self.state_idx += 1
            if self.state_idx >= len(STATES):
                if self.loop:
                    self.state_idx = 0
                else:
                    self.master.after(500, self.master.destroy)
                    return
        self.master.after(speed, self._tick)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hold", type=float, default=5.0,
                        help="seconds to show each state (default: 5)")
    parser.add_argument("--loop", action="store_true",
                        help="cycle states forever")
    parser.add_argument("--faces-dir", default=None,
                        help="override faces directory (default: ./faces relative to repo root)")
    args = parser.parse_args()

    root_dir = Path(__file__).resolve().parent.parent
    faces_dir = Path(args.faces_dir) if args.faces_dir else (root_dir / "faces")
    if not faces_dir.exists():
        print(f"faces dir not found: {faces_dir}", file=sys.stderr)
        return 1

    # Allow running over SSH with X-forwarding by honoring DISPLAY; on the Pi
    # desktop you should run from the LCD session.
    if not os.environ.get("DISPLAY"):
        print("warning: DISPLAY is unset; Tk will fail. On the Pi, run from the LCD desktop "
              "(or ssh -X and set DISPLAY).", file=sys.stderr)

    root = tk.Tk()
    FacePreview(root, faces_dir, args.hold, args.loop)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
