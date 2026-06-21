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
    AWAITING_VISITOR_NAME_CONFIRM = "awaiting_visitor_name_confirm"
    AWAITING_VISITOR_NAME_SPELL = "awaiting_visitor_name_spell"
    AWAITING_RETURNING_CHOICE = "awaiting_returning_choice"
    AWAITING_HOST_NAME = "awaiting_host_name"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_CHECKOUT_NAME = "awaiting_checkout_name"
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


# Look up a returning visitor's still-open visit by name (the app wires this to
# VisitorLog.find_open_visit). Returns the visit dict, or None if not on-site.
OpenVisitLookup = Callable[[str], Optional[dict]]

# Close a visit on departure: (visit_dict, spoken_visitor_name). The app writes
# the check_out row and notifies the host.
CheckOutHook = Callable[[dict, str], None]


def _noop_lookup(name: str) -> Optional[dict]:
    return None


def _noop_check_out(visit: dict, visitor_name: str) -> None:
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
    "visitor_name_confirm": "I heard {name} — is that right?",
    "spell_name": "Could you spell your name for me, one letter at a time?",
    "returning_visitor": "Welcome back, {name}! You're still checked in to see {host}. Say 'check out' to leave, or tell me who you're here to see now.",
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
    # Quick acknowledgement once the visitor stops speaking (while we
    # transcribe), so they know they were heard. A list -> picked at random.
    "ack": ["Got it!", "Okay!", "One moment.", "Sure."],
    # Spoken just before the check-in photo (live preview is on screen):
    "hold_still": "Look at the camera and hold still for your photo.",
    # Check-out (visitor leaving):
    "checkout_ask_name": "Sure — what name did you check in under, so I can sign you out?",
    "checkout_done": "Thanks {name}, you're all signed out. Take care!",
    "checkout_not_found": "Hmm, I don't see you checked in. What name did you check in under?",
    "checkout_not_found_giveup": "I can't find your check-in — no worries, have a great day!",
    # Visitor cancelled / "go to sleep":
    "stopped": "No problem — say my name whenever you need me.",
    # Notification SENT TO THE HOST when their visitor leaves (not spoken):
    "exit_notice": "{visitor} has checked out and left the building.",
}


# First-utterance check-out intent. Phrases are matched against the normalized
# text (apostrophes stripped, lowercased); single words against its tokens.
_CHECKOUT_PHRASES = (
    "checking out", "check out", "checkout", "heading out", "head out",
    "going home", "sign out", "signing out", "im leaving", "im out", "im off",
    "leaving now",
)
_CHECKOUT_WORDS = {"leaving", "goodbye", "bye"}


def is_checkout_intent(text: str) -> bool:
    norm = _normalize(text)
    if not norm:
        return False
    if any(p in norm for p in _CHECKOUT_PHRASES):
        return True
    return bool(set(norm.split()) & _CHECKOUT_WORDS)


# Cancel / "go to sleep" — recognized at any point in a session.
_STOP_PHRASES = ("go to sleep", "never mind", "forget it", "go away", "shut down", "shut up")
_STOP_WORDS = {"stop", "cancel", "sleep", "quiet"}


def is_stop_intent(text: str) -> bool:
    norm = _normalize(text)
    if not norm:
        return False
    if any(p in norm for p in _STOP_PHRASES):
        return True
    return bool(set(norm.split()) & _STOP_WORDS)


def name_looks_uncertain(name: str) -> bool:
    """Heuristic: does this transcribed name look garbled enough to spell out?"""
    n = (name or "").strip()
    if len(n) < 2 or len(n) > 30:
        return True
    if re.search(r"[0-9]", n):
        return True
    if re.search(r"[^A-Za-z .'\-]", n):  # letters, space, period, apostrophe, hyphen only
        return True
    return len(n.split()) > 3


# How Whisper tends to spell back spoken letters — letter-names and the NATO
# alphabet — mapped to the letter. Single characters are handled separately.
_PHONETIC = {
    # letter names / common Whisper spellings
    "ay": "a", "eh": "a",
    "be": "b", "bee": "b",
    "see": "c", "sea": "c", "cee": "c",
    "de": "d", "dee": "d",
    "ee": "e",
    "ef": "f", "eff": "f",
    "ge": "g", "gee": "g",
    "aitch": "h", "haitch": "h",
    "eye": "i", "aye": "i",
    "jay": "j",
    "kay": "k",
    "el": "l", "ell": "l",
    "em": "m",
    "en": "n",
    "oh": "o",
    "pe": "p", "pee": "p", "pea": "p",
    "cue": "q", "queue": "q", "kew": "q",
    "ar": "r", "are": "r",
    "es": "s", "ess": "s",
    "te": "t", "tee": "t", "tea": "t",
    "you": "u", "yu": "u",
    "ve": "v", "vee": "v",
    "doubleu": "w", "dub": "w", "dubya": "w",
    "ex": "x", "eks": "x",
    "why": "y", "wy": "y",
    "ze": "z", "zee": "z", "zed": "z",
    # NATO phonetic alphabet
    "alpha": "a", "alfa": "a", "bravo": "b", "charlie": "c", "delta": "d",
    "echo": "e", "foxtrot": "f", "golf": "g", "hotel": "h", "india": "i",
    "juliet": "j", "juliett": "j", "kilo": "k", "lima": "l", "mike": "m",
    "november": "n", "oscar": "o", "papa": "p", "quebec": "q", "romeo": "r",
    "sierra": "s", "tango": "t", "uniform": "u", "victor": "v", "whiskey": "w",
    "xray": "x", "yankee": "y", "zulu": "z",
}


def reconstruct_spelled_name(text: str) -> str:
    """Rebuild a name from a spelled-out utterance.

    Handles single letters ("A L I C E" / "A-L-I-C-E"), phonetic letter names
    ("ay, el, eye, see, ee"), and the NATO alphabet ("alpha lima india...").
    Tokens that don't map to a letter are treated as noise and dropped. If
    nothing maps (they just said the name as a word), falls back to the word.
    """
    raw = (text or "").strip()
    tokens = re.findall(r"[a-z]+", raw.lower())
    letters = []
    for t in tokens:
        if len(t) == 1:
            letters.append(t)
        elif t in _PHONETIC:
            letters.append(_PHONETIC[t])
    if len(letters) >= 2:
        return "".join(letters).capitalize()
    compact = re.sub(r"[^A-Za-z]", "", raw)
    return compact.capitalize() if compact else ""


def _pick_template(value):
    """Pick a usable template string from a value that may be a string, a list
    of strings (one chosen at random), or empty/None. Returns None if unusable."""
    if isinstance(value, (list, tuple)):
        variants = [v for v in value if isinstance(v, str) and v.strip()]
        return random.choice(variants) if variants else None
    if isinstance(value, str) and value.strip():
        return value
    return None


def resolve_phrase(phrases, key, **kw):
    """Return a ready-to-speak line for ``key``, applying any config override.

    ``phrases`` is the config "phrases" block (may be empty/None). Both the
    override AND the default may be a string or a list of strings (one picked
    at random). Falls back to DEFAULT_PHRASES when the override is missing/blank
    or references an unknown placeholder.
    """
    template = _pick_template((phrases or {}).get(key)) or _pick_template(DEFAULT_PHRASES.get(key))
    if not template:
        return ""
    try:
        return template.format(**kw)
    except (KeyError, IndexError):
        fallback = _pick_template(DEFAULT_PHRASES.get(key)) or ""
        try:
            return fallback.format(**kw)
        except (KeyError, IndexError):
            return fallback


@dataclass
class GreeterFlow:
    directory: list[Employee]
    notifier: Notifier
    event_logger: EventLogger = field(default=_noop_logger)
    on_check_in: CheckInHook = field(default=_noop_check_in)
    open_visit_lookup: OpenVisitLookup = field(default=_noop_lookup)
    on_check_out: CheckOutHook = field(default=_noop_check_out)
    opening_line: str = DEFAULT_OPENING_LINE
    phrases: dict = field(default_factory=dict)
    state: FlowState = FlowState.GREET
    visitor_name: str = ""
    host: Optional[Employee] = None
    _retry: int = 0
    _open_visit: Optional[dict] = None  # set when a returning, still-checked-in visitor is found

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
        # "Hey XeBop, stop / go to sleep" — bail out from anywhere in a session.
        if is_stop_intent(text):
            self.state = FlowState.DONE
            return FlowResult(say=self._say("stopped"), state=self.state, done=True)
        if self.state == FlowState.AWAITING_VISITOR_NAME:
            return self._on_visitor_name(text)
        if self.state == FlowState.AWAITING_VISITOR_NAME_CONFIRM:
            return self._on_visitor_name_confirm(text)
        if self.state == FlowState.AWAITING_VISITOR_NAME_SPELL:
            return self._on_visitor_name_spell(text)
        if self.state == FlowState.AWAITING_RETURNING_CHOICE:
            return self._on_returning_choice(text)
        if self.state == FlowState.AWAITING_HOST_NAME:
            return self._on_host_name(text)
        if self.state == FlowState.AWAITING_CONFIRMATION:
            return self._on_confirm(text)
        if self.state == FlowState.AWAITING_CHECKOUT_NAME:
            return self._on_checkout_name(text)
        if self.state in (FlowState.NOTIFYING, FlowState.DONE):
            return FlowResult(
                say=self._say("already_on_way"),
                state=FlowState.DONE,
                done=True,
            )
        # GREET fallback — start() should have been called
        return self.start()

    def _on_visitor_name(self, text: str) -> FlowResult:
        # A returning visitor may open with "I'm leaving" instead of a name.
        if is_checkout_intent(text):
            self._retry = 0
            self.state = FlowState.AWAITING_CHECKOUT_NAME
            return FlowResult(say=self._say("checkout_ask_name"), state=self.state)
        name = extract_visitor_name(text)
        if not name:
            return FlowResult(
                say=self._say("didnt_catch_name"),
                state=self.state,
            )
        self.visitor_name = name
        # A garbled-looking name skips straight to spelling; otherwise confirm it.
        if name_looks_uncertain(name):
            self.state = FlowState.AWAITING_VISITOR_NAME_SPELL
            return FlowResult(say=self._say("spell_name"), state=self.state)
        self.state = FlowState.AWAITING_VISITOR_NAME_CONFIRM
        return FlowResult(say=self._say("visitor_name_confirm"), state=self.state)

    def _on_visitor_name_confirm(self, text: str) -> FlowResult:
        if _is_yes(text):
            return self._after_name()
        if _is_no(text):
            self.state = FlowState.AWAITING_VISITOR_NAME_SPELL
            return FlowResult(say=self._say("spell_name"), state=self.state)
        return FlowResult(say=self._say("confirm_unclear"), state=self.state)

    def _on_visitor_name_spell(self, text: str) -> FlowResult:
        name = reconstruct_spelled_name(text)
        if not name:
            return FlowResult(say=self._say("didnt_catch_name"), state=self.state)
        self.visitor_name = name
        return self._after_name()

    def _after_name(self) -> FlowResult:
        """Name is settled — if they're already checked in, handle that first;
        otherwise ask who they're here to see."""
        visit = self.open_visit_lookup(self.visitor_name)
        if visit:
            self._open_visit = visit
            self.state = FlowState.AWAITING_RETURNING_CHOICE
            return FlowResult(
                say=resolve_phrase(
                    self.phrases, "returning_visitor",
                    name=self.visitor_name, visitor=self.visitor_name,
                    host=visit.get("host") or "someone",
                ),
                state=self.state,
            )
        self.state = FlowState.AWAITING_HOST_NAME
        return FlowResult(say=self._say("ask_host"), state=self.state)

    def _on_returning_choice(self, text: str) -> FlowResult:
        visit = self._open_visit
        self._open_visit = None
        if is_checkout_intent(text):
            if visit is not None:
                self.on_check_out(visit, self.visitor_name)
            self.state = FlowState.DONE
            return FlowResult(say=self._say("checkout_done"), state=self.state, done=True)
        # They're here to see someone new — close the stale visit so we don't
        # stack a second open one, then treat this turn as the host.
        if visit is not None:
            self.on_check_out(visit, self.visitor_name)
        self.state = FlowState.AWAITING_HOST_NAME
        return self._on_host_name(text)

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

    def _on_checkout_name(self, text: str) -> FlowResult:
        name = extract_visitor_name(text)
        if not name:
            return FlowResult(say=self._say("didnt_catch_name"), state=self.state)
        visit = self.open_visit_lookup(name)
        if visit is None:
            self._retry += 1
            if self._retry >= 2:
                self.state = FlowState.DONE
                return FlowResult(
                    say=self._say("checkout_not_found_giveup"),
                    state=self.state,
                    done=True,
                )
            return FlowResult(say=self._say("checkout_not_found"), state=self.state)
        self.visitor_name = name
        self.on_check_out(visit, name)
        self.state = FlowState.DONE
        return FlowResult(say=self._say("checkout_done"), state=self.state, done=True)

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
