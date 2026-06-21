import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from greeter.config import deep_merge, load_json_file, load_layered_config  # noqa: E402


class DeepMergeTests(unittest.TestCase):
    def test_fills_nested_leaf_without_dropping_siblings(self):
        base = {
            "notify": {
                "email": {"host": "smtp.example.com", "port": 587, "password": ""},
                "teams_webhook_url": "https://teams/x",
            }
        }
        override = {"notify": {"email": {"password": "s3cret"}}}
        merged = deep_merge(base, override)
        # the secret leaf is filled in...
        self.assertEqual(merged["notify"]["email"]["password"], "s3cret")
        # ...without dropping its siblings (the shallow-update regression)
        self.assertEqual(merged["notify"]["email"]["host"], "smtp.example.com")
        self.assertEqual(merged["notify"]["email"]["port"], 587)
        self.assertEqual(merged["notify"]["teams_webhook_url"], "https://teams/x")

    def test_non_dict_override_replaces(self):
        merged = deep_merge({"a": {"b": 1}}, {"a": 2})
        self.assertEqual(merged["a"], 2)

    def test_does_not_mutate_inputs(self):
        base = {"a": {"b": 1}}
        override = {"a": {"c": 2}}
        deep_merge(base, override)
        self.assertEqual(base, {"a": {"b": 1}})
        self.assertEqual(override, {"a": {"c": 2}})

    def test_result_is_deep_copied_from_base(self):
        # mutating the merged result must not reach back into base defaults
        base = {"a": {"b": 1}}
        merged = deep_merge(base, {})
        merged["a"]["b"] = 99
        self.assertEqual(base["a"]["b"], 1)

    def test_adds_new_keys_from_override(self):
        merged = deep_merge({"a": 1}, {"b": 2})
        self.assertEqual(merged, {"a": 1, "b": 2})


class LoadJsonFileTests(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(load_json_file("/no/such/file.json"), {})

    def test_invalid_json_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text("{not json")
            self.assertEqual(load_json_file(p), {})

    def test_non_object_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "arr.json"
            p.write_text("[1, 2, 3]")
            self.assertEqual(load_json_file(p), {})


class LoadLayeredConfigTests(unittest.TestCase):
    DEFAULTS = {
        "text_model": "gemma3:1b",
        "notify": {"email": {"host": "", "password": ""}},
        "directory": {"source": "local", "m365": {"client_id": "", "client_secret": ""}},
    }

    def _write(self, d, name, obj):
        p = Path(d) / name
        p.write_text(json.dumps(obj))
        return p

    def test_precedence_defaults_config_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._write(d, "config.json", {
                "notify": {"email": {"host": "smtp.example.com"}},
                "directory": {"source": "m365", "m365": {"client_id": "abc"}},
            })
            sec = self._write(d, "secrets.json", {
                "notify": {"email": {"password": "p"}},
                "directory": {"m365": {"client_secret": "shh"}},
            })
            merged = load_layered_config(self.DEFAULTS, cfg, sec)

            # default preserved
            self.assertEqual(merged["text_model"], "gemma3:1b")
            # config.json overrides default
            self.assertEqual(merged["directory"]["source"], "m365")
            self.assertEqual(merged["notify"]["email"]["host"], "smtp.example.com")
            self.assertEqual(merged["directory"]["m365"]["client_id"], "abc")
            # secrets.json fills secret leaves without dropping config siblings
            self.assertEqual(merged["notify"]["email"]["password"], "p")
            self.assertEqual(merged["directory"]["m365"]["client_secret"], "shh")

    def test_missing_files_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            merged = load_layered_config(
                self.DEFAULTS, Path(d) / "nope.json", Path(d) / "nope2.json"
            )
            self.assertEqual(merged, self.DEFAULTS)
            # and it's a copy, not the same object
            self.assertIsNot(merged, self.DEFAULTS)


if __name__ == "__main__":
    unittest.main()
