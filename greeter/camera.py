"""Camera access for XeBop: live preview + still capture, single owner.

One object owns the camera so the in-GUI preview and the check-in still never
contend for the device. Uses Picamera2 (libcamera, the Bookworm standard) when
it imports; otherwise `available` is False and the agent falls back to a
one-shot `rpicam-still`.

Picamera2 is imported lazily so this module loads fine on a dev box with no
camera (the import simply fails and we degrade). Frames come back as RGB
ndarrays; rotation/mirroring is left to the caller (the agent already rotates
captures with PIL and mirrors the preview for a selfie view).
"""

from __future__ import annotations

import threading
from typing import Optional


class CameraManager:
    def __init__(self, width: int = 640, height: int = 480):
        self.width = width
        self.height = height
        self._picam2 = None
        self._lock = threading.Lock()
        try:
            from picamera2 import Picamera2  # noqa: F401  (probe availability)
            self._Picamera2 = Picamera2
            self.available = True
        except Exception:
            self._Picamera2 = None
            self.available = False

    def _ensure_started_locked(self) -> None:
        if self._picam2 is None:
            cam = self._Picamera2()
            cfg = cam.create_preview_configuration(
                main={"size": (self.width, self.height), "format": "RGB888"}
            )
            cam.configure(cfg)
            cam.start()
            self._picam2 = cam

    def start(self) -> bool:
        """Open + start the camera. Returns True on success."""
        if not self.available:
            return False
        with self._lock:
            try:
                self._ensure_started_locked()
                return True
            except Exception as e:
                print(f"[CAMERA] start failed: {e}", flush=True)
                self.available = False
                return False

    def read_frame(self):
        """Return the latest frame as an RGB ndarray, or None."""
        if not self.available:
            return None
        with self._lock:
            if self._picam2 is None:
                return None
            try:
                return self._picam2.capture_array()
            except Exception:
                return None

    def capture_still(self, path: str) -> Optional[str]:
        """Capture a still to `path`. Returns the path, or None on failure."""
        if not self.available:
            return None
        with self._lock:
            try:
                self._ensure_started_locked()
                self._picam2.capture_file(path)
                return path
            except Exception as e:
                print(f"[CAMERA] capture failed: {e}", flush=True)
                return None

    def stop(self) -> None:
        """Release the camera (idempotent)."""
        with self._lock:
            if self._picam2 is not None:
                try:
                    self._picam2.stop()
                    self._picam2.close()
                except Exception:
                    pass
                self._picam2 = None
