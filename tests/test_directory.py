import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from greeter.directory import (  # noqa: E402
    DEFAULT_M365_CACHE_PATH,
    resolve_directory_path,
)


class ResolveDirectoryPathTests(unittest.TestCase):
    def test_local_is_default(self):
        self.assertEqual(resolve_directory_path({}), "employees.json")
        self.assertEqual(resolve_directory_path(None), "employees.json")

    def test_local_respects_passed_path(self):
        self.assertEqual(
            resolve_directory_path({"directory": {"source": "local"}}, "/etc/emp.json"),
            "/etc/emp.json",
        )

    def test_m365_returns_cache_path(self):
        cfg = {"directory": {"source": "m365", "m365": {"cache_path": "cache.json"}}}
        self.assertEqual(resolve_directory_path(cfg), "cache.json")

    def test_m365_without_cache_path_uses_default(self):
        cfg = {"directory": {"source": "m365"}}
        self.assertEqual(resolve_directory_path(cfg), DEFAULT_M365_CACHE_PATH)

    def test_unknown_source_falls_back_to_local(self):
        cfg = {"directory": {"source": "carrier-pigeon"}}
        self.assertEqual(resolve_directory_path(cfg, "employees.json"), "employees.json")


if __name__ == "__main__":
    unittest.main()
