import tempfile
import pytest
from core.protocols.operations import OperationsProtocol


def _make_ops():
    td = tempfile.TemporaryDirectory()
    ops = OperationsProtocol(data_dir=td.name)
    # Keep the TemporaryDirectory alive for the lifetime of the ops object
    ops._tmpdir = td
    return ops


def test_add_task_accepts_due_time():
    ops = _make_ops()
    task = ops.add_task("Smoke", due="2026-06-30", due_time="17:00")
    assert task["due"] == "2026-06-30"
    assert task["due_time"] == "17:00"


def test_add_task_due_time_defaults_to_none():
    ops = _make_ops()
    task = ops.add_task("No time")
    assert task.get("due_time") is None


def test_update_task_due_time():
    ops = _make_ops()
    task = ops.add_task("Edit me")
    updated = ops.update_task(task["id"], due_time="09:30")
    assert updated["due_time"] == "09:30"


def test_due_time_persists_to_disk():
    ops = _make_ops()
    ops.add_task("Persist", due="2026-06-30", due_time="17:00")
    # Reload from same dir
    ops2 = OperationsProtocol(data_dir=ops._tmpdir.name)
    assert ops2._tasks[-1]["due_time"] == "17:00"


def test_due_time_migration_backfills_none_on_old_tasks():
    """Tasks loaded from a tasks.json without due_time should get None."""
    import json, pathlib
    ops = _make_ops()
    old_task = {
        "id": 99, "text": "Legacy", "priority": "normal",
        "category": "general", "due": None, "created": "2026-01-01T00:00:00",
        "completed": False, "completed_at": None,
        "subtasks": [], "starred": False, "activity_type": "general",
        "notes": "", "attachments": []
    }
    p = pathlib.Path(ops._tmpdir.name) / "tasks.json"
    p.write_text(json.dumps([old_task]))
    ops2 = OperationsProtocol(data_dir=ops._tmpdir.name)
    assert "due_time" in ops2._tasks[0]
    assert ops2._tasks[0]["due_time"] is None
