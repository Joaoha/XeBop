import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from greeter.flow import (  # noqa: E402
    DEFAULT_PHRASES,
    Employee,
    FlowState,
    GreeterFlow,
    resolve_phrase,
)


def _dir():
    return [Employee("Joao Hage", "FE", ("joao",), "slack:U01")]


class ResolvePhraseTests(unittest.TestCase):
    def test_default_when_no_override(self):
        self.assertEqual(
            resolve_phrase({}, "confirm_no"), DEFAULT_PHRASES["confirm_no"]
        )

    def test_string_override(self):
        self.assertEqual(
            resolve_phrase({"confirm_no": "Oops, who again?"}, "confirm_no"),
            "Oops, who again?",
        )

    def test_blank_override_falls_back_to_default(self):
        self.assertEqual(
            resolve_phrase({"confirm_no": "   "}, "confirm_no"),
            DEFAULT_PHRASES["confirm_no"],
        )

    def test_placeholders_filled(self):
        out = resolve_phrase({"ask_host": "Hi {name}, who?"}, "ask_host", name="Alice")
        self.assertEqual(out, "Hi Alice, who?")

    def test_unknown_placeholder_falls_back_to_default(self):
        out = resolve_phrase(
            {"ask_host": "Hi {bogus}!"}, "ask_host", name="Alice"
        )
        self.assertEqual(out, DEFAULT_PHRASES["ask_host"].format(name="Alice"))

    def test_list_variant_is_one_of_the_options(self):
        opts = ["Who are you here for?", "Who's the visit for?"]
        seen = {resolve_phrase({"ask_host": opts}, "ask_host", name="A") for _ in range(25)}
        self.assertTrue(seen.issubset(set(opts)))

    def test_list_valued_default_with_no_override(self):
        # regression: "ack" defaults to a list; resolving with no override
        # must pick a variant, not crash on list.format()
        out = resolve_phrase({}, "ack")
        self.assertIn(out, DEFAULT_PHRASES["ack"])

    def test_missing_override_for_list_default(self):
        # a phrases block that lacks "ack" entirely
        out = resolve_phrase({"confirm_no": "x"}, "ack")
        self.assertIn(out, DEFAULT_PHRASES["ack"])

    def test_empty_list_falls_back_to_default(self):
        self.assertEqual(
            resolve_phrase({"ask_host": []}, "ask_host", name="A"),
            DEFAULT_PHRASES["ask_host"].format(name="A"),
        )


class FlowWithCustomPhrasesTests(unittest.TestCase):
    def test_custom_phrases_used_in_flow(self):
        phrases = {
            "ask_host": "Hey {name}! Who do you want?",
            "confirm_host": "So {visitor} wants {host}, ya?",
        }
        flow = GreeterFlow(directory=_dir(), notifier=lambda e, m: None, phrases=phrases)
        flow.start()
        flow.handle("Alice")
        r1 = flow.handle("yes")     # confirm name -> ask_host
        self.assertEqual(r1.say, "Hey Alice! Who do you want?")
        r2 = flow.handle("Joao")
        self.assertEqual(r2.say, "So Alice wants Joao Hage, ya?")
        self.assertEqual(r2.state, FlowState.AWAITING_CONFIRMATION)

    def test_default_phrases_unchanged_without_override(self):
        flow = GreeterFlow(directory=_dir(), notifier=lambda e, m: None)
        flow.start()
        flow.handle("Alice")
        r1 = flow.handle("yes")     # confirm name -> ask_host
        self.assertEqual(r1.say, "Nice to meet you, Alice. Who are you here to see?")


if __name__ == "__main__":
    unittest.main()
