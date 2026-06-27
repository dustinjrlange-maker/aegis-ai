import time
from unittest.mock import MagicMock, patch

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
