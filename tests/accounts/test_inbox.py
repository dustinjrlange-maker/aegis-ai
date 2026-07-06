# tests/accounts/test_inbox.py
from unittest.mock import patch
from core.accounts.inbox import fetch_unread_all_accounts
from tests.test_briefing_accounts import FakeAccounts  # reuse the fake


class S:  # minimal session stub
    pass


def _msgs(sender, subject):
    return [{"id": "1", "subject": subject, "sender": sender,
             "date": "", "snippet": ""}]


def test_returns_none_when_no_session_accounts():
    assert fetch_unread_all_accounts(S()) is None


def test_returns_none_when_no_eligible_accounts():
    s = S()
    s.accounts = FakeAccounts(
        [{"id": "a", "label": "P", "features": {"inbox_scan": False}}], {})
    assert fetch_unread_all_accounts(s) is None


def test_aggregates_and_tags_by_account_label():
    s = S()
    s.accounts = FakeAccounts(
        [{"id": "a", "label": "Personal", "features": {"inbox_scan": True}},
         {"id": "b", "label": "HBO", "features": {"inbox_scan": True}}],
        {"a": object(), "b": object()})
    by_creds = {id(s.accounts._creds["a"]): _msgs("x@a.com", "hello"),
                id(s.accounts._creds["b"]): _msgs("y@b.com", "call sheet")}
    with patch("core.accounts.inbox.gmail_list_messages",
               side_effect=lambda creds, **kw: by_creds[id(creds)]):
        out = fetch_unread_all_accounts(s)
    assert {(e["account"], e["from"]) for e in out} == {
        ("Personal", "x@a.com"), ("HBO", "y@b.com")}
    assert all("subject" in e for e in out)


def test_skips_account_with_no_creds():
    s = S()
    s.accounts = FakeAccounts(
        [{"id": "a", "label": "Personal", "features": {"inbox_scan": True}},
         {"id": "b", "label": "HBO", "features": {"inbox_scan": True}}],
        {"a": object()})   # b has no creds
    with patch("core.accounts.inbox.gmail_list_messages",
               return_value=_msgs("x@a.com", "hi")):
        out = fetch_unread_all_accounts(s)
    assert [e["account"] for e in out] == ["Personal"]
