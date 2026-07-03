"""MCPManager unit tests — fake session, no real MCP subprocess."""
import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


class FakeSession:
    """Echoes calls; supports list_tools; can be primed to fail."""
    def __init__(self, fail_method=None):
        self.fail_method = fail_method
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == self.fail_method:
            return SimpleNamespace(content=[SimpleNamespace(text="boom")], isError=True)
        return SimpleNamespace(
            content=[SimpleNamespace(text=f"{name}:{arguments}")], isError=False
        )

    async def list_tools(self):
        return SimpleNamespace(tools=[
            SimpleNamespace(name="echo", description="Echo tool", inputSchema={"type": "object"})
        ])


def _patched_manager(monkeypatch, session=None, spawn_error=None):
    from core.tooling.mcp_manager import MCPManager
    mgr = MCPManager()
    sess = session or FakeSession()

    @asynccontextmanager
    async def fake_open(self, command, args, env):
        if spawn_error:
            raise RuntimeError(spawn_error)
        yield sess

    monkeypatch.setattr(MCPManager, "_open_session", fake_open)
    return mgr, sess


def test_start_call_returns_payload(monkeypatch):
    mgr, sess = _patched_manager(monkeypatch)
    mgr.ensure_started("u", "echo", "cmd", [], timeout=5)
    out = mgr.call("u", "echo", "hello", {"a": 1}, timeout=5)
    assert out == ["hello:{'a': 1}"]
    mgr.shutdown()


def test_list_tools_normalized(monkeypatch):
    mgr, _ = _patched_manager(monkeypatch)
    mgr.ensure_started("u", "echo", "cmd", [], timeout=5)
    tools = mgr.list_tools("u", "echo", timeout=5)
    assert tools == [{"name": "echo", "description": "Echo tool",
                      "input_schema": {"type": "object"}}]
    mgr.shutdown()


def test_is_error_result_raises(monkeypatch):
    mgr, _ = _patched_manager(monkeypatch, session=FakeSession(fail_method="bad"))
    mgr.ensure_started("u", "echo", "cmd", [], timeout=5)
    with pytest.raises(RuntimeError, match="boom"):
        mgr.call("u", "echo", "bad", {}, timeout=5)
    mgr.shutdown()


def test_spawn_failure_raises_with_reason(monkeypatch):
    mgr, _ = _patched_manager(monkeypatch, spawn_error="npx not found")
    with pytest.raises(RuntimeError, match="npx not found"):
        mgr.ensure_started("u", "fs", "cmd", [], timeout=5)
    mgr.shutdown()


def test_call_without_start_raises(monkeypatch):
    mgr, _ = _patched_manager(monkeypatch)
    with pytest.raises(RuntimeError, match="not running"):
        mgr.call("u", "never", "x", {}, timeout=2)
    mgr.shutdown()


def test_sessions_keyed_per_user(monkeypatch):
    """Two users of the same tool get independent sessions."""
    from core.tooling.mcp_manager import MCPManager
    mgr = MCPManager()
    sessions = []

    @asynccontextmanager
    async def fake_open(self, command, args, env):
        s = FakeSession()
        sessions.append(s)
        yield s

    monkeypatch.setattr(MCPManager, "_open_session", fake_open)
    mgr.ensure_started("alice", "fs", "cmd", [], timeout=5)
    mgr.ensure_started("bob", "fs", "cmd", [], timeout=5)
    mgr.call("alice", "fs", "ping", {}, timeout=5)
    assert len(sessions) == 2
    assert sessions[0].calls and not sessions[1].calls
    mgr.shutdown()


def test_concurrent_calls_from_threads(monkeypatch):
    mgr, _ = _patched_manager(monkeypatch)
    mgr.ensure_started("u", "echo", "cmd", [], timeout=5)
    results, errors = [], []

    def worker(i):
        try:
            results.append(mgr.call("u", "echo", f"m{i}", {}, timeout=5))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors and len(results) == 8
    mgr.shutdown()


def test_stop_then_call_raises(monkeypatch):
    mgr, _ = _patched_manager(monkeypatch)
    mgr.ensure_started("u", "echo", "cmd", [], timeout=5)
    mgr.stop("u", "echo")
    with pytest.raises(RuntimeError, match="not running"):
        mgr.call("u", "echo", "x", {}, timeout=2)
    mgr.shutdown()


def test_double_shutdown_is_safe(monkeypatch):
    mgr, _ = _patched_manager(monkeypatch)
    mgr.ensure_started("u", "echo", "cmd", [], timeout=5)
    mgr.shutdown()
    mgr.shutdown()  # must not raise
