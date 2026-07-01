# tests/llm/test_config.py
import json
import core.llm.config as cfgmod
from core.llm.config import RouterConfig, load_config


def test_defaults_are_local_only():
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
