"""
Tests for server security hardening — loopback-only shutdown, login rate limiting.
"""

import sys
from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from core.auth import active_sessions, create_user, _failed_logins
from server.app import app, _is_loopback


class TestIsLoopback:
    def test_accepts_ipv4_loopback(self):
        assert _is_loopback("127.0.0.1") is True

    def test_accepts_ipv6_loopback(self):
        assert _is_loopback("::1") is True

    def test_accepts_localhost_name(self):
        assert _is_loopback("localhost") is True

    def test_rejects_lan_ip(self):
        assert _is_loopback("192.168.1.50") is False

    def test_rejects_none(self):
        assert _is_loopback(None) is False

    def test_rejects_empty(self):
        assert _is_loopback("") is False


class TestShutdownLoopbackOnly:
    def test_shutdown_rejected_from_non_loopback(self):
        # TestClient requests arrive with client host "testclient" — not loopback.
        client = TestClient(app)
        response = client.post("/api/shutdown")
        assert response.status_code == 403


class TestLoginLockoutEndpoint:
    @pytest.fixture(autouse=True)
    def clean_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.auth.USERS_FILE", tmp_path / "users.json")
        monkeypatch.setattr("core.auth.USERS_DIR", tmp_path / "users")
        monkeypatch.setattr("core.auth.SESSIONS_FILE", tmp_path / "sessions.json")
        active_sessions.clear()
        _failed_logins.clear()
        create_user("switch", "Switch", "test1234")
        yield
        active_sessions.clear()
        _failed_logins.clear()

    def test_lockout_after_repeated_failures(self):
        client = TestClient(app)
        for _ in range(5):
            r = client.post(
                "/api/auth/login",
                json={"username": "switch", "passcode": "wrong"},
            )
            assert r.json()["success"] is False

        # Locked out now — even the CORRECT passcode is rejected.
        r = client.post(
            "/api/auth/login",
            json={"username": "switch", "passcode": "test1234"},
        )
        body = r.json()
        assert body["success"] is False
        assert "try again" in body["error"].lower()

    def test_successful_login_clears_failure_counter(self):
        client = TestClient(app)
        for _ in range(4):
            client.post(
                "/api/auth/login",
                json={"username": "switch", "passcode": "wrong"},
            )
        r = client.post(
            "/api/auth/login",
            json={"username": "switch", "passcode": "test1234"},
        )
        assert r.json()["success"] is True

        # Counter was cleared — four fresh failures still don't lock.
        for _ in range(4):
            client.post(
                "/api/auth/login",
                json={"username": "switch", "passcode": "wrong"},
            )
        r = client.post(
            "/api/auth/login",
            json={"username": "switch", "passcode": "test1234"},
        )
        assert r.json()["success"] is True
