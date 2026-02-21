"""
Alarm Manager -- Aegis AI
Manages user alarms with day-of-week scheduling.
"""

import json
import uuid
from datetime import datetime, date
from pathlib import Path


class AlarmManager:
    """Manages alarms stored as JSON."""

    # Day name abbreviations
    DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    def __init__(self, data_dir):
        self._file = Path(data_dir) / "alarms.json"
        self._alarms = []
        self._load()

    def _load(self):
        if self._file.exists():
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    self._alarms = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._alarms = []

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._alarms, f, indent=2, ensure_ascii=False)

    def add_alarm(self, label: str, time: str, days: list[str] | None = None):
        """Create a new alarm. time is HH:MM, days is list of day abbreviations."""
        alarm = {
            "id": uuid.uuid4().hex[:12],
            "label": label,
            "time": time,
            "days": days or [],
            "enabled": True,
            "last_triggered": None,
            "created": datetime.now().isoformat(),
        }
        self._alarms.append(alarm)
        self._save()
        return alarm

    def list_alarms(self):
        """List all alarms."""
        return list(self._alarms)

    def toggle_alarm(self, alarm_id: str) -> dict | None:
        """Toggle an alarm's enabled state."""
        for a in self._alarms:
            if a["id"] == alarm_id:
                a["enabled"] = not a["enabled"]
                self._save()
                return a
        return None

    def delete_alarm(self, alarm_id: str) -> bool:
        """Delete an alarm by ID."""
        before = len(self._alarms)
        self._alarms = [a for a in self._alarms if a["id"] != alarm_id]
        if len(self._alarms) < before:
            self._save()
            return True
        return False

    def check_due_alarms(self):
        """Check for alarms that should fire right now. Returns list of due alarms."""
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_day = self.DAYS[now.weekday()]
        today_str = date.today().isoformat()
        due = []

        for a in self._alarms:
            if not a.get("enabled", True):
                continue
            if a["time"] != current_time:
                continue
            if a.get("last_triggered") == today_str:
                continue
            # Check day filter (empty = every day)
            if a["days"] and current_day not in a["days"]:
                continue
            due.append(a)

        return due

    def dismiss(self, alarm_id: str):
        """Mark an alarm as triggered for today so it won't fire again."""
        today_str = date.today().isoformat()
        for a in self._alarms:
            if a["id"] == alarm_id:
                a["last_triggered"] = today_str
                self._save()
                return a
        return None
