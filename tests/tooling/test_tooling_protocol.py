"""ToolingProtocol slash-command tests — service layer mocked."""
import pytest


@pytest.fixture
def proto(monkeypatch):
    from core.protocols import tooling as tooling_mod
    p = tooling_mod.ToolingProtocol(username="switch")

    calls = {}
    monkeypatch.setattr(tooling_mod.service, "install_tool",
                        lambda u, t, c: calls.setdefault("install", (u, t, c)) or "installed-msg")
    monkeypatch.setattr(tooling_mod.service, "uninstall_tool",
                        lambda u, t: "uninstalled-msg")
    monkeypatch.setattr(tooling_mod.service, "call_tool",
                        lambda u, t, m, a: calls.update({"call": (t, m, a)}) or
                        {"status": "ok", "result": ["r1", "r2"]})
    monkeypatch.setattr(tooling_mod.service, "confirm_pending",
                        lambda u, p: calls.update({"pin": p}) or
                        {"status": "ok", "result": ["done"]})
    monkeypatch.setattr(tooling_mod.service, "installed_summary",
                        lambda u: [{"tool_id": "time", "trust_tier": "read_scoped",
                                    "running": True, "call_count": 3}])
    monkeypatch.setattr(tooling_mod.wishlist, "add",
                        lambda u, d: calls.setdefault("wish", d) or "path")
    return p, calls


def test_protocol_shape(proto):
    p, _ = proto
    result = p.process_input("hello", {})
    assert result["intercept"] is False
    out = p.process_output("resp", {})
    assert out["response"] == "resp" and out["suppress"] is False
    cmds = p.get_commands()
    assert any(c["command"] == "tools" for c in cmds)


def test_tools_list(proto):
    p, _ = proto
    reply = p.cmd_tools("list")
    assert "time" in reply and "read_scoped" in reply


def test_tools_find(proto):
    p, _ = proto
    reply = p.cmd_tools("find file")
    assert "filesystem" in reply          # real catalog search


def test_tools_install_parses_config(proto):
    p, calls = proto
    p.cmd_tools("install filesystem approved_dirs=C:/a,C:/b")
    u, t, c = calls["install"]
    assert t == "filesystem" and c == {"approved_dirs": ["C:/a", "C:/b"]}


def test_tools_call_parses_kv_args(proto):
    p, calls = proto
    reply = p.cmd_tools("call time get_current_time timezone=UTC")
    assert calls["call"] == ("time", "get_current_time", {"timezone": "UTC"})
    assert "r1" in reply


def test_tools_wish(proto):
    p, calls = proto
    reply = p.cmd_tools("wish batch photo renaming")
    assert calls["wish"] == "batch photo renaming"
    assert "wishlist" in reply.lower()


def test_tools_pin_redacted(proto, caplog):
    import logging
    p, calls = proto
    with caplog.at_level(logging.INFO):   # capture INFO or the check is vacuous
        reply = p.cmd_tools("pin 123456")
    assert calls["pin"] == "123456"       # service gets the real PIN
    assert "123456" not in caplog.text    # but it is never logged
    assert "****" in caplog.text          # redacted marker was logged instead
    assert "done" in reply


def test_parse_kv_empty_value_yields_empty_list(proto):
    p, _ = proto
    assert p._parse_kv(["approved_dirs="], split_commas=True) == {"approved_dirs": []}
    assert p._parse_kv(["approved_dirs=,"], split_commas=True) == {"approved_dirs": []}
    assert p._parse_kv(["approved_dirs=C:/a"], split_commas=True) == {"approved_dirs": ["C:/a"]}
    assert p._parse_kv(["tz=UTC"], split_commas=False) == {"tz": "UTC"}   # unchanged for call args


def test_tools_help_on_unknown(proto):
    p, _ = proto
    reply = p.cmd_tools("bogus")
    assert "/tools list" in reply
