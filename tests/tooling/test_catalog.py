"""Catalog loader tests — uses the real shipped catalog.json."""


def test_catalog_has_time_and_filesystem():
    from core.tooling import catalog
    entries = catalog.all_entries()
    assert set(entries) >= {"time", "filesystem"}


def test_get_entry_fields():
    from core.tooling import catalog
    fs = catalog.get_entry("filesystem")
    assert fs["default_tier"] == "read_broad"
    assert fs["launch"]["command"] == "npx"
    assert fs["method_tiers"]["write_file"] == "write_destructive"
    assert "approved_dirs" in fs["config_fields"]
    t = catalog.get_entry("time")
    assert t["default_tier"] == "read_scoped"
    assert t["config_fields"] == []


def test_get_entry_missing_returns_none():
    from core.tooling import catalog
    assert catalog.get_entry("nope") is None


def test_search_matches_name_and_description():
    from core.tooling import catalog
    assert "filesystem" in catalog.search("file")
    assert "time" in catalog.search("timezone")
    assert catalog.search("zzzznothing") == []
