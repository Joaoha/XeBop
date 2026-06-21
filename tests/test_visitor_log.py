import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from greeter.flow import Employee, GreeterFlow  # noqa: E402
from greeter.visitor_log import VisitorLog  # noqa: E402


def _emp():
    return Employee("Joao Hage", "FE", ("joao",), "slack:U01")


class VisitorLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "log.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_minimal_mode_hashes_visitor_name(self):
        log = VisitorLog(path=self.path, mode="minimal")
        log.record("Alice", _emp(), "notified")
        entries = list(log.entries())
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["visitor"].startswith("sha256:"))
        self.assertNotIn("Alice", entries[0]["visitor"])
        self.assertEqual(entries[0]["host"], "Joao Hage")
        self.assertEqual(entries[0]["outcome"], "notified")

    def test_standard_mode_keeps_visitor_name(self):
        log = VisitorLog(path=self.path, mode="standard")
        log.record("Alice", _emp(), "notified")
        entry = next(log.entries())
        self.assertEqual(entry["visitor"], "Alice")

    def test_unknown_host_records_null_host(self):
        log = VisitorLog(path=self.path, mode="standard")
        log.record("Alice", None, "unknown_host")
        entry = next(log.entries())
        self.assertIsNone(entry["host"])
        self.assertEqual(entry["outcome"], "unknown_host")

    def test_prune_drops_old_entries(self):
        log = VisitorLog(path=self.path, mode="standard", retention_days=7)
        # write one fresh, one old (by hand)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
        with self.path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": old_ts, "visitor": "Old", "host": None, "outcome": "x", "mode": "standard"}) + "\n")
        log.record("Alice", _emp(), "notified")
        removed = log.prune()
        self.assertEqual(removed, 1)
        names = [e["visitor"] for e in log.entries()]
        self.assertEqual(names, ["Alice"])


class VisitLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "log.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_in_opens_visit(self):
        log = VisitorLog(path=self.path, mode="standard")
        vid = log.check_in("Alice", _emp(), photo="visitor_photos/x.jpg")
        self.assertTrue(vid)
        opens = log.open_visits()
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0]["visit_id"], vid)
        self.assertEqual(opens[0]["host"], "Joao Hage")
        self.assertEqual(opens[0]["photo"], "visitor_photos/x.jpg")
        self.assertIsInstance(opens[0]["duration_seconds"], int)

    def test_check_out_closes_visit(self):
        log = VisitorLog(path=self.path, mode="standard")
        vid = log.check_in("Alice", _emp())
        log.check_out(vid)
        self.assertEqual(log.open_visits(), [])

    def test_find_open_visit_standard_case_insensitive(self):
        log = VisitorLog(path=self.path, mode="standard")
        log.check_in("Alice Smith", _emp())
        self.assertIsNotNone(log.find_open_visit("alice smith"))
        self.assertIsNone(log.find_open_visit("Bob"))

    def test_find_open_visit_minimal_same_day(self):
        log = VisitorLog(path=self.path, mode="minimal")
        log.check_in("Alice", _emp())
        v = log.find_open_visit("alice")  # encoder lowercases/strips
        self.assertIsNotNone(v)

    def test_find_returns_none_after_checkout(self):
        log = VisitorLog(path=self.path, mode="standard")
        vid = log.check_in("Alice", _emp())
        log.check_out(vid)
        self.assertIsNone(log.find_open_visit("Alice"))

    def test_prune_deletes_photo_file(self):
        photo = Path(self.tmp.name) / "face.jpg"
        photo.write_bytes(b"\xff\xd8\xff")  # fake jpeg
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
        with self.path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": old_ts, "visit_id": "abc", "kind": "check_in",
                "visitor": "Old", "host": None, "photo": str(photo), "mode": "standard",
            }) + "\n")
        log = VisitorLog(path=self.path, mode="standard", retention_days=7)
        self.assertEqual(log.prune(), 1)
        self.assertFalse(photo.exists())

    def test_legacy_rows_parse_and_are_ignored_by_open_visits(self):
        # an old-style audit row with no kind/visit_id
        with self.path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2020-01-01T00:00:00+00:00", "visitor": "X", "outcome": "notified"}) + "\n")
        log = VisitorLog(path=self.path, mode="standard")
        self.assertEqual(len(list(log.entries())), 1)
        self.assertEqual(log.open_visits(), [])


class FlowIntegrationTests(unittest.TestCase):
    def test_on_check_in_invoked_on_confirm(self):
        checkins = []
        flow = GreeterFlow(
            directory=[_emp()],
            notifier=lambda e, m: None,
            on_check_in=lambda name, host: checkins.append((name, host.name if host else None)),
        )
        flow.start()
        flow.handle("Alice Smith")
        flow.handle("yes")          # confirm visitor name
        flow.handle("Joao")
        flow.handle("yes")          # confirm host pairing
        self.assertEqual(checkins, [("Alice Smith", "Joao Hage")])

    def test_event_logger_invoked_on_unknown_host(self):
        events = []
        flow = GreeterFlow(
            directory=[_emp()],
            notifier=lambda e, m: None,
            event_logger=lambda *a: events.append(a),
        )
        flow.start()
        flow.handle("Alice Smith")
        flow.handle("yes")          # confirm visitor name
        flow.handle("Bob")
        flow.handle("Carol")
        flow.handle("Dan")
        self.assertEqual(events, [("Alice Smith", None, "unknown_host")])


if __name__ == "__main__":
    unittest.main()
