"""
Event Manager — Aegis AI
Local event storage: CRUD for user calendar events.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path


class EventManager:
    """Manages local calendar events stored as JSON."""

    def __init__(self, data_dir):
        self._file = Path(data_dir) / "events.json"
        self._events = []
        self._load()

    def _load(self):
        """Load events from disk."""
        if self._file.exists():
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    self._events = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._events = []

    def _save(self):
        """Persist events to disk."""
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._events, f, indent=2, ensure_ascii=False)

    def add_event(self, title, date, time_start=None, time_end=None,
                  description="", all_day=False, category="general"):
        """Create a new event and return it."""
        event = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "date": date,
            "time_start": time_start,
            "time_end": time_end,
            "description": description,
            "all_day": all_day or (time_start is None),
            "category": category,
            "created": datetime.now().isoformat(),
        }
        self._events.append(event)
        self._save()
        return event

    def get_event(self, event_id):
        """Get a single event by ID."""
        for e in self._events:
            if e["id"] == event_id:
                return e
        return None

    def update_event(self, event_id, **kwargs):
        """Update fields on an existing event."""
        allowed = {"title", "date", "time_start", "time_end",
                   "description", "all_day", "category"}
        for e in self._events:
            if e["id"] == event_id:
                for k, v in kwargs.items():
                    if k in allowed:
                        e[k] = v
                self._save()
                return e
        return None

    def delete_event(self, event_id):
        """Delete an event by ID. Returns True if found."""
        before = len(self._events)
        self._events = [e for e in self._events if e["id"] != event_id]
        if len(self._events) < before:
            self._save()
            return True
        return False

    def list_events(self, start_date=None, end_date=None):
        """List events, optionally filtered by date range (YYYY-MM-DD strings)."""
        if not start_date and not end_date:
            return list(self._events)
        results = []
        for e in self._events:
            d = e.get("date", "")
            if start_date and d < start_date:
                continue
            if end_date and d > end_date:
                continue
            results.append(e)
        return sorted(results, key=lambda x: (x.get("date", ""), x.get("time_start") or ""))

    def get_events_for_date(self, date_str):
        """Get all events for a specific date."""
        return [e for e in self._events if e.get("date") == date_str]
