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


def test_parse_datetime_at_5pm():
    d, t = OperationsProtocol._parse_natural_datetime("thursday at 5pm")
    assert d is not None
    assert t == "17:00"


def test_parse_datetime_at_2_30pm():
    d, t = OperationsProtocol._parse_natural_datetime("tomorrow at 2:30pm")
    assert d is not None
    assert t == "14:30"


def test_parse_datetime_at_2_pm_spaced():
    d, t = OperationsProtocol._parse_natural_datetime("tomorrow at 2 pm")
    assert d is not None
    assert t == "14:00"


def test_parse_datetime_by_9am():
    d, t = OperationsProtocol._parse_natural_datetime("friday by 9am")
    assert d is not None
    assert t == "09:00"


def test_parse_datetime_midnight():
    d, t = OperationsProtocol._parse_natural_datetime("today at 12am")
    assert d is not None
    assert t == "00:00"


def test_parse_datetime_noon():
    d, t = OperationsProtocol._parse_natural_datetime("today at 12pm")
    assert d is not None
    assert t == "12:00"


def test_parse_datetime_no_time():
    d, t = OperationsProtocol._parse_natural_datetime("thursday")
    assert d is not None
    assert t is None


def test_parse_datetime_only_time_returns_no_date():
    d, t = OperationsProtocol._parse_natural_datetime("at 5pm")
    # Bare time without a date is ambiguous; spec says return (None, None)
    assert d is None


def test_parse_datetime_malformed_time_preserves_date():
    """When the time tail is garbage (e.g. '13pm'), the date should still parse."""
    d, t = OperationsProtocol._parse_natural_datetime("thursday at 13pm")
    assert d is not None  # date still parses to Thursday
    assert t is None       # bad time correctly discarded


def test_session_handler_parses_time_suffix():
    """The | time: HH:MM suffix in [ADD_TASK:...] gets routed to due_time."""
    from core.session import UserSession
    ops = _make_ops()
    # Minimal stub user session — bypass init since we only need the handler
    sm = UserSession.__new__(UserSession)
    sm.protocol_registry = {"operations": ops}
    result = sm._handle_add_task("Pay hydro | due: friday | time: 17:00")
    assert result.startswith("Task #")  # confirmation string returned
    # Locate the created task
    pending = [t for t in ops._tasks if "Pay hydro" in t["text"]]
    assert len(pending) == 1
    assert pending[0]["due_time"] == "17:00"
