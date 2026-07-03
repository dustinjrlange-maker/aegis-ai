"""
Tooling Protocol — slash-command surface for MCP tool discovery (Phase 4A).
/tools list | find | install | uninstall | call | wish | pin
Pike does not auto-call tools yet (that's Phase 4B).
"""

import logging
import re

from core.protocols.base import Protocol
from core.tooling import catalog, service, wishlist

logger = logging.getLogger("aegis.protocols.tooling")


# [TOOL: tool_id.method key=value ...]   (validated against the catalog below)
# NOTE: tooling runs process_output at priority 50, ABOVE bracket_commands (49),
# so it strips [TOOL:] before bracket_commands sees it; and no "TOOL" handler is
# registered there anyway. Don't register a TOOL bracket handler or reorder these.
_TOOL_RE = re.compile(r"\[TOOL:\s*([a-z0-9_-]+)\.([a-z0-9_-]+)\s*(.*?)\]", re.I)

# Lenient fallback: the local 8B drops the "TOOL:" prefix under format drift and
# emits e.g. [filesystem.read_file path=...]. Safe to accept because the REAL
# gate is validation (installed tool + known method) — a shorthand bracket that
# doesn't validate is left completely untouched (no strip, no rejection), so
# ordinary bracketed prose can't false-positive into a tool call.
_TOOL_SHORTHAND_RE = re.compile(r"\[([a-z0-9_-]+)\.([a-z0-9_-]+)\s*(.*?)\]", re.I)


def _autocall_enabled():
    """Whether Pike may auto-call tools (Phase 4B). Default on."""
    from core.config import CONFIG
    return CONFIG.get("tooling", {}).get("autocall_enabled", True)


class ToolingProtocol(Protocol):
    """Manual tool management via /tools commands."""

    def __init__(self, username):
        super().__init__(
            name="tooling",
            description="MCP tool discovery: install and call external tools",
            priority=Protocol.PRIORITY_NORMAL,
        )
        self.username = username
        self._pending_tool_calls = []
        self._rejections = []

    # --- Protocol ABC ---

    def process_input(self, user_input, context):
        """Inject the installed tools' methods so Pike can call them (Phase 4B)."""
        empty = {"input": user_input, "context_injection": "",
                 "intercept": False, "response": ""}
        if not _autocall_enabled():
            return empty
        from core.tooling import registry
        installed = registry.installed_ids(self.username)
        if not installed:
            return empty
        lines = ["Available tools — emit [TOOL: tool.method key=value] on its own "
                 "line to use one:"]
        for tool_id in installed:
            entry = catalog.get_entry(tool_id)
            if not entry:
                continue
            for method, hint in entry.get("method_hints", {}).items():
                lines.append(f"  {tool_id}.{method} {hint}")
            # Surface config constraints (e.g. filesystem's approved dirs) so Pike
            # uses real absolute paths instead of guessing ~/… or /home/user/….
            reg_entry = registry.get(self.username, tool_id)
            approved = (reg_entry or {}).get("config", {}).get("approved_dirs")
            if approved:
                lines.append(f"    for {tool_id}, use absolute paths under: "
                             f"{', '.join(approved)}")
        if len(lines) == 1:            # installed tools had no hints
            return empty
        lines.append("Only call a tool when the request needs live data or an action "
                     "you can't do from memory. After a tool runs you'll see its result "
                     "and can answer or call another tool.")
        return {"input": user_input, "context_injection": "\n".join(lines),
                "intercept": False, "response": ""}

    def process_output(self, response, context):
        """Parse [TOOL: tool.method args] from Pike's output; stash + strip.
        Does NOT execute — the chat pipeline runs pending calls off the loop."""
        self._pending_tool_calls = []
        self._rejections = []
        if not _autocall_enabled():
            return {"response": response, "suppress": False, "append": ""}
        from core.tooling import registry
        strict = list(_TOOL_RE.finditer(response))
        strict_spans = [m.span() for m in strict]
        # Shorthand matches that don't overlap a strict match (a strict
        # [TOOL: x.y] also matches the shorthand pattern — dedupe by span).
        shorthand = [m for m in _TOOL_SHORTHAND_RE.finditer(response)
                     if not any(s[0] <= m.start() < s[1] for s in strict_spans)]
        if not strict and not shorthand:
            return {"response": response, "suppress": False, "append": ""}
        clean = response
        for m, is_strict in [(m, True) for m in strict] + [(m, False) for m in shorthand]:
            try:
                tool_id = m.group(1).lower()
                method = m.group(2).lower()
                raw = m.group(3).strip()
                entry = catalog.get_entry(tool_id)
                installed = registry.get(self.username, tool_id) is not None
                known = bool(entry) and (method in entry.get("method_tiers", {})
                                         or method in entry.get("method_hints", {}))
                if installed and known:
                    args = self._parse_kv(raw.split(), split_commas=False)
                    self._pending_tool_calls.append(
                        {"tool_id": tool_id, "method": method, "args": args})
                elif is_strict:
                    # Explicit [TOOL:] that doesn't validate → rejection (nudge Pike).
                    self._rejections.append(f"{tool_id}.{method}")
                else:
                    # Shorthand that doesn't validate is probably ordinary prose
                    # (e.g. "[example.com link]") — leave it completely untouched.
                    continue
            except Exception as e:
                logger.warning("Failed to parse a [TOOL:] call: %s", e)
                if is_strict:
                    self._rejections.append(f"{m.group(1)}.{m.group(2)}")
                else:
                    continue
            clean = clean.replace(m.group(0), "")
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        clean = re.sub(r"[ \t]+([.?,!])", r"\1", clean)
        clean = re.sub(r"\.(?:[ \t]*\.)+", ".", clean)   # collapse ".." left by a stripped tag
        clean = clean.strip()
        return {"response": clean, "suppress": False, "append": ""}

    def get_pending_tool_calls(self):
        """Structured [TOOL:] calls parsed from the most recent output."""
        return list(self._pending_tool_calls)

    def get_rejections(self):
        """`tool.method` strings that were emitted but aren't available."""
        return list(self._rejections)

    def get_commands(self):
        return [{"command": "tools",
                 "description": "Tool management (/tools list|find|install|call|wish|pin)",
                 "handler": "cmd_tools"}]

    # --- command dispatch ---

    def cmd_tools(self, args=""):
        parts = args.strip().split(None, 1)
        sub = parts[0].lower() if parts else "help"
        rest = parts[1].strip() if len(parts) > 1 else ""

        # SECURITY: never log `rest` for pin — it contains the vault PIN.
        if sub != "pin":
            logger.info("/tools %s %s", sub, rest)
        else:
            logger.info("/tools pin ****")

        if sub == "list":
            return self._list()
        if sub == "find":
            return self._find(rest)
        if sub == "install":
            return self._install(rest)
        if sub == "uninstall":
            return service.uninstall_tool(self.username, rest.split()[0]) if rest else "Usage: /tools uninstall <tool_id>"
        if sub == "call":
            return self._call(rest)
        if sub == "wish":
            return self._wish(rest)
        if sub == "pin":
            return self._pin(rest)
        return ("Tool commands:\n"
                "/tools list — installed tools\n"
                "/tools find <query> — search the catalog\n"
                "/tools install <tool_id> [key=v1,v2 …]\n"
                "/tools uninstall <tool_id>\n"
                "/tools call <tool_id> <method> [key=value …]\n"
                "/tools wish <description> — request a tool we don't have\n"
                "/tools pin <PIN> — confirm a pending out-of-tier operation")

    # --- subcommand impls ---

    def _list(self):
        rows = service.installed_summary(self.username)
        if not rows:
            return "No tools installed. Try /tools find <query> to browse the catalog."
        lines = ["Installed tools:"]
        for r in rows:
            state = "running" if r["running"] else "stopped"
            lines.append(f"- {r['tool_id']} [{r['trust_tier']}] {state}, "
                         f"{r['call_count']} calls")
        return "\n".join(lines)

    def _find(self, query):
        if not query:
            ids = list(catalog.all_entries())
        else:
            ids = catalog.search(query)
        if not ids:
            return (f"Nothing in the catalog matches '{query}'. "
                    f"Use /tools wish {query} to request it.")
        lines = ["Catalog matches:"]
        for tool_id in ids:
            e = catalog.get_entry(tool_id)
            lines.append(f"- {tool_id} [{e['default_tier']}]: {e['description']}")
        return "\n".join(lines)

    def _install(self, rest):
        if not rest:
            return "Usage: /tools install <tool_id> [key=value1,value2 …]"
        bits = rest.split()
        tool_id = bits[0]
        config = self._parse_kv(bits[1:], split_commas=True)
        return service.install_tool(self.username, tool_id, config)

    def _call(self, rest):
        bits = rest.split()
        if len(bits) < 2:
            return "Usage: /tools call <tool_id> <method> [key=value …]"
        tool_id, method = bits[0], bits[1]
        arguments = self._parse_kv(bits[2:], split_commas=False)
        result = service.call_tool(self.username, tool_id, method, arguments)
        if result["status"] == "ok":
            return "\n".join(result["result"]) or "(no output)"
        return result["message"]

    def _wish(self, description):
        if not description:
            return "Usage: /tools wish <what you need the tool to do>"
        wishlist.add(self.username, description)
        return ("Added to the tool wishlist. It'll be vetted in the weekly review — "
                "if a safe tool exists, it lands in the catalog.")

    def _pin(self, pin):
        if not pin:
            return "Usage: /tools pin <your vault PIN>"
        result = service.confirm_pending(self.username, pin.split()[0])
        if result["status"] == "ok":
            return "Confirmed and executed:\n" + ("\n".join(result["result"]) or "(no output)")
        return result["message"]

    @staticmethod
    def _parse_kv(tokens, split_commas):
        """Parse key=value tokens. split_commas turns 'a=1,2' into {'a': ['1','2']}."""
        out = {}
        for tok in tokens:
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            if split_commas and "," in v:
                out[k] = [p for p in v.split(",") if p]
            elif split_commas:
                out[k] = [v] if v else []
            else:
                out[k] = v
        return out
