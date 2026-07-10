"""Per-user feature toggles stored at data/users/{user}/features.json."""

import json

from core.jsonio import read_json_safe, write_json_atomic
from pathlib import Path

DEFAULT_FEATURES = {
    "chat": True,
    "voice": True,
    "tasks": True,
    "web_search": True,
    "personal_logs": True,
    "fact_memory": True,
    "notifications": True,
    "daily_briefing": True,
    "calendar": True,
    "mood_tracking": True,
    "contacts": True,
    "habits": True,
    "bad_dogs": False,
    "pinned_messages": True,
    "time_tracking": False,
    "weather": True,
    "alarms": False,
    "file_upload": False,
    "social_media": False,
    "night_mode": True,
}


def _features_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "features.json"


def load_feature_toggles(data_dir: str | Path) -> dict:
    """Load user's feature toggles, filling in defaults for missing keys."""
    path = _features_path(data_dir)
    toggles = dict(DEFAULT_FEATURES)
    stored = read_json_safe(path, {}, "features.json")
    toggles.update(stored)
    return toggles


def save_feature_toggles(data_dir: str | Path, toggles: dict) -> None:
    path = _features_path(data_dir)
    write_json_atomic(path, toggles, indent=2)


def is_feature_enabled(data_dir: str | Path, feature_name: str) -> bool:
    toggles = load_feature_toggles(data_dir)
    return toggles.get(feature_name, False)
