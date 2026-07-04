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
    assert set(st.keys()) == {
        "cloud_enabled", "key_set", "cloud_model", "deep_mode",
        "cloud_trouble_escalation", "trouble_private_consent",
    }
    assert st["cloud_enabled"] is False
    assert st["key_set"] is False
    assert st["cloud_model"] == "claude-opus-4-8"


def test_set_api_key_writes_and_status_reports_set_without_value(tmp_path, monkeypatch):
    key_file = tmp_path / "anthropic_key"
    monkeypatch.setattr(cfgmod, "_KEY_FILE", key_file)
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", tmp_path / "none.json")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cs.set_api_key("  sk-secret-123  ")
    assert key_file.read_text(encoding="utf-8") == "sk-secret-123"  # trimmed

    st = cs.get_cloud_status()
    assert st["key_set"] is True
    assert "sk-secret-123" not in json.dumps(st)   # value never exposed


def test_set_api_key_blank_removes_file(tmp_path, monkeypatch):
    key_file = tmp_path / "anthropic_key"
    key_file.write_text("sk-old", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", key_file)
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", tmp_path / "none.json")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cs.set_api_key("")
    assert not key_file.exists()
    assert cs.get_cloud_status()["key_set"] is False


def test_set_api_key_blank_when_no_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "anthropic_key")
    cs.set_api_key("")  # must not raise


class _OKBackend:
    def chat(self, messages, **kw):
        return "pong"


class _RaisingBackend:
    def __init__(self, exc):
        self._exc = exc

    def chat(self, messages, **kw):
        raise self._exc


def test_test_cloud_key_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nokey")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cs, "CloudBackend", lambda: (_ for _ in ()).throw(AssertionError("should not construct")))
    assert cs.test_cloud_key() == {"ok": False, "error": "No API key set"}


def test_test_cloud_key_ok(tmp_path, monkeypatch):
    kf = tmp_path / "anthropic_key"; kf.write_text("sk-x", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", kf)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cs, "CloudBackend", _OKBackend)
    assert cs.test_cloud_key() == {"ok": True}


def test_test_cloud_key_auth_error_maps_to_rejected(tmp_path, monkeypatch):
    kf = tmp_path / "anthropic_key"; kf.write_text("sk-bad", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", kf)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cs, "CloudBackend",
                        lambda: _RaisingBackend(Exception("authentication_error: invalid x-api-key")))
    assert cs.test_cloud_key() == {"ok": False, "error": "Key rejected"}


def test_test_cloud_key_generic_error_passes_message(tmp_path, monkeypatch):
    kf = tmp_path / "anthropic_key"; kf.write_text("sk-x", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", kf)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cs, "CloudBackend",
                        lambda: _RaisingBackend(RuntimeError("something odd")))
    out = cs.test_cloud_key()
    assert out["ok"] is False and "something odd" in out["error"]


def test_deep_mode_defaults_false(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", tmp_path / "none.json")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nokey")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cfgmod.load_config().deep_mode is False
    assert cs.get_cloud_status()["deep_mode"] is False


def test_set_deep_mode_writes_and_preserves(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    override.write_text(json.dumps({"cloud_enabled": True}), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nokey")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cs.set_deep_mode(True)
    data = json.loads(override.read_text(encoding="utf-8"))
    assert data["deep_mode"] is True
    assert data["cloud_enabled"] is True   # not clobbered
    assert cfgmod.load_config().deep_mode is True

    cs.set_deep_mode(False)
    assert cfgmod.load_config().deep_mode is False


def test_set_and_report_trouble_flags(tmp_path, monkeypatch):
    import core.llm.config as cfgmod
    from core.llm import cloud_settings as cs
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", tmp_path / "llm_router.json")
    cs.set_trouble_escalation(True)
    cs.set_trouble_private_consent(False)
    status = cs.get_cloud_status()
    assert status["cloud_trouble_escalation"] is True
    assert status["trouble_private_consent"] is False


def test_friendly_error_redacts_key_token():
    # A stray key token in an unexpected error message must be scrubbed.
    err = Exception("weird failure involving sk-ant-api03-SECRETSECRET_tok in the body")
    out = cs._friendly_error(err)
    assert "sk-ant-" not in out
    assert "[REDACTED]" in out
