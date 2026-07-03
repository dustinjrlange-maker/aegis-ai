"""Integration tests — REAL MCP subprocesses. Skipped when runtimes absent.
The npx test may take ~60s cold (package download)."""
import importlib.util
import shutil

import pytest

_HAS_TIME = importlib.util.find_spec("mcp_server_time") is not None
_HAS_NPX = shutil.which("npx") is not None


@pytest.mark.skipif(not _HAS_TIME, reason="mcp-server-time not installed")
def test_real_time_server_roundtrip():
    import sys
    from core.tooling.mcp_manager import MCPManager

    mgr = MCPManager()
    try:
        mgr.ensure_started("itest", "time", sys.executable,
                           ["-m", "mcp_server_time"], timeout=60)
        tools = mgr.list_tools("itest", "time", timeout=30)
        names = {t["name"] for t in tools}
        assert "get_current_time" in names
        out = mgr.call("itest", "time", "get_current_time",
                       {"timezone": "America/Vancouver"}, timeout=30)
        assert out and any(":" in text for text in out)   # contains a time
    finally:
        mgr.shutdown()


@pytest.mark.skipif(not _HAS_NPX, reason="npx not available")
def test_real_filesystem_server_lists_seeded_file(tmp_path):
    from core.tooling.mcp_manager import MCPManager

    (tmp_path / "hello.txt").write_text("hi", encoding="utf-8")
    mgr = MCPManager()
    try:
        mgr.ensure_started("itest", "filesystem", shutil.which("npx"),
                           ["-y", "@modelcontextprotocol/server-filesystem",
                            str(tmp_path)], timeout=120)
        out = mgr.call("itest", "filesystem", "list_directory",
                       {"path": str(tmp_path)}, timeout=60)
        assert any("hello.txt" in text for text in out)
    finally:
        mgr.shutdown()
