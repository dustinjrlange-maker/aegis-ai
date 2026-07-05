"""Linked-account registry for a user.

accounts.json holds metadata ONLY (no secrets). Per-account credentials live
in accounts/<id>/google_tokens.json — the same filename as the legacy
single-account file, so google_tools token load/refresh code works unchanged
when pointed at an account dir.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

REGISTRY_FILE = "accounts.json"
TOKEN_FILE = "google_tokens.json"          # Task 2/4 — per-account creds file, inside accounts/<id>/
DEFAULT_ACCOUNT_ID = "google-personal"     # Task 2 — id assigned to the migrated legacy account


class AccountManager:
    """Query and mutate the linked-account registry in a user's data dir."""

    def __init__(self, user_data_dir):
        self._dir = Path(user_data_dir)
        self._registry_path = self._dir / REGISTRY_FILE
        self._migrate_legacy_tokens()   # Task 2 — no-op until implemented

    # -- registry I/O ------------------------------------------------

    def _read(self):
        if not self._registry_path.exists():
            return {"accounts": []}
        try:
            return json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Could not load accounts.json: %s", e)
            return {"accounts": []}

    def _write(self, data):
        try:
            self._registry_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except IOError as e:
            logger.error("Could not write accounts.json: %s", e)
            raise

    # -- queries -----------------------------------------------------

    def list(self, feature=None):
        """All account records; with *feature*, only those with it enabled."""
        accounts = self._read().get("accounts", [])
        if feature is None:
            return accounts
        return [a for a in accounts if a.get("features", {}).get(feature)]

    def get(self, account_id):
        """Return the account record with this id, or None if not present."""
        for a in self.list():
            if a.get("id") == account_id:
                return a
        return None

    def default(self):
        """Account flagged is_default, else the first account, else None."""
        accounts = self.list()
        for a in accounts:
            if a.get("is_default"):
                return a
        return accounts[0] if accounts else None

    def resolve(self, hint):
        """Fuzzy-match *hint* to an account: exact id, then substring match on
        email/label/represent-as name (case-insensitive). Empty hint -> default.
        Unknown hint -> None (caller decides whether to fall back or ask)."""
        hint = (hint or "").strip().lower()
        if not hint:
            return self.default()
        accounts = self.list()
        for a in accounts:
            if a.get("id", "").lower() == hint:
                return a
        for a in accounts:
            haystacks = [
                a.get("email", ""), a.get("label", ""),
                (a.get("represent_as") or {}).get("name", ""),
            ]
            if any(hint in h.lower() for h in haystacks if h):
                return a
        return None

    def set_status(self, account_id, status):
        """Persist *status* on the matching account; silent no-op if id unknown."""
        data = self._read()
        for a in data.get("accounts", []):
            if a.get("id") == account_id:
                a["status"] = status
                self._write(data)
                return

    # -- migration (Task 2) -------------------------------------------

    def _migrate_legacy_tokens(self):
        pass
