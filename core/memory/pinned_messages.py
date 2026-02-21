"""
Pinned Messages -- Aegis AI
Allows pinning chat messages for later reference.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path


class PinnedMessageManager:
    """Manages pinned chat messages stored as JSON."""

    def __init__(self, data_dir):
        self._file = Path(data_dir) / "pinned_messages.json"
        self._pinned = []
        self._load()

    def _load(self):
        if self._file.exists():
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    self._pinned = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._pinned = []

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._pinned, f, indent=2, ensure_ascii=False)

    def pin_message(self, role: str, text: str, sender: str = "", note: str = ""):
        """Pin a chat message."""
        entry = {
            "id": uuid.uuid4().hex[:12],
            "role": role,
            "text": text,
            "sender": sender,
            "note": note,
            "pinned_at": datetime.now().isoformat(),
        }
        self._pinned.append(entry)
        self._save()
        return entry

    def unpin(self, pin_id: str) -> bool:
        """Remove a pinned message by ID."""
        before = len(self._pinned)
        self._pinned = [p for p in self._pinned if p["id"] != pin_id]
        if len(self._pinned) < before:
            self._save()
            return True
        return False

    def list_pinned(self):
        """List all pinned messages, newest first."""
        return sorted(self._pinned, key=lambda p: p.get("pinned_at", ""), reverse=True)
