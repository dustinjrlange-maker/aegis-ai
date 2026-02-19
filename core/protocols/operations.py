"""
Operations Protocol — Aegis AI
Digital assistant capabilities: tasks, calendar, email, file organization.
Handles the companion's daily digital life.
"""

import json
import re
from datetime import datetime, date, timedelta
from pathlib import Path
from core.protocols.base import Protocol
from core.config import PROJECT_ROOT


class OperationsProtocol(Protocol):
    """Digital assistant — tasks, scheduling, email, files."""

    # Patterns that suggest task-related intent
    TASK_PATTERNS = [
        r"remind\s+me\s+to\s+(.+)",
        r"add\s+(?:a\s+)?task\s*[:\-]?\s*(.+)",
        r"i\s+need\s+to\s+(.+?)(?:\s+by\s+|\s+before\s+|$)",
        r"don'?t\s+let\s+me\s+forget\s+(?:to\s+)?(.+)",
    ]

    def __init__(self, data_dir=None):
        super().__init__(
            name="operations",
            description="Digital assistant — tasks, calendar, email, file organization",
            priority=Protocol.PRIORITY_NORMAL - 5,  # Just below communications
        )
        if data_dir is not None:
            self.TASK_FILE = Path(data_dir) / "tasks.json"
            self.RECURRING_FILE = Path(data_dir) / "recurring.json"
        else:
            self.TASK_FILE = PROJECT_ROOT / "data" / "tasks.json"
            self.RECURRING_FILE = PROJECT_ROOT / "data" / "recurring.json"
        self._tasks = []
        self._recurring = []
        self._load_tasks()
        self._load_recurring()

    def _load_tasks(self):
        """Load tasks from disk, backfilling new schema defaults."""
        if self.TASK_FILE.exists():
            try:
                with open(self.TASK_FILE, "r", encoding="utf-8") as f:
                    self._tasks = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._tasks = []
        for task in self._tasks:
            task.setdefault("subtasks", [])
            task.setdefault("starred", False)
            task.setdefault("activity_type", "general")

    def _save_tasks(self):
        """Persist tasks to disk."""
        self.TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.TASK_FILE, "w", encoding="utf-8") as f:
            json.dump(self._tasks, f, indent=2, ensure_ascii=False)

    def _load_recurring(self):
        """Load recurring tasks from disk."""
        if self.RECURRING_FILE.exists():
            try:
                with open(self.RECURRING_FILE, "r", encoding="utf-8") as f:
                    self._recurring = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._recurring = []

    def _save_recurring(self):
        """Persist recurring tasks to disk."""
        self.RECURRING_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.RECURRING_FILE, "w", encoding="utf-8") as f:
            json.dump(self._recurring, f, indent=2, ensure_ascii=False)

    # --- Recurring Task Management ---

    def add_recurring(self, text, frequency, time=None, category="general"):
        """Add a recurring task/habit."""
        recurring = {
            "id": max((r["id"] for r in self._recurring), default=0) + 1,
            "text": text,
            "frequency": frequency,  # daily, weekly, weekday, weekend
            "time": time,            # optional preferred time e.g. "18:00"
            "category": category,
            "enabled": True,
            "last_generated": None,
            "created": datetime.now().isoformat(),
        }
        self._recurring.append(recurring)
        self._save_recurring()
        return recurring

    def remove_recurring(self, recurring_id):
        """Remove a recurring task by ID."""
        before = len(self._recurring)
        self._recurring = [r for r in self._recurring if r["id"] != recurring_id]
        if len(self._recurring) < before:
            self._save_recurring()
            return True
        return False

    def list_recurring(self):
        """List all recurring tasks."""
        return [r for r in self._recurring if r.get("enabled", True)]

    def check_recurring(self):
        """Check if any recurring tasks need to generate today's task.

        For each enabled recurring entry, determine if a task should be
        auto-created based on frequency and last_generated date.
        """
        today = date.today()
        today_str = today.isoformat()
        weekday = today.weekday()  # 0=Mon, 6=Sun
        generated = []

        for rec in self._recurring:
            if not rec.get("enabled", True):
                continue

            last = rec.get("last_generated")
            should_generate = False

            if rec["frequency"] == "daily":
                should_generate = (last != today_str)

            elif rec["frequency"] == "weekday":
                should_generate = (weekday < 5 and last != today_str)

            elif rec["frequency"] == "weekend":
                should_generate = (weekday >= 5 and last != today_str)

            elif rec["frequency"] == "weekly":
                # Generate on the same weekday as the created date
                try:
                    created_date = datetime.fromisoformat(rec["created"]).date()
                    created_weekday = created_date.weekday()
                except (ValueError, KeyError):
                    created_weekday = 0  # default to Monday

                if weekday == created_weekday:
                    # Check last_generated is not already this week
                    if last is None:
                        should_generate = True
                    else:
                        try:
                            last_date = date.fromisoformat(last)
                            week_start = today - timedelta(days=weekday)
                            should_generate = (last_date < week_start)
                        except ValueError:
                            should_generate = True

            if should_generate:
                category = rec.get("category", "general")
                task = self.add_task(rec["text"], category=category)
                rec["last_generated"] = today_str
                generated.append(task)

        if generated:
            self._save_recurring()

        return generated

    def process_input(self, user_input, context):
        """Detect task intent from natural language and inject pending task context."""
        result = {
            "input": user_input,
            "context_injection": "",
            "intercept": False,
            "response": "",
        }

        # Check recurring tasks — generates any due today (cheap date comparisons)
        self.check_recurring()

        injection_parts = []

        # NLP task detection — auto-create tasks from conversation
        lower = user_input.lower().strip()
        for pattern in self.TASK_PATTERNS:
            match = re.search(pattern, lower, re.IGNORECASE)
            if match:
                task_text = match.group(1).strip().rstrip(".!,")
                if len(task_text) > 3:
                    task = self.add_task(task_text)
                    injection_parts.append(
                        f"[System: A task was auto-detected and saved: "
                        f"'#{task['id']}: {task['text']}'. "
                        f"Acknowledge this naturally in your response.]"
                    )
                break

        # Always inject pending tasks — even when a new task was just created
        pending = self.get_pending_tasks()
        overdue = self.get_overdue_tasks()
        if pending:
            task_summary = []
            now = datetime.now()

            if overdue:
                task_summary.append(
                    f"OVERDUE tasks ({len(overdue)}): " +
                    ", ".join(f"#{t['id']}: {t['text']}" for t in overdue[:3])
                )

            high = [t for t in pending if t.get("priority") == "high"]
            if high:
                task_summary.append(
                    f"High priority ({len(high)}): " +
                    ", ".join(f"#{t['id']}: {t['text']}" for t in high[:3])
                )

            # Show age for tasks pending > 3 days
            stale = []
            for t in pending:
                try:
                    created = datetime.fromisoformat(t["created"])
                    age_days = (now - created).days
                    if age_days >= 3:
                        stale.append(f"#{t['id']}: {t['text']} (pending {age_days} days)")
                except (ValueError, KeyError):
                    pass
            if stale:
                task_summary.append("Aging tasks: " + ", ".join(stale[:3]))

            task_summary.append(f"Total pending: {len(pending)}")
            injection_parts.append(
                "[Companion's pending tasks: " +
                "; ".join(task_summary) +
                ". Do NOT mention tasks unless they specifically ask about tasks, plans, or to-do items.]"
            )

        if injection_parts:
            result["context_injection"] = "\n".join(injection_parts)

        return result

    def process_output(self, response, context):
        """Pass through."""
        return {"response": response, "suppress": False, "append": ""}

    # --- Task Management ---

    def add_task(self, text, priority="normal", due=None, category="general",
                 activity_type="general"):
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
            "subtasks": [],
            "starred": False,
            "activity_type": activity_type,
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

    def update_task(self, task_id, **updates):
        """Update allowed fields on a task."""
        allowed = {"text", "priority", "due", "activity_type", "starred"}
        for task in self._tasks:
            if task["id"] == task_id:
                for k, v in updates.items():
                    if k in allowed:
                        task[k] = v
                self._save_tasks()
                return task
        return None

    def add_subtask(self, task_id, text):
        """Add a subtask to a task."""
        for task in self._tasks:
            if task["id"] == task_id:
                task.setdefault("subtasks", [])
                task["subtasks"].append({"text": text, "completed": False})
                self._save_tasks()
                return task
        return None

    def complete_subtask(self, task_id, subtask_idx):
        """Mark a subtask as completed."""
        for task in self._tasks:
            if task["id"] == task_id:
                subs = task.get("subtasks", [])
                if 0 <= subtask_idx < len(subs):
                    subs[subtask_idx]["completed"] = True
                    self._save_tasks()
                    return task
        return None

    def remove_subtask(self, task_id, subtask_idx):
        """Remove a subtask by index."""
        for task in self._tasks:
            if task["id"] == task_id:
                subs = task.get("subtasks", [])
                if 0 <= subtask_idx < len(subs):
                    subs.pop(subtask_idx)
                    self._save_tasks()
                    return task
        return None

    def toggle_star(self, task_id):
        """Toggle the starred flag on a task."""
        for task in self._tasks:
            if task["id"] == task_id:
                task["starred"] = not task.get("starred", False)
                self._save_tasks()
                return task
        return None

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
            {"command": "habit", "description": "Recurring tasks/habits", "handler": "cmd_habit"},
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

    def cmd_habit(self, args=""):
        """Handle /habit commands."""
        parts = args.strip().split(None, 1) if args.strip() else []
        if not parts:
            return self._habit_help()

        subcmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if subcmd == "add":
            # Parse: /habit add <frequency> <text>
            habit_parts = rest.strip().split(None, 1) if rest.strip() else []
            if len(habit_parts) < 2:
                return "  Usage: /habit add <frequency> <text>\n  Frequencies: daily, weekly, weekday, weekend"
            frequency = habit_parts[0].lower()
            if frequency not in ("daily", "weekly", "weekday", "weekend"):
                return f"  Unknown frequency '{frequency}'. Use: daily, weekly, weekday, weekend"
            text = habit_parts[1]
            rec = self.add_recurring(text, frequency)
            return f"  Added recurring #{rec['id']}: {rec['text']} [{rec['frequency']}]"

        elif subcmd == "list":
            recurring = self.list_recurring()
            if not recurring:
                return "  No recurring tasks."
            lines = ["", "  Recurring Tasks / Habits:"]
            for r in recurring:
                time_str = f" at {r['time']}" if r.get("time") else ""
                last_str = f" (last: {r['last_generated']})" if r.get("last_generated") else " (never run)"
                lines.append(f"    #{r['id']}: {r['text']} [{r['frequency']}{time_str}]{last_str}")
            return "\n".join(lines)

        elif subcmd == "remove":
            try:
                rid = int(rest.strip().lstrip("#"))
            except ValueError:
                return "  Usage: /habit remove <id>"
            if self.remove_recurring(rid):
                return f"  Removed recurring task #{rid}."
            return f"  Recurring task #{rid} not found."

        else:
            return self._habit_help()

    def _habit_help(self):
        return (
            "\n  Habit Commands:\n"
            "    /habit add <freq> <text>  — Add a recurring task\n"
            "    /habit list               — Show all recurring tasks\n"
            "    /habit remove <id>        — Remove a recurring task\n"
            "  Frequencies: daily, weekly, weekday, weekend"
        )

    def _task_help(self):
        return (
            "\n  Task Commands:\n"
            "    /task add <text>       — Add a task (prefix !! for high priority)\n"
            "    /task done <id>        — Mark task completed\n"
            "    /task remove <id>      — Remove a task\n"
            "    /task list             — Show pending tasks\n"
            "    /task all              — Show all tasks including completed\n"
            "    /tasks                 — Quick list\n"
            "    /briefing              — Daily briefing\n"
            "    /habit                 — Recurring tasks (see /habit for details)"
        )

    def get_status(self):
        status = super().get_status()
        pending = self.get_pending_tasks()
        status["pending_tasks"] = len(pending)
        status["total_tasks"] = len(self._tasks)
        status["overdue"] = len(self.get_overdue_tasks())
        status["recurring_tasks"] = len(self.list_recurring())
        return status
