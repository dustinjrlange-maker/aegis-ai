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


def _reg_installed(monkeypatch, installed_ids):
    from core.tooling import registry
    monkeypatch.setattr(registry, "get",
                        lambda u, t: {"trust_tier": "read_broad"} if t in installed_ids else None)


def test_parse_stashes_structured_call_and_strips(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    out = p.process_output("Let me check. [TOOL: filesystem.list_directory path=C:/x]", {})
    assert "[TOOL:" not in out["response"]
    calls = p.get_pending_tool_calls()
    assert calls == [{"tool_id": "filesystem", "method": "list_directory",
                      "args": {"path": "C:/x"}}]
    assert p.get_rejections() == []


def test_parse_rejects_uninstalled_tool(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, [])                      # nothing installed
    p = tooling.ToolingProtocol(username="switch")
    p.process_output("[TOOL: filesystem.read_file path=x]", {})
    assert p.get_pending_tool_calls() == []
    assert p.get_rejections() == ["filesystem.read_file"]


def test_parse_rejects_unknown_method(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    p.process_output("[TOOL: filesystem.teleport path=x]", {})
    assert p.get_pending_tool_calls() == []
    assert p.get_rejections() == ["filesystem.teleport"]


def test_parse_ignores_non_tool_brackets(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    out = p.process_output("Sure. [REMEMBER: milk]", {})
    assert out["response"] == "Sure. [REMEMBER: milk]"   # untouched
    assert p.get_pending_tool_calls() == []


def test_parse_noop_when_toggle_off(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": False}})
    _reg_installed(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    out = p.process_output("[TOOL: filesystem.list_directory path=x]", {})
    assert out["response"] == "[TOOL: filesystem.list_directory path=x]"   # left intact
    assert p.get_pending_tool_calls() == []


def test_parse_survives_corrupt_catalog(monkeypatch):
    """A broken catalog must not raise through process_output."""
    import core.config
    from core.protocols import tooling
    from core.tooling import catalog
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, ["filesystem"])
    monkeypatch.setattr(catalog, "get_entry", lambda t: (_ for _ in ()).throw(OSError("locked")))
    p = tooling.ToolingProtocol(username="switch")
    # must not raise; a call it can't validate is treated as a rejection
    out = p.process_output("[TOOL: filesystem.read_file path=x]", {})
    assert "[TOOL:" not in out["response"]
    assert p.get_pending_tool_calls() == []


def test_parse_rejects_hyphenated_unknown_tool(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    p.process_output("[TOOL: some-server.do_thing x=1]", {})   # matches regex now, not installed
    assert p.get_pending_tool_calls() == []
    assert p.get_rejections() == ["some-server.do_thing"]
