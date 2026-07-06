import json
import time
import types
from unittest.mock import ANY, MagicMock, patch

import pytest

from core.accounts.manager import AccountManager


def _mock_session(narrative_text="Pike's brief"):
    """Build a fake session enough for get_inbox_digest to run."""
    session = MagicMock()
    session.user_id = "test_user"
    session.system_prompt_base = "system"
    session.clean_reply = lambda s: s
    return session


def test_narrative_cache_returns_cached_within_ttl():
    """Two calls within the TTL should produce ONE LLM call."""
    from core import email_assistant as ea
    ea._narrative_cache.clear()  # isolate

    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea.gt, "gmail_unread_count", return_value=2), \
         patch.object(ea.gt, "gmail_list_messages", return_value=[
             {"id": "m1", "subject": "Hi", "sender": "Bill", "date": "now", "snippet": "x"}
         ]), \
         patch.object(ea, "_llm", return_value="Pike's brief") as llm_mock:
        r1 = ea.get_inbox_digest(session)
        r2 = ea.get_inbox_digest(session)

    assert r1["narrative"] == "Pike's brief"
    assert r2["narrative"] == "Pike's brief"
    assert llm_mock.call_count == 1  # second call was served from cache


def test_narrative_cache_busted_by_fresh_kwarg():
    """fresh=True forces an LLM regeneration."""
    from core import email_assistant as ea
    ea._narrative_cache.clear()

    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea.gt, "gmail_unread_count", return_value=1), \
         patch.object(ea.gt, "gmail_list_messages", return_value=[
             {"id": "m1", "subject": "x", "sender": "y", "date": "z", "snippet": "s"}
         ]), \
         patch.object(ea, "_llm", side_effect=["first", "second"]) as llm_mock:
        ea.get_inbox_digest(session)
        ea.get_inbox_digest(session, fresh=True)

    assert llm_mock.call_count == 2


def test_narrative_cache_expires_after_ttl():
    """After TTL, the cache is rebuilt."""
    from core import email_assistant as ea
    ea._narrative_cache.clear()

    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea.gt, "gmail_unread_count", return_value=0), \
         patch.object(ea.gt, "gmail_list_messages", return_value=[
             {"id": "m1", "subject": "x", "sender": "y", "date": "z", "snippet": "s"}
         ]), \
         patch.object(ea, "_llm", side_effect=["first", "second"]) as llm_mock, \
         patch.object(ea, "_NARRATIVE_TTL_S", 0.05):  # 50ms TTL for fast test
        ea.get_inbox_digest(session)
        time.sleep(0.1)
        ea.get_inbox_digest(session)

    assert llm_mock.call_count == 2


def test_failed_narrative_is_not_cached():
    """If the LLM call raises, the next call should retry — not serve a stale failure."""
    from core import email_assistant as ea
    ea._narrative_cache.clear()

    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea.gt, "gmail_unread_count", return_value=0), \
         patch.object(ea.gt, "gmail_list_messages", return_value=[
             {"id": "m1", "subject": "x", "sender": "y", "date": "z", "snippet": "s"}
         ]), \
         patch.object(ea, "_llm", side_effect=[RuntimeError("boom"), "fresh narrative"]) as llm_mock:
        r1 = ea.get_inbox_digest(session)
        r2 = ea.get_inbox_digest(session)

    # First call returned the failure-string narrative
    assert "Briefing failed" in r1["narrative"] or r1["narrative"].startswith("[")
    # Second call HIT the LLM again (no cache poisoning from the failure)
    assert llm_mock.call_count == 2
    # And the second call's narrative is the successful one
    assert r2["narrative"] == "fresh narrative"


def test_draft_new_threads_cc_and_bcc_to_gmail_create():
    """draft_new should forward cc and bcc into gmail_create_draft."""
    from core import email_assistant as ea
    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea, "_llm", return_value="Subject: Hi\n\nBody"), \
         patch.object(ea.gt, "gmail_create_draft", return_value={
             "success": True, "draft_id": "d1", "message_id": "m1"
         }) as mock_create:
        ea.draft_new(
            session, to="bill@example.com",
            intent="say hi",
            cc="tyler@example.com",
            bcc="audit@example.com",
        )
    # Inspect the kwargs the mock received
    kwargs = mock_create.call_args.kwargs
    assert kwargs.get("cc") == "tyler@example.com"
    assert kwargs.get("bcc") == "audit@example.com"


def test_draft_new_omits_cc_bcc_when_not_provided():
    """Backwards compat — existing callers without cc/bcc still work."""
    from core import email_assistant as ea
    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea, "_llm", return_value="Subject: Hi\n\nBody"), \
         patch.object(ea.gt, "gmail_create_draft", return_value={
             "success": True, "draft_id": "d1", "message_id": "m1"
         }) as mock_create:
        ea.draft_new(session, to="bill@example.com", intent="say hi")
    kwargs = mock_create.call_args.kwargs
    # cc/bcc may be passed as None or absent — both are fine
    assert not kwargs.get("cc")
    assert not kwargs.get("bcc")


def test_mark_read_calls_gmail_modify():
    """mark_read should call gmail_mark_read with the message id."""
    from core import email_assistant as ea
    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea.gt, "gmail_mark_read", return_value={"ok": True}) as mock_mark:
        result = ea.mark_read(session, "msg_abc")
    assert result == {"ok": True}
    mock_mark.assert_called_once_with(ANY, "msg_abc")


def test_mark_read_returns_error_when_not_authorized():
    from core import email_assistant as ea
    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=None):
        result = ea.mark_read(session, "msg_abc")
    # Normalized {ok, error?} shape — frontend can branch on result.ok
    assert result == {"ok": False, "error": "not_authorized"}


def test_gmail_get_message_helper_is_callable():
    """Sanity: gmail_get_message is importable and accepts (creds, message_id)."""
    from core.protocols.google_tools import gmail_get_message
    assert callable(gmail_get_message)


# --- Task 11: represent-as persona injection into drafts -------------------


def _stitch_session(tmp_path):
    """Session backed by a real AccountManager whose default account has a
    represent_as persona configured (label SwitchStitch)."""
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": [
        {"id": "google-stitch", "label": "SwitchStitch",
         "email": "TheSwitchStitch@gmail.com", "is_default": True,
         "represent_as": {"name": "Switch", "signoff": "Switch",
                          "tone_hint": "maker-brand"}},
    ]}), encoding="utf-8")
    return types.SimpleNamespace(
        system_prompt_base="SYS", user_id="u", accounts=AccountManager(tmp_path))


def test_draft_new_injects_represent_as(tmp_path):
    """draft_new with an account_id prepends the represent-as block."""
    from core import email_assistant as ea
    session = _stitch_session(tmp_path)
    captured = {}
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea, "_llm",
                      side_effect=lambda messages, **kw: captured.update(
                          messages=messages) or "Subject: Hi\n\nBody"), \
         patch.object(ea.gt, "gmail_create_draft",
                      return_value={"success": True, "draft_id": "d1"}):
        ea.draft_new(session, to="x@y.com", intent="say hi",
                     account_id="google-stitch")
    user_msg = captured["messages"][-1]["content"]
    assert "SwitchStitch" in user_msg
    assert "Present the user as Switch" in user_msg
    assert "Sign off as: Switch" in user_msg
    assert "Tone: maker-brand" in user_msg


def test_draft_reply_injects_represent_as(tmp_path):
    """draft_reply also prepends the represent-as block for the account."""
    from core import email_assistant as ea
    session = _stitch_session(tmp_path)
    captured = {}
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea.gt, "gmail_get_message", return_value={
             "from": "Ann <ann@x.ca>", "subject": "Hi", "date": "now",
             "body": "hello"}), \
         patch.object(ea, "_llm",
                      side_effect=lambda messages, **kw: captured.update(
                          messages=messages) or "Sure thing."), \
         patch.object(ea.gt, "gmail_create_draft",
                      return_value={"success": True, "draft_id": "d1"}):
        ea.draft_reply(session, "m1", intent="agree", account_id="google-stitch")
    user_msg = captured["messages"][-1]["content"]
    assert "Present the user as Switch" in user_msg
    # The original drafting instruction still follows the injected block.
    assert "Draft a reply email IN THE USER'S VOICE" in user_msg


def test_draft_new_no_account_layer_prompt_unchanged():
    """No accounts layer -> block is '' and the prompt is byte-identical."""
    from core import email_assistant as ea
    session = types.SimpleNamespace(
        system_prompt_base="SYS", user_id="u", accounts=None)
    captured = {}
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea, "_llm",
                      side_effect=lambda messages, **kw: captured.update(
                          messages=messages) or "Subject: Hi\n\nBody"), \
         patch.object(ea.gt, "gmail_create_draft",
                      return_value={"success": True, "draft_id": "d1"}):
        ea.draft_new(session, to="x@y.com", intent="say hi")
    user_msg = captured["messages"][-1]["content"]
    assert "Present the user as" not in user_msg
    assert "Sign off as:" not in user_msg
    assert "Tone:" not in user_msg
    # Prompt starts with the original instruction — nothing prepended.
    assert user_msg.startswith("Draft an email IN THE USER'S VOICE")


def test_draft_forward_builds_quoted_draft(monkeypatch):
    import core.email_assistant as ea
    from core.protocols import google_tools as gt

    class _G:
        def _get_creds(self, account_id=None): return "CREDS"
    class _R:
        def get(self, n): return _G() if n == "google" else None
    class _S:
        protocol_registry = _R()
        system_prompt_base = "SYS"
        user_id = "u"

    monkeypatch.setattr(gt, "gmail_get_message", lambda creds, mid: {
        "subject": "Quarterly numbers", "from": "Ann <ann@x.ca>",
        "date": "Mon, 1 Jun 2026", "body": "Here are the figures.",
    })
    captured = {}
    monkeypatch.setattr(gt, "gmail_create_draft",
                        lambda creds, to, subject, body, **kw: captured.update(
                            to=to, subject=subject, body=body) or {"success": True, "draft_id": "d9"})

    res = ea.draft_forward(_S(), "m1", "bob@x.ca", note="fyi")
    assert res["success"] is True
    assert res["draft_id"] == "d9"
    assert captured["to"] == "bob@x.ca"
    assert captured["subject"] == "Fwd: Quarterly numbers"
    assert "fyi" in captured["body"]
    assert "Forwarded message" in captured["body"]
    assert "Here are the figures." in captured["body"]
