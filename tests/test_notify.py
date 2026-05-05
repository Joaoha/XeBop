import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from greeter.flow import Employee  # noqa: E402
from greeter.notify import (  # noqa: E402
    ConsoleNotifier,
    EmailNotifier,
    RoutingNotifier,
    SlackWebhookNotifier,
    TeamsWebhookNotifier,
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


class TeamsWebhookNotifierTests(unittest.TestCase):
    def test_posts_json_with_target(self):
        n = TeamsWebhookNotifier(webhook_url="https://outlook.example/webhook")
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b"1"
            n(_emp("teams:bren@example.com"), "Alice in lobby")
            args, _ = urlopen.call_args
            req = args[0]
            self.assertEqual(req.full_url, "https://outlook.example/webhook")
            body = req.data.decode("utf-8")
            self.assertIn("bren@example.com", body)
            self.assertIn("Alice in lobby", body)

    def test_swallows_network_errors(self):
        import urllib.error

        n = TeamsWebhookNotifier(webhook_url="https://outlook.example/webhook")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            n(_emp("teams:bren@example.com"), "msg")  # should not raise


class EmailNotifierTests(unittest.TestCase):
    def _factory(self):
        client = MagicMock()
        factory = MagicMock(return_value=client)
        return factory, client

    def test_sends_to_host_address(self):
        factory, client = self._factory()
        n = EmailNotifier(
            host="smtp.test", from_addr="greeter@test", smtp_factory=factory, use_starttls=False
        )
        n(_emp("email:sam@example.com"), "Alice in lobby")
        factory.assert_called_once_with("smtp.test", 587, timeout=10.0)
        client.send_message.assert_called_once()
        msg = client.send_message.call_args.args[0]
        self.assertEqual(msg["To"], "sam@example.com")
        self.assertEqual(msg["From"], "greeter@test")
        self.assertIn("Alice in lobby", msg.get_content())

    def test_logs_in_when_credentials_provided(self):
        factory, client = self._factory()
        n = EmailNotifier(
            host="smtp.test",
            username="user",
            password="pass",
            smtp_factory=factory,
            use_starttls=False,
        )
        n(_emp("email:sam@example.com"), "msg")
        client.login.assert_called_once_with("user", "pass")

    def test_skips_when_address_missing(self):
        factory, client = self._factory()
        n = EmailNotifier(host="smtp.test", smtp_factory=factory)
        n(_emp("email:"), "msg")
        client.send_message.assert_not_called()

    def test_swallows_smtp_errors(self):
        import smtplib as _smtplib

        factory = MagicMock(side_effect=_smtplib.SMTPException("boom"))
        n = EmailNotifier(host="smtp.test", smtp_factory=factory)
        n(_emp("email:sam@example.com"), "msg")  # should not raise


class FactoryTests(unittest.TestCase):
    def test_default_is_console(self):
        self.assertIsInstance(make_notifier(None), ConsoleNotifier)
        self.assertIsInstance(make_notifier({}), ConsoleNotifier)

    def test_with_slack_webhook_returns_router(self):
        n = make_notifier({"notify": {"slack_webhook_url": "https://x"}})
        self.assertIsInstance(n, RoutingNotifier)
        self.assertIn("slack", n.handlers)

    def test_with_teams_webhook_returns_router(self):
        n = make_notifier({"notify": {"teams_webhook_url": "https://outlook.example"}})
        self.assertIsInstance(n, RoutingNotifier)
        self.assertIn("teams", n.handlers)
        self.assertIsInstance(n.handlers["teams"], TeamsWebhookNotifier)

    def test_with_email_config_returns_router(self):
        n = make_notifier(
            {
                "notify": {
                    "email": {
                        "host": "smtp.test",
                        "port": 2525,
                        "from": "greeter@test",
                        "use_starttls": False,
                    }
                }
            }
        )
        self.assertIsInstance(n, RoutingNotifier)
        self.assertIn("email", n.handlers)
        email = n.handlers["email"]
        self.assertIsInstance(email, EmailNotifier)
        self.assertEqual(email.host, "smtp.test")
        self.assertEqual(email.port, 2525)
        self.assertEqual(email.from_addr, "greeter@test")
        self.assertFalse(email.use_starttls)

    def test_email_config_without_host_is_ignored(self):
        n = make_notifier({"notify": {"email": {}}})
        self.assertIsInstance(n, ConsoleNotifier)


if __name__ == "__main__":
    unittest.main()
