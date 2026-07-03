"""
Tooling Protocol — slash-command surface for MCP tool discovery (Phase 4A).
/tools list | find | install | uninstall | call | wish | pin
Pike does not auto-call tools yet (that's Phase 4B).
"""

import logging

from core.protocols.base import Protocol
from core.tooling import catalog, service, wishlist

logger = logging.getLogger("aegis.protocols.tooling")


class ToolingProtocol(Protocol):
    """Manual tool management via /tools commands."""

    def __init__(self, username):
        super().__init__(
            name="tooling",
            description="MCP tool discovery: install and call external tools",
            priority=Protocol.PRIORITY_NORMAL,
        )
        self.username = username

    # --- Protocol ABC ---

    def process_input(self, user_input, context):
        return {"input": user_input, "context_injection": "",
                "intercept": False, "response": ""}

    def process_output(self, response, context):
        return {"response": response, "suppress": False, "append": ""}

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
                out[k] = [v]
            else:
                out[k] = v
        return out
