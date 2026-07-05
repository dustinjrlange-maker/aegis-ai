# tests/accounts/test_token_resolution.py
import json
from core.protocols.google_tools import _resolve_token_dir


def _registry(tmp_path, accounts):
    (tmp_path / "accounts.json").write_text(
        json.dumps({"accounts": accounts}), encoding="utf-8")


def test_no_registry_resolves_to_user_dir(tmp_path):
    assert _resolve_token_dir(tmp_path) == tmp_path


def test_registry_default_account(tmp_path):
    _registry(tmp_path, [
        {"id": "a", "is_default": False}, {"id": "b", "is_default": True}])
    assert _resolve_token_dir(tmp_path) == tmp_path / "accounts" / "b"


def test_registry_explicit_account_id(tmp_path):
    _registry(tmp_path, [{"id": "a", "is_default": True}, {"id": "b"}])
    assert _resolve_token_dir(tmp_path, account_id="b") == tmp_path / "accounts" / "b"


def test_unknown_account_id_falls_back_to_user_dir(tmp_path):
    _registry(tmp_path, [{"id": "a", "is_default": True}])
    assert _resolve_token_dir(tmp_path, account_id="zzz") == tmp_path


def test_empty_or_corrupt_registry_falls_back(tmp_path):
    (tmp_path / "accounts.json").write_text("{corrupt", encoding="utf-8")
    assert _resolve_token_dir(tmp_path) == tmp_path


def test_registry_no_default_falls_back_to_first_account(tmp_path):
    _registry(tmp_path, [{"id": "a"}, {"id": "b"}])   # neither is_default
    assert _resolve_token_dir(tmp_path) == tmp_path / "accounts" / "a"
