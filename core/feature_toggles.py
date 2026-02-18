"""Per-user feature toggles stored at data/users/{user}/features.json."""

import json
from pathlib import Path

DEFAULT_FEATURES = {
    "chat": True,
    "voice": True,
    "tasks": True,
    "web_search": True,
    "personal_logs": True,
    "fact_memory": True,
    # Future phases — off by default until implemented
    "calendar": False,
    "mood_tracking": False,
    "contacts": False,
    "habits": False,
    "timer": False,
    "weather": False,
    "file_upload": False,
    "pinned_messages": False,
}


def _features_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "features.json"


def load_feature_toggles(data_dir: str | Path) -> dict:
    """Load user's feature toggles, filling in defaults for missing keys."""
    path = _features_path(data_dir)
    toggles = dict(DEFAULT_FEATURES)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        toggles.update(stored)
    return toggles


def save_feature_toggles(data_dir: str | Path, toggles: dict) -> None:
    path = _features_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(toggles, f, indent=2)


def is_feature_enabled(data_dir: str | Path, feature_name: str) -> bool:
    toggles = load_feature_toggles(data_dir)
    return toggles.get(feature_name, False)
