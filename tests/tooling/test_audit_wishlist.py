"""Audit JSONL + wishlist append tests."""
import json


def test_audit_appends_jsonl(tmp_path, monkeypatch):
    from core.tooling import audit
    monkeypatch.setattr(audit, "_DATA_ROOT", tmp_path)
    audit.log("switch", "time", "get_current_time", {"timezone": "UTC"}, "ok", 12)
    audit.log("switch", "filesystem", "write_file", {"path": "x"}, "denied", 0)
    lines = (tmp_path / "switch" / "mcp_tools" / "audit.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first, second = json.loads(lines[0]), json.loads(lines[1])
    assert first["outcome"] == "ok" and first["duration_ms"] == 12
    assert second["outcome"] == "denied"          # denials logged too


def test_audit_read_recent(tmp_path, monkeypatch):
    from core.tooling import audit
    monkeypatch.setattr(audit, "_DATA_ROOT", tmp_path)
    for i in range(5):
        audit.log("switch", "time", f"m{i}", {}, "ok", i)
    recent = audit.recent("switch", limit=3)
    assert len(recent) == 3
    assert recent[-1]["method"] == "m4"           # newest last


def test_wishlist_appends_with_timestamp(tmp_path, monkeypatch):
    import core.config
    from core.tooling import wishlist
    wl = tmp_path / "wish.md"
    monkeypatch.setattr(core.config, "CONFIG",
                        {"tooling": {"wishlist_path": str(wl)}})
    wishlist.add("switch", "batch photo renaming")
    wishlist.add("switch", "PDF splitting")
    text = wl.read_text(encoding="utf-8")
    assert "batch photo renaming" in text and "PDF splitting" in text
    assert "switch" in text


def test_wishlist_default_path_under_data(monkeypatch):
    import core.config
    from core.config import PROJECT_ROOT
    from core.tooling import wishlist
    monkeypatch.setattr(core.config, "CONFIG", {})
    assert wishlist._wishlist_path() == PROJECT_ROOT / "data" / "tool_wishlist.md"
