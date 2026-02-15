"""
Vault PIN Management — Aegis AI
Optional 4-6 digit PIN that gates access to vault logs via the HTTP API.
PIN hash stored in users.json alongside the user's passcode hash.
Pike (MemoryManager) always retains direct disk access regardless of PIN state.
"""

import re
import secrets
import logging
from datetime import datetime, timedelta

import bcrypt as _bcrypt

from core.auth import load_users, save_users

logger = logging.getLogger(__name__)

# In-memory vault unlock tokens: {token: {username, expires}}
_vault_tokens: dict[str, dict] = {}

VAULT_UNLOCK_MINUTES = 10


# --- PIN CRUD ---

def has_vault_pin(username: str) -> bool:
    """Check if a user has a vault PIN set."""
    users = load_users()
    user = users.get(username.lower().strip(), {})
    return bool(user.get("vault_pin_hash"))


def set_vault_pin(username: str, pin: str):
    """Set or change the vault PIN. Must be 4-6 digits."""
    username = username.lower().strip()
    if not re.fullmatch(r"\d{4,6}", pin):
        raise ValueError("PIN must be 4-6 digits")

    users = load_users()
    if username not in users:
        raise ValueError("User not found")

    hashed = _bcrypt.hashpw(pin.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    users[username]["vault_pin_hash"] = hashed
    save_users(users)
    logger.info("Vault PIN set for user: %s", username)


def verify_vault_pin(username: str, pin: str) -> bool:
    """Verify a PIN against the stored hash."""
    users = load_users()
    user = users.get(username.lower().strip(), {})
    pin_hash = user.get("vault_pin_hash")
    if not pin_hash:
        return False
    return _bcrypt.checkpw(pin.encode("utf-8"), pin_hash.encode("utf-8"))


def remove_vault_pin(username: str, current_pin: str) -> bool:
    """Remove the vault PIN after verifying the current one. Returns True on success."""
    username = username.lower().strip()
    if not verify_vault_pin(username, current_pin):
        return False

    users = load_users()
    if username in users:
        users[username].pop("vault_pin_hash", None)
        save_users(users)
        # Invalidate any active vault tokens for this user
        _purge_user_tokens(username)
        logger.info("Vault PIN removed for user: %s", username)
    return True


# --- Unlock Tokens ---

def create_vault_unlock(username: str) -> str:
    """Issue a 10-minute in-memory unlock token."""
    token = secrets.token_urlsafe(32)
    _vault_tokens[token] = {
        "username": username.lower().strip(),
        "expires": datetime.now() + timedelta(minutes=VAULT_UNLOCK_MINUTES),
    }
    return token


def validate_vault_unlock(username: str, vault_token: str) -> bool:
    """Check if a vault token is valid for the given user."""
    if not vault_token:
        return False
    entry = _vault_tokens.get(vault_token)
    if not entry:
        return False
    if entry["username"] != username.lower().strip():
        return False
    if datetime.now() > entry["expires"]:
        _vault_tokens.pop(vault_token, None)
        return False
    # Extend expiry on successful validation (sliding window)
    entry["expires"] = datetime.now() + timedelta(minutes=VAULT_UNLOCK_MINUTES)
    return True


def invalidate_vault_unlock(vault_token: str):
    """Revoke a vault unlock token (e.g. on logout)."""
    _vault_tokens.pop(vault_token, None)


def _purge_user_tokens(username: str):
    """Remove all vault tokens for a user."""
    username = username.lower().strip()
    to_remove = [t for t, v in _vault_tokens.items() if v["username"] == username]
    for t in to_remove:
        del _vault_tokens[t]
