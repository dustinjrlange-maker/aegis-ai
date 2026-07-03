"""Endpoint tests via TestClient with require_user overridden."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    from core.tooling import registry, audit
    monkeypatch.setattr(registry, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_DATA_ROOT", tmp_path)

    from server.app import app, require_user
    app.dependency_overrides[require_user] = lambda: "switch"
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_get_catalog(client):
    resp = client.get("/api/tools/catalog")
    assert resp.status_code == 200
    assert "time" in resp.json()


def test_get_installed_empty(client):
    resp = client.get("/api/tools/installed")
    assert resp.status_code == 200
    assert resp.json() == []


def test_install_unknown_tool_reports(client):
    resp = client.post("/api/tools/install", json={"tool_id": "nope", "config": {}})
    assert resp.status_code == 200
    assert "catalog" in resp.json()["message"].lower()


def test_call_uninstalled_tool(client):
    resp = client.post("/api/tools/call",
                       json={"tool_id": "time", "method": "get_current_time", "args": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error" and "not installed" in body["message"]


def test_audit_empty(client):
    resp = client.get("/api/tools/audit")
    assert resp.status_code == 200
    assert resp.json() == []


def test_uninstall_not_installed_reports(client):
    resp = client.post("/api/tools/uninstall/nope")
    assert resp.status_code == 200
    assert "message" in resp.json()
    assert "isn't installed" in resp.json()["message"] or "not installed" in resp.json()["message"].lower()
