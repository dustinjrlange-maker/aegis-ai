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


def test_malformed_important_email_does_not_raise(monkeypatch):
    """Keyword-important email missing a 'from' key must not crash the body-builder."""
    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: [
        {"subject": "urgent invoice"},
    ])
    ctx = _ctx({"important_senders": [], "notify_threshold": 1,
                "keywords": ["urgent", "invoice"]})
    result = inbox_scan.run(ctx)
    assert result.notify is True


def test_sender_match_is_case_insensitive(monkeypatch):
    """Sender comparison must ignore case on both sides."""
    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: [
        {"from": "BOSS@studio.com", "subject": "hello"},
    ])
    ctx = _ctx({"important_senders": ["boss@studio.com"], "notify_threshold": 1})
    result = inbox_scan.run(ctx)
    assert result.notify is True


def test_threshold_above_one(monkeypatch):
    """notify_threshold=2 stays silent at 1 important, escalates at 2."""
    ctx = _ctx({"important_senders": ["boss@studio.com"], "notify_threshold": 2})

    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: [
        {"from": "boss@studio.com", "subject": "one"},
        {"from": "spam@promo.io", "subject": "50% off"},
    ])
    result = inbox_scan.run(ctx)
    assert result.notify is False

    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: [
        {"from": "boss@studio.com", "subject": "one"},
        {"from": "boss@studio.com", "subject": "two"},
    ])
    result = inbox_scan.run(ctx)
    assert result.notify is True


def test_notification_lines_include_account_tag(monkeypatch):
    """Email with 'account' key must prefix [account] in notification body."""
    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: [
        {"from": "hbo@x.com", "subject": "urgent call sheet", "account": "HBO"},
    ])
    ctx = _ctx({"important_senders": [], "notify_threshold": 1,
                "keywords": ["urgent"]})
    result = inbox_scan.run(ctx)
    assert result.notify is True
    assert "- [HBO] hbo@x.com: urgent call sheet" in result.body


def test_notification_lines_without_account_key_unchanged(monkeypatch):
    """Email without 'account' key must render as before — no tag, no brackets."""
    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: [
        {"from": "hbo@x.com", "subject": "urgent call sheet"},
    ])
    ctx = _ctx({"important_senders": [], "notify_threshold": 1,
                "keywords": ["urgent"]})
    result = inbox_scan.run(ctx)
    assert result.notify is True
    assert "- hbo@x.com: urgent call sheet" in result.body
    assert "[" not in result.body
