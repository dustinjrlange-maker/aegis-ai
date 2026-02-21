"""
Behavior Tracker -- Aegis AI
Tracks behaviors to quit with relapse logging and clean-day counters.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path


class BehaviorTracker:
    """Manages behavior tracking (Bad Dogs equivalent)."""

    def __init__(self, data_dir):
        self._file = Path(data_dir) / "behaviors.json"
        self._behaviors = []
        self._load()

    def _load(self):
        if self._file.exists():
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    self._behaviors = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._behaviors = []

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._behaviors, f, indent=2, ensure_ascii=False)

    def add_behavior(self, name: str):
        """Start tracking a new behavior to quit."""
        behavior = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "clean_since": datetime.now().isoformat(),
            "relapses": [],
            "best_clean_days": 0,
            "created": datetime.now().isoformat(),
        }
        self._behaviors.append(behavior)
        self._save()
        return behavior

    def log_relapse(self, behavior_id: str, note: str = ""):
        """Log a relapse for a behavior, resetting the clean counter."""
        now = datetime.now()
        for b in self._behaviors:
            if b["id"] == behavior_id:
                # Calculate days clean before relapse
                clean_since = datetime.fromisoformat(b["clean_since"])
                days_clean = (now - clean_since).days
                if days_clean > b.get("best_clean_days", 0):
                    b["best_clean_days"] = days_clean
                b["relapses"].append({
                    "date": now.isoformat(),
                    "note": note,
                    "days_clean_before": days_clean,
                })
                b["clean_since"] = now.isoformat()
                self._save()
                return b
        return None

    def list_behaviors(self):
        """List all tracked behaviors with current days clean."""
        now = datetime.now()
        result = []
        for b in self._behaviors:
            clean_since = datetime.fromisoformat(b["clean_since"])
            days_clean = (now - clean_since).days
            entry = dict(b)
            entry["current_clean_days"] = days_clean
            result.append(entry)
        return result

    def delete_behavior(self, behavior_id: str) -> bool:
        """Delete a tracked behavior by ID."""
        before = len(self._behaviors)
        self._behaviors = [b for b in self._behaviors if b["id"] != behavior_id]
        if len(self._behaviors) < before:
            self._save()
            return True
        return False
