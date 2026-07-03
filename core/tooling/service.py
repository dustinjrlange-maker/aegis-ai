"""
Tooling Service — shared install/call/confirm flows used by both the chat
protocol (/tools commands) and the /api/tools/* endpoints. Composes catalog,
registry, trust, audit, and the MCPManager.
"""

import logging
import shutil
import sys
import time as _time

from core.tooling import audit, catalog, registry, trust
from core.tooling.mcp_manager import MANAGER, SPAWN_TIMEOUT

logger = logging.getLogger("aegis.tooling.service")


def _resolve_launch(entry, config):
    """Build (command, args) for a catalog entry. Raises RuntimeError if the
    runtime isn't available. Windows rule: resolve npx via shutil.which
    ('npx' alone won't spawn — it's npx.cmd)."""
    launch = entry["launch"]
    command = launch["command"]
    args = list(launch["args"])
    if command == "python":
        command = sys.executable
    elif command == "npx":
        resolved = shutil.which("npx")
        if not resolved:
            raise RuntimeError("Node/npx not found — install Node.js to use this tool.")
        command = resolved
    append_key = launch.get("append_config")
    if append_key:
        args.extend(config.get(append_key, []))
    return command, args


def _ensure_running(username, tool_id, reg_entry, cat_entry):
    command, args = _resolve_launch(cat_entry, reg_entry.get("config", {}))
    MANAGER.ensure_started(username, tool_id, command, args, timeout=SPAWN_TIMEOUT)


def install_tool(username, tool_id, config):
    """Install a catalog tool for a user; warm up the server. Returns a message."""
    entry = catalog.get_entry(tool_id)
    if entry is None:
        return f"'{tool_id}' isn't in the catalog. Try /tools find <query>, or /tools wish <description>."

    config = config or {}
    missing = [f for f in entry["config_fields"] if not config.get(f)]
    if missing:
        return (f"'{tool_id}' needs config before install: {', '.join(missing)}. "
                f"Example: /tools install filesystem approved_dirs=C:/Users/you/Documents")

    tier = entry["default_tier"]
    registry.install(username, tool_id, trust_tier=tier, config=config)

    # Warm-up: spawn now so npx package download happens at install, not first call.
    try:
        reg_entry = registry.get(username, tool_id)
        _ensure_running(username, tool_id, reg_entry, entry)
        tools = MANAGER.list_tools(username, tool_id, timeout=SPAWN_TIMEOUT)
        names = ", ".join(t["name"] for t in tools[:8])
        return (f"Installed '{tool_id}' at trust tier {tier}. "
                f"Server is up — methods: {names}")
    except Exception as e:
        logger.warning("Warm-up failed for %s: %s", tool_id, e)
        return (f"Installed '{tool_id}' at trust tier {tier}, but the server "
                f"failed to start: {e}")


def uninstall_tool(username, tool_id):
    """Uninstall and stop a tool."""
    MANAGER.stop(username, tool_id)
    if registry.uninstall(username, tool_id):
        return f"Uninstalled '{tool_id}'."
    return f"'{tool_id}' isn't installed."


def call_tool(username, tool_id, method, arguments):
    """Trust-checked tool invocation.

    Returns {"status": "ok", "result": [...]} |
            {"status": "needs_pin", "message": str} |
            {"status": "error", "message": str}
    """
    reg_entry = registry.get(username, tool_id)
    if reg_entry is None:
        return {"status": "error", "message": f"'{tool_id}' is not installed."}
    cat_entry = catalog.get_entry(tool_id)
    if cat_entry is None:
        return {"status": "error", "message": f"'{tool_id}' is no longer in the catalog."}

    decision = trust.check(reg_entry["trust_tier"], cat_entry, method)
    if decision == "needs_pin":
        trust.stash_pending(username, tool_id, method, arguments)
        audit.log(username, tool_id, method, arguments, "denied", 0)
        return {"status": "needs_pin", "message": (
            f"'{method}' is a {trust.required_tier(cat_entry, method)} operation — "
            f"outside {tool_id}'s granted tier ({reg_entry['trust_tier']}). "
            f"Confirm once with: /tools pin <your vault PIN> (expires in "
            f"{trust.PENDING_MINUTES} min)")}

    return _execute(username, tool_id, method, arguments, reg_entry, cat_entry, "ok")


def confirm_pending(username, pin):
    """Verify PIN and execute the stashed out-of-tier operation once."""
    ok, entry_or_msg = trust.confirm_with_pin(username, pin)
    if not ok:
        return {"status": "error", "message": entry_or_msg}
    entry = entry_or_msg
    reg_entry = registry.get(username, entry["tool_id"])
    cat_entry = catalog.get_entry(entry["tool_id"])
    if reg_entry is None or cat_entry is None:
        return {"status": "error", "message": f"'{entry['tool_id']}' is not installed."}
    return _execute(username, entry["tool_id"], entry["method"], entry["args"],
                    reg_entry, cat_entry, "pin_escalated")


def _execute(username, tool_id, method, arguments, reg_entry, cat_entry, outcome_tag):
    started = _time.monotonic()
    try:
        _ensure_running(username, tool_id, reg_entry, cat_entry)
        result = MANAGER.call(username, tool_id, method, arguments)
        registry.touch(username, tool_id)
        audit.log(username, tool_id, method, arguments, outcome_tag,
                  int((_time.monotonic() - started) * 1000))
        return {"status": "ok", "result": result}
    except Exception as e:
        audit.log(username, tool_id, method, arguments, "error",
                  int((_time.monotonic() - started) * 1000))
        return {"status": "error", "message": f"{tool_id}.{method} failed: {e}"}


def installed_summary(username):
    """[{tool_id, tier, running, call_count}] for /tools list and the endpoint."""
    out = []
    for tool_id in registry.installed_ids(username):
        entry = registry.get(username, tool_id)
        out.append({
            "tool_id": tool_id,
            "trust_tier": entry["trust_tier"],
            "running": MANAGER.is_running(username, tool_id),
            "call_count": entry["call_count"],
        })
    return out
