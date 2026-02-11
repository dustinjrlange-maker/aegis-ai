"""
Tests for core.auth — user registration, login, token management, passcode change.
"""

import sys
from pathlib import Path
import json
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.auth import (
    load_users, save_users, create_user, verify_user,
    change_passcode, generate_session_token, create_session,
    validate_token, invalidate_token, get_user_data_dir,
    load_user_preferences, save_user_preferences,
    USERS_FILE, USERS_DIR, active_sessions,
)


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Redirect all auth files to a temp directory."""
    test_users_file = tmp_path / "users.json"
    test_users_dir = tmp_path / "users"
    monkeypatch.setattr("core.auth.USERS_FILE", test_users_file)
    monkeypatch.setattr("core.auth.USERS_DIR", test_users_dir)
    active_sessions.clear()
    yield
    active_sessions.clear()


class TestUserRegistration:
    def test_create_user(self):
        user = create_user("alice", "Alice Smith", "secret123")
        assert user["display_name"] == "Alice Smith"
        assert "passcode_hash" in user

        # User directory created
        user_dir = get_user_data_dir("alice")
        assert user_dir.exists()
        assert (user_dir / "conversation_logs").exists()
        assert (user_dir / "knowledge_base").exists()

    def test_create_user_normalizes_username(self):
        create_user("BOB", "Bob", "pass1234")
        users = load_users()
        assert "bob" in users

    def test_duplicate_username_rejected(self):
        create_user("charlie", "Charlie", "pass1234")
        with pytest.raises(ValueError, match="already exists"):
            create_user("charlie", "Charlie 2", "pass5678")

    def test_short_passcode_rejected(self):
        with pytest.raises(ValueError, match="at least 4"):
            create_user("dave", "Dave", "abc")

    def test_non_alnum_username_rejected(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            create_user("bad-user", "Bad", "pass1234")


class TestUserVerification:
    def test_verify_correct_passcode(self):
        create_user("eve", "Eve", "mypasscode")
        assert verify_user("eve", "mypasscode") is True

    def test_verify_wrong_passcode(self):
        create_user("frank", "Frank", "correct")
        assert verify_user("frank", "wrong") is False

    def test_verify_nonexistent_user(self):
        assert verify_user("nobody", "anything") is False


class TestPasscodeChange:
    def test_change_passcode_success(self):
        create_user("grace", "Grace", "oldpass1")
        assert change_passcode("grace", "oldpass1", "newpass1") is True
        assert verify_user("grace", "newpass1") is True
        assert verify_user("grace", "oldpass1") is False

    def test_change_passcode_wrong_old(self):
        create_user("heidi", "Heidi", "mypass1")
        assert change_passcode("heidi", "wrongold", "newpass1") is False

    def test_change_passcode_too_short(self):
        create_user("ivan", "Ivan", "mypass1")
        with pytest.raises(ValueError, match="at least 4"):
            change_passcode("ivan", "mypass1", "ab")


class TestSessionTokens:
    def test_create_and_validate_session(self):
        create_user("judy", "Judy", "pass1234")
        token = create_session("judy")
        assert isinstance(token, str)
        assert len(token) > 20

        username = validate_token(token)
        assert username == "judy"

    def test_invalid_token(self):
        assert validate_token("bogus-token-123") is None

    def test_invalidate_token(self):
        create_user("karen", "Karen", "pass1234")
        token = create_session("karen")
        assert validate_token(token) == "karen"

        invalidate_token(token)
        assert validate_token(token) is None

    def test_generate_unique_tokens(self):
        t1 = generate_session_token()
        t2 = generate_session_token()
        assert t1 != t2


class TestUserPreferences:
    def test_load_save_preferences(self):
        create_user("leo", "Leo", "pass1234")
        save_user_preferences("leo", {"active_personality": "pike"})
        prefs = load_user_preferences("leo")
        assert prefs["active_personality"] == "pike"

    def test_empty_preferences(self):
        create_user("mel", "Mel", "pass1234")
        prefs = load_user_preferences("mel")
        assert prefs == {}
