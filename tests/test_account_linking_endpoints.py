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
    mock_session.accounts.list.return_value = [
        {"id": "g1", "label": "Work", "email": "a@b.com",
         "status": "ok", "is_default": True, "token": "secret"}
    ]
    with patch("server.app.session_manager") as mock_sm:
        mock_sm.get_or_create.return_value = mock_session
        resp = client.get("/api/google/accounts")
    assert resp.status_code == 200
    body = resp.json()["accounts"]
    assert len(body) == 1
    assert body[0] == {"id": "g1", "label": "Work", "email": "a@b.com",
                       "status": "ok", "is_default": True}
    assert "token" not in body[0]


def test_callback_links_new_account(client, monkeypatch):
    import server.app as app_mod

    state = "teststate_link_123"
    app_mod._oauth_states[state] = {
        "user_id": "linktest_user",
        "pending": {"label": "SwitchStitch", "name": "Switch"},
    }
    monkeypatch.setattr("core.protocols.google_tools.exchange_code",
                        lambda code, redirect_uri: object())
    monkeypatch.setattr("core.protocols.google_tools.get_account_email",
                        lambda creds: "TheSwitchStitch@gmail.com")
    saved = {}
    monkeypatch.setattr("core.protocols.google_tools.save_credentials",
                        lambda d, creds, account_id=None: saved.update(
                            {"dir": str(d), "account_id": account_id}))

    captured_upsert = {}
    from core.accounts.manager import AccountManager
    monkeypatch.setattr(AccountManager, "upsert_account",
                        lambda self, label, email, represent_as=None: captured_upsert.update(
                            {"label": label, "email": email, "rep": represent_as}) or "google-switchstitch")

    resp = client.get(f"/api/google/callback?code=abc&state={state}")
    assert resp.status_code == 200
    assert saved["account_id"] == "google-switchstitch"
    assert captured_upsert["label"] == "SwitchStitch"
    assert captured_upsert["email"] == "TheSwitchStitch@gmail.com"
    assert captured_upsert["rep"] == {"name": "Switch"}
    assert state not in app_mod._oauth_states   # state consumed


def test_callback_blank_email_aborts_link(client, monkeypatch):
    import server.app as app_mod

    state = "teststate_blankemail_000"
    app_mod._oauth_states[state] = {
        "user_id": "linktest_user",
        "pending": {"label": "SwitchStitch", "name": "Switch"},
    }
    monkeypatch.setattr("core.protocols.google_tools.exchange_code",
                        lambda code, redirect_uri: object())
    # getProfile blip -> blank email; the link must abort, not persist junk.
    monkeypatch.setattr("core.protocols.google_tools.get_account_email",
                        lambda creds: "")

    upsert_called = []
    from core.accounts.manager import AccountManager
    monkeypatch.setattr(AccountManager, "upsert_account",
                        lambda self, label, email, represent_as=None: upsert_called.append(True) or "google-x")
    save_called = []
    monkeypatch.setattr("core.protocols.google_tools.save_credentials",
                        lambda d, creds, account_id=None: save_called.append(True))

    resp = client.get(f"/api/google/callback?code=abc&state={state}")
    assert resp.status_code == 200
    assert "Couldn't verify account" in resp.text
    assert upsert_called == []                   # no junk record created
    assert save_called == []                     # no tokens saved
    assert state not in app_mod._oauth_states    # state still consumed


def test_callback_default_connect_still_saves_to_default(client, monkeypatch):
    import server.app as app_mod
    state = "teststate_default_456"
    app_mod._oauth_states[state] = {"user_id": "linktest_user"}   # no pending
    monkeypatch.setattr("core.protocols.google_tools.exchange_code",
                        lambda code, redirect_uri: object())
    saved = {}
    monkeypatch.setattr("core.protocols.google_tools.save_credentials",
                        lambda d, creds, account_id=None: saved.update({"account_id": account_id}))

    resp = client.get(f"/api/google/callback?code=abc&state={state}")
    assert resp.status_code == 200
    assert saved["account_id"] is None   # default connect -> no account_id


def test_callback_save_failure_marks_account_error(client, monkeypatch):
    import server.app as app_mod

    state = "teststate_savefail_789"
    app_mod._oauth_states[state] = {
        "user_id": "linktest_user",
        "pending": {"label": "SwitchStitch", "name": "Switch"},
    }
    monkeypatch.setattr("core.protocols.google_tools.exchange_code",
                        lambda code, redirect_uri: object())
    monkeypatch.setattr("core.protocols.google_tools.get_account_email",
                        lambda creds: "TheSwitchStitch@gmail.com")

    def _raise_save(d, creds, account_id=None):
        raise OSError("disk full")
    monkeypatch.setattr("core.protocols.google_tools.save_credentials", _raise_save)

    from core.accounts.manager import AccountManager
    monkeypatch.setattr(AccountManager, "upsert_account",
                        lambda self, label, email, represent_as=None: "google-x")
    status_calls = []
    monkeypatch.setattr(AccountManager, "set_status",
                        lambda self, account_id, status: status_calls.append((account_id, status)))

    resp = client.get(f"/api/google/callback?code=abc&state={state}")
    assert resp.status_code == 200
    assert ("google-x", "error") in status_calls
    assert "Could not save credentials" in resp.text
