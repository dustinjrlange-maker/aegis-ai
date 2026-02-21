"""
Time Tracker -- Aegis AI
Start/stop timer for activity tracking.
"""

import json
import uuid
from datetime import datetime, date
from pathlib import Path


class TimeTracker:
    """Manages time tracking entries with start/stop timer."""

    def __init__(self, data_dir):
        self._file = Path(data_dir) / "time_entries.json"
        self._entries = []
        self._load()

    def _load(self):
        if self._file.exists():
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    self._entries = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._entries = []

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._entries, f, indent=2, ensure_ascii=False)

    def start_timer(self, activity: str, category: str = "general"):
        """Start a new timer. Stops any active timer first."""
        self.stop_timer()
        entry = {
            "id": uuid.uuid4().hex[:12],
            "activity": activity,
            "category": category,
            "started": datetime.now().isoformat(),
            "ended": None,
            "duration_seconds": 0,
            "created": datetime.now().isoformat(),
        }
        self._entries.append(entry)
        self._save()
        return entry

    def stop_timer(self):
        """Stop the active timer, if any. Returns the stopped entry or None."""
        for entry in self._entries:
            if entry["ended"] is None:
                now = datetime.now()
                started = datetime.fromisoformat(entry["started"])
                entry["ended"] = now.isoformat()
                entry["duration_seconds"] = int((now - started).total_seconds())
                self._save()
                return entry
        return None

    def get_active_timer(self):
        """Get the currently running timer, if any."""
        for entry in self._entries:
            if entry["ended"] is None:
                started = datetime.fromisoformat(entry["started"])
                elapsed = int((datetime.now() - started).total_seconds())
                result = dict(entry)
                result["elapsed_seconds"] = elapsed
                return result
        return None

    def list_entries(self, start: str | None = None, end: str | None = None):
        """List completed entries, optionally filtered by date range."""
        results = [e for e in self._entries if e["ended"] is not None]
        if start or end:
            filtered = []
            for e in results:
                d = e.get("started", "")[:10]
                if start and d < start:
                    continue
                if end and d > end:
                    continue
                filtered.append(e)
            results = filtered
        return sorted(results, key=lambda x: x.get("started", ""), reverse=True)

    def get_today_summary(self):
        """Get time tracking summary for today."""
        today = date.today().isoformat()
        entries = [e for e in self._entries
                   if e["ended"] is not None and e.get("started", "")[:10] == today]
        total_seconds = sum(e.get("duration_seconds", 0) for e in entries)
        by_category = {}
        for e in entries:
            cat = e.get("category", "general")
            by_category[cat] = by_category.get(cat, 0) + e.get("duration_seconds", 0)
        return {
            "total_seconds": total_seconds,
            "entry_count": len(entries),
            "by_category": by_category,
        }

    def delete_entry(self, entry_id: str) -> bool:
        """Delete a time entry by ID."""
        before = len(self._entries)
        self._entries = [e for e in self._entries if e["id"] != entry_id]
        if len(self._entries) < before:
            self._save()
            return True
        return False
