"""Bracket Command Protocol — Aegis AI
Parses [COMMAND: arg] tags from LLM output and routes them to registered handlers.
Injected at priority 49 (just below Communications at 50) so the instruction
line appears in context but doesn't override conversation flow.
"""

import logging
import re
from typing import Callable

from core.protocols.base import Protocol

logger = logging.getLogger(__name__)

# Matches [COMMAND_NAME: argument text]
BRACKET_RE = re.compile(r"\[([A-Z_]+):\s*(.+?)\]")


class BracketCommandProtocol(Protocol):
    """Emits available bracket commands into context and parses them from output."""

    def __init__(self):
        super().__init__(
            name="bracket_commands",
            description="LLM bracket command parser",
            priority=Protocol.PRIORITY_NORMAL - 1,  # 49
        )
        self._handlers: dict[str, Callable[[str], str]] = {}
        self._pending_actions: list[dict] = []

    def register_handler(self, command_name: str, handler: Callable[[str], str]):
        """Register a handler for a bracket command name (uppercase)."""
        self._handlers[command_name.upper()] = handler

    def process_input(self, user_input, context):
        """Inject a one-line list of available bracket commands."""
        self._pending_actions = []

        if not self._handlers:
            return {
                "input": user_input,
                "context_injection": "",
                "intercept": False,
                "response": "",
            }

        names = ", ".join(f"[{n}:]" for n in sorted(self._handlers))
        injection = (
            f"Available actions you can place at the END of your response: {names}. "
            "Use them when appropriate — do NOT use them in every message."
        )

        return {
            "input": user_input,
            "context_injection": injection,
            "intercept": False,
            "response": "",
        }

    def process_output(self, response, context):
        """Parse bracket commands from LLM output, execute handlers, strip tags."""
        self._pending_actions = []
        clean = response

        matches = list(BRACKET_RE.finditer(response))
        if not matches:
            return {"response": response, "suppress": False, "append": ""}

        for match in matches:
            cmd_name = match.group(1).upper()
            arg = match.group(2).strip()
            handler = self._handlers.get(cmd_name)

            if handler is None:
                logger.debug("No handler for bracket command: %s", cmd_name)
                continue

            try:
                result = handler(arg)
                self._pending_actions.append({
                    "command": cmd_name,
                    "arg": arg,
                    "result": result or "OK",
                })
                logger.info("Bracket command executed: [%s: %s] -> %s", cmd_name, arg, result)
            except Exception as e:
                logger.error("Bracket command [%s: %s] failed: %s", cmd_name, arg, e)
                self._pending_actions.append({
                    "command": cmd_name,
                    "arg": arg,
                    "result": f"Error: {e}",
                })

            # Strip the tag from visible text
            clean = clean.replace(match.group(0), "")

        # Clean up extra whitespace left by removed tags
        clean = re.sub(r"\n{3,}", "\n\n", clean).strip()

        return {"response": clean, "suppress": False, "append": ""}

    def get_pending_actions(self) -> list[dict]:
        """Return actions executed in the most recent process_output call."""
        return list(self._pending_actions)

    def get_status(self):
        status = super().get_status()
        status["registered_commands"] = sorted(self._handlers.keys())
        return status
