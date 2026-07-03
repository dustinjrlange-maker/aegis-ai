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
        self.dead = False                  # set synchronously once task exits/fails


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
            handle.dead = True  # mark not-live so ensure_started can respawn
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
        username = username.lower().strip()
        self._ensure_loop()
        key = (username, tool_id)
        with self._lock:
            existing = self._servers.get(key)
            if existing is not None and not existing.dead:
                handle = existing          # someone already started it; wait below
            else:
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
        """Invoke a tool method. Returns list of text payloads. Raises on error.

        Requests to one server are serviced serially by its single task, and the
        ``timeout`` clock starts at submission — so a call can time out while
        still queued behind earlier calls. Callers under contention should size
        timeouts accordingly.
        """
        username = username.lower().strip()
        handle = self._servers.get((username, tool_id))
        if handle is None or handle.dead or handle.queue is None:
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
        username = username.lower().strip()
        handle = self._servers.get((username, tool_id))
        return handle is not None and not handle.dead and handle.queue is not None

    def stop(self, username, tool_id):
        """Stop one server gracefully."""
        username = username.lower().strip()
        handle = self._servers.pop((username, tool_id), None)
        if handle is None or handle.queue is None or self._loop is None:
            return
        self._loop.call_soon_threadsafe(handle.queue.put_nowait, _STOP)

    def shutdown(self):
        """Stop all servers and the manager loop. Safe to call repeatedly."""
        with self._lock:
            loop = self._loop
            thread = self._thread
            handles = list(self._servers.values())
            self._servers.clear()
            self._loop = None
            self._thread = None
        if loop is None or not loop.is_running():
            return

        async def _drain():
            for h in handles:
                if h.queue is not None:
                    h.queue.put_nowait(_STOP)
            tasks = [h.task for h in handles if h.task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(_drain(), loop).result(timeout=10)
        except Exception as e:
            logger.warning("MCP manager drain failed: %s", e)
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)


MANAGER = MCPManager()
