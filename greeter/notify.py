"""Pluggable host-notification adapter.

Board picked email + Microsoft Teams as the v1 host channels. The flow only
needs a callable `Notifier` (see `greeter.flow.Notifier`); this module
provides the available backends and a routing layer so the employee
directory can mix channels per-host:

- `ConsoleNotifier`        — prints to stdout, default fallback for dev/bench
- `EmailNotifier`          — SMTP send to `email:<address>`
- `TeamsWebhookNotifier`   — posts to a Microsoft Teams incoming webhook
- `SlackWebhookNotifier`   — kept for completeness; not the v1 default
- `RoutingNotifier`        — dispatches by the `host_channel_id` prefix
  (`email:`, `teams:`, `slack:`) and falls back to `default`
- `make_notifier(config)`  — factory that wires backends from a config dict
"""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable, Mapping, Optional, Protocol

from .flow import Employee

log = logging.getLogger(__name__)


class NotifierProtocol(Protocol):
    def __call__(self, employee: Employee, message: str) -> None: ...


@dataclass
class ConsoleNotifier:
    """Default fallback. Useful in dev and as the safe default when no
    real backend is configured."""

    prefix: str = "[notify]"

    def __call__(self, employee: Employee, message: str) -> None:
        print(f"{self.prefix} {employee.name} ({employee.host_channel_id}): {message}")


@dataclass
class SlackWebhookNotifier:
    """Posts to a Slack incoming webhook.

    `host_channel_id` is expected to look like `slack:U01ABC` or `slack:#lobby`.
    The webhook itself is bound to a single channel in Slack; we still pass
    the resolved id in the message so a shared `#front-desk` webhook can
    @-mention the right host.
    """

    webhook_url: str
    timeout_s: float = 5.0

    def __call__(self, employee: Employee, message: str) -> None:
        target = employee.host_channel_id.removeprefix("slack:")
        text = f"<@{target}> {message}" if target.startswith("U") else f"{target} — {message}"
        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                resp.read()
        except urllib.error.URLError as exc:
            log.warning("slack notify failed for %s: %s", employee.name, exc)


@dataclass
class TeamsWebhookNotifier:
    """Posts to a Microsoft Teams incoming webhook.

    The webhook is bound to a single Teams channel, so all `teams:`-prefixed
    hosts notified through the same notifier land in that channel. The
    resolved host id is included in the message body so a shared
    `#front-desk` channel can still attribute to the right person.
    """

    webhook_url: str
    timeout_s: float = 5.0

    def __call__(self, employee: Employee, message: str) -> None:
        target = employee.host_channel_id.removeprefix("teams:")
        text = f"{target} — {message}" if target else message
        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                resp.read()
        except urllib.error.URLError as exc:
            log.warning("teams notify failed for %s: %s", employee.name, exc)


@dataclass
class EmailNotifier:
    """Sends a plaintext SMTP email to `email:<address>` hosts.

    The SMTP transport is parameterised so tests can swap it for a fake.
    `use_starttls` upgrades the connection after EHLO; `use_ssl` opens a
    direct SMTPS connection. If both are false, mail is sent in the clear
    (useful for a local relay only).
    """

    host: str
    port: int = 587
    username: Optional[str] = None
    password: Optional[str] = None
    from_addr: str = "greeter@localhost"
    subject: str = "Visitor at the front desk"
    use_starttls: bool = True
    use_ssl: bool = False
    timeout_s: float = 10.0
    smtp_factory: Optional[Callable[..., smtplib.SMTP]] = None

    def _connect(self) -> smtplib.SMTP:
        if self.smtp_factory is not None:
            return self.smtp_factory(self.host, self.port, timeout=self.timeout_s)
        if self.use_ssl:
            return smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout_s, context=ssl.create_default_context()
            )
        return smtplib.SMTP(self.host, self.port, timeout=self.timeout_s)

    def __call__(self, employee: Employee, message: str) -> None:
        to_addr = employee.host_channel_id.removeprefix("email:")
        if not to_addr:
            log.warning("email notify skipped: no address for %s", employee.name)
            return
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = to_addr
        msg["Subject"] = self.subject
        msg.set_content(message)
        try:
            client = self._connect()
            try:
                if self.use_starttls and not self.use_ssl:
                    client.starttls(context=ssl.create_default_context())
                if self.username and self.password:
                    client.login(self.username, self.password)
                client.send_message(msg)
            finally:
                try:
                    client.quit()
                except Exception:
                    pass
        except (OSError, smtplib.SMTPException) as exc:
            log.warning("email notify failed for %s: %s", employee.name, exc)


@dataclass
class RoutingNotifier:
    """Dispatches by `host_channel_id` prefix.

    Falls back to `default` (typically Console) when no handler matches.
    """

    handlers: Mapping[str, NotifierProtocol]
    default: NotifierProtocol

    def __call__(self, employee: Employee, message: str) -> None:
        prefix = employee.host_channel_id.split(":", 1)[0]
        handler = self.handlers.get(prefix, self.default)
        handler(employee, message)


def make_notifier(config: Optional[Mapping[str, object]]) -> Callable[[Employee, str], None]:
    """Build a notifier from a config dict. Defaults to `ConsoleNotifier`.

    Expected shape::

        {
          "notify": {
            "email": {
              "host": "smtp.example.com",
              "port": 587,
              "username": "...", "password": "...",
              "from": "greeter@example.com",
              "subject": "Visitor at the front desk",
              "use_starttls": true, "use_ssl": false
            } | null,
            "teams_webhook_url": "https://outlook.office.com/webhook/..." | null,
            "slack_webhook_url": "https://hooks.slack.com/services/..." | null
          }
        }
    """
    cfg = (config or {}).get("notify") if config else None
    cfg = cfg if isinstance(cfg, Mapping) else {}

    console = ConsoleNotifier()
    handlers: dict[str, NotifierProtocol] = {}

    email_cfg = cfg.get("email")
    if isinstance(email_cfg, Mapping) and email_cfg.get("host"):
        handlers["email"] = EmailNotifier(
            host=str(email_cfg["host"]),
            port=int(email_cfg.get("port", 587)),
            username=email_cfg.get("username") or None,
            password=email_cfg.get("password") or None,
            from_addr=str(email_cfg.get("from") or "greeter@localhost"),
            subject=str(email_cfg.get("subject") or "Visitor at the front desk"),
            use_starttls=bool(email_cfg.get("use_starttls", True)),
            use_ssl=bool(email_cfg.get("use_ssl", False)),
        )

    teams_url = cfg.get("teams_webhook_url")
    if isinstance(teams_url, str) and teams_url:
        handlers["teams"] = TeamsWebhookNotifier(webhook_url=teams_url)

    slack_url = cfg.get("slack_webhook_url")
    if isinstance(slack_url, str) and slack_url:
        handlers["slack"] = SlackWebhookNotifier(webhook_url=slack_url)

    if not handlers:
        return console
    return RoutingNotifier(handlers=handlers, default=console)
