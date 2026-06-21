import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webui.settings_store import (  # noqa: E402
    hash_password,
    save_settings,
    set_webui_password,
    split_settings,
    verify_password,
)


class SplitSettingsTests(unittest.TestCase):
    def test_routes_secret_and_nonsecret_leaves(self):
        updates = {
            "notify": {
                "email": {"host": "smtp.example.com", "password": "shh"},
                "teams_webhook_url": "https://teams/x",
            },
            "directory": {"m365": {"client_id": "cid", "client_secret": "topsecret"}},
        }
        config_part, secret_part = split_settings(updates)

        # non-secrets in config_part
        self.assertEqual(config_part["notify"]["email"]["host"], "smtp.example.com")
        self.assertEqual(config_part["notify"]["teams_webhook_url"], "https://teams/x")
        self.assertEqual(config_part["directory"]["m365"]["client_id"], "cid")
        # secrets in secret_part only
        self.assertEqual(secret_part["notify"]["email"]["password"], "shh")
        self.assertEqual(secret_part["directory"]["m365"]["client_secret"], "topsecret")
        # and NOT cross-contaminated
        self.assertNotIn("password", config_part["notify"]["email"])
        self.assertNotIn("client_secret", config_part["directory"]["m365"])
        self.assertNotIn("host", secret_part["notify"]["email"])


class SaveSettingsTests(unittest.TestCase):
    def test_secrets_never_written_to_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.json"
            sec = Path(d) / "secrets.json"
            cfg.write_text(json.dumps({"text_model": "gemma3:1b", "notify": {"email": {"host": "old"}}}))

            save_settings(
                {"notify": {"email": {"host": "smtp.example.com", "password": "shh"}}},
                cfg, sec,
            )

            config_after = json.loads(cfg.read_text())
            secrets_after = json.loads(sec.read_text())

            # the secret is in secrets.json...
            self.assertEqual(secrets_after["notify"]["email"]["password"], "shh")
            # ...and absent from the tracked config.json
            self.assertNotIn("password", config_after["notify"]["email"])
            # non-secret updated; unrelated key preserved
            self.assertEqual(config_after["notify"]["email"]["host"], "smtp.example.com")
            self.assertEqual(config_after["text_model"], "gemma3:1b")

    def test_preserves_existing_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.json"
            sec = Path(d) / "secrets.json"
            sec.write_text(json.dumps({"webui": {"password_hash": "h", "salt": "s"}}))

            save_settings({"directory": {"m365": {"client_secret": "x"}}}, cfg, sec)

            secrets_after = json.loads(sec.read_text())
            self.assertEqual(secrets_after["webui"]["password_hash"], "h")
            self.assertEqual(secrets_after["directory"]["m365"]["client_secret"], "x")

    def test_no_temp_files_left(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.json"
            sec = Path(d) / "secrets.json"
            save_settings({"a": 1, "webui": {"salt": "z"}}, cfg, sec)
            names = sorted(p.name for p in Path(d).iterdir())
            self.assertEqual(names, ["config.json", "secrets.json"])


class PasswordTests(unittest.TestCase):
    def test_hash_then_verify(self):
        h, s = hash_password("hunter2")
        self.assertTrue(verify_password("hunter2", h, s))
        self.assertFalse(verify_password("wrong", h, s))

    def test_salt_makes_hashes_unique(self):
        h1, s1 = hash_password("same")
        h2, s2 = hash_password("same")
        self.assertNotEqual(s1, s2)
        self.assertNotEqual(h1, h2)

    def test_empty_hash_rejected(self):
        self.assertFalse(verify_password("x", "", ""))

    def test_set_webui_password_lands_in_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.json"
            sec = Path(d) / "secrets.json"
            set_webui_password("letmein", cfg, sec)
            secrets_after = json.loads(sec.read_text())
            h = secrets_after["webui"]["password_hash"]
            s = secrets_after["webui"]["salt"]
            self.assertTrue(verify_password("letmein", h, s))
            # plaintext never stored
            self.assertNotIn("letmein", sec.read_text())
            # nothing leaked to config.json
            self.assertFalse(cfg.exists() and "letmein" in cfg.read_text())


if __name__ == "__main__":
    unittest.main()
