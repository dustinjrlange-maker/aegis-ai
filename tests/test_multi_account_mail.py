"""Tests for multi-account mail: /api/email/active-account + account_id threading."""
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from server.app import app, require_user
    app.dependency_overrides[require_user] = lambda: "switch"
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/email/active-account
# ---------------------------------------------------------------------------

def test_set_active_account_valid(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock()
    sess.accounts.get.return_value = {
        "id": "google-stitch", "label": "SwitchStitch",
        "email": "s@x.com", "status": "ok", "is_default": False,
    }
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    resp = client.post("/api/email/active-account", json={"account_id": "google-stitch"})
    assert resp.status_code == 200
    assert sess.current_mail_account == "google-stitch"
    assert resp.json()["active"]["id"] == "google-stitch"


def test_set_active_account_unknown_400(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock()
    sess.accounts.get.return_value = None
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    resp = client.post("/api/email/active-account", json={"account_id": "nope"})
    assert resp.status_code == 400


def test_set_active_account_null_clears_to_default(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock()
    sess.accounts.default.return_value = {
        "id": "google-personal", "label": "Personal",
        "email": "p@x.com", "status": "ok", "is_default": True,
    }
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    resp = client.post("/api/email/active-account", json={"account_id": None})
    assert resp.status_code == 200
    assert sess.current_mail_account is None


def test_set_active_account_empty_string_clears_to_default(client, monkeypatch):
    """Empty string body should behave the same as null (clear to default)."""
    import server.app as app_mod
    sess = MagicMock()
    sess.accounts.default.return_value = None
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    resp = client.post("/api/email/active-account", json={"account_id": ""})
    assert resp.status_code == 200
    assert sess.current_mail_account is None


# ---------------------------------------------------------------------------
# account_id threading through email endpoints
# ---------------------------------------------------------------------------

def test_inbox_digest_passes_active_account(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock()
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    monkeypatch.setattr("core.email_assistant.active_account_id", lambda s: "google-stitch")
    captured = {}
    monkeypatch.setattr(
        "core.email_assistant.get_inbox_digest",
        lambda session, **kw: captured.update(kw) or {"messages": []},
    )
    client.get("/api/email/inbox-digest")
    assert captured.get("account_id") == "google-stitch"


def test_list_drafts_passes_active_account(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock()
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    monkeypatch.setattr("core.email_assistant.active_account_id", lambda s: "google-work")
    captured = {}
    monkeypatch.setattr(
        "core.email_assistant.list_drafts",
        lambda session, **kw: captured.update(kw) or [],
    )
    client.get("/api/email/drafts")
    assert captured.get("account_id") == "google-work"


def test_get_draft_passes_active_account(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock()
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    monkeypatch.setattr("core.email_assistant.active_account_id", lambda s: "google-stitch")
    captured = {}
    monkeypatch.setattr(
        "core.email_assistant.get_draft",
        lambda session, draft_id, **kw: captured.update({"draft_id": draft_id, **kw}) or {"id": draft_id},
    )
    client.get("/api/email/drafts/draft-abc")
    assert captured.get("account_id") == "google-stitch"


def test_mark_read_passes_active_account(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock()
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    monkeypatch.setattr("core.email_assistant.active_account_id", lambda s: "google-personal")
    captured = {}
    monkeypatch.setattr(
        "core.email_assistant.mark_read",
        lambda session, message_id, **kw: captured.update({"message_id": message_id, **kw}) or {"ok": True},
    )
    client.post("/api/email/mark-read/msg-xyz")
    assert captured.get("account_id") == "google-personal"


def test_discard_draft_passes_active_account(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock()
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    monkeypatch.setattr("core.email_assistant.active_account_id", lambda s: "google-work")
    captured = {}
    monkeypatch.setattr(
        "core.email_assistant.discard_draft",
        lambda session, draft_id, **kw: captured.update({"draft_id": draft_id, **kw}) or {"ok": True},
    )
    client.delete("/api/email/drafts/draft-del")
    assert captured.get("account_id") == "google-work"


# ---------------------------------------------------------------------------
# Inline-creds endpoints (no email_assistant wrapper) — guard the threaded arg
# ---------------------------------------------------------------------------

def test_get_message_passes_active_account(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock()
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    monkeypatch.setattr("core.email_assistant.active_account_id", lambda s: "google-stitch")
    captured = {}
    monkeypatch.setattr("core.email_assistant._creds_from_session",
                        lambda session, account_id=None: captured.setdefault("aid", account_id))  # returns None
    client.get("/api/email/messages/m1")
    assert captured["aid"] == "google-stitch"


def test_update_draft_passes_active_account(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock()
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    monkeypatch.setattr("core.email_assistant.active_account_id", lambda s: "google-stitch")
    captured = {}
    monkeypatch.setattr("core.email_assistant._creds_from_session",
                        lambda session, account_id=None: captured.setdefault("aid", account_id))  # returns None
    client.patch("/api/email/drafts/d1", json={"subject": "x", "body": "y"})
    assert captured["aid"] == "google-stitch"
