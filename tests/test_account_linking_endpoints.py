"""Tests for account-linking endpoints: /api/google/accounts/add and /api/google/accounts."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from server.app import app, require_user
    app.dependency_overrides[require_user] = lambda: "switch"
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_add_account_returns_auth_url_and_uses_select_account(client, monkeypatch):
    import server.app as app_mod
    captured = {}

    def fake_build(redirect_uri, state=None, prompt="consent"):
        captured["prompt"] = prompt
        captured["state"] = state
        return "https://accounts.google.com/o/oauth2/auth?fake=1"

    monkeypatch.setattr("core.protocols.google_tools.build_auth_url", fake_build)
    monkeypatch.setattr("integrations.google_config.is_enabled", lambda: True)

    resp = client.post("/api/google/accounts/add", json={"label": "SwitchStitch", "name": "Switch"})
    assert resp.status_code == 200
    assert resp.json()["auth_url"].startswith("https://accounts.google.com")
    assert captured["prompt"] == "select_account consent"
    assert app_mod._oauth_states[captured["state"]]["pending"] == {"label": "SwitchStitch", "name": "Switch"}


def test_add_account_rejects_empty_label(client, monkeypatch):
    monkeypatch.setattr("integrations.google_config.is_enabled", lambda: True)
    resp = client.post("/api/google/accounts/add", json={"label": "  ", "name": "Switch"})
    assert resp.status_code == 400


def test_list_accounts_returns_metadata(client):
    mock_session = MagicMock()
    mock_session.accounts.list.return_value = []
    with patch("server.app.session_manager") as mock_sm:
        mock_sm.get_or_create.return_value = mock_session
        resp = client.get("/api/google/accounts")
    assert resp.status_code == 200
    body = resp.json()["accounts"]
    assert all(set(a.keys()) == {"id", "label", "email", "status", "is_default"} for a in body)
