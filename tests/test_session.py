"""
Tests for core.session — UserSession lifecycle and SessionManager.
"""

import sys
from pathlib import Path
import json
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.auth import create_user, active_sessions, USERS_FILE, USERS_DIR
from core.session import SessionManager, UserSession


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Redirect auth and data to temp directory."""
    test_users_file = tmp_path / "users.json"
    test_users_dir = tmp_path / "users"
    monkeypatch.setattr("core.auth.USERS_FILE", test_users_file)
    monkeypatch.setattr("core.auth.USERS_DIR", test_users_dir)

    # Redirect PROJECT_ROOT for session/memory to use temp
    monkeypatch.setattr("core.memory.manager.PROJECT_ROOT", tmp_path)

    active_sessions.clear()
    yield
    active_sessions.clear()


@pytest.fixture
def test_user(tmp_path):
    """Create a test user and their data directory."""
    create_user("testuser", "Test User", "pass1234")
    # Create the user data dir in the temp PROJECT_ROOT location
    user_dir = tmp_path / "data" / "users" / "testuser"
    user_dir.mkdir(parents=True, exist_ok=True)
    for sub in ["conversation_logs", "session_journals", "knowledge_base", "security_protocols"]:
        (user_dir / sub).mkdir(exist_ok=True)
    return "testuser"


class TestSessionManager:
    def test_get_or_create(self, test_user):
        sm = SessionManager()
        session = sm.get_or_create(test_user)
        assert isinstance(session, UserSession)
        assert session.user_id == test_user
        assert session.agent_name is not None

    def test_same_session_returned(self, test_user):
        sm = SessionManager()
        s1 = sm.get_or_create(test_user)
        s2 = sm.get_or_create(test_user)
        assert s1 is s2

    def test_end_session(self, test_user):
        sm = SessionManager()
        sm.get_or_create(test_user)
        assert test_user in sm.active_users()

        sm.end_session(test_user)
        assert test_user not in sm.active_users()

    def test_end_all(self, test_user, tmp_path):
        # Create a second user
        create_user("user2", "User Two", "pass5678")
        user2_dir = tmp_path / "data" / "users" / "user2"
        user2_dir.mkdir(parents=True, exist_ok=True)
        for sub in ["conversation_logs", "session_journals", "knowledge_base", "security_protocols"]:
            (user2_dir / sub).mkdir(exist_ok=True)

        sm = SessionManager()
        sm.get_or_create(test_user)
        sm.get_or_create("user2")
        assert len(sm.active_users()) == 2

        sm.end_all()
        assert len(sm.active_users()) == 0

    def test_session_has_protocol_registry(self, test_user):
        sm = SessionManager()
        session = sm.get_or_create(test_user)
        proto_names = session.protocol_registry.list_protocols()
        assert "communications" in proto_names
        assert "security" in proto_names
        assert "operations" in proto_names


class TestUserSession:
    def test_session_creates_with_messages(self, test_user):
        sm = SessionManager()
        session = sm.get_or_create(test_user)
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "system"

    def test_session_not_ended_initially(self, test_user):
        sm = SessionManager()
        session = sm.get_or_create(test_user)
        assert session.session_ended is False

    def test_session_end_marks_ended(self, test_user):
        sm = SessionManager()
        session = sm.get_or_create(test_user)
        session.end()
        assert session.session_ended is True

    def test_session_double_end_safe(self, test_user):
        sm = SessionManager()
        session = sm.get_or_create(test_user)
        session.end()
        session.end()  # Should not raise
        assert session.session_ended is True
