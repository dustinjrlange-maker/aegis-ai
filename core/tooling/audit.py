"""
Tool Audit Log — append-only JSONL of every tool call, denial, and escalation.
Stored at data/users/<user>/mcp_tools/audit.jsonl.
"""

import json
import logging
from datetime import datetime

from core.config import PROJECT_ROOT

_DATA_ROOT = PROJECT_ROOT / "data" / "users"

logger = logging.getLogger("aegis.tooling.audit")


def _audit_path(username):
    return _DATA_ROOT / username.lower().strip() / "mcp_tools" / "audit.jsonl"


def log(username, tool_id, method, args, outcome, duration_ms):
    """Append one audit entry. outcome: ok | error | denied | pin_escalated.
    Best-effort: an I/O failure is logged and swallowed so callers that must
    not raise (the service layer) stay safe."""
    try:
        path = _audit_path(username)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(),
            "tool": tool_id,
            "method": method,
            "args": args,
            "outcome": outcome,
            "duration_ms": duration_ms,
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("Could not write audit entry for '%s': %s", username, e)


def recent(username, limit=50):
    """Return the newest `limit` entries, oldest first."""
    path = _audit_path(username)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines[-limit:] if line.strip()]
