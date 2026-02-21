"""
Mood Manager -- Aegis AI
Tracks user moods with energy levels and notes.
"""

import json
import uuid
from datetime import datetime, date
from pathlib import Path


class MoodManager:
    """Manages mood entries stored as JSON."""

    def __init__(self, data_dir):
        self._file = Path(data_dir) / "moods.json"
        self._moods = []
        self._load()

    def _load(self):
        if self._file.exists():
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    self._moods = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._moods = []

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._moods, f, indent=2, ensure_ascii=False)

    def add_mood(self, moods: list[str], note: str = "", energy: int | None = None):
        """Log a mood entry. moods is a list of mood words."""
        now = datetime.now()
        entry = {
            "id": uuid.uuid4().hex[:12],
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "moods": moods,
            "energy": energy,
            "note": note,
            "created": now.isoformat(),
        }
        self._moods.append(entry)
        self._save()
        return entry

    def list_moods(self, start: str | None = None, end: str | None = None):
        """List moods, optionally filtered by date range (YYYY-MM-DD)."""
        if not start and not end:
            return list(self._moods)
        results = []
        for m in self._moods:
            d = m.get("date", "")
            if start and d < start:
                continue
            if end and d > end:
                continue
            results.append(m)
        return sorted(results, key=lambda x: (x.get("date", ""), x.get("time", "")))

    def get_today_moods(self):
        """Get all mood entries for today."""
        today = date.today().isoformat()
        return [m for m in self._moods if m.get("date") == today]

    def delete_mood(self, mood_id: str) -> bool:
        """Delete a mood entry by ID."""
        before = len(self._moods)
        self._moods = [m for m in self._moods if m["id"] != mood_id]
        if len(self._moods) < before:
            self._save()
            return True
        return False
