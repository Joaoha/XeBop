"""Local visitor log (JSONL).

JSONL was chosen over SQLite because the log is small (tens of entries/day),
must be trivially inspectable by the office manager, and easy to redact in
place by rewriting the file.

Retention/redaction modes are tied to the still-open camera-privacy decision
on [XEB-3](/XEB/issues/XEB-3). Two modes are supported so the deploy-time
choice is a config flip, not a code change:

- `standard`: visitor name + host kept for `retention_days` (default 30)
- `minimal` : visitor name hashed at write time; host kept; retention 7 days

Until the board picks a posture, callers should default to `minimal`.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Literal, Optional

from .flow import Employee

Mode = Literal["standard", "minimal"]


@dataclass
class VisitorLog:
    path: Path
    mode: Mode = "minimal"
    retention_days: int = 7
    salt: str = ""

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, entry: dict) -> dict:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def record(
        self,
        visitor_name: str,
        host: Optional[Employee],
        outcome: str,
    ) -> dict:
        """Append a non-visit audit entry. `outcome` is e.g. 'unknown_host',
        'no_confirmation'. (Successful arrivals use `check_in`.)"""
        return self._append({
            "ts": self._now(),
            "visitor": self._encode_visitor(visitor_name),
            "host": host.name if host else None,
            "host_channel_id": host.host_channel_id if host else None,
            "outcome": outcome,
            "mode": self.mode,
        })

    def check_in(
        self,
        visitor_name: str,
        host: Optional[Employee],
        photo: Optional[str] = None,
    ) -> str:
        """Open a visit: append a check_in event and return its visit_id."""
        visit_id = uuid.uuid4().hex[:12]
        self._append({
            "ts": self._now(),
            "visit_id": visit_id,
            "kind": "check_in",
            "visitor": self._encode_visitor(visitor_name),
            "host": host.name if host else None,
            "host_channel_id": host.host_channel_id if host else None,
            "photo": photo,
            "outcome": "checked_in",
            "mode": self.mode,
        })
        return visit_id

    def check_out(self, visit_id: str) -> dict:
        """Close a visit: append a check_out event for `visit_id`."""
        return self._append({
            "ts": self._now(),
            "visit_id": visit_id,
            "kind": "check_out",
            "outcome": "checked_out",
            "mode": self.mode,
        })

    def open_visits(self, now: Optional[datetime] = None) -> list[dict]:
        """Check-ins with no matching check-out, newest first.

        Each is annotated with `duration_seconds`. Derived by replay — the log
        itself is never mutated.
        """
        now = now or datetime.now(timezone.utc)
        checkins: dict[str, dict] = {}
        closed: set[str] = set()
        for e in self.entries():
            vid = e.get("visit_id")
            if not vid:
                continue
            if e.get("kind") == "check_in":
                checkins[vid] = e
            elif e.get("kind") == "check_out":
                closed.add(vid)
        out: list[dict] = []
        for vid, e in checkins.items():
            if vid in closed:
                continue
            entry = dict(e)
            try:
                ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
                entry["duration_seconds"] = int((now - ts).total_seconds())
            except (ValueError, KeyError):
                entry["duration_seconds"] = None
            out.append(entry)
        out.sort(key=lambda x: x.get("ts", ""), reverse=True)
        return out

    def find_open_visit(self, name: str) -> Optional[dict]:
        """Match a returning visitor (by name) to their open visit, or None.

        In `minimal` mode the per-day hash is stable, so same-day check-outs
        match; in `standard` mode we also compare names case-insensitively.
        """
        target = self._encode_visitor(name)
        norm = name.strip().lower()
        for v in self.open_visits():
            stored = v.get("visitor")
            if stored == target:
                return v
            if self.mode == "standard" and isinstance(stored, str) and stored.strip().lower() == norm:
                return v
        return None

    def prune(self, now: Optional[datetime] = None) -> int:
        """Drop entries older than `retention_days`. Returns count removed."""
        if not self.path.exists():
            return 0
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=self.retention_days)
        kept: list[str] = []
        removed = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = datetime.fromisoformat(entry["ts"].replace("Z", "+00:00"))
            except (ValueError, KeyError):
                kept.append(line)  # preserve malformed lines for human review
                continue
            if ts < cutoff:
                removed += 1
                # Drop the visitor's photo along with the expired record (PII).
                photo = entry.get("photo")
                if photo:
                    try:
                        os.unlink(photo)
                    except OSError:
                        pass
                continue
            kept.append(line)
        self._atomic_rewrite(kept)
        return removed

    def entries(self) -> Iterator[dict]:
        if not self.path.exists():
            return iter(())
        return (json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip())

    def _encode_visitor(self, name: str) -> str:
        if self.mode == "standard":
            return name
        # minimal: stable per-day hash so we can spot repeats without storing the name
        day = time.strftime("%Y-%m-%d", time.gmtime())
        h = hashlib.sha256(f"{self.salt}:{day}:{name.strip().lower()}".encode("utf-8"))
        return f"sha256:{h.hexdigest()[:16]}"

    def _atomic_rewrite(self, lines: Iterable[str]) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".visitor_log.", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
