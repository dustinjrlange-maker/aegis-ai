# Tool Discovery Phase 4A — Design (Wave 2, part 1 of 2)

**Date:** 2026-07-03
**Status:** Approved design, review fixes folded in
**Branch:** `phase-4/tool-discovery-4a`
**Parent plan:** `docs/plans/tool-discovery-plan.md` (all 6 open questions answered 2026-06-27)
**Session scope:** 4A (this spec) → then 4B (`[TOOL: …]` bracket protocol) as its own spec/plan cycle in the same session.

## Goal

Tools can be browsed, installed, trust-classified, and **manually** called via `/tools`
slash commands and `/api/tools/*` endpoints. Real MCP subprocesses for both starter
tools. Pike does NOT auto-call tools yet — that is 4B.

## Decisions locked (from Switch's 2026-06-27 answers + this session)

- **Real socket + both real tools.** Official `mcp` Python SDK (already installed).
  `time` = pip `mcp-server-time` (Python). `filesystem` = Node
  `@modelcontextprotocol/server-filesystem` via npx (Node v24 + npx already installed —
  no new runtime).
- **Catalog-only discovery** (no npm/pypi search). Claude curates `catalog.json`; the
  curated catalog IS the vet-before-install gate.
- **Tool Wishlist write-side only** in 4A (weekly Claude vet routine = 4A.5, later).
- **PIN soft-block escalation** for out-of-tier operations, reusing
  `core/vault_pin.py`'s bcrypt PIN. Per-operation, never permanent re-tiering.
- **4-tier trust model** per the parent plan, unchanged.

## Architecture

### The load-bearing component: `MCPManager` (dedicated loop + per-server tasks)

MCP stdio sessions are async; slash commands are sync (`handle_command` in the protocol
registry) and FastAPI endpoints are async on the server's own loop. `MCPManager` bridges
all of it:

- Runs a **dedicated asyncio event loop in a background thread** (started lazily on
  first use, stopped at server shutdown).
- Exposes a **synchronous API**: `ensure_started(user, tool_id)`, `list_tools(user,
  tool_id)`, `call(user, tool_id, method, args, timeout)`, `stop(user, tool_id)`,
  `shutdown()`. Callers use `asyncio.run_coroutine_threadsafe(...).result(timeout)`.
  This serves the sync command path and async endpoints identically (endpoints wrap
  calls in `asyncio.to_thread` to avoid blocking the server loop).

**Context-lifetime rule (implementation-critical):** `stdio_client(...)` and
`ClientSession` are async context managers that MUST be entered and exited **within the
same task** — anyio raises `RuntimeError: Attempted to exit cancel scope in a different
task` otherwise. Therefore each live server gets a **dedicated long-running task** on the
manager loop that:
1. Enters `stdio_client` + `ClientSession` contexts and calls `session.initialize()`.
2. Loops over an `asyncio.Queue` of requests: `(method, args, future)` — calls
   `session.call_tool(method, args)` (or `list_tools`) and resolves the future.
3. On a sentinel/stop request or fatal error, exits the contexts **in that same task**
   and marks the session dead.

No naive "open context in one coroutine, call from another" — the queue/future handshake
is the required shape.

**Per-user session keying:** live sessions are keyed by **`(username, tool_id)`**, not
`tool_id` alone. Filesystem's approved directories are per-user config baked into the
spawn args, so a global session would leak one user's directory scope to another. The
per-user registry already exists; the manager matches it.

**Lifecycle:** spawn lazily on first call; stay warm; killed on `shutdown()` (wired into
the FastAPI lifespan next to the Telegram bot stop). Spawn failure → session marked
unavailable with the error message stored; calls return a clear error; no crash.

**Timeouts:** call timeout 10s default (per-call override allowed); spawn/initialize
timeout 60s (npx cold-start can download the package).

### Module layout — `core/tooling/` (new)

| File | Responsibility |
|---|---|
| `mcp_manager.py` | Background loop thread + per-server tasks (above). The ONLY place MCP SDK is imported. |
| `catalog.py` | Loads/searches `catalog.json`. |
| `catalog.json` | Vetted tools: id, name, description, launch spec (command/args/env), recommended tier, per-method tier map, required install config, author, source URL. |
| `registry.py` | Per-user installed-tools registry → `data/users/<user>/mcp_tools/registry.json`: `{tool_id: {trust_tier, config, installed, last_used, call_count}}`. Load/save/install/uninstall/touch. |
| `trust.py` | Tier constants; per-method required-tier resolution (from catalog); `check(user, tool_id, method) -> allow / needs_confirm / needs_pin / deny`; pending-escalation stash (`(user) -> pending op`, single slot, 5-min expiry); `confirm_with_pin(user, pin)` → verifies via `core.vault_pin.verify_vault_pin`, executes ONCE, clears. |
| `wishlist.py` | Append an unmet tool need (timestamp, user, description) to the wishlist markdown file. Path from `core_config.json` key `tooling.wishlist_path` — NOT hardcoded (Aegis is distributable). Default: `data/tool_wishlist.md`; Switch's config points it at `D:\ObsidianBrain\10-Projects\aegis-tool-wishlist.md`. |
| `audit.py` | Append-only JSONL log → `data/users/<user>/mcp_tools/audit.jsonl`: timestamp, tool, method, args (PIN-redacted), outcome (ok/error/denied/pin_escalated), duration_ms. Denials and escalations are logged, not just successes. |

### Protocol — `core/protocols/tooling.py` (new)

Standard Protocol ABC subclass, PRIORITY_NORMAL, registered in `core/agent.py`.
Slash commands (via `get_commands()`):

- `/tools list` — installed tools + tiers + status
- `/tools find <query>` — search catalog
- `/tools install <tool_id> [config…]` — shows tier + what it grants, installs, warm-up
- `/tools uninstall <tool_id>`
- `/tools call <tool_id> <method> [key=value …]` — manual invocation through trust check
- `/tools wish <description>` — append to wishlist
- `/tools pin <PIN>` — confirm the pending out-of-tier operation

**PIN redaction (security-critical):** slash commands return via the early-return path in
`chat_pipeline.process_chat` (line ~60), which happens BEFORE `session.messages.append`
— verify this in implementation so the raw PIN never enters message history or
transcripts. Additionally: the audit log and any logger line record `/tools pin ****`,
never the digits. If the user has no vault PIN set, `/tools pin` explains how to set one
(existing vault PIN flow) instead of failing cryptically.

### Trust flow (concrete)

| Tier | Example | Flow |
|---|---|---|
| `read_scoped` | time.* | auto-approve |
| `read_broad` | filesystem read methods | approved once at install |
| `write_scoped_undoable` | (none in 4A catalog) | approve once per session |
| `write_destructive` | filesystem write methods (out-of-tier) | PIN escalation |

Filesystem installs at `read_broad`. Its write methods (`write_file`, `edit_file`,
`move_file`, `create_directory`) are mapped `write_destructive` in the catalog's
per-method map → calling one soft-blocks: reply explains it's outside the granted tier
and asks for `/tools pin <PIN>`. Correct PIN executes that ONE stashed operation, then
clears. Wrong PIN: error, stash kept until 5-min expiry. No permanent re-tier.

### Starter catalog (2 entries)

1. **time** — launch: `[sys.executable, "-m", "mcp_server_time"]` (pip package
   `mcp-server-time`; use the running interpreter, not "python" from PATH). Tier
   `read_scoped`. No config. Methods: `get_current_time`, `convert_time`.
2. **filesystem** — launch: `[<resolved npx>, "-y", "@modelcontextprotocol/server-filesystem", *approved_dirs]`.
   Tier `read_broad`; install prompts for the approved directories (stored in per-user
   registry config). **Windows spawn rule:** resolve via `shutil.which("npx")` (finds
   `npx.cmd`) — bare `"npx"` does not spawn on Windows. Same lesson as
   `audio_io._resolve_ffmpeg`.
   **Install-time warm-up:** after registry write, spawn the server once and
   `list_tools` (60s timeout) so the npx package download happens at install, not on the
   first real call; report warm-up success/failure in the install reply.

### Server endpoints (`server/app.py`)

`GET /api/tools/catalog` · `GET /api/tools/installed` · `POST /api/tools/install`
(body `{tool_id, config?}`) · `POST /api/tools/uninstall/{tool_id}` ·
`POST /api/tools/call` (body `{tool_id, method, args}`; trust-checked; PIN escalation
returns a `needs_pin` status rather than executing) · `GET /api/tools/audit`.
All auth-gated the same way as existing user endpoints; handlers wrap manager calls in
`asyncio.to_thread`.

### Error handling

- Spawn/initialize failure → tool marked unavailable + stored reason; `/tools list`
  shows it; calls return the reason. Never crashes chat.
- Call timeout → error result + audit entry; session kept (one timeout ≠ dead server);
  session restarted on next call if the process died.
- Trust denial / PIN expiry → informative chat reply + audit entry.
- Missing `mcp` SDK or missing npx → catalog entry shows "unavailable: <reason>" at
  install time.

### Testing

Unit (no subprocess, mock the manager/SDK):
- catalog load + search; registry CRUD roundtrip; per-method tier resolution.
- trust decisions for all four tiers; PIN escalation: wrong PIN blocks, right PIN
  executes once and clears, second use requires new escalation, 5-min expiry.
- PIN redaction: audit entry and any log line for `/tools pin 123456` contains `****`
  and not the digits.
- wishlist append (path from config, tmp_path override); audit append includes denials.
- slash-command parsing/dispatch with a mocked manager.
- MCPManager queue/future handshake with a fake in-process "server task" (no real MCP).

Integration (real subprocess, `@pytest.mark.skipif` guards):
- Real `time` server: spawn → `get_current_time` → assert parseable timestamp (skip if
  `mcp_server_time` not importable).
- Real filesystem server via npx against `tmp_path`: `list_directory` returns a seeded
  file (skip if `shutil.which("npx")` is None; generous timeout for cold npx).

Dependencies: add `mcp>=1.0` and `mcp-server-time` to `requirements.txt`.

## Out of scope (explicitly)

- 4B `[TOOL: …]` bracket protocol, system-prompt tool injection, Pike-initiated calls —
  **next spec, same session.**
- Wishlist vet routine (4A.5), npm/pypi discovery, browser automation (4D), Tools panel
  UI, rate limiting.
