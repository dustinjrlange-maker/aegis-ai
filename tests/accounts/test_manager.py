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
    # spoken hints: spacing/dots must not break matching (2026-07-09 incident:
    # "the switch stitch email" failed to resolve to theswitchstitch@gmail.com)
    ("switch stitch", "google-stitch"),
    ("the switch stitch", "google-stitch"),
    ("dustin jr lange", "google-personal"),
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


def test_creds_for_returns_none_without_account(tmp_path):
    assert AccountManager(tmp_path).creds_for() is None


def test_creds_for_marks_error_when_tokens_exist_but_load_fails(tmp_path, monkeypatch):
    _write_registry(tmp_path, [ACCT_A])
    token_dir = tmp_path / "accounts" / "google-personal"
    token_dir.mkdir(parents=True)
    (token_dir / "google_tokens.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "core.protocols.google_tools.load_credentials", lambda d, account_id=None: None)
    am = AccountManager(tmp_path)
    assert am.creds_for("google-personal") is None
    assert am.get("google-personal")["status"] == "error"


def test_creds_for_success_resets_status(tmp_path, monkeypatch):
    _write_registry(tmp_path, [dict(ACCT_A, status="error")])
    monkeypatch.setattr(
        "core.protocols.google_tools.load_credentials",
        lambda d, account_id=None: object())
    am = AccountManager(tmp_path)
    assert am.creds_for("google-personal") is not None
    assert am.get("google-personal")["status"] == "ok"


def test_write_is_atomic_and_leaves_no_tmp(tmp_path):
    """_write swaps in via a temp file; no .tmp residue, file always parses."""
    _write_registry(tmp_path, [ACCT_A])
    AccountManager(tmp_path).set_status("google-personal", "error")
    data = json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
    assert data["accounts"][0]["status"] == "error"
    assert not (tmp_path / "accounts.json.tmp").exists()


def test_concurrent_set_status_different_accounts_no_corruption(tmp_path):
    """Two threads pounding set_status on DIFFERENT ids must not corrupt the
    file (always parseable) and both accounts' final statuses must survive —
    the RLock makes each read-modify-write atomic, os.replace makes reads see a
    complete file."""
    import threading

    _write_registry(tmp_path, [ACCT_A, ACCT_B])
    am = AccountManager(tmp_path)
    barrier = threading.Barrier(2)
    errors = []

    def hammer(account_id):
        barrier.wait()
        try:
            for i in range(200):
                am.set_status(account_id, f"s{i}")
                # concurrent read must never see a partial/corrupt file
                json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    t1 = threading.Thread(target=hammer, args=("google-personal",))
    t2 = threading.Thread(target=hammer, args=("google-stitch",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not errors, errors
    data = json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
    by_id = {a["id"]: a for a in data["accounts"]}
    # Both accounts present with their last written status (no lost account).
    assert by_id["google-personal"]["status"] == "s199"
    assert by_id["google-stitch"]["status"] == "s199"
    assert not (tmp_path / "accounts.json.tmp").exists()
