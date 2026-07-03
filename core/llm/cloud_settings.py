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
from core.llm.backends import CloudBackend

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


def set_api_key(key: str) -> None:
    """Write the API key to data/anthropic_key (trimmed). A blank key DELETES
    the file. The key is never returned or logged."""
    key = (key or "").strip()
    path = _cfg._KEY_FILE
    if not key:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")


def _friendly_error(e: Exception) -> str:
    """Map a cloud failure to a short, key-safe message for the UI."""
    text = f"{type(e).__name__}: {e}".lower()
    if "authentication" in text or "invalid x-api-key" in text or "401" in text:
        return "Key rejected"
    if "connection" in text or "network" in text or "timeout" in text:
        return "Couldn't reach Anthropic (network)"
    if "rate" in text and "limit" in text:
        return "Rate limited — try again shortly"
    return str(e) or type(e).__name__


def test_cloud_key() -> dict:
    """Validate the current key with one tiny real Claude call.
    Returns {'ok': True} or {'ok': False, 'error': <friendly str>}. Never the key."""
    if _cfg.resolve_api_key() is None:
        return {"ok": False, "error": "No API key set"}
    try:
        out = CloudBackend().chat([{"role": "user", "content": "ping"}])
    except Exception as e:  # any failure -> friendly message, never fatal
        return {"ok": False, "error": _friendly_error(e)}
    if isinstance(out, str) and out.strip():
        return {"ok": True}
    return {"ok": False, "error": "Empty response"}
