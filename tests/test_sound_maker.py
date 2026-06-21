import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from greeter.sound_maker import (  # noqa: E402
    CATEGORIES,
    clip_path,
    delete_clip,
    list_clips,
    safe_name,
)


class SafeNameTests(unittest.TestCase):
    def test_sanitizes(self):
        self.assertEqual(safe_name("All Systems Go!"), "all_systems_go")
        self.assertEqual(safe_name("hello-world"), "hello-world")

    def test_blocks_traversal(self):
        self.assertEqual(safe_name("../../etc/passwd"), "etcpasswd")
        self.assertEqual(safe_name("a/b"), "ab")

    def test_empty(self):
        self.assertEqual(safe_name("  ...  "), "")


class ClipPathTests(unittest.TestCase):
    def test_under_sounds_dir(self):
        p = clip_path("/srv/xebop", "greeting", "Hi There")
        self.assertEqual(p, Path("/srv/xebop/sounds/greeting_sounds/hi_there.wav"))

    def test_bad_category(self):
        with self.assertRaises(ValueError):
            clip_path("/x", "bogus", "n")

    def test_bad_name(self):
        with self.assertRaises(ValueError):
            clip_path("/x", "greeting", "***")


class ListDeleteTests(unittest.TestCase):
    def test_list_and_delete(self):
        with tempfile.TemporaryDirectory() as d:
            gdir = Path(d) / "sounds" / "greeting_sounds"
            gdir.mkdir(parents=True)
            (gdir / "one.wav").write_bytes(b"\x00")
            (gdir / "two.wav").write_bytes(b"\x00")
            clips = list_clips(d)
            self.assertEqual(clips["greeting"], ["one", "two"])
            self.assertEqual(clips["thinking"], [])
            self.assertTrue(delete_clip(d, "greeting", "one"))
            self.assertEqual(list_clips(d)["greeting"], ["two"])
            self.assertFalse(delete_clip(d, "greeting", "nope"))

    def test_categories_known(self):
        self.assertIn("greeting", CATEGORIES)
        self.assertIn("thinking", CATEGORIES)


if __name__ == "__main__":
    unittest.main()
