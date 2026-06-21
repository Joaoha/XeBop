"""Visitor greeter conversation flow.

Pure state machine — no audio, no LLM, no Slack. The hosting app feeds it
recognized text and either speaks the returned line or routes it back to the
LLM as a system instruction.

States mirror the persona prompt's contract.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Optional


class FlowState(str, Enum):
    GREET = "greet"
    AWAITING_VISITOR_NAME = "awaiting_visitor_name"
    AWAITING_HOST_NAME = "awaiting_host_name"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    NOTIFYING = "notifying"
    DONE = "done"


@dataclass(frozen=True)
class Employee:
    name: str
    role: str
    alt_names: tuple[str, ...]
    host_channel_id: str

    def matches(self, query: str) -> bool:
        q = _normalize(query)
        if not q:
            return False
        if q == _normalize(self.name):
            return True
        # last-name and first-name fragment match
        parts = [_normalize(p) for p in self.name.split()]
        if q in parts:
            return True
        return any(q == _normalize(a) for a in self.alt_names)


@dataclass
class FlowResult:
    """One turn's output. `say` is what the agent should speak."""

    say: str
    state: FlowState
    notify: Optional[Employee] = None  # set when we should notify a host
    done: bool = False


# Notifier is the side-effect dependency we mock in tests.
Notifier = Callable[[Employee, str], None]

# EventLogger receives terminal-state events for visitor-log writes.
# (visitor_name, host_or_none, outcome)
EventLogger = Callable[[str, Optional["Employee"], str], None]


def _noop_logger(visitor_name: str, host: Optional["Employee"], outcome: str) -> None:
    return None


# Called when a visitor is confirmed and the host notified (a successful
# arrival). The hosting app uses this to snap the photo and open the visit
# record; kept separate from event_logger so the pure flow stays I/O-free.
CheckInHook = Callable[[str, Optional["Employee"]], None]


def _noop_check_in(visitor_name: str, host: Optional["Employee"]) -> None:
    return None


def load_employees(path: str | Path) -> list[Employee]:
    data = json.loads(Path(path).read_text())
    out: list[Employee] = []
    for raw in data.get("employees", []):
        out.append(
            Employee(
                name=raw["name"],
                role=raw.get("role", ""),
                alt_names=tuple(raw.get("alt_names", [])),
                host_channel_id=raw["host_channel_id"],
            )
        )
    return out


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()


_AFFIRMATIVE = {"yes", "yeah", "yep", "yup", "correct", "right", "sure", "ok", "okay", "y"}
_NEGATIVE = {"no", "nope", "nah", "wrong", "n"}


def _is_yes(text: str) -> bool:
    norm = _normalize(text)
    if not norm:
        return False
    first = norm.split()[0]
    return first in _AFFIRMATIVE


def _is_no(text: str) -> bool:
    norm = _normalize(text)
    if not norm:
        return False
    first = norm.split()[0]
    return first in _NEGATIVE


_NAME_PREFIXES = (
    "my name is ",
    "i am ",
    "i'm ",
    "this is ",
    "it's ",
    "its ",
    "im ",
)

_HOST_PREFIXES = (
    "i'm here to see ",
    "im here to see ",
    "here to see ",
    "to see ",
    "see ",
    "for ",
    "meeting with ",
    "meeting ",
    "visiting ",
)


def _strip_prefix(text: str, prefixes: Iterable[str]) -> str:
    norm = text.strip().lower()
    for p in prefixes:
        if norm.startswith(p):
            return text.strip()[len(p):].strip(" .,!?")
    return text.strip(" .,!?")


def extract_visitor_name(text: str) -> str:
    return _strip_prefix(text, _NAME_PREFIXES)


def extract_host_query(text: str) -> str:
    return _strip_prefix(text, _HOST_PREFIXES)


def find_employee(query: str, directory: list[Employee]) -> Optional[Employee]:
    if not query:
        return None
    matches = [e for e in directory if e.matches(query)]
    if len(matches) == 1:
        return matches[0]
    return None


DEFAULT_OPENING_LINE = "Hi there! Welcome. What's your name?"


# Every line the greeter speaks during the flow, keyed by situation. These are
# the defaults; config.json's "phrases" block (edited in the settings web UI)
# overrides any of them. A value may be a single string OR a list of strings —
# a list picks one at random each time, so you can add variety.
#
# Placeholders available (Python str.format): {name} / {visitor} (the visitor's
# name) and {host} (the matched employee's name). Unknown placeholders in a
# custom phrase safely fall back to the default for that key.
DEFAULT_PHRASES = {
    "didnt_catch_name": "Sorry, I didn't catch your name. Could you say it again?",
    "ask_host": "Nice to meet you, {name}. Who are you here to see?",
    "host_unknown_retry": "I don't have anyone by that name. Could you spell it?",
    "host_unknown_giveup": "I can't find that name. Please ring the doorbell for a human.",
    "confirm_host": "{visitor} here to see {host}, correct?",
    "notified_host": "Great. I'm letting {host} know — they're on their way.",
    "confirm_no": "My mistake. Who are you here to see?",
    "confirm_unclear": "Sorry — yes or no? Did I get the name right?",
    "already_on_way": "They're on their way. Please have a seat.",
    # Spoken by agent.py when a turn produced no transcription:
    "didnt_catch": "I didn't catch that. Could you say it again?",
}


def resolve_phrase(phrases, key, **kw):
    """Return a ready-to-speak line for ``key``, applying any config override.

    ``phrases`` is the config "phrases" block (may be empty/None). A value can
    be a string or a list of strings (one picked at random). Falls back to
    DEFAULT_PHRASES when missing/blank or if a custom template references an
    unknown placeholder.
    """
    value = (phrases or {}).get(key)
    if isinstance(value, (list, tuple)):
        variants = [v for v in value if isinstance(v, str) and v.strip()]
        template = random.choice(variants) if variants else DEFAULT_PHRASES[key]
    elif isinstance(value, str) and value.strip():
        template = value
    else:
        template = DEFAULT_PHRASES[key]
    try:
        return template.format(**kw)
    except (KeyError, IndexError):
        return DEFAULT_PHRASES[key].format(**kw)


@dataclass
class GreeterFlow:
    directory: list[Employee]
    notifier: Notifier
    event_logger: EventLogger = field(default=_noop_logger)
    on_check_in: CheckInHook = field(default=_noop_check_in)
    opening_line: str = DEFAULT_OPENING_LINE
    phrases: dict = field(default_factory=dict)
    state: FlowState = FlowState.GREET
    visitor_name: str = ""
    host: Optional[Employee] = None
    _retry: int = 0

    def _say(self, key: str) -> str:
        """Resolve a spoken line, filling in the current visitor/host names."""
        host_name = self.host.name if self.host else ""
        return resolve_phrase(
            self.phrases,
            key,
            name=self.visitor_name,
            visitor=self.visitor_name,
            host=host_name,
        )

    def start(self) -> FlowResult:
        """Visitor was detected approaching. Open the conversation."""
        self.state = FlowState.AWAITING_VISITOR_NAME
        return FlowResult(
            say=self.opening_line,
            state=self.state,
        )

    def handle(self, text: str) -> FlowResult:
        """Advance the flow with one turn of recognized speech."""
        text = (text or "").strip()
        if self.state == FlowState.AWAITING_VISITOR_NAME:
            return self._on_visitor_name(text)
        if self.state == FlowState.AWAITING_HOST_NAME:
            return self._on_host_name(text)
        if self.state == FlowState.AWAITING_CONFIRMATION:
            return self._on_confirm(text)
        if self.state in (FlowState.NOTIFYING, FlowState.DONE):
            return FlowResult(
                say=self._say("already_on_way"),
                state=FlowState.DONE,
                done=True,
            )
        # GREET fallback — start() should have been called
        return self.start()

    def _on_visitor_name(self, text: str) -> FlowResult:
        name = extract_visitor_name(text)
        if not name:
            return FlowResult(
                say=self._say("didnt_catch_name"),
                state=self.state,
            )
        self.visitor_name = name
        self.state = FlowState.AWAITING_HOST_NAME
        return FlowResult(
            say=self._say("ask_host"),
            state=self.state,
        )

    def _on_host_name(self, text: str) -> FlowResult:
        query = extract_host_query(text)
        host = find_employee(query, self.directory)
        if host is None:
            self._retry += 1
            if self._retry >= 3:
                self.state = FlowState.DONE
                self.event_logger(self.visitor_name, None, "unknown_host")
                return FlowResult(
                    say=self._say("host_unknown_giveup"),
                    state=self.state,
                    done=True,
                )
            return FlowResult(
                say=self._say("host_unknown_retry"),
                state=self.state,
            )
        self.host = host
        self._retry = 0
        self.state = FlowState.AWAITING_CONFIRMATION
        return FlowResult(
            say=self._say("confirm_host"),
            state=self.state,
        )

    def _on_confirm(self, text: str) -> FlowResult:
        if _is_yes(text):
            assert self.host is not None
            self.state = FlowState.NOTIFYING
            message = f"{self.visitor_name} is in the lobby to see you."
            self.notifier(self.host, message)
            self.state = FlowState.DONE
            self.on_check_in(self.visitor_name, self.host)
            result = FlowResult(
                say=self._say("notified_host"),
                state=self.state,
                notify=self.host,
                done=True,
            )
            return result
        if _is_no(text):
            self.host = None
            self.state = FlowState.AWAITING_HOST_NAME
            return FlowResult(
                say=self._say("confirm_no"),
                state=self.state,
            )
        return FlowResult(
            say=self._say("confirm_unclear"),
            state=self.state,
        )
