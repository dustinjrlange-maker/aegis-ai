"""
Habit Manager -- Aegis AI
Tracks habits with streaks and daily completions.
"""

import json
import uuid
from datetime import datetime, date, timedelta
from pathlib import Path


class HabitManager:
    """Manages habits with completion tracking and streaks."""

    def __init__(self, data_dir):
        self._file = Path(data_dir) / "habits.json"
        self._habits = []
        self._load()

    def _load(self):
        if self._file.exists():
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    self._habits = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._habits = []

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._habits, f, indent=2, ensure_ascii=False)

    def add_habit(self, name: str, frequency: str = "daily"):
        """Create a new habit."""
        habit = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "frequency": frequency,
            "completions": {},
            "current_streak": 0,
            "best_streak": 0,
            "created": datetime.now().isoformat(),
        }
        self._habits.append(habit)
        self._save()
        return habit

    def check_in(self, habit_id: str, check_date: str | None = None):
        """Mark a habit as done for a date (default today)."""
        d = check_date or date.today().isoformat()
        for h in self._habits:
            if h["id"] == habit_id:
                h["completions"][d] = True
                self._recalculate_streak(h)
                self._save()
                return h
        return None

    def uncheck(self, habit_id: str, check_date: str | None = None):
        """Remove a completion for a date."""
        d = check_date or date.today().isoformat()
        for h in self._habits:
            if h["id"] == habit_id:
                h["completions"].pop(d, None)
                self._recalculate_streak(h)
                self._save()
                return h
        return None

    def _recalculate_streak(self, habit: dict):
        """Recalculate current and best streak for a habit."""
        completions = sorted(habit.get("completions", {}).keys(), reverse=True)
        if not completions:
            habit["current_streak"] = 0
            return

        # Current streak: count consecutive days back from today
        today = date.today()
        current = 0
        check = today
        while check.isoformat() in habit["completions"]:
            current += 1
            check -= timedelta(days=1)

        habit["current_streak"] = current
        if current > habit.get("best_streak", 0):
            habit["best_streak"] = current

    def recalculate_streak(self, habit_id: str):
        """Public method to recalculate a specific habit's streak."""
        for h in self._habits:
            if h["id"] == habit_id:
                self._recalculate_streak(h)
                self._save()
                return h
        return None

    def list_habits(self):
        """List all habits."""
        return list(self._habits)

    def get_today_status(self):
        """Get all habits with their completion status for today."""
        today = date.today().isoformat()
        result = []
        for h in self._habits:
            result.append({
                "id": h["id"],
                "name": h["name"],
                "frequency": h["frequency"],
                "done_today": h.get("completions", {}).get(today, False),
                "current_streak": h.get("current_streak", 0),
                "best_streak": h.get("best_streak", 0),
            })
        return result

    def delete_habit(self, habit_id: str) -> bool:
        """Delete a habit by ID."""
        before = len(self._habits)
        self._habits = [h for h in self._habits if h["id"] != habit_id]
        if len(self._habits) < before:
            self._save()
            return True
        return False
