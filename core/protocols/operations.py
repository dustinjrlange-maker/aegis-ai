"""
Operations Protocol — Aegis AI
Digital assistant capabilities: tasks, calendar, email, file organization.
Handles the companion's daily digital life.
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from core.protocols.base import Protocol
from core.config import PROJECT_ROOT


class OperationsProtocol(Protocol):
    """Digital assistant — tasks, scheduling, email, files."""

    TASK_FILE = PROJECT_ROOT / "data" / "tasks.json"

    # Patterns that suggest task-related intent
    TASK_PATTERNS = [
        r"remind\s+me\s+to\s+(.+)",
        r"add\s+(?:a\s+)?task\s*[:\-]?\s*(.+)",
        r"i\s+need\s+to\s+(.+?)(?:\s+by\s+|\s+before\s+|$)",
        r"don'?t\s+let\s+me\s+forget\s+(?:to\s+)?(.+)",
    ]

    def __init__(self):
        super().__init__(
            name="operations",
            description="Digital assistant — tasks, calendar, email, file organization",
            priority=Protocol.PRIORITY_NORMAL - 5,  # Just below communications
        )
        self._tasks = []
        self._load_tasks()

    def _load_tasks(self):
        """Load tasks from disk."""
        if self.TASK_FILE.exists():
            try:
                with open(self.TASK_FILE, "r", encoding="utf-8") as f:
                    self._tasks = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._tasks = []

    def _save_tasks(self):
        """Persist tasks to disk."""
        self.TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.TASK_FILE, "w", encoding="utf-8") as f:
            json.dump(self._tasks, f, indent=2, ensure_ascii=False)

    def process_input(self, user_input, context):
        """Operations doesn't intercept — it adds context about pending tasks."""
        result = {
            "input": user_input,
            "context_injection": "",
            "intercept": False,
            "response": "",
        }
        return result

    def process_output(self, response, context):
        """Pass through."""
        return {"response": response, "suppress": False, "append": ""}

    # --- Task Management ---

    def add_task(self, text, priority="normal", due=None, category="general"):
        """Add a task to the list."""
        task = {
            "id": len(self._tasks) + 1,
            "text": text,
            "priority": priority,
            "category": category,
            "due": due,
            "created": datetime.now().isoformat(),
            "completed": False,
            "completed_at": None,
        }
        self._tasks.append(task)
        self._save_tasks()
        return task

    def complete_task(self, task_id):
        """Mark a task as completed."""
        for task in self._tasks:
            if task["id"] == task_id and not task["completed"]:
                task["completed"] = True
                task["completed_at"] = datetime.now().isoformat()
                self._save_tasks()
                return task
        return None

    def remove_task(self, task_id):
        """Remove a task from the list."""
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t["id"] != task_id]
        if len(self._tasks) < before:
            self._save_tasks()
            return True
        return False

    def get_pending_tasks(self):
        """Get all incomplete tasks."""
        return [t for t in self._tasks if not t["completed"]]

    def get_overdue_tasks(self):
        """Get tasks that are past their due date."""
        now = datetime.now().isoformat()
        return [
            t for t in self._tasks
            if not t["completed"] and t.get("due") and t["due"] < now
        ]

    def format_task_list(self, tasks=None):
        """Format tasks as a readable list."""
        if tasks is None:
            tasks = self.get_pending_tasks()

        if not tasks:
            return "  No pending tasks."

        lines = []
        for t in tasks:
            status = "x" if t["completed"] else " "
            pri = {"high": "!!", "normal": " ", "low": ".."}[t.get("priority", "normal")]
            due_str = f" (due: {t['due'][:10]})" if t.get("due") else ""
            lines.append(f"  [{status}] {pri} #{t['id']}: {t['text']}{due_str}")
        return "\n".join(lines)

    def get_daily_briefing(self):
        """Generate a daily briefing of tasks and schedule."""
        pending = self.get_pending_tasks()
        overdue = self.get_overdue_tasks()

        lines = ["  DAILY BRIEFING", "  =============="]

        if overdue:
            lines.append(f"  OVERDUE ({len(overdue)}):")
            for t in overdue:
                lines.append(f"    !! #{t['id']}: {t['text']} (was due {t['due'][:10]})")

        high = [t for t in pending if t.get("priority") == "high"]
        if high:
            lines.append(f"  HIGH PRIORITY ({len(high)}):")
            for t in high:
                lines.append(f"    ! #{t['id']}: {t['text']}")

        normal = [t for t in pending if t.get("priority", "normal") == "normal"]
        if normal:
            lines.append(f"  TASKS ({len(normal)}):")
            for t in normal:
                lines.append(f"    #{t['id']}: {t['text']}")

        if not pending:
            lines.append("  All clear. No pending tasks.")

        return "\n".join(lines)

    # --- Commands ---

    def get_commands(self):
        return [
            {"command": "task", "description": "Task management", "handler": "cmd_task"},
            {"command": "tasks", "description": "List pending tasks", "handler": "cmd_list_tasks"},
            {"command": "briefing", "description": "Daily briefing", "handler": "cmd_briefing"},
        ]

    def cmd_task(self, args=""):
        """Handle /task commands."""
        parts = args.strip().split(None, 1) if args.strip() else []
        if not parts:
            return self._task_help()

        subcmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if subcmd == "add":
            if not rest:
                return "  Usage: /task add <description>"
            # Parse optional priority: /task add !! call mom
            priority = "normal"
            if rest.startswith("!"):
                priority = "high"
                rest = rest.lstrip("!").strip()
            elif rest.startswith(".."):
                priority = "low"
                rest = rest[2:].strip()
            task = self.add_task(rest, priority=priority)
            return f"  Added task #{task['id']}: {task['text']} [{task['priority']}]"

        elif subcmd == "done":
            try:
                tid = int(rest.strip().lstrip("#"))
            except ValueError:
                return "  Usage: /task done <id>"
            task = self.complete_task(tid)
            if task:
                return f"  Completed: #{task['id']} {task['text']}"
            return f"  Task #{tid} not found or already completed."

        elif subcmd == "remove":
            try:
                tid = int(rest.strip().lstrip("#"))
            except ValueError:
                return "  Usage: /task remove <id>"
            if self.remove_task(tid):
                return f"  Removed task #{tid}."
            return f"  Task #{tid} not found."

        elif subcmd == "list":
            return "\n  Pending Tasks:\n" + self.format_task_list()

        elif subcmd == "all":
            return "\n  All Tasks:\n" + self.format_task_list(self._tasks)

        else:
            return self._task_help()

    def cmd_list_tasks(self, args=""):
        """List pending tasks."""
        return "\n  Pending Tasks:\n" + self.format_task_list()

    def cmd_briefing(self, args=""):
        """Show daily briefing."""
        return "\n" + self.get_daily_briefing()

    def _task_help(self):
        return (
            "\n  Task Commands:\n"
            "    /task add <text>       — Add a task (prefix !! for high priority)\n"
            "    /task done <id>        — Mark task completed\n"
            "    /task remove <id>      — Remove a task\n"
            "    /task list             — Show pending tasks\n"
            "    /task all              — Show all tasks including completed\n"
            "    /tasks                 — Quick list\n"
            "    /briefing              — Daily briefing"
        )

    def get_status(self):
        status = super().get_status()
        pending = self.get_pending_tasks()
        status["pending_tasks"] = len(pending)
        status["total_tasks"] = len(self._tasks)
        status["overdue"] = len(self.get_overdue_tasks())
        return status
