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

    def record(
        self,
        visitor_name: str,
        host: Optional[Employee],
        outcome: str,
    ) -> dict:
        """Append one entry. `outcome` is e.g. 'notified', 'unknown_host',
        'no_confirmation'."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "visitor": self._encode_visitor(visitor_name),
            "host": host.name if host else None,
            "host_channel_id": host.host_channel_id if host else None,
            "outcome": outcome,
            "mode": self.mode,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

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
