#!/usr/bin/env python3
"""Generate generic animated face frames for the greeter.

Produces 800x480 PNGs in faces/<state>/. No branding, no wordmarks — a simple
friendly robot face (two eyes + mouth area) with per-state animation. These
replace the earlier branded placeholders; commission bespoke art later if the
pilot calls for it.

Usage:
    python3 scripts/generate_generic_faces.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 800, 480
BG = (18, 20, 28)
EYE = (240, 244, 252)
EYE_DIM = (120, 130, 150)
ACCENT = (90, 170, 240)
ACCENT_DIM = (50, 95, 150)
WARN = (220, 110, 90)

ROOT = Path(__file__).resolve().parent.parent
FACES = ROOT / "faces"

EYE_CX_L = 290
EYE_CX_R = 510
EYE_CY = 200
EYE_RX = 60
EYE_RY = 80


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
    return Image.new("RGB", (W, H), BG)


def _eyes(d: ImageDraw.ImageDraw, openness: float = 1.0, color=EYE, look_dx: int = 0, look_dy: int = 0) -> None:
    ry = max(2, int(EYE_RY * openness))
    for cx in (EYE_CX_L, EYE_CX_R):
        d.rounded_rectangle(
            (cx - EYE_RX + look_dx, EYE_CY - ry + look_dy,
             cx + EYE_RX + look_dx, EYE_CY + ry + look_dy),
            radius=min(EYE_RX, ry), fill=color,
        )


def _save(img: Image.Image, state: str, idx: int) -> None:
    out = FACES / state
    out.mkdir(parents=True, exist_ok=True)
    img.save(out / f"{state}_{idx:02d}.png", "PNG")


def gen_idle() -> None:
    # gentle blink loop: open, open, open, half, closed, half, open, open
    sequence = [1.0, 1.0, 1.0, 1.0, 0.5, 0.08, 0.5, 1.0]
    for i, o in enumerate(sequence):
        img = _base()
        d = ImageDraw.Draw(img)
        _eyes(d, openness=o)
        # subtle mouth line
        d.rounded_rectangle((W // 2 - 60, 360, W // 2 + 60, 372), radius=6, fill=EYE_DIM)
        _save(img, "idle", i + 1)


def gen_listening() -> None:
    # eyes look slightly up + concentric ripple under the face
    cx, cy = W // 2, 400
    for i in range(8):
        img = _base()
        d = ImageDraw.Draw(img)
        _eyes(d, openness=1.05, look_dy=-6)
        for k in range(3):
            r = 20 + ((i + k * 3) % 9) * 14
            d.arc((cx - r, cy - r // 2, cx + r, cy + r // 2),
                  start=200, end=340, fill=ACCENT, width=3)
        _save(img, "listening", i + 1)


def gen_thinking() -> None:
    # eyes look around + spinner dots above
    cx, cy = W // 2, 110
    R = 36
    look_pattern = [(-8, -4), (-4, -8), (0, -10), (4, -8), (8, -4), (4, 0), (0, 2), (-4, 0)]
    for i in range(8):
        img = _base()
        d = ImageDraw.Draw(img)
        dx, dy = look_pattern[i]
        _eyes(d, openness=0.9, look_dx=dx, look_dy=dy)
        for k in range(8):
            a = math.radians(k * 45 + i * 20)
            x = cx + R * math.cos(a)
            y = cy + R * math.sin(a)
            shade = 60 + ((k - i) % 8) * 22
            col = (shade, min(255, shade + 60), min(255, shade + 120))
            d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=col)
        _save(img, "thinking", i + 1)


def gen_speaking() -> None:
    # eyes steady, animated waveform mouth
    bars = 11
    bar_w = 14
    gap = 10
    span = bars * bar_w + (bars - 1) * gap
    x0 = W // 2 - span // 2
    cy = 370
    import random
    rnd = random.Random(7)
    for i in range(8):
        img = _base()
        d = ImageDraw.Draw(img)
        _eyes(d, openness=1.0)
        for k in range(bars):
            base = 30 + abs(math.sin((k + i) * 0.7)) * 70
            h = int(base + rnd.randint(-12, 18))
            x = x0 + k * (bar_w + gap)
            d.rounded_rectangle((x, cy - h // 2, x + bar_w, cy + h // 2),
                                radius=4, fill=ACCENT)
        _save(img, "speaking", i + 1)


def gen_error() -> None:
    # eyes squinted, frowny mouth, mild flash
    for i, accent in enumerate([WARN, (160, 70, 60), WARN, (160, 70, 60)]):
        img = _base()
        d = ImageDraw.Draw(img)
        # X-eyes
        for cx in (EYE_CX_L, EYE_CX_R):
            d.line((cx - 40, EYE_CY - 40, cx + 40, EYE_CY + 40), fill=accent, width=10)
            d.line((cx - 40, EYE_CY + 40, cx + 40, EYE_CY - 40), fill=accent, width=10)
        # frown
        d.arc((W // 2 - 90, 340, W // 2 + 90, 420), start=200, end=340, fill=accent, width=8)
        _save(img, "error", i + 1)


def gen_capturing() -> None:
    # camera shutter iris animation
    cx, cy = W // 2, H // 2
    R = 110
    for i in range(6):
        img = _base()
        d = ImageDraw.Draw(img)
        d.ellipse((cx - R - 6, cy - R - 6, cx + R + 6, cy + R + 6),
                  outline=ACCENT_DIM, width=3)
        blades = 8
        offset = i * 8
        for k in range(blades):
            a0 = math.radians(k * (360 / blades) + offset)
            a1 = math.radians((k + 1) * (360 / blades) + offset)
            p1 = (cx + R * math.cos(a0), cy + R * math.sin(a0))
            p2 = (cx + R * math.cos(a1), cy + R * math.sin(a1))
            d.polygon([(cx, cy), p1, p2], outline=ACCENT, width=2)
        inner = max(4, 36 - i * 6)
        d.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), fill=EYE)
        _save(img, "capturing", i + 1)


def gen_warmup() -> None:
    bar_x0, bar_x1 = 180, 620
    bar_y0, bar_y1 = 300, 340
    for i, frac in enumerate([0.1, 0.25, 0.45, 0.65, 0.85, 1.0]):
        img = _base()
        d = ImageDraw.Draw(img)
        # sleepy eyes that wake up
        openness = 0.2 + 0.8 * frac
        _eyes(d, openness=openness, color=EYE)
        d.rounded_rectangle((bar_x0, bar_y0, bar_x1, bar_y1),
                            radius=10, outline=ACCENT_DIM, width=2)
        fill_x1 = int(bar_x0 + (bar_x1 - bar_x0) * frac)
        d.rounded_rectangle((bar_x0 + 4, bar_y0 + 4, fill_x1 - 4, bar_y1 - 4),
                            radius=8, fill=ACCENT)
        f = _font(20)
        msg = "warming up"
        bbox = d.textbbox((0, 0), msg, font=f)
        tw = bbox[2] - bbox[0]
        d.text((W // 2 - tw // 2, bar_y1 + 18), msg, fill=EYE_DIM, font=f)
        _save(img, "warmup", i + 1)


def main() -> None:
    states = ["idle", "listening", "thinking", "speaking", "error", "capturing", "warmup"]
    for state in states:
        for old in (FACES / state).glob("*.png"):
            old.unlink()
    gen_idle()
    gen_listening()
    gen_thinking()
    gen_speaking()
    gen_error()
    gen_capturing()
    gen_warmup()
    print("regenerated generic animated frames under faces/")


if __name__ == "__main__":
    main()
