"""Phase 4B tool-autocall tests (Task 1: catalog hints + config helper)."""


def test_catalog_entries_have_method_hints():
    from core.tooling import catalog
    fs = catalog.get_entry("filesystem")
    assert fs["method_hints"]["list_directory"] == "path=<dir>"
    assert "content=" in fs["method_hints"]["write_file"]
    t = catalog.get_entry("time")
    assert "timezone=" in t["method_hints"]["get_current_time"]


def test_autocall_enabled_default_true(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {})
    assert tooling._autocall_enabled() is True


def test_autocall_enabled_reads_config(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": False}})
    assert tooling._autocall_enabled() is False


def _install(monkeypatch, tool_ids):
    """Point registry.installed_ids at a fixed list for the tooling protocol."""
    from core.tooling import registry
    monkeypatch.setattr(registry, "installed_ids", lambda u: list(tool_ids))


def test_injection_lists_installed_tool_methods(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _install(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    out = p.process_input("hi", {})
    inj = out["context_injection"]
    assert "[TOOL:" in inj
    assert "filesystem.list_directory path=<dir>" in inj
    assert out["intercept"] is False


def test_injection_empty_when_toggle_off(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": False}})
    _install(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    assert p.process_input("hi", {})["context_injection"] == ""


def test_injection_empty_when_no_tools(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _install(monkeypatch, [])
    p = tooling.ToolingProtocol(username="switch")
    assert p.process_input("hi", {})["context_injection"] == ""
