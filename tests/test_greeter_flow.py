import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from greeter.flow import (  # noqa: E402
    Employee,
    FlowState,
    GreeterFlow,
    find_employee,
    load_employees,
)


def _dir():
    return [
        Employee("Joao Hage", "Founding Engineer", ("joao", "hage", "jo"), "slack:U01"),
        Employee("Bren Polly", "Hardware Lead", ("bren", "polly"), "slack:U02"),
        Employee("Sam Rivera", "Office Manager", ("sam", "rivera"), "email:sam@x"),
    ]


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def __call__(self, employee, message):
        self.calls.append((employee, message))


class HappyPathTests(unittest.TestCase):
    def test_full_flow_resolves_and_notifies(self):
        notifier = FakeNotifier()
        flow = GreeterFlow(directory=_dir(), notifier=notifier)

        opening = flow.start()
        self.assertEqual(opening.state, FlowState.AWAITING_VISITOR_NAME)
        self.assertIn("name", opening.say.lower())

        r1 = flow.handle("My name is Alice")
        self.assertEqual(r1.state, FlowState.AWAITING_HOST_NAME)
        self.assertIn("Alice", r1.say)

        r2 = flow.handle("I'm here to see Joao")
        self.assertEqual(r2.state, FlowState.AWAITING_CONFIRMATION)
        self.assertIn("Joao Hage", r2.say)
        self.assertIn("Alice", r2.say)

        r3 = flow.handle("yes")
        self.assertEqual(r3.state, FlowState.DONE)
        self.assertTrue(r3.done)
        self.assertIsNotNone(r3.notify)
        self.assertEqual(r3.notify.name, "Joao Hage")

        self.assertEqual(len(notifier.calls), 1)
        emp, msg = notifier.calls[0]
        self.assertEqual(emp.host_channel_id, "slack:U01")
        self.assertIn("Alice", msg)
        self.assertIn("lobby", msg.lower())


class MatchingTests(unittest.TestCase):
    def test_alt_name_match(self):
        self.assertEqual(find_employee("bren", _dir()).name, "Bren Polly")

    def test_last_name_only(self):
        self.assertEqual(find_employee("rivera", _dir()).name, "Sam Rivera")

    def test_case_insensitive(self):
        self.assertEqual(find_employee("JOAO", _dir()).name, "Joao Hage")

    def test_unknown_returns_none(self):
        self.assertIsNone(find_employee("nobody", _dir()))

    def test_empty_returns_none(self):
        self.assertIsNone(find_employee("", _dir()))


class CorrectionTests(unittest.TestCase):
    def test_no_on_confirm_reasks_host(self):
        flow = GreeterFlow(directory=_dir(), notifier=FakeNotifier())
        flow.start()
        flow.handle("Alice")
        flow.handle("Bren")
        r = flow.handle("no")
        self.assertEqual(r.state, FlowState.AWAITING_HOST_NAME)
        self.assertIsNone(flow.host)

    def test_unrecognized_confirm_stays(self):
        flow = GreeterFlow(directory=_dir(), notifier=FakeNotifier())
        flow.start()
        flow.handle("Alice")
        flow.handle("Joao")
        r = flow.handle("uhh")
        self.assertEqual(r.state, FlowState.AWAITING_CONFIRMATION)

    def test_unknown_host_retries_then_gives_up(self):
        notifier = FakeNotifier()
        flow = GreeterFlow(directory=_dir(), notifier=notifier)
        flow.start()
        flow.handle("Alice")
        r1 = flow.handle("Bob")
        self.assertEqual(r1.state, FlowState.AWAITING_HOST_NAME)
        r2 = flow.handle("Carol")
        self.assertEqual(r2.state, FlowState.AWAITING_HOST_NAME)
        r3 = flow.handle("Dan")
        self.assertEqual(r3.state, FlowState.DONE)
        self.assertTrue(r3.done)
        self.assertEqual(notifier.calls, [])

    def test_blank_visitor_name_reasks(self):
        flow = GreeterFlow(directory=_dir(), notifier=FakeNotifier())
        flow.start()
        r = flow.handle("")
        self.assertEqual(r.state, FlowState.AWAITING_VISITOR_NAME)


class EmployeesFileTests(unittest.TestCase):
    def test_sample_file_loads(self):
        path = Path(__file__).resolve().parent.parent / "employees.json"
        emps = load_employees(path)
        self.assertGreaterEqual(len(emps), 1)
        for e in emps:
            self.assertTrue(e.name)
            self.assertTrue(e.host_channel_id)

    def test_sample_file_is_valid_json_with_schema_block(self):
        path = Path(__file__).resolve().parent.parent / "employees.json"
        data = json.loads(path.read_text())
        self.assertIn("employees", data)
        self.assertIn("_schema", data)


if __name__ == "__main__":
    unittest.main()
