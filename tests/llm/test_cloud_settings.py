# tests/llm/test_cloud_settings.py
import json
import core.llm.config as cfgmod
import core.llm.cloud_settings as cs


def test_set_cloud_enabled_writes_and_reads_back(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nokey")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cs.set_cloud_enabled(True)
    assert json.loads(override.read_text(encoding="utf-8"))["cloud_enabled"] is True
    assert cs.get_cloud_status()["cloud_enabled"] is True

    cs.set_cloud_enabled(False)
    assert cs.get_cloud_status()["cloud_enabled"] is False


def test_set_cloud_enabled_preserves_other_keys(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    override.write_text(json.dumps({
        "cloud_enabled": False,
        "cloud_opt_in_features": ["summarize"],
        "cloud_model": "claude-sonnet-4-6",
    }), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)

    cs.set_cloud_enabled(True)
    data = json.loads(override.read_text(encoding="utf-8"))
    assert data["cloud_enabled"] is True
    assert data["cloud_opt_in_features"] == ["summarize"]
    assert data["cloud_model"] == "claude-sonnet-4-6"


def test_set_cloud_enabled_on_corrupt_file_starts_fresh(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    override.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)
    cs.set_cloud_enabled(True)  # must not raise
    assert json.loads(override.read_text(encoding="utf-8"))["cloud_enabled"] is True


def test_get_cloud_status_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", tmp_path / "none.json")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nokey")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    st = cs.get_cloud_status()
    assert set(st.keys()) == {"cloud_enabled", "key_set", "cloud_model"}
    assert st["cloud_enabled"] is False
    assert st["key_set"] is False
    assert st["cloud_model"] == "claude-opus-4-8"
