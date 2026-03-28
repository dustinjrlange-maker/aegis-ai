"""
Event Manager — Aegis AI
Local event storage: CRUD for user calendar events.
Supports recurring events, conflict detection, and reminders.
"""

import json
import uuid
from datetime import datetime, date, timedelta
from pathlib import Path


class EventManager:
    """Manages local calendar events stored as JSON."""

    VALID_REPEAT_TYPES = {"none", "daily", "weekly", "monthly", "weekdays"}

    def __init__(self, data_dir):
        self._file = Path(data_dir) / "events.json"
        self._events = []
        self._reminded_today = set()
        self._reminded_date = date.today().isoformat()
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
                  description="", all_day=False, category="general",
                  repeat_type="none", repeat_until=None, reminder_minutes=0):
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
            "repeat_type": repeat_type if repeat_type in self.VALID_REPEAT_TYPES else "none",
            "repeat_until": repeat_until,
            "exceptions": [],
            "reminder_minutes": reminder_minutes or 0,
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
                   "description", "all_day", "category",
                   "repeat_type", "repeat_until", "reminder_minutes"}
        for e in self._events:
            if e["id"] == event_id:
                for k, v in kwargs.items():
                    if k in allowed:
                        if k == "repeat_type" and v not in self.VALID_REPEAT_TYPES:
                            continue
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
        """List events, optionally filtered by date range (YYYY-MM-DD strings).

        Recurring events are expanded into virtual instances within the range.
        """
        if not start_date and not end_date:
            # Return raw stored events (no expansion without a range)
            return list(self._events)
        results = []
        for e in self._events:
            rt = e.get("repeat_type", "none")
            if rt and rt != "none" and start_date and end_date:
                results.extend(self._expand_recurring(e, start_date, end_date))
            else:
                d = e.get("date", "")
                if start_date and d < start_date:
                    continue
                if end_date and d > end_date:
                    continue
                results.append(e)
        return sorted(results, key=lambda x: (x.get("date", ""), x.get("time_start") or ""))

    def get_events_for_date(self, date_str):
        """Get all events for a specific date, including recurring instances."""
        results = []
        for e in self._events:
            rt = e.get("repeat_type", "none")
            if rt and rt != "none":
                results.extend(self._expand_recurring(e, date_str, date_str))
            elif e.get("date") == date_str:
                results.append(e)
        return results

    # --- Recurring event expansion ---

    def _expand_recurring(self, event, start_date, end_date):
        """Generate virtual instances of a recurring event within [start_date, end_date]."""
        repeat_type = event.get("repeat_type", "none")
        if repeat_type == "none":
            return []

        exceptions = set(event.get("exceptions", []))
        event_date_str = event.get("date", "")
        repeat_until = event.get("repeat_until")

        try:
            event_start = date.fromisoformat(event_date_str)
            range_start = date.fromisoformat(start_date)
            range_end = date.fromisoformat(end_date)
        except (ValueError, TypeError):
            return []

        # Don't generate instances before the event's own start date
        if range_start < event_start:
            range_start = event_start

        # Respect repeat_until
        if repeat_until:
            try:
                until = date.fromisoformat(repeat_until)
                if range_end > until:
                    range_end = until
            except (ValueError, TypeError):
                pass

        instances = []
        current = range_start
        parent_id = event["id"]

        while current <= range_end:
            date_str = current.isoformat()
            should_include = False

            if repeat_type == "daily":
                should_include = True
            elif repeat_type == "weekly":
                should_include = (current.weekday() == event_start.weekday())
            elif repeat_type == "monthly":
                should_include = (current.day == event_start.day)
            elif repeat_type == "weekdays":
                should_include = (current.weekday() < 5)  # Mon-Fri

            if should_include and date_str not in exceptions:
                instance = dict(event)
                instance["id"] = f"{parent_id}_r{date_str.replace('-', '')}"
                instance["date"] = date_str
                instance["_parent_id"] = parent_id
                instance["_is_recurring_instance"] = True
                instances.append(instance)

            current += timedelta(days=1)

        return instances

    def delete_occurrence(self, event_id, date_str):
        """Delete a single occurrence of a recurring event by adding to exceptions."""
        # event_id could be the synthetic ID like "abc123_r20260320"
        parent_id = event_id
        if "_r" in event_id:
            parent_id = event_id.split("_r")[0]

        for e in self._events:
            if e["id"] == parent_id:
                exceptions = e.setdefault("exceptions", [])
                if date_str not in exceptions:
                    exceptions.append(date_str)
                    self._save()
                return True
        return False

    # --- Conflict detection ---

    def check_conflicts(self, date_str, time_start, time_end, exclude_id=None):
        """Check for time overlaps on a given date. Returns list of conflicting events."""
        if not time_start or not time_end:
            return []

        day_events = self.get_events_for_date(date_str)
        conflicts = []

        for ev in day_events:
            if exclude_id and (ev.get("id") == exclude_id or ev.get("_parent_id") == exclude_id):
                continue
            ev_start = ev.get("time_start")
            ev_end = ev.get("time_end")
            if not ev_start or not ev_end:
                continue
            # Overlap: new_start < existing_end AND new_end > existing_start
            if time_start < ev_end and time_end > ev_start:
                conflicts.append({
                    "id": ev["id"],
                    "title": ev.get("title", ""),
                    "time_start": ev_start,
                    "time_end": ev_end,
                })

        return conflicts

    # --- Reminders ---

    def check_due_reminders(self):
        """Check for events with reminders due now. Returns list of due events."""
        today_str = date.today().isoformat()

        # Reset reminded set at midnight
        if self._reminded_date != today_str:
            self._reminded_today = set()
            self._reminded_date = today_str

        now = datetime.now()
        now_time_str = now.strftime("%H:%M")
        day_events = self.get_events_for_date(today_str)
        due = []

        for ev in day_events:
            rem_min = ev.get("reminder_minutes", 0)
            ev_start = ev.get("time_start")
            if not rem_min or not ev_start:
                continue

            ev_id = ev["id"]
            if ev_id in self._reminded_today:
                continue

            try:
                event_dt = datetime.strptime(f"{today_str} {ev_start}", "%Y-%m-%d %H:%M")
                reminder_dt = event_dt - timedelta(minutes=rem_min)
                # Fire if we're within a 1-minute window of the reminder time
                diff = (now - reminder_dt).total_seconds()
                if 0 <= diff < 60:
                    self._reminded_today.add(ev_id)
                    due.append({
                        "id": ev_id,
                        "title": ev.get("title", ""),
                        "time_start": ev_start,
                        "reminder_minutes": rem_min,
                    })
            except (ValueError, TypeError):
                continue

        return due
