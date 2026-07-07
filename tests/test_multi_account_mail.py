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


# ---------------------------------------------------------------------------
# Task 4: chat email handlers follow the active account
# ---------------------------------------------------------------------------

import core.protocols.email_ops as email_ops
from core.protocols.email_ops import EmailOpsProtocol


def _proto_active(monkeypatch, active_id, captured):
    p = EmailOpsProtocol()
    p._session = MagicMock()
    monkeypatch.setattr("core.email_assistant.active_account_id", lambda s: active_id)
    monkeypatch.setattr(email_ops.ea, "_creds_from_session",
                        lambda session, account_id=None: captured.setdefault("aid", account_id) or "CREDS")
    return p


def test_recent_inbox_uses_active_account(monkeypatch):
    captured = {}
    p = _proto_active(monkeypatch, "google-stitch", captured)
    monkeypatch.setattr(email_ops.gt, "gmail_list_messages",
                        lambda creds, max_results=15, categories=None: [])
    p._recent_inbox()
    assert captured["aid"] == "google-stitch"


def test_do_mark_read_uses_active_account(monkeypatch):
    captured = {}
    p = _proto_active(monkeypatch, "google-stitch", captured)
    p._id_map = {1: "m1"}
    monkeypatch.setattr(email_ops.gt, "gmail_mark_read", lambda creds, mid: {"ok": True})
    p._do_mark_read({"ref": "1"}, "mark 1 read")
    assert captured["aid"] == "google-stitch"


def test_do_archive_uses_active_account(monkeypatch):
    captured = {}
    p = _proto_active(monkeypatch, "google-stitch", captured)
    p._id_map = {1: "m1"}
    monkeypatch.setattr(email_ops.gt, "gmail_archive", lambda creds, mid: {"ok": True})
    p._do_archive({"ref": "1"}, "archive 1")
    assert captured["aid"] == "google-stitch"


def test_resolve_account_no_hint_falls_back_to_active(monkeypatch):
    p = EmailOpsProtocol()
    p._session = MagicMock()
    p._session.accounts.get.return_value = {"id": "google-stitch", "label": "SwitchStitch"}
    monkeypatch.setattr("core.email_assistant.active_account_id", lambda s: "google-stitch")
    acct, note = p._resolve_account({})     # no ACCOUNT= hint
    assert acct["id"] == "google-stitch"
    assert note == ""


def test_resolve_account_explicit_hint_still_wins(monkeypatch):
    p = EmailOpsProtocol()
    p._session = MagicMock()
    p._session.accounts.resolve.return_value = {"id": "google-personal", "label": "Personal"}
    monkeypatch.setattr("core.email_assistant.active_account_id", lambda s: "google-stitch")
    acct, note = p._resolve_account({"account": "personal"})   # explicit hint
    assert acct["id"] == "google-personal"   # explicit hint wins over active
