import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import flask  # noqa: F401
    HAVE_FLASK = True
except Exception:
    HAVE_FLASK = False


@unittest.skipUnless(HAVE_FLASK, "flask not installed (runs on the Pi / in the venv)")
class WebUITests(unittest.TestCase):
    def setUp(self):
        from webui import app as appmod
        self.appmod = appmod
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self.cfg = d / "config.json"
        self.sec = d / "secrets.json"
        self.emp = d / "employees.json"
        self.cfg.write_text(json.dumps({"notify": {"email": {"host": "old"}}}))
        self.emp.write_text(json.dumps({"$schema_version": 1, "employees": []}))
        # Point the module at the temp files.
        self._orig = (appmod.CONFIG_PATH, appmod.SECRETS_PATH, appmod.EMPLOYEES_PATH, appmod.ROOT)
        appmod.CONFIG_PATH = self.cfg
        appmod.SECRETS_PATH = self.sec
        appmod.EMPLOYEES_PATH = self.emp
        appmod.ROOT = d
        self.app = appmod.create_app()
        self.app.config["TESTING"] = True

    def tearDown(self):
        (self.appmod.CONFIG_PATH, self.appmod.SECRETS_PATH,
         self.appmod.EMPLOYEES_PATH, self.appmod.ROOT) = self._orig
        self._tmp.cleanup()

    def test_index_requires_login(self):
        with self.app.test_client() as c:
            r = c.get("/")
            self.assertEqual(r.status_code, 302)
            self.assertIn("/login", r.headers["Location"])

    def test_first_run_sets_password_then_allows_access(self):
        with self.app.test_client() as c:
            r = c.post("/login", data={"password": "hunter2", "confirm": "hunter2"},
                       follow_redirects=False)
            self.assertEqual(r.status_code, 302)
            # password hash landed in secrets.json, not config.json
            secrets_after = json.loads(self.sec.read_text())
            self.assertTrue(secrets_after["webui"]["password_hash"])
            self.assertNotIn("hunter2", self.sec.read_text())
            # now authed -> index renders
            self.assertEqual(c.get("/").status_code, 200)

    def test_save_notify_routes_secret_to_secrets_file(self):
        with self.app.test_client() as c:
            c.post("/login", data={"password": "hunter2", "confirm": "hunter2"})
            c.post("/save/notify", data={
                "smtp_host": "smtp.example.com", "smtp_port": "587",
                "smtp_password": "s3cret", "smtp_starttls": "on",
            })
            config_after = json.loads(self.cfg.read_text())
            secrets_after = json.loads(self.sec.read_text())
            self.assertEqual(config_after["notify"]["email"]["host"], "smtp.example.com")
            self.assertNotIn("password", config_after["notify"]["email"])
            self.assertEqual(secrets_after["notify"]["email"]["password"], "s3cret")

    def test_save_endpoints_require_login(self):
        with self.app.test_client() as c:
            # configure a password but don't log in this client session
            from webui.settings_store import set_webui_password
            set_webui_password("pw1234", self.cfg, self.sec)
            r = c.post("/save/audio", data={"aplay_device": "x"})
            self.assertEqual(r.status_code, 302)
            self.assertIn("/login", r.headers["Location"])


if __name__ == "__main__":
    unittest.main()
