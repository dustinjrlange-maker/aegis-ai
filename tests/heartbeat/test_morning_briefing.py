"""Tests for the morning_briefing heartbeat job."""
from datetime import datetime

from core.heartbeat.job import JobContext
from core.heartbeat.jobs import morning_briefing


def test_run_builds_notify_result(monkeypatch):
    """Job returns notify=True with briefing narrative as body."""
    monkeypatch.setattr(
        morning_briefing,
        "generate_narrative_briefing",
        lambda session, period=None: {
            "narrative": "Good morning. 3 tasks today.",
            "facts": {},
            "period": "morning",
        },
    )
    ctx = JobContext("switch", object(), datetime(2026, 7, 4, 7, 0), {})
    result = morning_briefing.run(ctx)
    assert result.notify is True
    assert result.body == "Good morning. 3 tasks today."
    assert "briefing" in result.title.lower() or "morning" in result.title.lower()


def test_run_empty_briefing_still_notifies(monkeypatch):
    """Empty narrative falls back to a default line and still notifies."""
    monkeypatch.setattr(
        morning_briefing,
        "generate_narrative_briefing",
        lambda session, period=None: {
            "narrative": "",
            "facts": {},
            "period": "morning",
        },
    )
    ctx = JobContext("switch", object(), datetime(2026, 7, 4, 7, 0), {})
    result = morning_briefing.run(ctx)
    assert result.notify is True
    assert result.body


def test_run_none_session_is_silent_no_raise():
    """A None session yields a silent JobResult and does not raise."""
    ctx = JobContext("switch", None, datetime(2026, 7, 4, 7, 0), {})
    result = morning_briefing.run(ctx)
    assert result.notify is False
    assert "no active session" in result.silent_log
