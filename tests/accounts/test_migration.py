# tests/accounts/test_migration.py
import json
from core.accounts.manager import AccountManager, DEFAULT_ACCOUNT_ID

FAKE_TOKENS = {"token": "t", "refresh_token": "r", "client_id": "c",
               "client_secret": "s", "scopes": []}


def test_legacy_tokens_migrate_on_first_load(tmp_path):
    (tmp_path / "google_tokens.json").write_text(
        json.dumps(FAKE_TOKENS), encoding="utf-8")
    am = AccountManager(tmp_path)

    # registry created with one default google account
    accounts = am.list()
    assert len(accounts) == 1
    acct = accounts[0]
    assert acct["id"] == DEFAULT_ACCOUNT_ID
    assert acct["provider"] == "google"
    assert acct["is_default"] is True
    assert acct["features"] == {"briefing_calendar": True, "inbox_scan": True}

    # tokens copied into the account dir, original kept as backup
    moved = tmp_path / "accounts" / DEFAULT_ACCOUNT_ID / "google_tokens.json"
    assert json.loads(moved.read_text(encoding="utf-8")) == FAKE_TOKENS
    assert not (tmp_path / "google_tokens.json").exists()
    assert (tmp_path / "google_tokens.json.migrated").exists()


def test_migration_is_idempotent(tmp_path):
    (tmp_path / "google_tokens.json").write_text(
        json.dumps(FAKE_TOKENS), encoding="utf-8")
    AccountManager(tmp_path)
    AccountManager(tmp_path)  # second load must not duplicate or crash
    assert len(AccountManager(tmp_path).list()) == 1


def test_no_legacy_file_no_registry_write(tmp_path):
    AccountManager(tmp_path)
    assert not (tmp_path / "accounts.json").exists()


def test_corrupt_legacy_tokens_left_untouched(tmp_path):
    (tmp_path / "google_tokens.json").write_text("{corrupt", encoding="utf-8")
    am = AccountManager(tmp_path)
    assert am.list() == []                                  # no registry
    assert (tmp_path / "google_tokens.json").exists()       # original intact
    assert not (tmp_path / "google_tokens.json.migrated").exists()
