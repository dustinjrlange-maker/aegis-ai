# core/llm/cloud_settings.py
"""Writes the router's runtime files (data/llm_router.json, data/anthropic_key)
and a one-shot key test. Reads live in config.py; writes live here so config.py
stays read-only. Paths are accessed through the config MODULE so tests that
monkeypatch core.llm.config._OVERRIDE_PATH / ._KEY_FILE affect these writes too.
"""
from __future__ import annotations

import json
import logging

from core.llm import config as _cfg

logger = logging.getLogger(__name__)


def get_cloud_status() -> dict:
    """Cloud status for the UI. Never includes the API key value."""
    cfg = _cfg.load_config()
    return {
        "cloud_enabled": cfg.cloud_enabled,
        "key_set": _cfg.resolve_api_key() is not None,
        "cloud_model": cfg.cloud_model,
    }


def set_cloud_enabled(enabled: bool) -> None:
    """Read-modify-write data/llm_router.json, updating only cloud_enabled and
    preserving every other key. A missing/corrupt file starts from {}."""
    path = _cfg._OVERRIDE_PATH
    data = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            logger.exception("Corrupt %s — starting fresh", path)
    data["cloud_enabled"] = bool(enabled)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
