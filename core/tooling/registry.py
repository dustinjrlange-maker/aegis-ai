"""
Installed-Tools Registry — per-user record of installed MCP tools.
Stored at data/users/<user>/mcp_tools/registry.json.
"""

import json
from datetime import datetime
from pathlib import Path

from core.config import PROJECT_ROOT

_DATA_ROOT = PROJECT_ROOT / "data" / "users"


def _registry_path(username):
    return _DATA_ROOT / username.lower().strip() / "mcp_tools" / "registry.json"


def _load(username):
    path = _registry_path(username)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save(username, data):
    path = _registry_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def install(username, tool_id, trust_tier, config):
    """Record a tool installation for a user."""
    data = _load(username)
    data[tool_id] = {
        "trust_tier": trust_tier,
        "config": config or {},
        "installed": datetime.now().isoformat(),
        "last_used": None,
        "call_count": 0,
    }
    _save(username, data)


def uninstall(username, tool_id):
    """Remove a tool. Returns True if it was installed."""
    data = _load(username)
    if tool_id not in data:
        return False
    del data[tool_id]
    _save(username, data)
    return True


def get(username, tool_id):
    """Return the registry entry for a tool, or None."""
    return _load(username).get(tool_id)


def installed_ids(username):
    """List installed tool_ids for a user."""
    return list(_load(username).keys())


def touch(username, tool_id):
    """Bump call_count and last_used after a successful call."""
    data = _load(username)
    if tool_id in data:
        data[tool_id]["call_count"] += 1
        data[tool_id]["last_used"] = datetime.now().isoformat()
        _save(username, data)
