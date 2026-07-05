"""
Tests for check_recurring() time-of-day gate.

Locks in four behaviors:
1. A recurring task with time="09:00" does NOT fire before 09:00.
2. It DOES fire at/after 09:00.
3. It fires only once per day (once-per-day guard still works).
4. An entry with no time field fires regardless of the hour (backward-compat).
"""

from datetime import datetime
from core.protocols.operations import OperationsProtocol


def _ops(tmp_path):
    return OperationsProtocol(data_dir=tmp_path)


def test_recurring_not_fired_before_its_time(tmp_path):
    op = _ops(tmp_path)
    op.add_recurring(text="Standup", frequency="daily", time="09:00")
    fired = op.check_recurring(now=datetime(2026, 7, 4, 8, 30))
    assert fired == []


def test_recurring_fires_at_or_after_time(tmp_path):
    op = _ops(tmp_path)
    op.add_recurring(text="Standup", frequency="daily", time="09:00")
    fired = op.check_recurring(now=datetime(2026, 7, 4, 9, 1))
    assert any(t["text"] == "Standup" for t in fired)


def test_recurring_fires_once_per_day(tmp_path):
    op = _ops(tmp_path)
    op.add_recurring(text="Standup", frequency="daily", time="09:00")
    first = op.check_recurring(now=datetime(2026, 7, 4, 9, 1))
    second = op.check_recurring(now=datetime(2026, 7, 4, 12, 0))
    assert len(first) == 1 and second == []


def test_recurring_without_time_fires_on_date(tmp_path):
    op = _ops(tmp_path)
    op.add_recurring(text="Water plants", frequency="daily")   # no time= given
    fired = op.check_recurring(now=datetime(2026, 7, 4, 0, 5))  # very early, no time gate
    assert any(t["text"] == "Water plants" for t in fired)
