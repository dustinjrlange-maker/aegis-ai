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
