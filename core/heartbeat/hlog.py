"""Structured heartbeat run log (JSONL). One record per job per tick that did
something. outcome in {silent_log, notified, deferred, skipped_cooldown,
skipped_quiet, skipped_disabled, error}. Size-capped by dropping oldest lines."""

import json
import logging
from pathlib import Path

logger = logging.getLogger("aegis.heartbeat")

_VALID = {"silent_log", "notified", "deferred", "skipped_cooldown",
          "skipped_quiet", "skipped_disabled", "error"}


class HeartbeatLog:
    """Append-only JSONL log for heartbeat job runs.

    Each call to :meth:`write` appends one JSON record to the backing file.
    When the file exceeds *max_bytes* the oldest half of lines is dropped,
    preventing unbounded growth on an always-on server.
    """

    def __init__(self, path, max_bytes=1_000_000):
        """Initialise the log.

        Args:
            path: Path (or str) to the ``.jsonl`` backing file.
            max_bytes: Soft size cap in bytes. When the file exceeds this
                limit after a write, the oldest 50 % of lines are removed.
        """
        self.path = Path(path)
        self.max_bytes = max_bytes

    def write(self, now, job_id, kind, outcome, detail="", duration_ms=0):
        """Append one run record to the log file.

        Args:
            now: :class:`~datetime.datetime` of the tick (used as ``ts``).
            job_id: Identifier of the job that ran.
            kind: Job kind string (e.g. ``"silent"``, ``"notify"``).
            outcome: One of ``_VALID``; falls back to ``"silent_log"`` if
                unknown, so callers never silently discard records.
            detail: Optional free-text description (truncated to 500 chars).
            duration_ms: Wall-clock run time in milliseconds.
        """
        if outcome not in _VALID:
            outcome = "silent_log"
        rec = {
            "ts": now.isoformat(),
            "job_id": job_id,
            "kind": kind,
            "outcome": outcome,
            "detail": (detail or "")[:500],
            "duration_ms": duration_ms,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            self._cap()
        except OSError:
            logger.exception("failed writing heartbeat log")

    def _cap(self):
        """Drop the oldest 50 % of log lines when the file exceeds *max_bytes*."""
        try:
            if self.path.stat().st_size <= self.max_bytes:
                return
            lines = self.path.read_text(encoding="utf-8").splitlines()
            keep = lines[len(lines) // 2:]
            self.path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            logger.exception("failed capping heartbeat log")
