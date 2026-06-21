#!/usr/bin/env python3
"""Generate interim branded placeholder face frames for the greeter.

Produces 800x480 PNGs in faces/<state>/ matching the BRANDING.md style:
near-black background, cool-blue motifs, white XeBop wordmark, geometric.
These are placeholders — replace with commissioned art when it lands.

Usage:
    python3 scripts/generate_branded_faces.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 800, 480
BG = (10, 14, 26)
GRID = (20, 28, 48)
BLUE = (58, 130, 247)
BLUE_DIM = (32, 72, 140)
WHITE = (235, 240, 250)
RED_DIM = (200, 90, 90)

ROOT = Path(__file__).resolve().parent.parent
FACES = ROOT / "faces"


def _font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _base() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    for x in range(0, W, 40):
        d.line([(x, 0), (x, H)], fill=GRID, width=1)
    for y in range(0, H, 40):
        d.line([(0, y), (W, y)], fill=GRID, width=1)
    f_brand = _font(28)
    f_tag = _font(16)
    d.text((24, 18), "XeBop", fill=WHITE, font=f_brand)
    d.text((24, H - 36), "XENON", fill=BLUE, font=f_tag)
    return img


def _label(img: Image.Image, text: str) -> None:
    d = ImageDraw.Draw(img)
    f = _font(18)
    bbox = d.textbbox((0, 0), text, font=f)
    tw = bbox[2] - bbox[0]
    d.text((W - tw - 24, H - 36), text, fill=WHITE, font=f)


def _save(img: Image.Image, state: str, idx: int) -> None:
    out = FACES / state
    out.mkdir(parents=True, exist_ok=True)
    img.save(out / f"{state}_{idx:02d}.png", "PNG")


def gen_sleep() -> None:
    cx, cy = W // 2, H // 2
    f_small, f_med, f_big = _font(28), _font(42), _font(60)
    for i in range(6):
        img = _base()
        d = ImageDraw.Draw(img)
        # closed eyes: two short arcs
        for ex in (cx - 70, cx + 70):
            d.arc((ex - 30, cy - 16, ex + 30, cy + 20), start=200, end=340, fill=BLUE, width=4)
        drift = (i % 6) * 5
        bx, by = cx + 60, cy - 70
        d.text((bx - drift, by - drift), "z", fill=BLUE_DIM, font=f_small)
        d.text((bx + 26 - drift, by - 38 - drift), "z", fill=BLUE, font=f_med)
        d.text((bx + 64 - drift, by - 90 - drift), "Z", fill=WHITE, font=f_big)
        _label(img, "sleep")
        _save(img, "sleep", i + 1)


def gen_idle() -> None:
    img = _base()
    d = ImageDraw.Draw(img)
    cx, cy = W // 2, H // 2
    d.ellipse((cx - 60, cy - 60, cx + 60, cy + 60), outline=BLUE_DIM, width=2)
    d.ellipse((cx - 12, cy - 12, cx + 12, cy + 12), fill=BLUE)
    _label(img, "idle")
    _save(img, "idle", 1)


def gen_listening() -> None:
    cx, cy = W // 2, H // 2
    for i, r0 in enumerate([30, 60, 90]):
        img = _base()
        d = ImageDraw.Draw(img)
        for k, r in enumerate([r0, r0 + 35, r0 + 70]):
            alpha = max(40, 200 - k * 60)
            color = (BLUE[0], BLUE[1], BLUE[2])
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=3)
        d.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=WHITE)
        _label(img, "listening")
        _save(img, "listening", i + 1)


def gen_thinking() -> None:
    cx, cy = W // 2, H // 2
    R = 90
    for i in range(4):
        img = _base()
        d = ImageDraw.Draw(img)
        d.ellipse((cx - R, cy - R, cx + R, cy + R), outline=BLUE_DIM, width=2)
        start = i * 90
        d.arc((cx - R, cy - R, cx + R, cy + R), start=start, end=start + 70, fill=BLUE, width=6)
        d.arc((cx - R, cy - R, cx + R, cy + R), start=start + 180, end=start + 250, fill=BLUE, width=6)
        _label(img, "thinking")
        _save(img, "thinking", i + 1)


def gen_speaking() -> None:
    cx, cy = W // 2, H // 2
    bars = 9
    bar_w = 14
    gap = 10
    span = bars * bar_w + (bars - 1) * gap
    x0 = cx - span // 2
    heights = [
        [40, 80, 130, 90, 50, 110, 140, 70, 30],
        [60, 110, 70, 140, 90, 50, 100, 130, 60],
        [30, 70, 100, 60, 130, 90, 70, 110, 140],
        [80, 50, 120, 100, 70, 140, 60, 90, 50],
    ]
    for i, hs in enumerate(heights):
        img = _base()
        d = ImageDraw.Draw(img)
        for k, h in enumerate(hs):
            x = x0 + k * (bar_w + gap)
            d.rounded_rectangle((x, cy - h // 2, x + bar_w, cy + h // 2), radius=4, fill=BLUE)
        _label(img, "speaking")
        _save(img, "speaking", i + 1)


def gen_error() -> None:
    img = _base()
    d = ImageDraw.Draw(img)
    cx, cy = W // 2, H // 2
    pts = [(cx, cy - 70), (cx - 80, cy + 60), (cx + 80, cy + 60)]
    d.polygon(pts, outline=RED_DIM, width=4)
    f = _font(72)
    d.text((cx - 8, cy - 30), "!", fill=RED_DIM, font=f)
    _label(img, "error")
    _save(img, "error", 1)


def gen_capturing() -> None:
    img = _base()
    d = ImageDraw.Draw(img)
    cx, cy = W // 2, H // 2
    R = 90
    blades = 6
    for k in range(blades):
        a0 = math.radians(k * (360 / blades))
        a1 = math.radians((k + 1) * (360 / blades))
        p1 = (cx + R * math.cos(a0), cy + R * math.sin(a0))
        p2 = (cx + R * math.cos(a1), cy + R * math.sin(a1))
        d.polygon([(cx, cy), p1, p2], outline=BLUE, width=2)
    d.ellipse((cx - 18, cy - 18, cx + 18, cy + 18), fill=WHITE)
    _label(img, "capturing")
    _save(img, "capturing", 1)


def gen_warmup() -> None:
    bar_x0, bar_x1 = 200, 600
    bar_y0, bar_y1 = 220, 260
    for i, frac in enumerate([0.25, 0.6, 0.95]):
        img = _base()
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((bar_x0, bar_y0, bar_x1, bar_y1), radius=8, outline=BLUE_DIM, width=2)
        fill_x1 = int(bar_x0 + (bar_x1 - bar_x0) * frac)
        d.rounded_rectangle((bar_x0 + 4, bar_y0 + 4, fill_x1 - 4, bar_y1 - 4), radius=6, fill=BLUE)
        f = _font(20)
        d.text((W // 2 - 50, bar_y1 + 24), "warming up", fill=WHITE, font=f)
        _label(img, "warmup")
        _save(img, "warmup", i + 1)


def main() -> None:
    for state in ["idle", "sleep", "listening", "thinking", "speaking", "error", "capturing", "warmup"]:
        for old in (FACES / state).glob("*.png"):
            old.unlink()
    gen_sleep()
    gen_idle()
    gen_listening()
    gen_thinking()
    gen_speaking()
    gen_error()
    gen_capturing()
    gen_warmup()
    print("regenerated branded placeholder frames under faces/")


if __name__ == "__main__":
    main()
