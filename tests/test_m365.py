import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from greeter.flow import load_employees  # noqa: E402
from greeter import m365  # noqa: E402


def _resp(obj):
    """A context-manager mock whose .read() returns obj as JSON bytes."""
    m = MagicMock()
    m.__enter__.return_value.read.return_value = json.dumps(obj).encode("utf-8")
    return m


def _user(**kw):
    base = {
        "displayName": "Ada Lovelace",
        "jobTitle": "Engineer",
        "givenName": "Ada",
        "surname": "Lovelace",
        "mail": "ada@example.com",
        "userPrincipalName": "ada@example.com",
        "mailNickname": "ada",
        "accountEnabled": True,
        "userType": "Member",
    }
    base.update(kw)
    return base


class TokenTests(unittest.TestCase):
    def test_fetch_token_returns_access_token(self):
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = [_resp({"access_token": "tok123"})]
            tok = m365.fetch_token("tenant", "client", "secret")
            self.assertEqual(tok, "tok123")

    def test_fetch_token_requires_all_creds(self):
        with self.assertRaises(m365.GraphError):
            m365.fetch_token("", "client", "secret")

    def test_fetch_token_raises_without_access_token(self):
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = [_resp({"error": "bad"})]
            with self.assertRaises(m365.GraphError):
                m365.fetch_token("t", "c", "s")


class ListUsersPagingTests(unittest.TestCase):
    def test_follows_next_link(self):
        page1 = {"value": [_user(displayName="A")], "@odata.nextLink": "https://graph/next"}
        page2 = {"value": [_user(displayName="B")]}
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = [_resp(page1), _resp(page2)]
            users = m365.list_users("tok")
            self.assertEqual([u["displayName"] for u in users], ["A", "B"])
            self.assertEqual(urlopen.call_count, 2)


class MappingTests(unittest.TestCase):
    def test_basic_email_mapping(self):
        emp = m365.user_to_employee(_user(), host_channel="email")
        self.assertEqual(emp.name, "Ada Lovelace")
        self.assertEqual(emp.role, "Engineer")
        self.assertEqual(emp.host_channel_id, "email:ada@example.com")

    def test_teams_mapping_uses_upn(self):
        emp = m365.user_to_employee(_user(), host_channel="teams")
        self.assertEqual(emp.host_channel_id, "teams:ada@example.com")

    def test_skips_disabled(self):
        self.assertIsNone(m365.user_to_employee(_user(accountEnabled=False)))

    def test_skips_guest(self):
        self.assertIsNone(m365.user_to_employee(_user(userType="Guest")))

    def test_skips_email_route_without_mailbox(self):
        self.assertIsNone(m365.user_to_employee(_user(mail=None), host_channel="email"))

    def test_skips_blank_display_name(self):
        self.assertIsNone(m365.user_to_employee(_user(displayName="  ")))

    def test_alt_names_sparse_no_given_surname(self):
        emp = m365.user_to_employee(_user(mailNickname="alovelace"))
        # only the mailNickname, never given/surname
        self.assertEqual(emp.alt_names, ("alovelace",))

    def test_redundant_nickname_dropped(self):
        # nickname equal to a name part adds nothing
        emp = m365.user_to_employee(_user(displayName="Ada Lovelace", mailNickname="ada"))
        self.assertEqual(emp.alt_names, ())


class FetchDirectoryTests(unittest.TestCase):
    def test_token_then_users_filtered_and_sorted(self):
        page = {"value": [
            _user(displayName="Zara Zed", mail="zara@example.com"),
            _user(displayName="Disabled Dan", accountEnabled=False),
            _user(displayName="Ada Lovelace"),
            _user(displayName="Guest Greg", userType="Guest"),
        ]}
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = [_resp({"access_token": "tok"}), _resp(page)]
            emps = m365.fetch_directory("t", "c", "s", host_channel="email")
            names = [e.name for e in emps]
            # disabled + guest filtered, remainder sorted by name
            self.assertEqual(names, ["Ada Lovelace", "Zara Zed"])


class CollisionTests(unittest.TestCase):
    def test_detects_shared_first_name(self):
        from greeter.flow import Employee
        emps = [
            Employee("John Smith", "", (), "email:js@x.com"),
            Employee("John Doe", "", (), "email:jd@x.com"),
            Employee("Ada Lovelace", "", (), "email:ada@x.com"),
        ]
        collisions = m365.detect_collisions(emps)
        tokens = {c["token"] for c in collisions}
        self.assertIn("john", tokens)
        john = next(c for c in collisions if c["token"] == "john")
        self.assertCountEqual(john["names"], ["John Smith", "John Doe"])

    def test_no_collisions_when_unique(self):
        from greeter.flow import Employee
        emps = [
            Employee("Ada Lovelace", "", (), "email:a@x.com"),
            Employee("Grace Hopper", "", (), "email:g@x.com"),
        ]
        self.assertEqual(m365.detect_collisions(emps), [])


class CacheWriteTests(unittest.TestCase):
    def test_round_trips_through_load_employees(self):
        from greeter.flow import Employee
        emps = [
            Employee("Ada Lovelace", "Engineer", ("alovelace",), "email:ada@example.com"),
            Employee("Grace Hopper", "Admiral", (), "teams:grace@example.com"),
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "m365_directory.json"
            m365.write_cache(path, emps)
            loaded = load_employees(path)
            self.assertEqual([e.name for e in loaded], ["Ada Lovelace", "Grace Hopper"])
            self.assertEqual(loaded[0].host_channel_id, "email:ada@example.com")
            self.assertEqual(loaded[0].alt_names, ("alovelace",))
            self.assertEqual(loaded[1].host_channel_id, "teams:grace@example.com")

    def test_write_is_atomic_no_leftover_temp(self):
        from greeter.flow import Employee
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "m365_directory.json"
            m365.write_cache(path, [Employee("A", "", (), "email:a@x.com")])
            leftovers = [p.name for p in Path(d).iterdir() if p.name != "m365_directory.json"]
            self.assertEqual(leftovers, [])


class TestConnectionTests(unittest.TestCase):
    def test_ok(self):
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = [_resp({"access_token": "tok"}), _resp({"value": []})]
            result = m365.test_connection("t", "c", "s")
            self.assertTrue(result["ok"])

    def test_failure_is_structured_not_raised(self):
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None)
            result = m365.test_connection("t", "c", "s")
            self.assertFalse(result["ok"])
            self.assertIn("401", result["message"])


if __name__ == "__main__":
    unittest.main()
