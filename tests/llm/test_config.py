# tests/llm/test_config.py
import json
import core.llm.config as cfgmod
from core.llm.config import RouterConfig, load_config


def test_defaults_are_local_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", tmp_path / "nonexistent.json")
    cfg = load_config()
    assert cfg.cloud_enabled is False
    assert cfg.cloud_opt_in_features == ()


def test_override_file_flips_toggle(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    override.write_text(json.dumps(
        {"cloud_enabled": True, "cloud_opt_in_features": ["summarize"]}
    ), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)
    cfg = load_config()
    assert cfg.cloud_enabled is True
    assert cfg.cloud_opt_in_features == ("summarize",)


def test_corrupt_override_falls_back_to_defaults(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    override.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)
    cfg = load_config()  # must not raise
    assert cfg.cloud_enabled is False
    assert cfg.cloud_opt_in_features == ()


def test_cloud_model_and_tokens_load_from_defaults():
    cfg = load_config()
    assert cfg.cloud_model == "claude-opus-4-8"
    assert cfg.cloud_max_tokens == 2048


def test_cloud_model_and_tokens_override(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    override.write_text(json.dumps(
        {"cloud_model": "claude-sonnet-4-6", "cloud_max_tokens": 512}
    ), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)
    cfg = load_config()
    assert cfg.cloud_model == "claude-sonnet-4-6"
    assert cfg.cloud_max_tokens == 512


def test_resolve_api_key_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "anthropic_key")
    assert cfgmod.resolve_api_key() == "sk-env"


def test_resolve_api_key_falls_back_to_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    key_file = tmp_path / "anthropic_key"
    key_file.write_text("  sk-file\n", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", key_file)
    assert cfgmod.resolve_api_key() == "sk-file"  # trimmed


def test_resolve_api_key_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nope")
    assert cfgmod.resolve_api_key() is None


def test_resolve_api_key_blank_env_falls_through(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nope")
    assert cfgmod.resolve_api_key() is None


def test_trouble_flags_default_off(tmp_path, monkeypatch):
    import core.llm.config as cfgmod
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", tmp_path / "llm_router.json")
    cfg = cfgmod.load_config()
    assert cfg.cloud_trouble_escalation is False
    assert cfg.trouble_private_consent is True


def test_trouble_flags_load_from_override(tmp_path, monkeypatch):
    import json, core.llm.config as cfgmod
    p = tmp_path / "llm_router.json"
    p.write_text(json.dumps({"cloud_trouble_escalation": True,
                             "trouble_private_consent": False}), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", p)
    cfg = cfgmod.load_config()
    assert cfg.cloud_trouble_escalation is True
    assert cfg.trouble_private_consent is False
