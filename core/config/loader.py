"""
Aegis Configuration Loader
Loads and manages all system configuration for Aegis AI.
"""

import json
import logging
from pathlib import Path


# Project root is two levels up from core/config/
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "core" / "config" / "core_config.json"


def load_config():
    """Load Aegis configuration from core_config.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Resolve all paths to absolute paths relative to project root
    resolved = dict(config)
    resolved["_paths"] = {}
    for key, rel_path in config["paths"].items():
        resolved["_paths"][key] = PROJECT_ROOT / rel_path

    return resolved


def get_path(config, name):
    """Get an absolute path from config by name."""
    return config["_paths"][name]


def load_capabilities():
    """Load capabilities manifest and return a formatted prompt string."""
    cap_path = PROJECT_ROOT / "core" / "config" / "capabilities.json"
    try:
        with open(cap_path, "r", encoding="utf-8") as f:
            caps = json.load(f)
    except FileNotFoundError:
        return ""
    except json.JSONDecodeError as e:
        # A missing manifest is a normal minimal install; a CORRUPT one is a
        # real file being silently ignored — say so.
        logging.getLogger(__name__).error(
            "capabilities.json is corrupt (%s) — capabilities prompt disabled", e)
        return ""

    can_do = ", ".join(caps.get("can_do", []))
    cannot_do = ", ".join(caps.get("cannot_do", []))
    directive = caps.get("on_missing_feature", "")

    lines = [
        "=== SYSTEM CAPABILITIES ===",
        f"You CAN: {can_do}",
        f"You CANNOT: {cannot_do}",
        f"IMPORTANT: {directive}",
    ]
    return "\n".join(lines)


# Load once at import time
CONFIG = load_config()
