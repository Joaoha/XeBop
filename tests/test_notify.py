import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from greeter.flow import Employee  # noqa: E402
from greeter.notify import (  # noqa: E402
    ConsoleNotifier,
    RoutingNotifier,
    SlackWebhookNotifier,
    make_notifier,
)


def _emp(channel="slack:U01ABC"):
    return Employee("Joao Hage", "FE", ("joao",), channel)


class ConsoleNotifierTests(unittest.TestCase):
    def test_prints(self):
        n = ConsoleNotifier()
        # smoke: should not raise
        n(_emp(), "Alice in lobby")


class SlackWebhookNotifierTests(unittest.TestCase):
    def test_posts_json_with_user_mention(self):
        n = SlackWebhookNotifier(webhook_url="https://hooks.slack.com/test")
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b"ok"
            n(_emp("slack:U01ABC"), "Alice in lobby")
            args, _ = urlopen.call_args
            req = args[0]
            self.assertEqual(req.full_url, "https://hooks.slack.com/test")
            body = req.data.decode("utf-8")
            self.assertIn("<@U01ABC>", body)
            self.assertIn("Alice in lobby", body)

    def test_swallows_network_errors(self):
        import urllib.error

        n = SlackWebhookNotifier(webhook_url="https://hooks.slack.com/test")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            n(_emp(), "msg")  # should not raise


class RoutingTests(unittest.TestCase):
    def test_dispatches_by_prefix(self):
        slack = MagicMock()
        default = MagicMock()
        n = RoutingNotifier(handlers={"slack": slack}, default=default)
        n(_emp("slack:U01"), "hi")
        n(_emp("email:a@b"), "hi")
        slack.assert_called_once()
        default.assert_called_once()


class FactoryTests(unittest.TestCase):
    def test_default_is_console(self):
        self.assertIsInstance(make_notifier(None), ConsoleNotifier)
        self.assertIsInstance(make_notifier({}), ConsoleNotifier)

    def test_with_webhook_returns_router(self):
        n = make_notifier({"notify": {"slack_webhook_url": "https://x"}})
        self.assertIsInstance(n, RoutingNotifier)


if __name__ == "__main__":
    unittest.main()
