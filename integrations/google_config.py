"""
Google Integration -- Config Management
Loads/saves data/google_client.json with OAuth2 client credentials.
This is the installation-level config (same for all users).
Per-user OAuth tokens are stored in data/users/<username>/google_tokens.json.
"""

import json
import logging
from pathlib import Path

from core.config import PROJECT_ROOT

logger = logging.getLogger("aegis.google.config")

CONFIG_PATH = PROJECT_ROOT / "data" / "google_client.json"

# OAuth2 scopes required for Gmail + Calendar
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    # gmail.compose covers draft create/update/delete AND sending drafts/messages.
    # gmail.send alone cannot touch the drafts API, which the Mail feature relies on.
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

_DEFAULT_CONFIG = {
    "enabled": False,
    "client_id": "",
    "client_secret": "",
}


def _load_config() -> dict:
    """Load the Google client config from disk, creating a default if missing."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Could not load google_client.json: %s", e)
    return dict(_DEFAULT_CONFIG)


def _save_config(cfg: dict):
    """Save the Google client config to disk."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def is_enabled() -> bool:
    """Check if Google integration is enabled and has client credentials."""
    cfg = _load_config()
    return cfg.get("enabled", False) and bool(cfg.get("client_id"))


def get_client_config() -> dict:
    """Get the OAuth2 client credentials."""
    cfg = _load_config()
    return {
        "client_id": cfg.get("client_id", ""),
        "client_secret": cfg.get("client_secret", ""),
    }


def create_default_config():
    """Create a default disabled config file if one doesn't exist."""
    if not CONFIG_PATH.exists():
        _save_config(_DEFAULT_CONFIG)
        logger.info("Created default google_client.json at %s", CONFIG_PATH)
