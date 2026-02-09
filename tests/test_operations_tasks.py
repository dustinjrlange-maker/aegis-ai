"""
Deep tests for the OperationsProtocol task system.

Covers task ID incrementing, priority handling, formatting, daily briefing,
overdue detection, and persistence (save/load cycle).
"""
import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path

from core.protocols.operations import OperationsProtocol


@pytest.fixture(autouse=True)
def _redirect_task_file(tmp_path, monkeypatch):
    """Redirect TASK_FILE to a temporary location for every test in this module."""
    temp_task_file = tmp_path / "tasks.json"
    monkeypatch.setattr(OperationsProtocol, "TASK_FILE", temp_task_file)


# =============================================================================
# Task ID Incrementing
# =============================================================================

class TestTaskIdIncrementing:
    """Task IDs should increment sequentially."""

    def test_first_task_gets_id_1(self):
        proto = OperationsProtocol()
        task = proto.add_task("First")
        assert task["id"] == 1

    def test_second_task_gets_id_2(self):
        proto = OperationsProtocol()
        proto.add_task("First")
        task = proto.add_task("Second")
        assert task["id"] == 2

    def test_ids_increment_after_removal(self):
        """IDs are based on list length, so after removal the next ID reuses."""
        proto = OperationsProtocol()
        proto.add_task("A")
        proto.add_task("B")
        proto.remove_task(2)
        # After removing B, list has 1 item, so next ID = 2 (len + 1)
        task = proto.add_task("C")
        assert task["id"] == 2

    def test_multiple_tasks_sequential_ids(self):
        proto = OperationsProtocol()
        ids = []
        for i in range(5):
            task = proto.add_task(f"Task {i}")
            ids.append(task["id"])
        assert ids == [1, 2, 3, 4, 5]


# =============================================================================
# Task Priority Handling
# =============================================================================

class TestTaskPriority:
    """Task priority values (high, normal, low) are stored correctly."""

    def test_default_priority_is_normal(self):
        proto = OperationsProtocol()
        task = proto.add_task("Default priority")
        assert task["priority"] == "normal"

    def test_high_priority(self):
        proto = OperationsProtocol()
        task = proto.add_task("Urgent", priority="high")
        assert task["priority"] == "high"

    def test_low_priority(self):
        proto = OperationsProtocol()
        task = proto.add_task("Eventually", priority="low")
        assert task["priority"] == "low"

    def test_cmd_task_add_high_priority_with_exclamation(self):
        proto = OperationsProtocol()
        result = proto.cmd_task("add !! Call the boss")
        assert "[high]" in result
        assert len(proto._tasks) == 1
        assert proto._tasks[0]["priority"] == "high"

    def test_cmd_task_add_low_priority_with_dots(self):
        proto = OperationsProtocol()
        result = proto.cmd_task("add ..organize desktop later")
        assert len(proto._tasks) == 1
        assert proto._tasks[0]["priority"] == "low"

    def test_cmd_task_add_normal_priority(self):
        proto = OperationsProtocol()
        result = proto.cmd_task("add Buy groceries")
        assert len(proto._tasks) == 1
        assert proto._tasks[0]["priority"] == "normal"


# =============================================================================
# Format Task List
# =============================================================================

class TestFormatTaskList:
    """format_task_list() rendering for various states."""

    def test_empty_list(self):
        proto = OperationsProtocol()
        result = proto.format_task_list()
        assert "No pending tasks" in result

    def test_single_pending_task(self):
        proto = OperationsProtocol()
        proto.add_task("Write tests")
        result = proto.format_task_list()
        assert "Write tests" in result
        assert "[ ]" in result  # not completed

    def test_completed_task_shows_x(self):
        proto = OperationsProtocol()
        task = proto.add_task("Done task")
        proto.complete_task(task["id"])
        # format_task_list with explicit task list (including completed)
        result = proto.format_task_list(proto._tasks)
        assert "[x]" in result

    def test_high_priority_marker(self):
        proto = OperationsProtocol()
        proto.add_task("Important", priority="high")
        result = proto.format_task_list()
        assert "!!" in result

    def test_low_priority_marker(self):
        proto = OperationsProtocol()
        proto.add_task("Someday", priority="low")
        result = proto.format_task_list()
        assert ".." in result

    def test_due_date_shown(self):
        proto = OperationsProtocol()
        proto.add_task("Due task", due="2025-12-31T23:59:59")
        result = proto.format_task_list()
        assert "2025-12-31" in result

    def test_mixed_tasks_all_rendered(self):
        proto = OperationsProtocol()
        proto.add_task("Task A", priority="high")
        proto.add_task("Task B", priority="normal")
        proto.add_task("Task C", priority="low")
        result = proto.format_task_list()
        assert "Task A" in result
        assert "Task B" in result
        assert "Task C" in result


# =============================================================================
# Daily Briefing
# =============================================================================

class TestDailyBriefing:
    """get_daily_briefing() generates a structured report."""

    def test_briefing_with_no_tasks(self):
        proto = OperationsProtocol()
        briefing = proto.get_daily_briefing()
        assert "DAILY BRIEFING" in briefing
        assert "All clear" in briefing

    def test_briefing_with_pending_tasks(self):
        proto = OperationsProtocol()
        proto.add_task("Review PR")
        proto.add_task("Deploy staging")
        briefing = proto.get_daily_briefing()
        assert "DAILY BRIEFING" in briefing
        assert "Review PR" in briefing
        assert "Deploy staging" in briefing

    def test_briefing_shows_high_priority_section(self):
        proto = OperationsProtocol()
        proto.add_task("Critical bug", priority="high")
        proto.add_task("Nice to have", priority="normal")
        briefing = proto.get_daily_briefing()
        assert "HIGH PRIORITY" in briefing
        assert "Critical bug" in briefing

    def test_briefing_shows_overdue_section(self):
        proto = OperationsProtocol()
        # Create a task with a due date in the past
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        proto.add_task("Late task", due=yesterday)
        briefing = proto.get_daily_briefing()
        assert "OVERDUE" in briefing
        assert "Late task" in briefing

    def test_cmd_briefing_returns_string(self):
        proto = OperationsProtocol()
        proto.add_task("Something")
        result = proto.cmd_briefing()
        assert isinstance(result, str)
        assert "DAILY BRIEFING" in result


# =============================================================================
# Overdue Task Detection
# =============================================================================

class TestOverdueDetection:
    """get_overdue_tasks() identifies tasks past their due date."""

    def test_no_overdue_when_no_tasks(self):
        proto = OperationsProtocol()
        assert proto.get_overdue_tasks() == []

    def test_no_overdue_when_no_due_dates(self):
        proto = OperationsProtocol()
        proto.add_task("No due date")
        assert proto.get_overdue_tasks() == []

    def test_past_due_task_is_overdue(self):
        proto = OperationsProtocol()
        past = (datetime.now() - timedelta(days=2)).isoformat()
        proto.add_task("Old task", due=past)
        overdue = proto.get_overdue_tasks()
        assert len(overdue) == 1
        assert overdue[0]["text"] == "Old task"

    def test_future_due_task_is_not_overdue(self):
        proto = OperationsProtocol()
        future = (datetime.now() + timedelta(days=7)).isoformat()
        proto.add_task("Future task", due=future)
        assert proto.get_overdue_tasks() == []

    def test_completed_task_is_not_overdue(self):
        proto = OperationsProtocol()
        past = (datetime.now() - timedelta(days=2)).isoformat()
        task = proto.add_task("Done old task", due=past)
        proto.complete_task(task["id"])
        assert proto.get_overdue_tasks() == []

    def test_multiple_overdue(self):
        proto = OperationsProtocol()
        past1 = (datetime.now() - timedelta(days=1)).isoformat()
        past2 = (datetime.now() - timedelta(days=3)).isoformat()
        proto.add_task("Late 1", due=past1)
        proto.add_task("Late 2", due=past2)
        assert len(proto.get_overdue_tasks()) == 2


# =============================================================================
# Task Persistence (save/load cycle)
# =============================================================================

class TestTaskPersistence:
    """Tasks should survive a save/load cycle via the JSON file."""

    def test_save_creates_file(self, tmp_path):
        proto = OperationsProtocol()
        proto.add_task("Persist me")
        assert proto.TASK_FILE.exists()

    def test_load_restores_tasks(self, tmp_path):
        # Create and save tasks with one instance
        proto1 = OperationsProtocol()
        proto1.add_task("Alpha", priority="high")
        proto1.add_task("Beta", priority="low")
        proto1.complete_task(2)

        # Create a new instance that should load from the same file
        proto2 = OperationsProtocol()
        assert len(proto2._tasks) == 2
        assert proto2._tasks[0]["text"] == "Alpha"
        assert proto2._tasks[0]["priority"] == "high"
        assert proto2._tasks[1]["text"] == "Beta"
        assert proto2._tasks[1]["completed"] is True

    def test_save_is_valid_json(self, tmp_path):
        proto = OperationsProtocol()
        proto.add_task("JSON check")
        with open(proto.TASK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["text"] == "JSON check"

    def test_load_handles_missing_file(self, tmp_path):
        """If the task file does not exist, tasks start empty."""
        proto = OperationsProtocol()
        assert proto._tasks == []

    def test_load_handles_corrupt_file(self, tmp_path):
        """If the task file is corrupt JSON, tasks start empty."""
        task_file = OperationsProtocol.TASK_FILE
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_file.write_text("NOT VALID JSON {{{{", encoding="utf-8")
        proto = OperationsProtocol()
        assert proto._tasks == []

    def test_removal_persists(self, tmp_path):
        proto1 = OperationsProtocol()
        proto1.add_task("Keep")
        proto1.add_task("Remove")
        proto1.remove_task(2)

        proto2 = OperationsProtocol()
        assert len(proto2._tasks) == 1
        assert proto2._tasks[0]["text"] == "Keep"

    def test_completion_persists(self, tmp_path):
        proto1 = OperationsProtocol()
        proto1.add_task("Complete me")
        proto1.complete_task(1)

        proto2 = OperationsProtocol()
        assert proto2._tasks[0]["completed"] is True
        assert proto2._tasks[0]["completed_at"] is not None


# =============================================================================
# Operations get_status
# =============================================================================

class TestOperationsStatus:
    """get_status() includes task counts."""

    def test_status_has_pending_tasks(self):
        proto = OperationsProtocol()
        proto.add_task("A")
        proto.add_task("B")
        status = proto.get_status()
        assert status["pending_tasks"] == 2
        assert status["total_tasks"] == 2

    def test_status_overdue_count(self):
        proto = OperationsProtocol()
        past = (datetime.now() - timedelta(days=1)).isoformat()
        proto.add_task("Late", due=past)
        status = proto.get_status()
        assert status["overdue"] == 1

    def test_status_after_completion(self):
        proto = OperationsProtocol()
        task = proto.add_task("Done")
        proto.complete_task(task["id"])
        status = proto.get_status()
        assert status["pending_tasks"] == 0
        assert status["total_tasks"] == 1


# =============================================================================
# NLP Pattern Detection Edge Cases
# =============================================================================

class TestNLPPatternDetection:
    """Edge cases for natural language task detection in process_input."""

    def test_add_task_pattern(self):
        proto = OperationsProtocol()
        ctx = {"messages": [], "memory": None, "char_memory": None, "agent_name": "T"}
        result = proto.process_input("add a task: update the readme", ctx)
        assert "auto-detected" in result["context_injection"]
        assert len(proto._tasks) == 1

    def test_dont_let_me_forget_pattern(self):
        proto = OperationsProtocol()
        ctx = {"messages": [], "memory": None, "char_memory": None, "agent_name": "T"}
        result = proto.process_input("don't let me forget to send the invoice", ctx)
        assert "auto-detected" in result["context_injection"]

    def test_i_need_to_pattern(self):
        proto = OperationsProtocol()
        ctx = {"messages": [], "memory": None, "char_memory": None, "agent_name": "T"}
        result = proto.process_input("i need to schedule a meeting", ctx)
        assert "auto-detected" in result["context_injection"]

    def test_short_matches_are_ignored(self):
        """Task text shorter than 4 chars should not create a task."""
        proto = OperationsProtocol()
        ctx = {"messages": [], "memory": None, "char_memory": None, "agent_name": "T"}
        # "remind me to go" -> match group is "go" (2 chars) -- too short
        result = proto.process_input("remind me to go", ctx)
        # "go" is only 2 chars, should be ignored
        assert len(proto._tasks) == 0

    def test_no_match_for_unrelated_input(self):
        proto = OperationsProtocol()
        ctx = {"messages": [], "memory": None, "char_memory": None, "agent_name": "T"}
        result = proto.process_input("what is the capital of France?", ctx)
        # No task pattern match, so no auto-detected injection
        assert "auto-detected" not in result.get("context_injection", "")


# =============================================================================
# Command helpers (done/remove with bad input)
# =============================================================================

class TestCmdTaskEdgeCases:
    """Edge cases for /task subcommands."""

    def test_cmd_done_bad_id(self):
        proto = OperationsProtocol()
        result = proto.cmd_task("done notanumber")
        assert "Usage" in result

    def test_cmd_remove_bad_id(self):
        proto = OperationsProtocol()
        result = proto.cmd_task("remove abc")
        assert "Usage" in result

    def test_cmd_done_already_completed(self):
        proto = OperationsProtocol()
        task = proto.add_task("Already done")
        proto.complete_task(task["id"])
        result = proto.cmd_task(f"done {task['id']}")
        assert "not found or already completed" in result

    def test_cmd_add_no_text(self):
        proto = OperationsProtocol()
        result = proto.cmd_task("add")
        assert "Usage" in result

    def test_cmd_remove_nonexistent(self):
        proto = OperationsProtocol()
        result = proto.cmd_task("remove 999")
        assert "not found" in result
