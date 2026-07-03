# core/llm/config.py
"""Loads router settings: core_config.json defaults, overlaid by an optional
runtime override at data/llm_router.json (gitignored, created out-of-band)."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

from core.config import CONFIG, PROJECT_ROOT

logger = logging.getLogger(__name__)

_OVERRIDE_PATH = PROJECT_ROOT / "data" / "llm_router.json"
_KEY_FILE = PROJECT_ROOT / "data" / "anthropic_key"


@dataclass
class RouterConfig:
    """Resolved router settings used by policy.decide() and router.chat()."""
    cloud_enabled: bool = False
    cloud_opt_in_features: tuple = field(default_factory=tuple)
    cloud_model: str = "claude-opus-4-8"
    cloud_max_tokens: int = 2048
    deep_mode: bool = False


def load_config():
    """Build a RouterConfig from config defaults + optional override file.

    A missing or corrupt override file is logged and ignored (never crashes).
    """
    defaults = CONFIG.get("llm_router", {})
    cloud_enabled = bool(defaults.get("cloud_enabled", False))
    opt_in = list(defaults.get("cloud_opt_in_features", []))
    cloud_model = str(defaults.get("cloud_model", "claude-opus-4-8"))
    cloud_max_tokens = int(defaults.get("cloud_max_tokens", 2048))
    deep_mode = bool(defaults.get("deep_mode", False))

    if _OVERRIDE_PATH.exists():
        try:
            data = json.loads(_OVERRIDE_PATH.read_text(encoding="utf-8"))
            if "cloud_enabled" in data:
                cloud_enabled = bool(data["cloud_enabled"])
            if "cloud_opt_in_features" in data:
                opt_in = list(data["cloud_opt_in_features"])
            if "cloud_model" in data:
                cloud_model = str(data["cloud_model"])
            if "cloud_max_tokens" in data:
                cloud_max_tokens = int(data["cloud_max_tokens"])
            if "deep_mode" in data:
                deep_mode = bool(data["deep_mode"])
        except Exception:
            logger.exception("Bad %s — using config defaults", _OVERRIDE_PATH)

    return RouterConfig(
        cloud_enabled=cloud_enabled,
        cloud_opt_in_features=tuple(opt_in),
        cloud_model=cloud_model,
        cloud_max_tokens=cloud_max_tokens,
        deep_mode=deep_mode,
    )


def resolve_api_key():
    """Resolve the Anthropic API key: ANTHROPIC_API_KEY env var first, then the
    gitignored data/anthropic_key file. Returns None if neither is present.
    Never raises — a bad key file is logged and treated as absent."""
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()
    if _KEY_FILE.exists():
        try:
            text = _KEY_FILE.read_text(encoding="utf-8").strip()
            return text or None
        except Exception:
            logger.exception("Bad %s — treating as no key", _KEY_FILE)
    return None
