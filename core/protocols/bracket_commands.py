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

        # Per-command guidance — explicit examples beat a bare list for small models.
        guidance = {
            "ADD_TASK": "[ADD_TASK: short task title] — REQUIRED when the user asks you to add, create, make, set, or schedule a task, or asks you to remind them about something. Use the user's exact subject as the title.",
            "ADD_EVENT": "[ADD_EVENT: YYYY-MM-DD | title] — when the user mentions an appointment or event on a specific date.",
            "COMPLETE_TASK": (
                "[COMPLETE_TASK: #N or task title] — use when the user has FINISHED or DONE a task. "
                "Trigger words: done, finished, completed, complete, wrapped up, checked off, knocked out. "
                "Examples: 'mark X done' → [COMPLETE_TASK: X]. 'I finished X' → [COMPLETE_TASK: X]. "
                "Keeps the task in history with a strikethrough."
            ),
            "REMOVE_TASK": (
                "[REMOVE_TASK: #N or task title] — use ONLY when the user wants the task ENTIRELY GONE because it "
                "was a mistake, no longer needed, or should be cancelled. "
                "Trigger words: delete, remove, cancel, get rid of, scrap, throw out. "
                "Examples: 'delete X' → [REMOVE_TASK: X]. 'cancel X' → [REMOVE_TASK: X]. "
                "If the user said the task is DONE/FINISHED, use COMPLETE_TASK instead, NOT this one."
            ),
            "ADD_MOOD": "[ADD_MOOD: mood1, mood2 | note] — when the user reports how they're feeling.",
            "ADD_CONTACT": "[ADD_CONTACT: name | relationship] — when the user mentions a new person to remember.",
            "REMEMBER": "[REMEMBER: fact] — when the user tells you something to commit to long-term memory.",
        }
        lines = ["Available actions for the END of your response:"]
        for name in sorted(self._handlers):
            lines.append("  " + guidance.get(name, f"[{name}: arg]"))
        lines.append(
            "Place each bracket on its own line at the very end of your reply. "
            "Emit a bracket EVERY time the user explicitly requests that action "
            "(\"add a task to ...\", \"make a task ...\", \"remind me to ...\"); "
            "do NOT skip it just because you already acknowledged in prose. "
            "Do NOT emit brackets during casual chat where the user is not requesting an action."
        )
        injection = "\n".join(lines)

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

        # Clean up whitespace + orphaned punctuation left by removed tags
        # (e.g. "Take your time. [REMEMBER: ...]." -> "Take your time. .")
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        clean = re.sub(r"[ \t]+([.?,!])", r"\1", clean)
        clean = re.sub(r"\.(?:[ \t]*\.)+", ".", clean)
        clean = clean.strip()

        return {"response": clean, "suppress": False, "append": ""}

    def get_pending_actions(self) -> list[dict]:
        """Return actions executed in the most recent process_output call."""
        return list(self._pending_actions)

    def get_status(self):
        status = super().get_status()
        status["registered_commands"] = sorted(self._handlers.keys())
        return status
