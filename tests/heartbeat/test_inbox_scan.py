"""Tests for the inbox_scan heartbeat job."""

from datetime import datetime
from core.heartbeat.job import JobContext
from core.heartbeat.jobs import inbox_scan


def _ctx(cfg=None):
    """Build a minimal JobContext for inbox_scan tests."""
    return JobContext("switch", object(), datetime(2026, 7, 4, 10, 0), cfg or {})


def test_important_sender_escalates(monkeypatch):
    """Known sender in unread list should push a notification."""
    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: [
        {"from": "boss@studio.com", "subject": "call sheet tomorrow"},
        {"from": "spam@promo.io", "subject": "50% off"},
    ])
    ctx = _ctx({"important_senders": ["boss@studio.com"], "notify_threshold": 1})
    result = inbox_scan.run(ctx)
    assert result.notify is True
    assert "boss@studio.com" in result.body


def test_below_threshold_is_silent(monkeypatch):
    """0 important emails below threshold=1 should be silent, log count."""
    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: [
        {"from": "spam@promo.io", "subject": "50% off"},
    ])
    ctx = _ctx({"important_senders": ["boss@studio.com"], "notify_threshold": 1})
    result = inbox_scan.run(ctx)
    assert result.notify is False
    assert "1" in result.silent_log


def test_keyword_signal_counts_as_important(monkeypatch):
    """Keyword match in subject should count as important regardless of sender."""
    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: [
        {"from": "unknown@x.com", "subject": "URGENT: invoice overdue"},
    ])
    ctx = _ctx({"important_senders": [], "notify_threshold": 1,
                "keywords": ["urgent", "invoice"]})
    result = inbox_scan.run(ctx)
    assert result.notify is True


def test_email_unconfigured_self_disables(monkeypatch):
    """fetch_unread returning None should self-disable with no notification."""
    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: None)
    result = inbox_scan.run(_ctx())
    assert result.notify is False
    assert "not configured" in result.silent_log.lower()
