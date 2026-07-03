"""
Tool Catalog — the curated, Claude-vetted list of installable MCP servers.
Catalog-only discovery: nothing outside this file can be installed (unmet
needs go to the wishlist instead).
"""

import json
from pathlib import Path

_CATALOG_PATH = Path(__file__).parent / "catalog.json"
_cache = None


def all_entries():
    """Return the full catalog as {tool_id: entry}."""
    global _cache
    if _cache is None:
        _cache = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    return _cache


def get_entry(tool_id):
    """Return one catalog entry, or None if the tool isn't in the catalog."""
    return all_entries().get(tool_id)


def search(query):
    """Return tool_ids whose id, name, or description contains the query."""
    q = query.lower().strip()
    return [
        tool_id for tool_id, e in all_entries().items()
        if q in tool_id.lower() or q in e["name"].lower() or q in e["description"].lower()
    ]
