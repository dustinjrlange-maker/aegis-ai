# Tool Autocall Phase 4B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pike autonomously emits `[TOOL: tool.method key=value]`, the system runs the tool off the event loop, feeds the result back, and Pike answers from it — all in one user turn, bounded to 3 tool rounds.

**Architecture:** `ToolingProtocol` gains a real `process_input` (inject installed tools' method hints) and `process_output` (parse/stash/strip `[TOOL:]`, no execution). A new pure-async `core/tooling/autocall.run_tool_loop` (with injected router/call_tool/process_output for testability) executes pending calls, threads results back as system context, and re-prompts. `chat_pipeline` wires it in. Trust/PIN/audit all reuse 4A.

**Tech Stack:** Python 3.12, FastAPI, the existing `core/llm` router (`chat_with_meta`), pytest.

**Spec:** `docs/superpowers/specs/2026-07-03-tool-autocall-4b-design.md`

**Grounded facts (verified):**
- `core/llm/chat_with_meta(messages, *, sensitivity, task=None, model=None, ...) -> (content, RouteMeta)`; `RouteMeta.backend_used` is `"local"|"cloud"`.
- `ToolingProtocol` (`core/protocols/tooling.py`): `process_input`/`process_output` are no-ops today; `_parse_kv(tokens, split_commas)` is a staticmethod; `self.username` set in `__init__`.
- `session.protocol_registry.get("tooling")` returns the protocol; `process_output(reply, ctx)` runs all protocols; a protocol's returned `context_injection` string is aggregated by the registry into `proto_result["context_injections"]`, which `chat_pipeline` injects as a system message.
- `chat_pipeline.process_chat` LLM path: first call at `~line 177` (`reply_content, route_meta = await asyncio.to_thread(router_chat_with_meta, messages_to_send, sensitivity="personal", task=task_tag, model=CONFIG["model"]["chat"])`); `reply = session.clean_reply(reply_content, mode=turn.mode)` (`~184`); `process_output` at `~187`; assistant appended at `~205`; `route_meta.backend_used == "cloud"` drives the ☁ suffix at `~208`.
- `service.call_tool(username, tool_id, method, arguments)` returns `{"status":"ok","result":[...]}` | `{"status":"needs_pin","message":...}` | `{"status":"error","message":...}`.
- 4A `tooling` config block is `{"wishlist_path": ""}` in `core/config/core_config.json`.

---

## File Structure

- Modify: `core/tooling/catalog.json` (add `method_hints` per entry)
- Modify: `core/config/core_config.json` (add `tooling.autocall_enabled`)
- Modify: `core/protocols/tooling.py` (module `_autocall_enabled()`; real `process_input`/`process_output`; `get_pending_tool_calls`/`get_rejections`; `_TOOL_RE`; init two lists)
- Modify: `core/tooling/service.py` (add `tool_id`/`method`/`required_tier` to the `needs_pin` return)
- Create: `core/tooling/autocall.py` (`run_tool_loop`, `_format_tool_result`)
- Modify: `server/chat_pipeline.py` (wire `run_tool_loop` after the first `process_output`)
- Test: `tests/tooling/test_tool_autocall.py` (new — injection, parse, loop), plus 1 assertion added to `tests/tooling/test_service.py`

---

## Task 1: Catalog method hints + autocall config flag + helper

**Files:**
- Modify: `core/tooling/catalog.json`, `core/config/core_config.json`, `core/protocols/tooling.py`
- Test: `tests/tooling/test_tool_autocall.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/tooling/test_tool_autocall.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tooling/test_tool_autocall.py -v`
Expected: FAIL — KeyError on `method_hints` / `AttributeError: _autocall_enabled`

- [ ] **Step 3: Add `method_hints` to `core/tooling/catalog.json`**

In the `"time"` entry, add after `"method_tiers": {}`:
```json
    "method_hints": {"get_current_time": "timezone=<IANA tz, e.g. America/Vancouver>"},
```
In the `"filesystem"` entry, add after its `"method_tiers": { … }` block (keep JSON valid — comma after the closing brace of method_tiers):
```json
    "method_hints": {
      "list_directory": "path=<dir>",
      "read_file": "path=<file>",
      "write_file": "path=<file> content=<text>"
    },
```

- [ ] **Step 4: Add the config flag to `core/config/core_config.json`**

Change the `tooling` block from `{"wishlist_path": ""}` to include the flag:
```json
    "tooling": {
        "wishlist_path": "",
        "autocall_enabled": true
    },
```
Verify: `python -c "import json; json.load(open('core/config/core_config.json')); print('valid')"`

- [ ] **Step 5: Add `_autocall_enabled()` to `core/protocols/tooling.py`**

At module level (after the `logger = ...` line, before the class):
```python
def _autocall_enabled():
    """Whether Pike may auto-call tools (Phase 4B). Default on."""
    from core.config import CONFIG
    return CONFIG.get("tooling", {}).get("autocall_enabled", True)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/tooling/test_tool_autocall.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add core/tooling/catalog.json core/config/core_config.json core/protocols/tooling.py tests/tooling/test_tool_autocall.py
git commit -m "phase 4B: catalog method_hints + autocall config flag"
```

---

## Task 2: Tool-schema injection in `process_input`

**Files:**
- Modify: `core/protocols/tooling.py`
- Test: `tests/tooling/test_tool_autocall.py` (append)

- [ ] **Step 1: Write the failing tests (append)**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tooling/test_tool_autocall.py -k injection -v`
Expected: FAIL — the current `process_input` returns an empty injection unconditionally.

- [ ] **Step 3: Replace `process_input` in `core/protocols/tooling.py`**

```python
    def process_input(self, user_input, context):
        """Inject the installed tools' methods so Pike can call them (Phase 4B)."""
        empty = {"input": user_input, "context_injection": "",
                 "intercept": False, "response": ""}
        if not _autocall_enabled():
            return empty
        from core.tooling import registry
        installed = registry.installed_ids(self.username)
        if not installed:
            return empty
        lines = ["Available tools — emit [TOOL: tool.method key=value] on its own "
                 "line to use one:"]
        for tool_id in installed:
            entry = catalog.get_entry(tool_id)
            if not entry:
                continue
            for method, hint in entry.get("method_hints", {}).items():
                lines.append(f"  {tool_id}.{method} {hint}")
        if len(lines) == 1:            # installed tools had no hints
            return empty
        lines.append("Only call a tool when the request needs live data or an action "
                     "you can't do from memory. After a tool runs you'll see its result "
                     "and can answer or call another tool.")
        return {"input": user_input, "context_injection": "\n".join(lines),
                "intercept": False, "response": ""}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/tooling/test_tool_autocall.py -k injection -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/protocols/tooling.py tests/tooling/test_tool_autocall.py
git commit -m "phase 4B: inject installed-tool methods into Pike's context"
```

---

## Task 3: `[TOOL: …]` parse/stash/strip in `process_output`

**Files:**
- Modify: `core/protocols/tooling.py`
- Test: `tests/tooling/test_tool_autocall.py` (append)

- [ ] **Step 1: Write the failing tests (append)**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tooling/test_tool_autocall.py -k parse -v`
Expected: FAIL — `AttributeError: get_pending_tool_calls` / current `process_output` no-op.

- [ ] **Step 3: Add the regex, init lists, and replace `process_output`**

Add `import re` to the imports at the top of `core/protocols/tooling.py`. After the `logger = ...`/`_autocall_enabled` block, add the pattern:
```python
# [TOOL: tool_id.method key=value ...]
_TOOL_RE = re.compile(r"\[TOOL:\s*([a-z_]+)\.([a-z_]+)\s*(.*?)\]", re.I)
```
In `__init__`, after `self.username = username`, add:
```python
        self._pending_tool_calls = []
        self._rejections = []
```
Replace the no-op `process_output` with:
```python
    def process_output(self, response, context):
        """Parse [TOOL: tool.method args] from Pike's output; stash + strip.
        Does NOT execute — the chat pipeline runs pending calls off the loop."""
        self._pending_tool_calls = []
        self._rejections = []
        if not _autocall_enabled():
            return {"response": response, "suppress": False, "append": ""}
        from core.tooling import registry
        matches = list(_TOOL_RE.finditer(response))
        if not matches:
            return {"response": response, "suppress": False, "append": ""}
        clean = response
        for m in matches:
            tool_id = m.group(1).lower()
            method = m.group(2).lower()
            raw = m.group(3).strip()
            entry = catalog.get_entry(tool_id)
            installed = registry.get(self.username, tool_id) is not None
            known = bool(entry) and (method in entry.get("method_tiers", {})
                                     or method in entry.get("method_hints", {}))
            if not installed or not known:
                self._rejections.append(f"{tool_id}.{method}")
            else:
                args = self._parse_kv(raw.split(), split_commas=False)
                self._pending_tool_calls.append(
                    {"tool_id": tool_id, "method": method, "args": args})
            clean = clean.replace(m.group(0), "")
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        clean = re.sub(r"[ \t]+([.?,!])", r"\1", clean)
        clean = clean.strip()
        return {"response": clean, "suppress": False, "append": ""}

    def get_pending_tool_calls(self):
        """Structured [TOOL:] calls parsed from the most recent output."""
        return list(self._pending_tool_calls)

    def get_rejections(self):
        """`tool.method` strings that were emitted but aren't available."""
        return list(self._rejections)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/tooling/test_tool_autocall.py -k parse -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add core/protocols/tooling.py tests/tooling/test_tool_autocall.py
git commit -m "phase 4B: parse [TOOL: ...] brackets into pending tool calls"
```

---

## Task 4: Machine-readable `needs_pin` in `service.call_tool`

**Files:**
- Modify: `core/tooling/service.py`
- Test: `tests/tooling/test_service.py` (extend the existing soft-block test)

- [ ] **Step 1: Add assertions to the existing test**

In `tests/tooling/test_service.py`, find `test_out_of_tier_write_soft_blocks` and add these assertions after the existing `assert result["status"] == "needs_pin"`:
```python
    assert result["tool_id"] == "filesystem"
    assert result["method"] == "write_file"
    assert result["required_tier"] == "write_destructive"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/tooling/test_service.py::test_out_of_tier_write_soft_blocks -v`
Expected: FAIL — KeyError, the needs_pin dict has no `tool_id`.

- [ ] **Step 3: Add the fields to the `needs_pin` return in `core/tooling/service.py`**

In `call_tool`, change the `needs_pin` branch from:
```python
    if decision == "needs_pin":
        trust.stash_pending(username, tool_id, method, arguments)
        audit.log(username, tool_id, method, arguments, "denied", 0)
        return {"status": "needs_pin", "message": (
            f"'{method}' is a {trust.required_tier(cat_entry, method)} operation — "
            f"outside {tool_id}'s granted tier ({reg_entry['trust_tier']}). "
            f"Confirm once with: /tools pin <your vault PIN> (expires in "
            f"{trust.PENDING_MINUTES} min)")}
```
to:
```python
    if decision == "needs_pin":
        trust.stash_pending(username, tool_id, method, arguments)
        audit.log(username, tool_id, method, arguments, "denied", 0)
        return {"status": "needs_pin",
                "tool_id": tool_id,
                "method": method,
                "required_tier": trust.required_tier(cat_entry, method),
                "message": (
            f"'{method}' is a {trust.required_tier(cat_entry, method)} operation — "
            f"outside {tool_id}'s granted tier ({reg_entry['trust_tier']}). "
            f"Confirm once with: /tools pin <your vault PIN> (expires in "
            f"{trust.PENDING_MINUTES} min)")}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/tooling/test_service.py -v`
Expected: PASS (all service tests, including the extended one)

- [ ] **Step 5: Commit**

```bash
git add core/tooling/service.py tests/tooling/test_service.py
git commit -m "phase 4B: machine-readable fields on service needs_pin result"
```

---

## Task 5: `run_tool_loop` (testable core of the re-prompt loop)

**Files:**
- Create: `core/tooling/autocall.py`
- Test: `tests/tooling/test_tool_autocall.py` (append)

- [ ] **Step 1: Write the failing tests (append)**

```python
import asyncio


class _FakeTooling:
    """Serves a list of pending calls per round; advance() loads the next round."""
    def __init__(self, rounds):
        self._rounds = rounds
        self._i = 0
        self._rej = []

    def get_pending_tool_calls(self):
        return self._rounds[self._i] if self._i < len(self._rounds) else []

    def get_rejections(self):
        return self._rej

    def advance(self):
        self._i += 1


def _run(**kw):
    from core.tooling import autocall
    base = dict(sensitivity="personal", task_tag="chat_task", model="qwen")
    base.update(kw)
    return asyncio.run(autocall.run_tool_loop(**base))


def test_loop_ok_reprompts_and_synthesizes():
    tooling = _FakeTooling([[{"tool_id": "filesystem", "method": "list_directory",
                              "args": {"path": "X"}}], []])
    calls, routed = [], []

    def call_tool(u, t, m, a):
        calls.append((t, m, a)); return {"status": "ok", "result": ["a.txt", "b.txt"]}

    def router(convo, s, t, model):
        routed.append(convo); return ("You have a.txt and b.txt.", "META2")

    def process_output(reply):
        tooling.advance(); return {"response": reply, "suppress": False}

    final, meta, pin = _run(
        username="switch", tooling=tooling, convo=[{"role": "user", "content": "ls"}],
        reply="checking", raw_reply="checking [TOOL: x]", route_meta="META1",
        router=router, call_tool=call_tool, process_output=process_output,
        clean_reply=lambda x: x)
    assert calls == [("filesystem", "list_directory", {"path": "X"})]
    assert final == "You have a.txt and b.txt."
    assert meta == "META2" and pin == []
    assert len(routed) == 1
    # the re-prompt convo carries Pike's own call + the tool result
    assert any(m["role"] == "assistant" and "[TOOL: x]" in m["content"] for m in routed[0])
    assert any(m["role"] == "system" and "a.txt" in m["content"] for m in routed[0])


def test_loop_needs_pin_appends_note_no_reprompt():
    tooling = _FakeTooling([[{"tool_id": "filesystem", "method": "write_file",
                              "args": {}}], []])
    routed = []

    def call_tool(u, t, m, a):
        return {"status": "needs_pin", "tool_id": "filesystem",
                "method": "write_file", "required_tier": "write_destructive",
                "message": "..."}

    def router(convo, s, t, model):
        routed.append(convo); return ("x", "M")

    final, meta, pin = _run(
        username="switch", tooling=tooling, convo=[{"role": "user", "content": "w"}],
        reply="on it", raw_reply="on it", route_meta="M0",
        router=router, call_tool=call_tool,
        process_output=lambda r: {"response": r, "suppress": False}, clean_reply=lambda x: x)
    assert "needs your PIN" in final and "write_file" in final
    assert routed == []                # only needs_pin -> no re-prompt
    assert meta == "M0" and len(pin) == 1


def test_loop_error_is_fed_back():
    tooling = _FakeTooling([[{"tool_id": "time", "method": "get_current_time",
                              "args": {}}], []])
    routed = []

    def call_tool(u, t, m, a):
        return {"status": "error", "message": "boom"}

    def router(convo, s, t, model):
        routed.append(convo); return ("sorry, that failed", "M2")

    def process_output(reply):
        tooling.advance(); return {"response": reply, "suppress": False}

    final, meta, pin = _run(
        username="switch", tooling=tooling, convo=[{"role": "user", "content": "t"}],
        reply="checking", raw_reply="checking", route_meta="M0",
        router=router, call_tool=call_tool, process_output=process_output,
        clean_reply=lambda x: x)
    assert len(routed) == 1
    assert any(m["role"] == "system" and "failed: boom" in m["content"] for m in routed[0])
    assert final == "sorry, that failed"


def test_loop_round_cap_stops_at_three():
    always = [{"tool_id": "time", "method": "get_current_time", "args": {}}]

    class Always:
        def get_pending_tool_calls(self): return always
        def get_rejections(self): return []

    n = {"c": 0}

    def call_tool(u, t, m, a):
        n["c"] += 1; return {"status": "ok", "result": ["t"]}

    def router(convo, s, t, model):
        return ("still going", "M")

    final, meta, pin = _run(
        username="switch", tooling=Always(), convo=[{"role": "user", "content": "x"}],
        reply="r", raw_reply="r", route_meta="M0",
        router=router, call_tool=call_tool,
        process_output=lambda r: {"response": r, "suppress": False},
        clean_reply=lambda x: x)
    assert n["c"] == 3                  # exactly max_rounds executions


def test_loop_exception_falls_back_to_preloop_reply():
    class Always:
        def get_pending_tool_calls(self):
            return [{"tool_id": "time", "method": "get_current_time", "args": {}}]
        def get_rejections(self): return []

    def call_tool(u, t, m, a):
        raise RuntimeError("kaboom")

    def router(convo, s, t, model):
        return ("unused", "M")

    final, meta, pin = _run(
        username="switch", tooling=Always(), convo=[{"role": "user", "content": "x"}],
        reply="preloop reply", raw_reply="preloop", route_meta="M0",
        router=router, call_tool=call_tool,
        process_output=lambda r: {"response": r, "suppress": False},
        clean_reply=lambda x: x)
    assert final == "preloop reply"     # degraded gracefully, no raise
    assert meta == "M0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tooling/test_tool_autocall.py -k loop -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.tooling.autocall'`

- [ ] **Step 3: Create `core/tooling/autocall.py`**

```python
"""
Tool Autocall Loop (Phase 4B) — runs Pike's [TOOL:] calls and feeds results back.

Kept dependency-injected (router/call_tool/process_output are passed in) so the
control flow is unit-testable without a real LLM, session, or subprocess. The
chat pipeline wires in the real implementations.
"""

import asyncio
import logging

logger = logging.getLogger("aegis.tooling.autocall")

MAX_TOOL_ROUNDS = 3


def _format_tool_result(result):
    """Render a tool result (list of text lines, or anything) for Pike's context."""
    if isinstance(result, list):
        return "\n".join(str(r) for r in result) or "(empty)"
    return str(result)


async def run_tool_loop(*, username, tooling, convo, reply, raw_reply, route_meta,
                        router, call_tool, process_output, clean_reply,
                        sensitivity, task_tag, model, max_rounds=MAX_TOOL_ROUNDS):
    """Execute pending [TOOL:] calls, thread results back, and re-prompt.

    Args (all injected for testability):
      tooling: object with get_pending_tool_calls() / get_rejections().
      convo: message list that produced `raw_reply` (the first LLM reply).
      reply: the cleaned first reply (fallback if the loop does nothing/raises).
      raw_reply: the uncleaned first reply (carries the [TOOL:] tag into context).
      route_meta: RouteMeta of the first call (updated as re-prompts route).
      router(convo, sensitivity, task_tag, model) -> (raw_reply, route_meta).
      call_tool(username, tool_id, method, args) -> service result dict.
      process_output(reply) -> registry process_output dict (re-parses [TOOL:]).
      clean_reply(raw) -> cleaned string.

    Returns (final_reply, route_meta, pin_notes).
    """
    pin_notes = []
    rounds = 0
    try:
        while tooling.get_pending_tool_calls() and rounds < max_rounds:
            rounds += 1
            result_msgs = []
            for call in tooling.get_pending_tool_calls():
                res = await asyncio.to_thread(
                    call_tool, username, call["tool_id"], call["method"], call["args"])
                status = res.get("status")
                if status == "ok":
                    result_msgs.append(
                        f"Tool result for {call['tool_id']}.{call['method']}: "
                        f"{_format_tool_result(res.get('result'))}")
                elif status == "needs_pin":
                    pin_notes.append(
                        f"🔒 {res.get('method', call['method'])} on "
                        f"{res.get('tool_id', call['tool_id'])} needs your PIN — "
                        f"reply /tools pin <your vault PIN> to run it.")
                else:
                    result_msgs.append(
                        f"Tool {call['tool_id']}.{call['method']} failed: "
                        f"{res.get('message', 'error')}")
            for rej in tooling.get_rejections():
                result_msgs.append(f"(You tried {rej}, which isn't an available tool.)")
            if not result_msgs:            # only needs_pin this round → nothing to synthesize
                break
            convo = convo + [
                {"role": "assistant", "content": raw_reply},
                {"role": "system", "content": "\n".join(result_msgs)},
            ]
            raw_reply, route_meta = await asyncio.to_thread(
                router, convo, sensitivity, task_tag, model)
            reply = clean_reply(raw_reply)
            out = process_output(reply)
            if not out.get("suppress"):
                reply = out["response"]
    except Exception as e:
        logger.error("Tool autocall loop error: %s", e)
        # fall through — `reply` holds the last good (or pre-loop) answer
    if pin_notes:
        reply = (reply + "\n\n" + "\n".join(pin_notes)).strip()
    return reply, route_meta, pin_notes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/tooling/test_tool_autocall.py -k loop -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add core/tooling/autocall.py tests/tooling/test_tool_autocall.py
git commit -m "phase 4B: run_tool_loop — bounded tool re-prompt loop (testable core)"
```

---

## Task 6: Wire `run_tool_loop` into the chat pipeline

**Files:**
- Modify: `server/chat_pipeline.py`
- Test: full suite (Task 7) — the loop's logic is covered by Task 5; this task is the wiring, verified by no-regression + live smoke.

- [ ] **Step 1: Add imports**

At the top of `server/chat_pipeline.py`, with the other `core.*` imports, add:
```python
from core.tooling import service as tool_service
from core.tooling.autocall import run_tool_loop
```

- [ ] **Step 2: Insert the wiring after the first `process_output`**

In `process_chat`, find the block (around line 187-189):
```python
        # Run through output protocols
        output_result = session.protocol_registry.process_output(reply, proto_context)
        if not output_result.get("suppress"):
            reply = output_result["response"]
```
Immediately AFTER that block (and before the `# Extract bracket command actions` block), insert:
```python
        # --- Phase 4B: tool auto-call loop (feed tool results back to Pike) ---
        tooling_proto = session.protocol_registry.get("tooling")
        if tooling_proto is not None and tooling_proto.get_pending_tool_calls():
            def _router(convo, sensitivity, task, model):
                return router_chat_with_meta(convo, sensitivity=sensitivity,
                                             task=task, model=model)

            def _process_output(r):
                return session.protocol_registry.process_output(r, proto_context)

            reply, route_meta, _pin_notes = await run_tool_loop(
                username=user_id, tooling=tooling_proto,
                convo=list(messages_to_send), reply=reply, raw_reply=reply_content,
                route_meta=route_meta,
                router=_router, call_tool=tool_service.call_tool,
                process_output=_process_output,
                clean_reply=lambda rc: session.clean_reply(rc, mode=turn.mode),
                sensitivity="personal", task_tag=task_tag,
                model=CONFIG["model"]["chat"])
```

Notes for the implementer:
- `messages_to_send`, `reply_content`, `route_meta`, `task_tag`, `turn`, `user_id`,
  `CONFIG`, `router_chat_with_meta` are all already in scope at that point (defined
  earlier in `process_chat`). Do not redefine them.
- The pin notes are already folded into `reply` by `run_tool_loop`; `_pin_notes` is
  unused (prefixed `_`).
- The existing bracket-action extraction (`bracket_proto.get_pending_actions()`) runs
  AFTER this block, so it correctly reflects the final synthesized reply.
- The ☁ cloud suffix logic (`route_meta.backend_used == "cloud"`) below is unchanged and
  now reflects the last re-prompt's backend.

- [ ] **Step 3: Import-graph + targeted checks**

Run: `python -c "import server.chat_pipeline; import server.app; print('imports OK')"`
Expected: `imports OK` (no circular-import error from the new top-level imports)

Run: `python -m pytest tests/tooling -q`
Expected: all tooling tests pass (Task 5 loop tests + earlier tasks).

- [ ] **Step 4: Commit**

```bash
git add server/chat_pipeline.py
git commit -m "phase 4B: wire tool autocall loop into the chat pipeline"
```

---

## Task 7: Full-suite verification + live smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the entire suite**

Run: `python -m pytest -q`
Expected: 501 baseline + the new 4B tests, all passing, no regressions.

- [ ] **Step 2: Import graph**

Run: `python -c "import server.app; import core.session; import core.agent; print('imports OK')"`
Expected: `imports OK`

- [ ] **Step 3: Live smoke (with the user, running server)**

Restart the server on the branch so it loads 4B, then in the web-UI chat (with `time` +
`filesystem` already installed from the 4A smoke, or reinstall):
1. Ask in natural language: **"what time is it in Vancouver?"** → Pike should emit a
   time tool call under the hood and answer with the actual time (no visible `[TOOL:]`).
2. Ask: **"what files are in my Documents folder?"** → Pike lists real files.
3. Ask: **"read my aegis_test.txt in Documents and tell me what it says"** → Pike answers
   "hi" (or whatever it contains) — a read chained into an answer.
4. Ask: **"make a file called pike_note.txt in Documents with the text hello"** (use a
   single-word value — see the parser note below) → Pike attempts the write, which
   soft-blocks; the reply ends with the 🔒 PIN note. Then `/tools pin <PIN>` → the file is
   written (verify it exists).
5. Confirm the audit log (`data/users/dustin/mcp_tools/audit.jsonl`) shows the auto-calls
   with outcomes, and that no `[TOOL:]` plumbing leaked into any visible reply.
6. Toggle test: set `tooling.autocall_enabled` false in `core_config.json`, restart, ask
   "what time is it in Vancouver?" → Pike answers from memory / says he can't, and does
   NOT auto-call (verify no new audit entry). Set it back to true.

- [ ] **Step 4: Final commit if smoke drove tweaks**

```bash
git add -A
git commit -m "phase 4B: tool autocall verified live"
```

---

## Known limitation (v1, documented — not a blocker)

`_parse_kv` splits args on whitespace, so a multi-word value like
`content=hello from Pike` captures only `hello`. Single-word values work; multi-word
tool args are a future parser improvement (quoting or last-key-consumes-rest). Fine for
4B v1 — the injected hints steer Pike toward simple `key=value` args.

## Definition of Done

- Installed tools' methods are injected into Pike's context only when tools are installed
  and `autocall_enabled` is on.
- Pike's `[TOOL: tool.method key=value]` is parsed, validated (installed + known method),
  stashed, and stripped from the visible reply; unknown tools/methods are rejected.
- `run_tool_loop` executes pending calls off the event loop, feeds results back, and
  re-prompts up to 3 rounds; `ok` synthesizes, `needs_pin` appends a PIN note without
  re-prompting, `error` is fed back, and any exception degrades to the pre-loop reply.
- `service.call_tool` needs_pin carries `tool_id`/`method`/`required_tier`.
- Writes soft-block and surface a `/tools pin` prompt; the user confirms and 4A executes.
- Full suite green; live smoke shows natural-language tool use with no plumbing leakage.
