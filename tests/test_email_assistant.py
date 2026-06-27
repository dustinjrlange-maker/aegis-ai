import time
from unittest.mock import ANY, MagicMock, patch

import pytest


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
