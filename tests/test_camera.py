import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from greeter.camera import CameraManager  # noqa: E402


class CameraManagerTests(unittest.TestCase):
    """Without Picamera2 (dev box / no camera) everything degrades safely."""

    def test_constructs(self):
        cam = CameraManager()
        self.assertIn(cam.available, (True, False))

    def test_unavailable_methods_are_safe_noops(self):
        cam = CameraManager()
        if cam.available:
            self.skipTest("Picamera2 present; skipping unavailable-path test")
        self.assertFalse(cam.start())
        self.assertIsNone(cam.read_frame())
        self.assertIsNone(cam.capture_still("/tmp/xebop_test_should_not_exist.jpg"))
        cam.stop()  # must not raise


if __name__ == "__main__":
    unittest.main()
