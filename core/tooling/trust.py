"""
Trust Tiers & PIN Escalation — 4-tier trust model for installed tools.

An operation within the installed tier runs; one above it soft-blocks and
requires a per-operation vault-PIN confirmation (never a permanent re-tier).
"""

import threading
from datetime import datetime, timedelta

from core.vault_pin import verify_vault_pin, has_vault_pin

# Order matters: index = privilege level.
TIERS = ["read_scoped", "read_broad", "write_scoped_undoable", "write_destructive"]

PENDING_MINUTES = 5

# username -> {tool_id, method, args, expires}. One pending op per user.
_pending = {}
_pending_lock = threading.Lock()


def required_tier(catalog_entry, method):
    """Tier a method needs: per-method map first, else the tool's default.
    Returns None if the catalog entry has no default_tier (handled fail-closed
    by check()). NOTE: default_tier must be set conservatively — any method not
    in method_tiers runs at default_tier, so under-classification is a fail-open
    risk in the CATALOG (not this code)."""
    method_tiers = catalog_entry.get("method_tiers") or {}
    return method_tiers.get(method, catalog_entry.get("default_tier"))


def check(installed_tier, catalog_entry, method):
    """Decide: 'allow' if the method fits the installed tier, else 'needs_pin'.
    Fails closed ('needs_pin') on any unknown/missing tier."""
    needed = required_tier(catalog_entry, method)
    if needed not in TIERS or installed_tier not in TIERS:
        return "needs_pin"
    if TIERS.index(needed) <= TIERS.index(installed_tier):
        return "allow"
    return "needs_pin"


def stash_pending(username, tool_id, method, args):
    """Hold an out-of-tier operation awaiting PIN confirmation."""
    with _pending_lock:
        _pending[username.lower().strip()] = {
            "tool_id": tool_id,
            "method": method,
            "args": args,
            "expires": datetime.now() + timedelta(minutes=PENDING_MINUTES),
        }


def confirm_with_pin(username, pin):
    """Verify the PIN and release the pending op for one-time execution.

    Returns (True, pending_entry) on success; (False, user_message) otherwise.
    The entry is consumed on success — a second confirmation needs a new stash.
    Verify-and-consume is atomic under _pending_lock so a double-submitted PIN
    cannot execute the operation twice.
    """
    username = username.lower().strip()
    with _pending_lock:
        entry = _pending.get(username)
        if entry is None:
            return False, "Nothing is waiting for PIN confirmation."
        if not has_vault_pin(username):
            return False, ("You don't have a vault PIN set. Set one from the vault "
                           "settings first, then retry the operation.")
        if datetime.now() > entry["expires"]:
            _pending.pop(username, None)
            return False, "That confirmation expired — run the operation again."
        if not verify_vault_pin(username, pin):
            return False, "Incorrect PIN. The operation is still pending."
        _pending.pop(username, None)
        return True, entry
