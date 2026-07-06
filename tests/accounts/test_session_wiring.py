"""
Task 5 — Wave 3.5: verify UserSession exposes an AccountManager as .accounts.
"""

import sys
from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.auth import create_user, active_sessions, USERS_FILE, USERS_DIR
from core.session import SessionManager
from core.accounts.manager import AccountManager


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Redirect auth and data to temp directory (mirrors tests/test_session.py)."""
    test_users_file = tmp_path / "users.json"
    test_users_dir = tmp_path / "users"
    monkeypatch.setattr("core.auth.USERS_FILE", test_users_file)
    monkeypatch.setattr("core.auth.USERS_DIR", test_users_dir)
    monkeypatch.setattr("core.memory.manager.PROJECT_ROOT", tmp_path)
    active_sessions.clear()
    yield
    active_sessions.clear()


@pytest.fixture
def test_user(tmp_path):
    """Create a test user with required subdirs (mirrors tests/test_session.py)."""
    create_user("testuser", "Test User", "pass1234")
    user_dir = tmp_path / "data" / "users" / "testuser"
    user_dir.mkdir(parents=True, exist_ok=True)
    for sub in ["conversation_logs", "session_journals", "knowledge_base", "security_protocols"]:
        (user_dir / sub).mkdir(exist_ok=True)
    return "testuser"


def test_user_session_exposes_account_manager(test_user):
    sm = SessionManager()
    session = sm.get_or_create(test_user)
    assert isinstance(session.accounts, AccountManager)


def test_session_wires_fetch_unread_emails(monkeypatch, test_user):
    """UserSession must expose a callable fetch_unread_emails that delegates
    to fetch_unread_all_accounts (Wave 3.5 Task 8 seam)."""
    sentinel = object()
    monkeypatch.setattr(
        "core.accounts.inbox.fetch_unread_all_accounts",
        lambda s: sentinel,
    )
    sm = SessionManager()
    session = sm.get_or_create(test_user)
    assert callable(session.fetch_unread_emails)
    assert session.fetch_unread_emails() is sentinel
