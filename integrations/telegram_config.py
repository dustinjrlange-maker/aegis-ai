"""
Telegram Integration — Config Management
Loads/saves data/telegram.json with bot token, allowed IDs, and user mappings.
"""

import json
import logging
from pathlib import Path

from core.config import PROJECT_ROOT

logger = logging.getLogger("aegis.telegram.config")

CONFIG_PATH = PROJECT_ROOT / "data" / "telegram.json"

_DEFAULT_CONFIG = {
    "enabled": False,
    "bot_token": "",
    "allowed_telegram_ids": [],
    "user_mappings": {},
}


def _load_config() -> dict:
    """Load the Telegram config from disk, creating a default if missing."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Could not load telegram.json: %s", e)
    return dict(_DEFAULT_CONFIG)


def _save_config(cfg: dict):
    """Save the Telegram config to disk."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def is_enabled() -> bool:
    """Check if Telegram integration is enabled and has a token."""
    cfg = _load_config()
    return cfg.get("enabled", False) and bool(cfg.get("bot_token"))


def get_bot_token() -> str:
    """Get the bot token (empty string if not configured)."""
    return _load_config().get("bot_token", "")


def get_allowed_ids() -> list[int]:
    """Get the list of allowed Telegram user IDs."""
    return _load_config().get("allowed_telegram_ids", [])


def is_allowed(telegram_id: int) -> bool:
    """Check if a Telegram user ID is in the whitelist.

    If the whitelist is empty, all users are allowed (open mode).
    """
    allowed = get_allowed_ids()
    if not allowed:
        return True
    return telegram_id in allowed


def get_user_mapping(telegram_id: int) -> str | None:
    """Get the Aegis username mapped to a Telegram user ID."""
    cfg = _load_config()
    return cfg.get("user_mappings", {}).get(str(telegram_id))


def get_chat_id_for(username: str) -> int | None:
    """Reverse lookup: the Telegram chat_id linked to an Aegis username.

    Returns None when the username has no Telegram mapping. Used by the
    heartbeat notifier to push proactively to a user by their Aegis name.
    """
    mappings = _load_config().get("user_mappings", {})
    for tg_id_str, uname in mappings.items():
        if uname == username:
            try:
                return int(tg_id_str)
            except (TypeError, ValueError):
                return None
    return None


def save_user_mapping(telegram_id: int, username: str):
    """Link a Telegram user ID to an Aegis username."""
    cfg = _load_config()
    cfg.setdefault("user_mappings", {})[str(telegram_id)] = username
    _save_config(cfg)
    logger.info("Linked Telegram user %d to Aegis account '%s'", telegram_id, username)


def remove_user_mapping(telegram_id: int):
    """Unlink a Telegram user ID from its Aegis account."""
    cfg = _load_config()
    mappings = cfg.get("user_mappings", {})
    removed = mappings.pop(str(telegram_id), None)
    if removed:
        _save_config(cfg)
        logger.info("Unlinked Telegram user %d from Aegis account '%s'", telegram_id, removed)


def create_default_config():
    """Create a default disabled config file if one doesn't exist."""
    if not CONFIG_PATH.exists():
        _save_config(_DEFAULT_CONFIG)
        logger.info("Created default telegram.json at %s", CONFIG_PATH)
