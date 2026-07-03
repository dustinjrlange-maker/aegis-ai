# Tool Discovery Phase 4A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Real MCP tool plumbing — browse a curated catalog, install, trust-classify, and manually call `time` and `filesystem` MCP servers via `/tools` slash commands and `/api/tools/*` endpoints, with 4-tier trust + PIN escalation, audit log, and wishlist.

**Architecture:** `MCPManager` runs all MCP stdio sessions on a dedicated asyncio loop in a background thread, one long-running task per server holding the SDK's async contexts open (they must enter/exit in the same task), serviced via a queue/future handshake; callers get a synchronous API. Catalog/registry/trust/audit/wishlist are small focused modules; a `service.py` layer composes them so the chat protocol and HTTP endpoints share one flow.

**Tech Stack:** Python 3.12, `mcp` SDK 1.27.1 (installed), `mcp-server-time` (pip, to install), `@modelcontextprotocol/server-filesystem` (via npx -y; Node v24 present), FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-07-03-tool-discovery-4a-design.md`

**Grounded facts (verified this session):**
- Slash-command dispatch: `registry.handle_command(cmd, args)` → finds `get_commands()` entry → calls `handler(args)` → returns `(True, str)`. Happens in `server/chat_pipeline.py` line ~60, BEFORE `session.messages.append` — the PIN never enters history on this path.
- Protocols registered per-session in `core/session.py` (~line 96–142, has `user_id`) and console path `core/agent.py` (~line 149–156).
- Endpoint auth: `user_id: str = Depends(require_user)`.
- PIN: `core.vault_pin.verify_vault_pin(username, pin)`, `has_vault_pin(username)`.
- `python -c "from mcp import ClientSession, StdioServerParameters; from mcp.client.stdio import stdio_client"` → OK.
- `mcp_server_time` NOT installed yet. `shutil.which("npx")` resolves (`npx.cmd`).

**Design deviation from spec (documented):** adds `core/tooling/service.py` — shared install/call/confirm flows used by both the protocol and the endpoints (DRY; spec's module table implied this logic lived in the protocol, which would have duplicated it in `app.py`).

---

## File Structure

- Create: `core/tooling/__init__.py` (empty), `mcp_manager.py`, `catalog.py`, `catalog.json`, `registry.py`, `trust.py`, `audit.py`, `wishlist.py`, `service.py`
- Create: `core/protocols/tooling.py`
- Modify: `core/session.py` (register protocol), `core/agent.py` (register protocol), `server/app.py` (endpoints + lifespan shutdown), `core/config/core_config.json` (`tooling` block), `requirements.txt`
- Test: `tests/tooling/__init__.py` (empty), `test_mcp_manager.py`, `test_catalog.py`, `test_registry.py`, `test_trust.py`, `test_audit_wishlist.py`, `test_service.py`, `test_tooling_protocol.py`, `test_tools_endpoints.py`, `test_mcp_integration.py`

---

## Task 1: MCPManager (dedicated loop + per-server task + queue/future handshake)

**Files:**
- Create: `core/tooling/__init__.py`, `core/tooling/mcp_manager.py`
- Test: `tests/tooling/__init__.py`, `tests/tooling/test_mcp_manager.py`

- [ ] **Step 1: Create empty `core/tooling/__init__.py` and `tests/tooling/__init__.py`**

- [ ] **Step 2: Write the failing tests**

Create `tests/tooling/test_mcp_manager.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/tooling/test_mcp_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.tooling.mcp_manager'`

- [ ] **Step 4: Implement `core/tooling/mcp_manager.py`**

```python
"""
MCP Manager — runs MCP stdio servers on a dedicated asyncio loop.

The ONLY module that imports the mcp SDK. Each live server gets a dedicated
long-running task that enters the SDK's async contexts (stdio_client +
ClientSession), services requests from a queue, and exits the contexts in
that same task — anyio requires same-task enter/exit, so no other shape works.
Sessions are keyed (username, tool_id): per-user config (e.g. filesystem
approved dirs) is baked into spawn args and must not leak across users.
"""

import asyncio
import concurrent.futures
import logging
import threading
from contextlib import asynccontextmanager

logger = logging.getLogger("aegis.tooling.mcp")

CALL_TIMEOUT = 10.0
SPAWN_TIMEOUT = 60.0  # npx cold-start downloads the package
_STOP = object()
_LIST_TOOLS = "__list_tools__"


class _ServerHandle:
    """State for one live server task."""
    def __init__(self):
        self.queue = None                  # asyncio.Queue, created on manager loop
        self.ready = threading.Event()     # set once session is up OR task died
        self.error = None                  # failure reason if task died
        self.task = None


class MCPManager:
    """Sync facade over MCP stdio sessions living on a private event loop."""

    def __init__(self):
        self._loop = None
        self._thread = None
        self._servers = {}                 # (username, tool_id) -> _ServerHandle
        self._lock = threading.Lock()

    # --- loop management ---

    def _ensure_loop(self):
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever, daemon=True, name="mcp-manager"
            )
            self._thread.start()

    # --- session opening (test seam) ---

    @asynccontextmanager
    async def _open_session(self, command, args, env):
        """Open a live, initialized MCP session. Monkeypatched in unit tests."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=command, args=args, env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    # --- the per-server task ---

    async def _server_task(self, handle, command, args, env):
        try:
            async with self._open_session(command, args, env) as session:
                handle.queue = asyncio.Queue()
                handle.ready.set()
                while True:
                    req = await handle.queue.get()
                    if req is _STOP:
                        return
                    method, arguments, timeout, fut = req
                    try:
                        if method == _LIST_TOOLS:
                            result = await asyncio.wait_for(session.list_tools(), timeout)
                            payload = [
                                {"name": t.name,
                                 "description": t.description or "",
                                 "input_schema": t.inputSchema}
                                for t in result.tools
                            ]
                        else:
                            result = await asyncio.wait_for(
                                session.call_tool(method, arguments or {}), timeout
                            )
                            texts = [c.text for c in result.content
                                     if getattr(c, "text", None)]
                            if getattr(result, "isError", False):
                                raise RuntimeError("; ".join(texts) or f"{method} failed")
                            payload = texts
                        if not fut.cancelled():
                            fut.set_result(payload)
                    except Exception as e:
                        if not fut.cancelled():
                            fut.set_exception(e)
        except Exception as e:
            handle.error = str(e)
            logger.warning("MCP server task died: %s", e)
        finally:
            handle.ready.set()  # unblock any spawn waiter
            if handle.queue is not None:
                while not handle.queue.empty():
                    req = handle.queue.get_nowait()
                    if req is not _STOP:
                        *_, fut = req
                        if not fut.done():
                            fut.set_exception(RuntimeError("MCP server stopped"))

    # --- public sync API ---

    def ensure_started(self, username, tool_id, command, args, env=None,
                       timeout=SPAWN_TIMEOUT):
        """Start the server for (username, tool_id) if not already running."""
        self._ensure_loop()
        key = (username, tool_id)
        with self._lock:
            handle = self._servers.get(key)
            if handle is not None and handle.task is not None and not handle.task.done():
                return
            handle = _ServerHandle()
            self._servers[key] = handle

            def _spawn():
                handle.task = self._loop.create_task(
                    self._server_task(handle, command, args, env)
                )
            self._loop.call_soon_threadsafe(_spawn)

        if not handle.ready.wait(timeout):
            handle.error = handle.error or "spawn timeout"
            raise RuntimeError(f"{tool_id}: server failed to start ({handle.error})")
        if handle.error:
            raise RuntimeError(f"{tool_id}: {handle.error}")

    def call(self, username, tool_id, method, arguments=None, timeout=CALL_TIMEOUT):
        """Invoke a tool method. Returns list of text payloads. Raises on error."""
        handle = self._servers.get((username, tool_id))
        if handle is None or handle.task is None or handle.task.done():
            raise RuntimeError(f"{tool_id}: server not running")
        fut = concurrent.futures.Future()
        self._loop.call_soon_threadsafe(
            handle.queue.put_nowait, (method, arguments, timeout, fut)
        )
        return fut.result(timeout + 5)

    def list_tools(self, username, tool_id, timeout=CALL_TIMEOUT):
        """List the server's tools as [{name, description, input_schema}]."""
        return self.call(username, tool_id, _LIST_TOOLS, timeout=timeout)

    def is_running(self, username, tool_id):
        handle = self._servers.get((username, tool_id))
        return handle is not None and handle.task is not None and not handle.task.done()

    def stop(self, username, tool_id):
        """Stop one server gracefully."""
        handle = self._servers.pop((username, tool_id), None)
        if handle is None or handle.queue is None or self._loop is None:
            return
        self._loop.call_soon_threadsafe(handle.queue.put_nowait, _STOP)

    def shutdown(self):
        """Stop all servers and the manager loop. Safe to call repeatedly."""
        for key in list(self._servers):
            self.stop(*key)
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=5)
            self._loop = None
            self._thread = None


MANAGER = MCPManager()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/tooling/test_mcp_manager.py -v`
Expected: PASS (7 passed)

- [ ] **Step 6: Commit**

```bash
git add core/tooling/ tests/tooling/
git commit -m "phase 4A: MCPManager — dedicated-loop MCP client with per-server tasks"
```

---

## Task 2: Catalog (catalog.json + loader)

**Files:**
- Create: `core/tooling/catalog.json`, `core/tooling/catalog.py`
- Test: `tests/tooling/test_catalog.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/tooling/test_catalog.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tooling/test_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError` / missing catalog.json

- [ ] **Step 3: Create `core/tooling/catalog.json`**

```json
{
  "time": {
    "name": "Time",
    "description": "Current time and timezone conversions (get_current_time, convert_time).",
    "launch": {"command": "python", "args": ["-m", "mcp_server_time"]},
    "default_tier": "read_scoped",
    "method_tiers": {},
    "config_fields": [],
    "author": "MCP community (Anthropic reference server)",
    "source": "https://pypi.org/project/mcp-server-time/"
  },
  "filesystem": {
    "name": "Filesystem",
    "description": "List, read, and search files in approved directories.",
    "launch": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"], "append_config": "approved_dirs"},
    "default_tier": "read_broad",
    "method_tiers": {
      "write_file": "write_destructive",
      "edit_file": "write_destructive",
      "move_file": "write_destructive",
      "create_directory": "write_destructive"
    },
    "config_fields": ["approved_dirs"],
    "author": "Anthropic (official MCP server)",
    "source": "https://www.npmjs.com/package/@modelcontextprotocol/server-filesystem"
  }
}
```

- [ ] **Step 4: Create `core/tooling/catalog.py`**

```python
"""
Tool Catalog — the curated, Claude-vetted list of installable MCP servers.
Catalog-only discovery: nothing outside this file can be installed (unmet
needs go to the wishlist instead).
"""

import json
from pathlib import Path

_CATALOG_PATH = Path(__file__).parent / "catalog.json"
_cache = None


def all_entries():
    """Return the full catalog as {tool_id: entry}."""
    global _cache
    if _cache is None:
        _cache = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    return _cache


def get_entry(tool_id):
    """Return one catalog entry, or None if the tool isn't in the catalog."""
    return all_entries().get(tool_id)


def search(query):
    """Return tool_ids whose id, name, or description contains the query."""
    q = query.lower().strip()
    return [
        tool_id for tool_id, e in all_entries().items()
        if q in tool_id.lower() or q in e["name"].lower() or q in e["description"].lower()
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/tooling/test_catalog.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add core/tooling/catalog.py core/tooling/catalog.json tests/tooling/test_catalog.py
git commit -m "phase 4A: curated tool catalog (time, filesystem)"
```

---

## Task 3: Per-user installed-tools registry

**Files:**
- Create: `core/tooling/registry.py`
- Test: `tests/tooling/test_registry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/tooling/test_registry.py`:

```python
"""Registry CRUD tests against a temp data dir."""
import pytest


@pytest.fixture
def reg(tmp_path, monkeypatch):
    from core.tooling import registry
    monkeypatch.setattr(registry, "_DATA_ROOT", tmp_path)
    return registry


def test_install_and_load_roundtrip(reg):
    reg.install("switch", "time", trust_tier="read_scoped", config={})
    entry = reg.get("switch", "time")
    assert entry["trust_tier"] == "read_scoped"
    assert entry["call_count"] == 0
    assert "installed" in entry


def test_installed_ids_and_uninstall(reg):
    reg.install("switch", "time", "read_scoped", {})
    reg.install("switch", "filesystem", "read_broad", {"approved_dirs": ["C:/x"]})
    assert set(reg.installed_ids("switch")) == {"time", "filesystem"}
    assert reg.uninstall("switch", "time") is True
    assert reg.installed_ids("switch") == ["filesystem"]
    assert reg.uninstall("switch", "time") is False


def test_get_missing_returns_none(reg):
    assert reg.get("switch", "nope") is None


def test_touch_updates_usage(reg):
    reg.install("switch", "time", "read_scoped", {})
    reg.touch("switch", "time")
    entry = reg.get("switch", "time")
    assert entry["call_count"] == 1
    assert entry["last_used"] is not None


def test_users_are_isolated(reg):
    reg.install("alice", "time", "read_scoped", {})
    assert reg.get("bob", "time") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tooling/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.tooling.registry'`

- [ ] **Step 3: Implement `core/tooling/registry.py`**

```python
"""
Installed-Tools Registry — per-user record of installed MCP tools.
Stored at data/users/<user>/mcp_tools/registry.json.
"""

import json
from datetime import datetime
from pathlib import Path

from core.config import PROJECT_ROOT

_DATA_ROOT = PROJECT_ROOT / "data" / "users"


def _registry_path(username):
    return _DATA_ROOT / username.lower().strip() / "mcp_tools" / "registry.json"


def _load(username):
    path = _registry_path(username)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save(username, data):
    path = _registry_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def install(username, tool_id, trust_tier, config):
    """Record a tool installation for a user."""
    data = _load(username)
    data[tool_id] = {
        "trust_tier": trust_tier,
        "config": config or {},
        "installed": datetime.now().isoformat(),
        "last_used": None,
        "call_count": 0,
    }
    _save(username, data)


def uninstall(username, tool_id):
    """Remove a tool. Returns True if it was installed."""
    data = _load(username)
    if tool_id not in data:
        return False
    del data[tool_id]
    _save(username, data)
    return True


def get(username, tool_id):
    """Return the registry entry for a tool, or None."""
    return _load(username).get(tool_id)


def installed_ids(username):
    """List installed tool_ids for a user."""
    return list(_load(username).keys())


def touch(username, tool_id):
    """Bump call_count and last_used after a successful call."""
    data = _load(username)
    if tool_id in data:
        data[tool_id]["call_count"] += 1
        data[tool_id]["last_used"] = datetime.now().isoformat()
        _save(username, data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/tooling/test_registry.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add core/tooling/registry.py tests/tooling/test_registry.py
git commit -m "phase 4A: per-user installed-tools registry"
```

---

## Task 4: Trust tiers + PIN escalation

**Files:**
- Create: `core/tooling/trust.py`
- Test: `tests/tooling/test_trust.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/tooling/test_trust.py`:

```python
"""Trust tier decisions + PIN escalation stash."""
from datetime import datetime, timedelta

import pytest


FS_CATALOG = {
    "default_tier": "read_broad",
    "method_tiers": {"write_file": "write_destructive"},
}
TIME_CATALOG = {"default_tier": "read_scoped", "method_tiers": {}}


def test_required_tier_uses_method_map_then_default():
    from core.tooling import trust
    assert trust.required_tier(FS_CATALOG, "write_file") == "write_destructive"
    assert trust.required_tier(FS_CATALOG, "read_file") == "read_broad"
    assert trust.required_tier(TIME_CATALOG, "get_current_time") == "read_scoped"


def test_check_allows_within_tier():
    from core.tooling import trust
    assert trust.check("read_broad", FS_CATALOG, "read_file") == "allow"
    assert trust.check("read_scoped", TIME_CATALOG, "get_current_time") == "allow"


def test_check_escalates_above_tier():
    from core.tooling import trust
    assert trust.check("read_broad", FS_CATALOG, "write_file") == "needs_pin"


def test_stash_and_pin_confirm_executes_once(monkeypatch):
    from core.tooling import trust
    trust._pending.clear()
    monkeypatch.setattr(trust, "verify_vault_pin", lambda u, p: p == "1234")
    monkeypatch.setattr(trust, "has_vault_pin", lambda u: True)

    trust.stash_pending("switch", "filesystem", "write_file", {"path": "x"})
    ok, entry = trust.confirm_with_pin("switch", "9999")
    assert ok is False and "PIN" in entry            # wrong pin -> message, stash kept
    ok, entry = trust.confirm_with_pin("switch", "1234")
    assert ok is True and entry["method"] == "write_file"
    ok, entry = trust.confirm_with_pin("switch", "1234")
    assert ok is False                                # consumed — nothing pending


def test_pending_expires(monkeypatch):
    from core.tooling import trust
    trust._pending.clear()
    monkeypatch.setattr(trust, "verify_vault_pin", lambda u, p: True)
    monkeypatch.setattr(trust, "has_vault_pin", lambda u: True)
    trust.stash_pending("switch", "filesystem", "write_file", {})
    trust._pending["switch"]["expires"] = datetime.now() - timedelta(seconds=1)
    ok, msg = trust.confirm_with_pin("switch", "1234")
    assert ok is False and "expired" in msg.lower()


def test_no_pin_set_gives_guidance(monkeypatch):
    from core.tooling import trust
    trust._pending.clear()
    monkeypatch.setattr(trust, "has_vault_pin", lambda u: False)
    trust.stash_pending("switch", "filesystem", "write_file", {})
    ok, msg = trust.confirm_with_pin("switch", "1234")
    assert ok is False and "vault pin" in msg.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tooling/test_trust.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.tooling.trust'`

- [ ] **Step 3: Implement `core/tooling/trust.py`**

```python
"""
Trust Tiers & PIN Escalation — 4-tier trust model for installed tools.

An operation within the installed tier runs; one above it soft-blocks and
requires a per-operation vault-PIN confirmation (never a permanent re-tier).
"""

from datetime import datetime, timedelta

from core.vault_pin import verify_vault_pin, has_vault_pin

# Order matters: index = privilege level.
TIERS = ["read_scoped", "read_broad", "write_scoped_undoable", "write_destructive"]

PENDING_MINUTES = 5

# username -> {tool_id, method, args, expires}. One pending op per user.
_pending = {}


def required_tier(catalog_entry, method):
    """Tier a method needs: per-method map first, else the tool's default."""
    return catalog_entry.get("method_tiers", {}).get(
        method, catalog_entry["default_tier"]
    )


def check(installed_tier, catalog_entry, method):
    """Decide: 'allow' if the method fits the installed tier, else 'needs_pin'."""
    needed = required_tier(catalog_entry, method)
    if TIERS.index(needed) <= TIERS.index(installed_tier):
        return "allow"
    return "needs_pin"


def stash_pending(username, tool_id, method, args):
    """Hold an out-of-tier operation awaiting PIN confirmation."""
    _pending[username.lower().strip()] = {
        "tool_id": tool_id,
        "method": method,
        "args": args,
        "expires": datetime.now() + timedelta(minutes=PENDING_MINUTES),
    }


def confirm_with_pin(username, pin):
    """Verify the PIN and release the pending op for one-time execution.

    Returns (True, pending_entry) on success; (False, user_message) otherwise.
    The entry is consumed on success — a second confirmation needs a new stash.
    """
    username = username.lower().strip()
    entry = _pending.get(username)
    if entry is None:
        return False, "Nothing is waiting for PIN confirmation."
    if not has_vault_pin(username):
        return False, ("You don't have a vault PIN set. Set one from the vault "
                       "settings first, then retry the operation.")
    if datetime.now() > entry["expires"]:
        _pending.pop(username, None)
        return False, "That confirmation expired — run the operation again."
    if not verify_vault_pin(username, pin):
        return False, "Incorrect PIN. The operation is still pending."
    _pending.pop(username, None)
    return True, entry
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/tooling/test_trust.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add core/tooling/trust.py tests/tooling/test_trust.py
git commit -m "phase 4A: 4-tier trust model with per-operation PIN escalation"
```

---

## Task 5: Audit log + wishlist

**Files:**
- Create: `core/tooling/audit.py`, `core/tooling/wishlist.py`
- Modify: `core/config/core_config.json` (add `tooling` block)
- Test: `tests/tooling/test_audit_wishlist.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/tooling/test_audit_wishlist.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tooling/test_audit_wishlist.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `core/tooling/audit.py`**

```python
"""
Tool Audit Log — append-only JSONL of every tool call, denial, and escalation.
Stored at data/users/<user>/mcp_tools/audit.jsonl.
"""

import json
from datetime import datetime

from core.config import PROJECT_ROOT

_DATA_ROOT = PROJECT_ROOT / "data" / "users"


def _audit_path(username):
    return _DATA_ROOT / username.lower().strip() / "mcp_tools" / "audit.jsonl"


def log(username, tool_id, method, args, outcome, duration_ms):
    """Append one audit entry. outcome: ok | error | denied | pin_escalated."""
    path = _audit_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now().isoformat(),
        "tool": tool_id,
        "method": method,
        "args": args,
        "outcome": outcome,
        "duration_ms": duration_ms,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def recent(username, limit=50):
    """Return the newest `limit` entries, oldest first."""
    path = _audit_path(username)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines[-limit:]]
```

- [ ] **Step 4: Implement `core/tooling/wishlist.py`**

```python
"""
Tool Wishlist — write-side only (4A). Unmet tool needs are appended here;
a scheduled Claude Code routine (4A.5) vets entries weekly.
Path comes from config key tooling.wishlist_path (Aegis is distributable —
never hardcode a machine-specific path).
"""

from datetime import datetime
from pathlib import Path

from core.config import PROJECT_ROOT

_HEADER = "# Aegis Tool Wishlist\n\nUnmet tool needs, appended by Aegis. Vetted weekly.\n"


def _wishlist_path():
    import core.config
    configured = core.config.CONFIG.get("tooling", {}).get("wishlist_path", "")
    if configured:
        return Path(configured)
    return PROJECT_ROOT / "data" / "tool_wishlist.md"


def add(username, description):
    """Append a wishlist entry. Returns the path written to."""
    path = _wishlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_HEADER, encoding="utf-8")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n- **{stamp}** ({username}): {description}\n")
    return path
```

- [ ] **Step 5: Add the `tooling` block to `core/config/core_config.json`**

Add a top-level key (after the `"voice"` block, keeping valid JSON):

```json
    "tooling": {
        "wishlist_path": ""
    },
```

(Empty string = use the default `data/tool_wishlist.md`. Switch can point it at
`D:\ObsidianBrain\10-Projects\aegis-tool-wishlist.md` locally later.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/tooling/test_audit_wishlist.py -v`
Expected: PASS (4 passed)

- [ ] **Step 7: Commit**

```bash
git add core/tooling/audit.py core/tooling/wishlist.py core/config/core_config.json tests/tooling/test_audit_wishlist.py
git commit -m "phase 4A: audit log + config-pathed tool wishlist"
```

---

## Task 6: Service layer (shared install/call/confirm flows)

**Files:**
- Create: `core/tooling/service.py`
- Test: `tests/tooling/test_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/tooling/test_service.py`:

```python
"""Service-layer tests — manager mocked, registry/trust real (temp dirs)."""
import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Real registry+audit on tmp dirs, mocked manager, clean trust stash."""
    from core.tooling import registry, audit, trust, service

    monkeypatch.setattr(registry, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_DATA_ROOT", tmp_path)
    trust._pending.clear()

    class FakeManager:
        def __init__(self):
            self.started = []
            self.calls = []
        def ensure_started(self, u, t, command, args, env=None, timeout=60):
            self.started.append((u, t, command, tuple(args)))
        def call(self, u, t, method, arguments=None, timeout=10):
            self.calls.append((u, t, method, arguments))
            return [f"{method}-result"]
        def list_tools(self, u, t, timeout=10):
            return [{"name": "read_file", "description": "", "input_schema": {}}]
        def is_running(self, u, t):
            return True
        def stop(self, u, t):
            pass

    fake = FakeManager()
    monkeypatch.setattr(service, "MANAGER", fake)
    return service, fake


def test_install_time_no_config(env):
    service, fake = env
    msg = service.install_tool("switch", "time", {})
    assert "time" in msg.lower() and "read_scoped" in msg
    assert fake.started                       # warm-up spawned the server
    from core.tooling import registry
    assert registry.get("switch", "time")["trust_tier"] == "read_scoped"


def test_install_filesystem_requires_dirs(env):
    service, _ = env
    msg = service.install_tool("switch", "filesystem", {})
    assert "approved_dirs" in msg             # missing config -> guidance, not install
    from core.tooling import registry
    assert registry.get("switch", "filesystem") is None


def test_install_unknown_tool(env):
    service, _ = env
    msg = service.install_tool("switch", "nope", {})
    assert "catalog" in msg.lower()


def test_call_within_tier_executes_and_audits(env):
    service, fake = env
    service.install_tool("switch", "time", {})
    result = service.call_tool("switch", "time", "get_current_time", {"timezone": "UTC"})
    assert result["status"] == "ok"
    assert result["result"] == ["get_current_time-result"]
    from core.tooling import audit
    entries = audit.recent("switch")
    assert entries[-1]["outcome"] == "ok"


def test_call_uninstalled_tool(env):
    service, _ = env
    result = service.call_tool("switch", "filesystem", "read_file", {})
    assert result["status"] == "error" and "not installed" in result["message"]


def test_out_of_tier_write_soft_blocks(env):
    service, fake = env
    service.install_tool("switch", "filesystem", {"approved_dirs": ["C:/safe"]})
    result = service.call_tool("switch", "filesystem", "write_file",
                               {"path": "C:/safe/a.txt", "content": "hi"})
    assert result["status"] == "needs_pin"
    assert not any(c[2] == "write_file" for c in fake.calls)   # NOT executed
    from core.tooling import audit
    assert audit.recent("switch")[-1]["outcome"] == "denied"


def test_pin_confirm_executes_once(env, monkeypatch):
    service, fake = env
    from core.tooling import trust
    monkeypatch.setattr(trust, "verify_vault_pin", lambda u, p: p == "1234")
    monkeypatch.setattr(trust, "has_vault_pin", lambda u: True)

    service.install_tool("switch", "filesystem", {"approved_dirs": ["C:/safe"]})
    service.call_tool("switch", "filesystem", "write_file", {"path": "C:/safe/a.txt"})

    result = service.confirm_pending("switch", "1234")
    assert result["status"] == "ok"
    assert any(c[2] == "write_file" for c in fake.calls)       # executed now
    from core.tooling import audit
    assert audit.recent("switch")[-1]["outcome"] == "pin_escalated"

    again = service.confirm_pending("switch", "1234")
    assert again["status"] == "error"                           # consumed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tooling/test_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.tooling.service'`

- [ ] **Step 3: Implement `core/tooling/service.py`**

```python
"""
Tooling Service — shared install/call/confirm flows used by both the chat
protocol (/tools commands) and the /api/tools/* endpoints. Composes catalog,
registry, trust, audit, and the MCPManager.
"""

import logging
import shutil
import sys
import time as _time

from core.tooling import audit, catalog, registry, trust
from core.tooling.mcp_manager import MANAGER, SPAWN_TIMEOUT

logger = logging.getLogger("aegis.tooling.service")


def _resolve_launch(entry, config):
    """Build (command, args) for a catalog entry. Raises RuntimeError if the
    runtime isn't available. Windows rule: resolve npx via shutil.which
    ('npx' alone won't spawn — it's npx.cmd)."""
    launch = entry["launch"]
    command = launch["command"]
    args = list(launch["args"])
    if command == "python":
        command = sys.executable
    elif command == "npx":
        resolved = shutil.which("npx")
        if not resolved:
            raise RuntimeError("Node/npx not found — install Node.js to use this tool.")
        command = resolved
    append_key = launch.get("append_config")
    if append_key:
        args.extend(config.get(append_key, []))
    return command, args


def _ensure_running(username, tool_id, reg_entry, cat_entry):
    command, args = _resolve_launch(cat_entry, reg_entry.get("config", {}))
    MANAGER.ensure_started(username, tool_id, command, args, timeout=SPAWN_TIMEOUT)


def install_tool(username, tool_id, config):
    """Install a catalog tool for a user; warm up the server. Returns a message."""
    entry = catalog.get_entry(tool_id)
    if entry is None:
        return f"'{tool_id}' isn't in the catalog. Try /tools find <query>, or /tools wish <description>."

    config = config or {}
    missing = [f for f in entry["config_fields"] if not config.get(f)]
    if missing:
        return (f"'{tool_id}' needs config before install: {', '.join(missing)}. "
                f"Example: /tools install filesystem approved_dirs=C:/Users/you/Documents")

    tier = entry["default_tier"]
    registry.install(username, tool_id, trust_tier=tier, config=config)

    # Warm-up: spawn now so npx package download happens at install, not first call.
    try:
        reg_entry = registry.get(username, tool_id)
        _ensure_running(username, tool_id, reg_entry, entry)
        tools = MANAGER.list_tools(username, tool_id, timeout=SPAWN_TIMEOUT)
        names = ", ".join(t["name"] for t in tools[:8])
        return (f"Installed '{tool_id}' at trust tier {tier}. "
                f"Server is up — methods: {names}")
    except Exception as e:
        logger.warning("Warm-up failed for %s: %s", tool_id, e)
        return (f"Installed '{tool_id}' at trust tier {tier}, but the server "
                f"failed to start: {e}")


def uninstall_tool(username, tool_id):
    """Uninstall and stop a tool."""
    MANAGER.stop(username, tool_id)
    if registry.uninstall(username, tool_id):
        return f"Uninstalled '{tool_id}'."
    return f"'{tool_id}' isn't installed."


def call_tool(username, tool_id, method, arguments):
    """Trust-checked tool invocation.

    Returns {"status": "ok", "result": [...]} |
            {"status": "needs_pin", "message": str} |
            {"status": "error", "message": str}
    """
    reg_entry = registry.get(username, tool_id)
    if reg_entry is None:
        return {"status": "error", "message": f"'{tool_id}' is not installed."}
    cat_entry = catalog.get_entry(tool_id)
    if cat_entry is None:
        return {"status": "error", "message": f"'{tool_id}' is no longer in the catalog."}

    decision = trust.check(reg_entry["trust_tier"], cat_entry, method)
    if decision == "needs_pin":
        trust.stash_pending(username, tool_id, method, arguments)
        audit.log(username, tool_id, method, arguments, "denied", 0)
        return {"status": "needs_pin", "message": (
            f"'{method}' is a {trust.required_tier(cat_entry, method)} operation — "
            f"outside {tool_id}'s granted tier ({reg_entry['trust_tier']}). "
            f"Confirm once with: /tools pin <your vault PIN> (expires in "
            f"{trust.PENDING_MINUTES} min)")}

    return _execute(username, tool_id, method, arguments, reg_entry, cat_entry, "ok")


def confirm_pending(username, pin):
    """Verify PIN and execute the stashed out-of-tier operation once."""
    ok, entry_or_msg = trust.confirm_with_pin(username, pin)
    if not ok:
        return {"status": "error", "message": entry_or_msg}
    entry = entry_or_msg
    reg_entry = registry.get(username, entry["tool_id"])
    cat_entry = catalog.get_entry(entry["tool_id"])
    if reg_entry is None or cat_entry is None:
        return {"status": "error", "message": f"'{entry['tool_id']}' is not installed."}
    return _execute(username, entry["tool_id"], entry["method"], entry["args"],
                    reg_entry, cat_entry, "pin_escalated")


def _execute(username, tool_id, method, arguments, reg_entry, cat_entry, outcome_tag):
    started = _time.monotonic()
    try:
        _ensure_running(username, tool_id, reg_entry, cat_entry)
        result = MANAGER.call(username, tool_id, method, arguments)
        registry.touch(username, tool_id)
        audit.log(username, tool_id, method, arguments, outcome_tag,
                  int((_time.monotonic() - started) * 1000))
        return {"status": "ok", "result": result}
    except Exception as e:
        audit.log(username, tool_id, method, arguments, "error",
                  int((_time.monotonic() - started) * 1000))
        return {"status": "error", "message": f"{tool_id}.{method} failed: {e}"}


def installed_summary(username):
    """[{tool_id, tier, running, call_count}] for /tools list and the endpoint."""
    out = []
    for tool_id in registry.installed_ids(username):
        entry = registry.get(username, tool_id)
        out.append({
            "tool_id": tool_id,
            "trust_tier": entry["trust_tier"],
            "running": MANAGER.is_running(username, tool_id),
            "call_count": entry["call_count"],
        })
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/tooling/test_service.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add core/tooling/service.py tests/tooling/test_service.py
git commit -m "phase 4A: service layer — shared install/call/PIN-confirm flows"
```

---

## Task 7: ToolingProtocol (slash commands) + session/agent registration

**Files:**
- Create: `core/protocols/tooling.py`
- Modify: `core/session.py` (~line 107, after CreativeProtocol), `core/agent.py` (~line 156)
- Test: `tests/tooling/test_tooling_protocol.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/tooling/test_tooling_protocol.py`:

```python
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
                        lambda u, t, m, a: calls.setdefault("call", (t, m, a)) or
                        {"status": "ok", "result": ["r1", "r2"]})
    monkeypatch.setattr(tooling_mod.service, "confirm_pending",
                        lambda u, p: calls.setdefault("pin", p) or
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


def test_tools_help_on_unknown(proto):
    p, _ = proto
    reply = p.cmd_tools("bogus")
    assert "/tools list" in reply
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tooling/test_tooling_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.protocols.tooling'`

- [ ] **Step 3: Implement `core/protocols/tooling.py`**

```python
"""
Tooling Protocol — slash-command surface for MCP tool discovery (Phase 4A).
/tools list | find | install | uninstall | call | wish | pin
Pike does not auto-call tools yet (that's Phase 4B).
"""

import logging

from core.protocols.base import Protocol
from core.tooling import catalog, service, wishlist

logger = logging.getLogger("aegis.protocols.tooling")


class ToolingProtocol(Protocol):
    """Manual tool management via /tools commands."""

    def __init__(self, username):
        super().__init__(
            name="tooling",
            description="MCP tool discovery: install and call external tools",
            priority=Protocol.PRIORITY_NORMAL,
        )
        self.username = username

    # --- Protocol ABC ---

    def process_input(self, user_input, context):
        return {"input": user_input, "context_injection": "",
                "intercept": False, "response": ""}

    def process_output(self, response, context):
        return {"response": response, "suppress": False, "append": ""}

    def get_commands(self):
        return [{"command": "tools",
                 "description": "Tool management (/tools list|find|install|call|wish|pin)",
                 "handler": "cmd_tools"}]

    # --- command dispatch ---

    def cmd_tools(self, args=""):
        parts = args.strip().split(None, 1)
        sub = parts[0].lower() if parts else "help"
        rest = parts[1].strip() if len(parts) > 1 else ""

        # SECURITY: never log `rest` for pin — it contains the vault PIN.
        if sub != "pin":
            logger.info("/tools %s %s", sub, rest)
        else:
            logger.info("/tools pin ****")

        if sub == "list":
            return self._list()
        if sub == "find":
            return self._find(rest)
        if sub == "install":
            return self._install(rest)
        if sub == "uninstall":
            return service.uninstall_tool(self.username, rest.split()[0]) if rest else "Usage: /tools uninstall <tool_id>"
        if sub == "call":
            return self._call(rest)
        if sub == "wish":
            return self._wish(rest)
        if sub == "pin":
            return self._pin(rest)
        return ("Tool commands:\n"
                "/tools list — installed tools\n"
                "/tools find <query> — search the catalog\n"
                "/tools install <tool_id> [key=v1,v2 …]\n"
                "/tools uninstall <tool_id>\n"
                "/tools call <tool_id> <method> [key=value …]\n"
                "/tools wish <description> — request a tool we don't have\n"
                "/tools pin <PIN> — confirm a pending out-of-tier operation")

    # --- subcommand impls ---

    def _list(self):
        rows = service.installed_summary(self.username)
        if not rows:
            return "No tools installed. Try /tools find <query> to browse the catalog."
        lines = ["Installed tools:"]
        for r in rows:
            state = "running" if r["running"] else "stopped"
            lines.append(f"- {r['tool_id']} [{r['trust_tier']}] {state}, "
                         f"{r['call_count']} calls")
        return "\n".join(lines)

    def _find(self, query):
        if not query:
            ids = list(catalog.all_entries())
        else:
            ids = catalog.search(query)
        if not ids:
            return (f"Nothing in the catalog matches '{query}'. "
                    f"Use /tools wish {query} to request it.")
        lines = ["Catalog matches:"]
        for tool_id in ids:
            e = catalog.get_entry(tool_id)
            lines.append(f"- {tool_id} [{e['default_tier']}]: {e['description']}")
        return "\n".join(lines)

    def _install(self, rest):
        if not rest:
            return "Usage: /tools install <tool_id> [key=value1,value2 …]"
        bits = rest.split()
        tool_id = bits[0]
        config = self._parse_kv(bits[1:], split_commas=True)
        return service.install_tool(self.username, tool_id, config)

    def _call(self, rest):
        bits = rest.split()
        if len(bits) < 2:
            return "Usage: /tools call <tool_id> <method> [key=value …]"
        tool_id, method = bits[0], bits[1]
        arguments = self._parse_kv(bits[2:], split_commas=False)
        result = service.call_tool(self.username, tool_id, method, arguments)
        if result["status"] == "ok":
            return "\n".join(result["result"]) or "(no output)"
        return result["message"]

    def _wish(self, description):
        if not description:
            return "Usage: /tools wish <what you need the tool to do>"
        wishlist.add(self.username, description)
        return ("Added to the tool wishlist. It'll be vetted in the weekly review — "
                "if a safe tool exists, it lands in the catalog.")

    def _pin(self, pin):
        if not pin:
            return "Usage: /tools pin <your vault PIN>"
        result = service.confirm_pending(self.username, pin.split()[0])
        if result["status"] == "ok":
            return "Confirmed and executed:\n" + ("\n".join(result["result"]) or "(no output)")
        return result["message"]

    @staticmethod
    def _parse_kv(tokens, split_commas):
        """Parse key=value tokens. split_commas turns 'a=1,2' into {'a': ['1','2']}."""
        out = {}
        for tok in tokens:
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            if split_commas and "," in v:
                out[k] = [p for p in v.split(",") if p]
            elif split_commas:
                out[k] = [v]
            else:
                out[k] = v
        return out
```

Note: `_parse_kv` with `split_commas=True` always wraps in a list (config fields
like `approved_dirs` are lists); with `False` values stay strings (MCP servers
coerce JSON-schema types from strings for simple cases; structured args come in 4B).

- [ ] **Step 4: Register in `core/session.py`**

After the `CreativeProtocol()` registration line (~107), add:

```python
        from core.protocols.tooling import ToolingProtocol
        self.protocol_registry.register(ToolingProtocol(username=user_id))
```

(Match the surrounding indentation; import can also go at the top of the file with the other protocol imports — follow the existing style there.)

- [ ] **Step 5: Register in `core/agent.py`**

After the `CreativeProtocol()` registration line (~156), add:

```python
    from core.protocols.tooling import ToolingProtocol
    protocol_registry.register(ToolingProtocol(username=user_id))
```

- [ ] **Step 6: Run tests + import check**

Run: `python -m pytest tests/tooling/test_tooling_protocol.py -v`
Expected: PASS (9 passed)

Run: `python -c "import core.session; import core.agent; print('imports OK')"`
Expected: `imports OK`

- [ ] **Step 7: Commit**

```bash
git add core/protocols/tooling.py core/session.py core/agent.py tests/tooling/test_tooling_protocol.py
git commit -m "phase 4A: ToolingProtocol slash commands + session/console registration"
```

---

## Task 8: /api/tools endpoints + lifespan shutdown

**Files:**
- Modify: `server/app.py`
- Test: `tests/tooling/test_tools_endpoints.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/tooling/test_tools_endpoints.py`:

```python
"""Endpoint tests via TestClient with require_user overridden."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    from core.tooling import registry, audit
    monkeypatch.setattr(registry, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_DATA_ROOT", tmp_path)

    from server.app import app, require_user
    app.dependency_overrides[require_user] = lambda: "switch"
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_get_catalog(client):
    resp = client.get("/api/tools/catalog")
    assert resp.status_code == 200
    assert "time" in resp.json()


def test_get_installed_empty(client):
    resp = client.get("/api/tools/installed")
    assert resp.status_code == 200
    assert resp.json() == []


def test_install_unknown_tool_reports(client):
    resp = client.post("/api/tools/install", json={"tool_id": "nope", "config": {}})
    assert resp.status_code == 200
    assert "catalog" in resp.json()["message"].lower()


def test_call_uninstalled_tool(client):
    resp = client.post("/api/tools/call",
                       json={"tool_id": "time", "method": "get_current_time", "args": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error" and "not installed" in body["message"]


def test_audit_empty(client):
    resp = client.get("/api/tools/audit")
    assert resp.status_code == 200
    assert resp.json() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tooling/test_tools_endpoints.py -v`
Expected: FAIL — 404s (routes don't exist)

- [ ] **Step 3: Add endpoints to `server/app.py`**

Near the other request models, add:

```python
class ToolInstallRequest(BaseModel):
    tool_id: str
    config: dict = {}


class ToolCallRequest(BaseModel):
    tool_id: str
    method: str
    args: dict = {}
```

With the other endpoints (e.g. after the tasks endpoints), add:

```python
# --- Tooling (Phase 4A) ---

@app.get("/api/tools/catalog")
async def tools_catalog(user_id: str = Depends(require_user)):
    from core.tooling import catalog
    return catalog.all_entries()


@app.get("/api/tools/installed")
async def tools_installed(user_id: str = Depends(require_user)):
    from core.tooling import service
    return await asyncio.to_thread(service.installed_summary, user_id)


@app.post("/api/tools/install")
async def tools_install(req: ToolInstallRequest, user_id: str = Depends(require_user)):
    from core.tooling import service
    message = await asyncio.to_thread(service.install_tool, user_id, req.tool_id, req.config)
    return {"message": message}


@app.post("/api/tools/uninstall/{tool_id}")
async def tools_uninstall(tool_id: str, user_id: str = Depends(require_user)):
    from core.tooling import service
    message = await asyncio.to_thread(service.uninstall_tool, user_id, tool_id)
    return {"message": message}


@app.post("/api/tools/call")
async def tools_call(req: ToolCallRequest, user_id: str = Depends(require_user)):
    from core.tooling import service
    return await asyncio.to_thread(service.call_tool, user_id, req.tool_id,
                                   req.method, req.args)


@app.get("/api/tools/audit")
async def tools_audit(limit: int = 50, user_id: str = Depends(require_user)):
    from core.tooling import audit
    return await asyncio.to_thread(audit.recent, user_id, limit)
```

(`asyncio` and `BaseModel` are already imported in `app.py`; verify and reuse.)

- [ ] **Step 4: Add manager shutdown to the lifespan**

In the `lifespan` function in `server/app.py`, after the Telegram bot stop block (before session save), add:

```python
    # Shutdown — stop MCP tool servers
    try:
        from core.tooling.mcp_manager import MANAGER
        MANAGER.shutdown()
    except Exception as e:
        logger.warning("Error stopping MCP manager: %s", e)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/tooling/test_tools_endpoints.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add server/app.py tests/tooling/test_tools_endpoints.py
git commit -m "phase 4A: /api/tools endpoints + MCP manager lifespan shutdown"
```

---

## Task 9: Real-subprocess integration tests + dependencies

**Files:**
- Modify: `requirements.txt`
- Test: `tests/tooling/test_mcp_integration.py`

- [ ] **Step 1: Install and pin dependencies**

Run: `pip install mcp-server-time`
Then add to `requirements.txt` (with the other deps, keeping the file's style):

```
mcp>=1.27
mcp-server-time
```

- [ ] **Step 2: Write the integration tests**

Create `tests/tooling/test_mcp_integration.py`:

```python
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
```

- [ ] **Step 3: Run the integration tests**

Run: `python -m pytest tests/tooling/test_mcp_integration.py -v`
Expected: PASS (2 passed — both runtimes are present on this box; first npx run may be slow)

If the filesystem test fails on method name (`list_directory`), run this to see the
server's actual tool names and adjust the test to match:
`python -m pytest tests/tooling/test_mcp_integration.py::test_real_time_server_roundtrip -v` then inspect via `mgr.list_tools`.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt tests/tooling/test_mcp_integration.py
git commit -m "phase 4A: real-subprocess MCP integration tests + deps"
```

---

## Task 10: Full-suite verification + live smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `python -m pytest -q`
Expected: 434 baseline + ~39 new, all passing. No import errors.

- [ ] **Step 2: Import graph check**

Run: `python -c "import server.app; import core.session; import core.agent; print('imports OK')"`
Expected: `imports OK`

- [ ] **Step 3: Live smoke (with the user, running server)**

1. Start the server, log into the web UI (or use Telegram — slash commands route the same).
2. `/tools find file` → filesystem listed.
3. `/tools install time` → installed at read_scoped, server warm, methods listed.
4. `/tools call time get_current_time timezone=America/Vancouver` → current time.
5. `/tools install filesystem approved_dirs=C:/Users/dusti/Documents` → installed read_broad, warm-up OK (first run downloads the npm package — may take up to a minute).
6. `/tools call filesystem list_directory path=C:/Users/dusti/Documents` → file list.
7. `/tools call filesystem write_file path=C:/Users/dusti/Documents/aegis_test.txt content=hi` → soft-block, asks for `/tools pin`.
8. `/tools pin <vault PIN>` → executes once; verify the file exists; delete it.
9. `/tools list` → both tools, tiers, call counts.
10. Check `data/users/dustin/mcp_tools/audit.jsonl` — includes ok, denied, and pin_escalated entries; no PIN digits anywhere.
11. `/tools wish plays chess with me` → appended to wishlist file.

- [ ] **Step 4: Final commit if smoke drove tweaks**

```bash
git add -A
git commit -m "phase 4A: tool discovery verified live"
```

---

## Definition of Done

- MCPManager spawns real `time` (Python) and `filesystem` (npx) servers; per-user keying; same-task context lifetime; 10s call / 60s spawn timeouts.
- Catalog-only install; filesystem requires approved_dirs at install; install warm-up.
- 4-tier trust: within-tier runs; out-of-tier write soft-blocks → `/tools pin` executes exactly once; 5-min expiry; PIN never in logs, transcripts, or audit.
- Audit JSONL logs ok/error/denied/pin_escalated; wishlist path from config.
- `/tools` commands + `/api/tools/*` endpoints share the service layer; manager shut down in lifespan.
- Full pytest suite green including 2 real-subprocess integration tests.
