"""Pluggable host-notification adapter.

Channel choice for v1 is an open board question (Slack vs email vs SMS — see
[XEB-3](/XEB/issues/XEB-3) open questions). The flow only needs a callable
`Notifier` (see `greeter.flow.Notifier`); this module provides:

- `ConsoleNotifier`     — prints to stdout, default for dev/bench
- `SlackWebhookNotifier` — posts to a Slack incoming-webhook URL
- `RoutingNotifier`     — dispatches based on the `host_channel_id` prefix
  (`slack:`, `email:`, `sms:`) so the directory can mix channels per-host
- `make_notifier(config)` — factory that returns a notifier from config dict
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
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
            "slack_webhook_url": "https://hooks.slack.com/services/..." | null,
            ...
          }
        }
    """
    cfg = (config or {}).get("notify") if config else None
    cfg = cfg if isinstance(cfg, Mapping) else {}

    console = ConsoleNotifier()
    handlers: dict[str, NotifierProtocol] = {}

    webhook = cfg.get("slack_webhook_url")
    if isinstance(webhook, str) and webhook:
        handlers["slack"] = SlackWebhookNotifier(webhook_url=webhook)

    if not handlers:
        return console
    return RoutingNotifier(handlers=handlers, default=console)
