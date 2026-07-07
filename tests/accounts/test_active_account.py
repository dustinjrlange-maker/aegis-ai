# tests/accounts/test_active_account.py
import json
from core.email_assistant import active_account_id


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
