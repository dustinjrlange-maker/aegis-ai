"""Durable per-job heartbeat state (last_fired_at / next_eligible_at) plus a
deferred-push queue. Atomic writes (temp + os.replace) so a crash never leaves
a torn target file."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("aegis.heartbeat")


class HeartbeatState:
    """Durable heartbeat job state and deferred-push queue with atomic saves."""

    def __init__(self, path):
        self.path = Path(path)
        self._jobs = {}      # job_id -> {"last_fired_at": dt, "next_eligible_at": dt}
        self._pending = []   # list of push dicts
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            jobs = {}
            for jid, rec in raw.get("jobs", {}).items():
                jobs[jid] = {
                    "last_fired_at": _from_iso(rec.get("last_fired_at")),
                    "next_eligible_at": _from_iso(rec.get("next_eligible_at")),
                }
            pending = raw.get("pending_pushes", [])
        except (OSError, ValueError, AttributeError, TypeError):
            logger.exception("heartbeat state unreadable; starting fresh")
            return
        # Commit only on fully-successful parse so a mid-loop failure can't leak.
        self._jobs = jobs
        self._pending = pending

    def _save(self):
        data = {
            "jobs": {
                jid: {
                    "last_fired_at": _to_iso(r["last_fired_at"]),
                    "next_eligible_at": _to_iso(r["next_eligible_at"]),
                }
                for jid, r in self._jobs.items()
            },
            "pending_pushes": self._pending,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic on same filesystem

    def get(self, job_id):
        """Return the state record for job_id, or None if never fired."""
        return self._jobs.get(job_id)

    def mark_fired(self, job_id, fired_at: datetime, next_eligible_at: datetime):
        """Record a job firing and persist atomically."""
        self._jobs[job_id] = {"last_fired_at": fired_at, "next_eligible_at": next_eligible_at}
        self._save()

    def queue_push(self, push: dict):
        """Append a push notification dict to the pending queue and persist."""
        self._pending.append(push)
        self._save()

    def drain_pushes(self):
        """Return and clear all pending push dicts, persisting the empty queue."""
        out, self._pending = self._pending, []
        self._save()
        return out


def _to_iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else None


def _from_iso(s):
    return datetime.fromisoformat(s) if s else None
