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


class FlowIntegrationTests(unittest.TestCase):
    def test_event_logger_invoked_on_notify(self):
        events = []

        def logger(name, host, outcome):
            events.append((name, host.name if host else None, outcome))

        flow = GreeterFlow(
            directory=[_emp()],
            notifier=lambda e, m: None,
            event_logger=logger,
        )
        flow.start()
        flow.handle("Alice")
        flow.handle("Joao")
        flow.handle("yes")
        self.assertEqual(events, [("Alice", "Joao Hage", "notified")])

    def test_event_logger_invoked_on_unknown_host(self):
        events = []
        flow = GreeterFlow(
            directory=[_emp()],
            notifier=lambda e, m: None,
            event_logger=lambda *a: events.append(a),
        )
        flow.start()
        flow.handle("Alice")
        flow.handle("Bob")
        flow.handle("Carol")
        flow.handle("Dan")
        self.assertEqual(events, [("Alice", None, "unknown_host")])


if __name__ == "__main__":
    unittest.main()
