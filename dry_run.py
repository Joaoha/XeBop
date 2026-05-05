"""Laptop dry-run for the greeter pipeline.

Walks the happy path (and a couple of error paths) without mic, camera, or
Tk — useful for verifying the agent runtime wiring on a dev machine before
deploying to the Pi.

Run::

    python3 dry_run.py            # scripted happy path
    python3 dry_run.py --interactive   # type lines yourself

Stub STT inputs come from a hard-coded transcript by default.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from greeter.flow import GreeterFlow, FlowState, load_employees
from greeter.notify import make_notifier
from greeter.visitor_log import VisitorLog


HAPPY_PATH = [
    "My name is Alice",
    "I'm here to see Joao",
    "yes",
]


def _say(line: str) -> None:
    print(f"BOT: {line}")


def _hear(prompt_idx: int, transcript: list[str]) -> str:
    text = transcript[prompt_idx]
    print(f"YOU: {text}")
    return text


def run_session(transcript: list[str]) -> None:
    config_path = Path("config.json")
    config = json.loads(config_path.read_text()) if config_path.exists() else {}

    directory = load_employees("employees.json")
    notifier = make_notifier(config)
    log_cfg = config.get("visitor_log") or {}
    visitor_log = VisitorLog(
        path=log_cfg.get("path", "visitor_log.jsonl"),
        mode=log_cfg.get("mode", "minimal"),
        retention_days=int(log_cfg.get("retention_days", 7)),
        salt=log_cfg.get("salt", ""),
    )

    flow = GreeterFlow(
        directory=directory,
        notifier=notifier,
        event_logger=visitor_log.record,
    )

    opening = flow.start()
    _say(opening.say)

    for i in range(len(transcript)):
        text = _hear(i, transcript)
        result = flow.handle(text)
        _say(result.say)
        if result.done:
            print(f"[state] terminal: {result.state.value}")
            return

    print(f"[state] non-terminal exit: {flow.state.value}")


def run_interactive() -> None:
    config = json.loads(Path("config.json").read_text())
    directory = load_employees("employees.json")
    notifier = make_notifier(config)
    visitor_log = VisitorLog(path="visitor_log.jsonl")

    flow = GreeterFlow(directory=directory, notifier=notifier, event_logger=visitor_log.record)
    _say(flow.start().say)
    while flow.state not in (FlowState.DONE,):
        try:
            text = input("YOU: ")
        except EOFError:
            break
        result = flow.handle(text)
        _say(result.say)
        if result.done:
            break


if __name__ == "__main__":
    if "--interactive" in sys.argv:
        run_interactive()
    else:
        run_session(HAPPY_PATH)
