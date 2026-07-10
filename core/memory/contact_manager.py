"""
Contact Manager -- Aegis AI
CRUD for user contacts (PupPals equivalent).
"""

import uuid
from datetime import datetime
from pathlib import Path

from core.jsonio import read_json_safe, write_json_atomic


class ContactManager:
    """Manages contacts stored as JSON."""

    def __init__(self, data_dir):
        self._file = Path(data_dir) / "contacts.json"
        self._contacts = []
        self._load()

    def _load(self):
        self._contacts = read_json_safe(self._file, [], "contacts.json")

    def _save(self):
        write_json_atomic(self._file, self._contacts, indent=2)

    def add_contact(self, name: str, **kwargs):
        """Create a new contact."""
        contact = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "relationship": kwargs.get("relationship", ""),
            "phone": kwargs.get("phone", ""),
            "email": kwargs.get("email", ""),
            "birthday": kwargs.get("birthday", ""),
            "likes": kwargs.get("likes", ""),
            "dislikes": kwargs.get("dislikes", ""),
            "notes": kwargs.get("notes", ""),
            "created": datetime.now().isoformat(),
        }
        self._contacts.append(contact)
        self._save()
        return contact

    def update_contact(self, contact_id: str, **kwargs):
        """Update fields on an existing contact."""
        allowed = {"name", "relationship", "phone", "email", "birthday",
                    "likes", "dislikes", "notes"}
        for c in self._contacts:
            if c["id"] == contact_id:
                for k, v in kwargs.items():
                    if k in allowed:
                        c[k] = v
                self._save()
                return c
        return None

    def delete_contact(self, contact_id: str) -> bool:
        """Delete a contact by ID."""
        before = len(self._contacts)
        self._contacts = [c for c in self._contacts if c["id"] != contact_id]
        if len(self._contacts) < before:
            self._save()
            return True
        return False

    def list_contacts(self):
        """List all contacts sorted by name."""
        return sorted(self._contacts, key=lambda c: c.get("name", "").lower())

    def search_contacts(self, query: str):
        """Search contacts by name, relationship, or notes."""
        q = query.lower()
        return [c for c in self._contacts
                if q in c.get("name", "").lower()
                or q in c.get("relationship", "").lower()
                or q in c.get("notes", "").lower()]

    def get_contact(self, contact_id: str):
        """Get a single contact by ID."""
        for c in self._contacts:
            if c["id"] == contact_id:
                return c
        return None
