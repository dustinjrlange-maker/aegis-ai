# tests/accounts/test_active_account.py
import json
from unittest.mock import patch
from core.email_assistant import active_account_id
import core.email_assistant as ea


class _FakeAccounts:
    def __init__(self, accounts):
        self._a = accounts
    def get(self, aid):
        return next((x for x in self._a if x["id"] == aid), None)
    def default(self):
        return next((x for x in self._a if x.get("is_default")), self._a[0] if self._a else None)


class _S:
    pass


def test_active_id_none_when_no_accounts():
    s = _S(); s.current_mail_account = None; s.accounts = _FakeAccounts([])
    assert active_account_id(s) is None


def test_active_id_defaults_when_unset():
    s = _S(); s.current_mail_account = None
    s.accounts = _FakeAccounts([{"id": "google-personal", "is_default": True}])
    assert active_account_id(s) == "google-personal"


def test_active_id_returns_selected_when_set_and_exists():
    s = _S(); s.current_mail_account = "google-stitch"
    s.accounts = _FakeAccounts([{"id": "google-personal", "is_default": True},
                                {"id": "google-stitch"}])
    assert active_account_id(s) == "google-stitch"


def test_active_id_falls_back_to_default_when_selected_is_stale():
    s = _S(); s.current_mail_account = "google-deleted"
    s.accounts = _FakeAccounts([{"id": "google-personal", "is_default": True}])
    assert active_account_id(s) == "google-personal"


def test_get_inbox_digest_threads_account_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(ea, "_creds_from_session",
                        lambda session, account_id=None: captured.setdefault("aid", account_id) or "CREDS")
    monkeypatch.setattr(ea.gt, "gmail_unread_count", lambda creds, categories=(): 0)
    monkeypatch.setattr(ea.gt, "gmail_list_messages",
                        lambda creds, max_results=10, categories=(): [])
    ea.get_inbox_digest(_S(), account_id="google-stitch")
    assert captured["aid"] == "google-stitch"


def test_mark_read_threads_account_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(ea, "_creds_from_session",
                        lambda session, account_id=None: captured.setdefault("aid", account_id) or "CREDS")
    monkeypatch.setattr(ea.gt, "gmail_mark_read", lambda creds, mid: {"ok": True})
    ea.mark_read(_S(), "m1", account_id="google-stitch")
    assert captured["aid"] == "google-stitch"


def test_list_drafts_threads_account_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(ea, "_creds_from_session",
                        lambda session, account_id=None: captured.setdefault("aid", account_id) or "CREDS")
    monkeypatch.setattr(ea.gt, "gmail_list_drafts", lambda creds, max_results=20: [])
    ea.list_drafts(_S(), account_id="google-stitch")
    assert captured["aid"] == "google-stitch"


def test_get_draft_threads_account_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(ea, "_creds_from_session",
                        lambda session, account_id=None: captured.setdefault("aid", account_id) or "CREDS")
    monkeypatch.setattr(ea.gt, "gmail_get_draft", lambda creds, did: {})
    ea.get_draft(_S(), "d1", account_id="google-stitch")
    assert captured["aid"] == "google-stitch"


def test_discard_draft_threads_account_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(ea, "_creds_from_session",
                        lambda session, account_id=None: captured.setdefault("aid", account_id) or "CREDS")
    monkeypatch.setattr(ea.gt, "gmail_delete_draft", lambda creds, did: {"success": True})
    ea.discard_draft(_S(), "d1", account_id="google-stitch")
    assert captured["aid"] == "google-stitch"


def test_inbox_digest_cache_is_per_account(monkeypatch):
    calls = {"n": 0}
    def fake_llm(msgs):
        calls["n"] += 1
        return f"summary {calls['n']}"
    monkeypatch.setattr(ea, "_creds_from_session", lambda session, account_id=None: "CREDS")
    monkeypatch.setattr(ea.gt, "gmail_unread_count", lambda creds, categories=(): 1)
    monkeypatch.setattr(ea.gt, "gmail_list_messages",
                        lambda creds, max_results=10, categories=(): [{"sender": "a", "subject": "s", "snippet": "x", "id": "1"}])
    monkeypatch.setattr(ea, "_llm", fake_llm)
    s = _S(); s.user_id = "u"; s.system_prompt_base = ""; s.clean_reply = lambda x: x
    r1 = ea.get_inbox_digest(s, account_id="acct-A")
    r2 = ea.get_inbox_digest(s, account_id="acct-B")
    assert calls["n"] == 2                       # distinct accounts -> not shared
    assert r1["narrative"] != r2["narrative"]
