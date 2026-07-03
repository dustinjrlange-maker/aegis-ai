"""Registry CRUD tests against a temp data dir."""
import pytest


@pytest.fixture
def reg(tmp_path, monkeypatch):
    from core.tooling import registry
    monkeypatch.setattr(registry, "_DATA_ROOT", tmp_path)
    return registry


def test_install_and_load_roundtrip(reg):
    reg.install("switch", "time", trust_tier="read_scoped", config={})
    entry = reg.get("switch", "time")
    assert entry["trust_tier"] == "read_scoped"
    assert entry["call_count"] == 0
    assert "installed" in entry


def test_installed_ids_and_uninstall(reg):
    reg.install("switch", "time", "read_scoped", {})
    reg.install("switch", "filesystem", "read_broad", {"approved_dirs": ["C:/x"]})
    assert set(reg.installed_ids("switch")) == {"time", "filesystem"}
    assert reg.uninstall("switch", "time") is True
    assert reg.installed_ids("switch") == ["filesystem"]
    assert reg.uninstall("switch", "time") is False


def test_get_missing_returns_none(reg):
    assert reg.get("switch", "nope") is None


def test_touch_updates_usage(reg):
    reg.install("switch", "time", "read_scoped", {})
    reg.touch("switch", "time")
    entry = reg.get("switch", "time")
    assert entry["call_count"] == 1
    assert entry["last_used"] is not None


def test_users_are_isolated(reg):
    reg.install("alice", "time", "read_scoped", {})
    assert reg.get("bob", "time") is None
