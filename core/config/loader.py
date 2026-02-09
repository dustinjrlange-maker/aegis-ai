"""
Aegis Configuration Loader
Loads and manages all system configuration for Aegis AI.
"""

import json
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


# Load once at import time
CONFIG = load_config()
