# tests/accounts/test_manager.py
import json
import pytest
from core.accounts.manager import AccountManager


def _write_registry(tmp_path, accounts):
    (tmp_path / "accounts.json").write_text(
        json.dumps({"accounts": accounts}), encoding="utf-8")


ACCT_A = {
    "id": "google-personal", "provider": "google",
    "email": "dustin.jr.lange@gmail.com", "label": "Personal",
    "is_default": True,
    "represent_as": {"name": "Dustin", "signoff": "Dustin", "tone_hint": "casual"},
    "features": {"briefing_calendar": True, "inbox_scan": True},
    "status": "ok",
}
ACCT_B = {
    "id": "google-stitch", "provider": "google",
    "email": "TheSwitchStitch@gmail.com", "label": "SwitchStitch",
    "is_default": False,
    "represent_as": {"name": "Switch", "signoff": "Switch", "tone_hint": "maker-brand"},
    "features": {"briefing_calendar": True, "inbox_scan": False},
    "status": "ok",
}


def test_empty_dir_gives_empty_registry(tmp_path):
    am = AccountManager(tmp_path)
    assert am.list() == []
    assert am.default() is None
    assert am.get("nope") is None


def test_list_and_feature_filter(tmp_path):
    _write_registry(tmp_path, [ACCT_A, ACCT_B])
    am = AccountManager(tmp_path)
    assert [a["id"] for a in am.list()] == ["google-personal", "google-stitch"]
    assert [a["id"] for a in am.list(feature="inbox_scan")] == ["google-personal"]


def test_default_prefers_is_default_flag(tmp_path):
    b_default = dict(ACCT_B, is_default=True)
    _write_registry(tmp_path, [dict(ACCT_A, is_default=False), b_default])
    am = AccountManager(tmp_path)
    assert am.default()["id"] == "google-stitch"


def test_default_falls_back_to_first(tmp_path):
    _write_registry(tmp_path, [dict(ACCT_A, is_default=False)])
    assert AccountManager(tmp_path).default()["id"] == "google-personal"


@pytest.mark.parametrize("hint,expected", [
    ("google-stitch", "google-stitch"),        # exact id
    ("switchstitch", "google-stitch"),         # label, case-insensitive
    ("theswitchstitch@gmail.com", "google-stitch"),  # email
    ("stitch", "google-stitch"),               # substring
    ("dustin", "google-personal"),             # represent_as name
    ("", "google-personal"),                   # empty -> default
    ("no-such-account", None),                 # unknown -> None
])
def test_resolve(tmp_path, hint, expected):
    _write_registry(tmp_path, [ACCT_A, ACCT_B])
    got = AccountManager(tmp_path).resolve(hint)
    assert (got["id"] if got else None) == expected


def test_corrupt_registry_is_empty_not_crash(tmp_path):
    (tmp_path / "accounts.json").write_text("{not json", encoding="utf-8")
    assert AccountManager(tmp_path).list() == []


def test_set_status_persists(tmp_path):
    _write_registry(tmp_path, [ACCT_A])
    AccountManager(tmp_path).set_status("google-personal", "error")
    data = json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
    assert data["accounts"][0]["status"] == "error"


def test_set_status_unknown_id_is_noop(tmp_path):
    _write_registry(tmp_path, [ACCT_A])
    AccountManager(tmp_path).set_status("no-such-id", "error")
    data = json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
    assert data["accounts"][0]["status"] == "ok"  # unchanged
