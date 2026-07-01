# core/llm/config.py
"""Loads router settings: core_config.json defaults, overlaid by an optional
runtime override at data/llm_router.json (gitignored, created out-of-band)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from core.config import CONFIG, PROJECT_ROOT

logger = logging.getLogger(__name__)

_OVERRIDE_PATH = PROJECT_ROOT / "data" / "llm_router.json"


@dataclass
class RouterConfig:
    cloud_enabled: bool = False
    cloud_opt_in_features: tuple = field(default_factory=tuple)


def load_config():
    """Build a RouterConfig from config defaults + optional override file.

    A missing or corrupt override file is logged and ignored (never crashes).
    """
    defaults = CONFIG.get("llm_router", {})
    cloud_enabled = bool(defaults.get("cloud_enabled", False))
    opt_in = list(defaults.get("cloud_opt_in_features", []))

    if _OVERRIDE_PATH.exists():
        try:
            data = json.loads(_OVERRIDE_PATH.read_text(encoding="utf-8"))
            if "cloud_enabled" in data:
                cloud_enabled = bool(data["cloud_enabled"])
            if "cloud_opt_in_features" in data:
                opt_in = list(data["cloud_opt_in_features"])
        except Exception:
            logger.exception("Bad %s — using config defaults", _OVERRIDE_PATH)

    return RouterConfig(cloud_enabled=cloud_enabled,
                        cloud_opt_in_features=tuple(opt_in))
