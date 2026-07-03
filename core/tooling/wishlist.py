"""
Tool Wishlist — write-side only (4A). Unmet tool needs are appended here;
a scheduled Claude Code routine (4A.5) vets entries weekly.
Path comes from config key tooling.wishlist_path (Aegis is distributable —
never hardcode a machine-specific path).
"""

from datetime import datetime
from pathlib import Path

from core.config import PROJECT_ROOT

_HEADER = "# Aegis Tool Wishlist\n\nUnmet tool needs, appended by Aegis. Vetted weekly.\n"


def _wishlist_path():
    import core.config
    configured = core.config.CONFIG.get("tooling", {}).get("wishlist_path", "")
    if configured:
        return Path(configured)
    return PROJECT_ROOT / "data" / "tool_wishlist.md"


def add(username, description):
    """Append a wishlist entry. Returns the path written to."""
    path = _wishlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_HEADER, encoding="utf-8")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n- **{stamp}** ({username}): {description}\n")
    return path
