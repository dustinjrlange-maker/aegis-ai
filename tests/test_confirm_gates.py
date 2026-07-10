"""Confirm gates on irreversible endpoints (2026-07-09 audit).

Google Calendar writes and draft discards fired on bare HTTP calls — a UI
bug, double-click, or scripted fetch performed irreversible actions with no
belt-and-suspenders check. Mirrors the email send-draft pattern: the request
must carry an explicit confirm flag.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def client():
    from server.app import app, require_user
    app.dependency_overrides[require_user] = lambda: "switch"
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def sess(monkeypatch):
    import server.app as app_mod
    s = MagicMock()
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: s)
    return s


# --- DELETE /api/email/drafts/{id} -------------------------------------------

def test_discard_draft_without_confirm_refuses(client, sess, monkeypatch):
    import server.app as app_mod
    called = {}
    monkeypatch.setattr("core.email_assistant.discard_draft",
                        lambda *a, **k: called.update(hit=True) or {"success": True})
    resp = client.delete("/api/email/drafts/d1")
    assert not called
    body = resp.json()
    assert body.get("success") is not True
    assert "confirm" in str(body).lower()


def test_discard_draft_with_confirm_discards(client, sess, monkeypatch):
    called = {}
    monkeypatch.setattr("core.email_assistant.discard_draft",
                        lambda session, draft_id, account_id=None:
                        called.update(draft_id=draft_id) or {"success": True})
    monkeypatch.setattr("core.email_assistant.active_account_id",
                        lambda session: "google-personal")
    resp = client.delete("/api/email/drafts/d1?confirm=true")
    assert called.get("draft_id") == "d1"
    assert resp.json().get("success") is True


# --- POST /api/calendar/google ------------------------------------------------

def _google_sess(sess):
    proto = MagicMock()
    proto._get_creds.return_value = "CREDS"
    sess.protocol_registry.get.return_value = proto
    return sess


def test_calendar_google_write_without_confirm_refuses(client, sess, monkeypatch):
    _google_sess(sess)
    called = {}
    monkeypatch.setattr("core.protocols.google_tools.calendar_create",
                        lambda *a, **k: called.update(hit=True) or {"success": True})
    resp = client.post("/api/calendar/google", json={
        "action": "create", "title": "Dentist", "date": "2026-07-15",
        "time_start": "10:00"})
    assert not called
    assert "confirm" in str(resp.json()).lower()


def test_calendar_google_delete_without_confirm_refuses(client, sess, monkeypatch):
    _google_sess(sess)
    called = {}
    monkeypatch.setattr("core.protocols.google_tools.calendar_delete",
                        lambda *a, **k: called.update(hit=True) or {"success": True})
    resp = client.post("/api/calendar/google", json={
        "action": "delete", "event_id": "e1"})
    assert not called
    assert "confirm" in str(resp.json()).lower()


def test_calendar_google_write_with_confirm_writes(client, sess, monkeypatch):
    _google_sess(sess)
    called = {}
    monkeypatch.setattr("core.protocols.google_tools.calendar_create",
                        lambda creds, title, start, end, description="":
                        called.update(title=title) or {"success": True})
    resp = client.post("/api/calendar/google", json={
        "action": "create", "title": "Dentist", "date": "2026-07-15",
        "time_start": "10:00", "confirm": True})
    assert called.get("title") == "Dentist"
    assert resp.json().get("success") is True


def test_events_save_to_google_without_confirm_refuses(client, sess, monkeypatch):
    _google_sess(sess)
    called = {}
    monkeypatch.setattr("core.protocols.google_tools.calendar_create",
                        lambda *a, **k: called.update(hit=True) or {"success": True})
    resp = client.post("/api/events", json={
        "action": "add", "title": "Dentist", "date": "2026-07-15",
        "save_to_google": True})
    assert not called
    assert "confirm" in str(resp.json()).lower()
