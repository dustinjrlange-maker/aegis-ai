"""
Crew Files Manager -- Aegis AI
Detailed profiles for people in the user's life (crew members).
"""

import json
import uuid
from datetime import datetime
from pathlib import Path


class CrewFilesManager:
    """Manages detailed crew member profiles stored as JSON."""

    def __init__(self, data_dir):
        self._file = Path(data_dir) / "crew_files.json"
        self._profiles = []
        self._load()

    def _load(self):
        if self._file.exists():
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    self._profiles = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._profiles = []

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._profiles, f, indent=2, ensure_ascii=False)

    def add_profile(self, name: str, **kwargs):
        """Create a new crew profile."""
        profile = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "role": kwargs.get("role", ""),
            "relationship": kwargs.get("relationship", ""),
            "department": kwargs.get("department", ""),
            "bio": kwargs.get("bio", ""),
            "phone": kwargs.get("phone", ""),
            "email": kwargs.get("email", ""),
            "birthday": kwargs.get("birthday", ""),
            "likes": kwargs.get("likes", ""),
            "dislikes": kwargs.get("dislikes", ""),
            "notes": kwargs.get("notes", ""),
            "history": kwargs.get("history", ""),
            "created": datetime.now().isoformat(),
            "updated": datetime.now().isoformat(),
        }
        self._profiles.append(profile)
        self._save()
        return profile

    def update_profile(self, profile_id: str, **kwargs):
        """Update fields on an existing crew profile."""
        allowed = {
            "name", "role", "relationship", "department", "bio",
            "phone", "email", "birthday", "likes", "dislikes",
            "notes", "history",
        }
        for p in self._profiles:
            if p["id"] == profile_id:
                for k, v in kwargs.items():
                    if k in allowed:
                        p[k] = v
                p["updated"] = datetime.now().isoformat()
                self._save()
                return p
        return None

    def delete_profile(self, profile_id: str) -> bool:
        """Delete a crew profile by ID."""
        before = len(self._profiles)
        self._profiles = [p for p in self._profiles if p["id"] != profile_id]
        if len(self._profiles) < before:
            self._save()
            return True
        return False

    def list_profiles(self):
        """List all crew profiles sorted by name."""
        return sorted(self._profiles, key=lambda p: p.get("name", "").lower())

    def get_profile(self, profile_id: str):
        """Get a single crew profile by ID."""
        for p in self._profiles:
            if p["id"] == profile_id:
                return p
        return None

    def search_profiles(self, query: str):
        """Search crew profiles by name, role, relationship, or notes."""
        q = query.lower()
        return [
            p for p in self._profiles
            if q in p.get("name", "").lower()
            or q in p.get("role", "").lower()
            or q in p.get("relationship", "").lower()
            or q in p.get("notes", "").lower()
            or q in p.get("department", "").lower()
        ]
