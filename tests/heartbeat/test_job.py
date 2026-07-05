from datetime import datetime, timedelta
from core.heartbeat import job as J


def test_every_due_when_never_fired():
    s = J.every(seconds=60)
    now = datetime(2026, 7, 4, 9, 0, 0)
    assert J.is_due(s, now, last_fired_at=None) is True


def test_every_not_due_within_interval():
    s = J.every(seconds=60)
    now = datetime(2026, 7, 4, 9, 0, 30)
    last = datetime(2026, 7, 4, 9, 0, 0)
    assert J.is_due(s, now, last_fired_at=last) is False


def test_every_due_after_interval():
    s = J.every(seconds=60)
    now = datetime(2026, 7, 4, 9, 1, 5)
    last = datetime(2026, 7, 4, 9, 0, 0)
    assert J.is_due(s, now, last_fired_at=last) is True


def test_daily_at_due_at_or_after_time_once_per_day():
    s = J.daily_at(7, 0)
    assert J.is_due(s, datetime(2026, 7, 4, 7, 0), last_fired_at=None) is True
    assert J.is_due(s, datetime(2026, 7, 4, 9, 0),
                    last_fired_at=datetime(2026, 7, 4, 7, 0)) is False
    assert J.is_due(s, datetime(2026, 7, 4, 7, 1),
                    last_fired_at=datetime(2026, 7, 3, 7, 0)) is True


def test_daily_at_not_due_before_time():
    s = J.daily_at(7, 0)
    assert J.is_due(s, datetime(2026, 7, 4, 6, 59), last_fired_at=None) is False


def test_quiet_hours_wrapping_window():
    q = ("22:00", "07:00")
    assert J.in_quiet_hours(datetime(2026, 7, 4, 23, 0), q) is True
    assert J.in_quiet_hours(datetime(2026, 7, 4, 3, 0), q) is True
    assert J.in_quiet_hours(datetime(2026, 7, 4, 7, 0), q) is False
    assert J.in_quiet_hours(datetime(2026, 7, 4, 12, 0), q) is False


def test_jobresult_defaults():
    r = J.JobResult(silent_log="ran")
    assert r.notify is False and r.title == "" and r.channels is None
