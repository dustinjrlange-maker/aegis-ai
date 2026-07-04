# Tool Autocall Phase 4B — Design (Wave 2, part 2 of 2)

**Date:** 2026-07-03
**Status:** Approved design, ready for implementation plan
**Branch:** `phase-4/tool-autocall-4b`
**Builds on:** Phase 4A (merged to main `2625f23`) — MCP tool plumbing, 4-tier trust + PIN, audit, `service` layer.
**Parent:** `docs/superpowers/specs/2026-07-03-tool-discovery-4a-design.md`

## Goal

Pike autonomously calls installed tools mid-conversation and answers **from their
results**, within a single user turn. 4A gave Pike hands (`/tools` commands); 4B gives him
the reflex to reach for them when a request needs one.

## Scope

**In scope**
- Inject the list of *installed* tools' methods into the system prompt (only when tools
  are installed and the feature toggle is on).
- Parse a new `[TOOL: tool.method key=value …]` bracket from Pike's output.
- A bounded **re-prompt loop**: execute the requested tool off the event loop, feed the
  result back to Pike as a system message, let him synthesize (or call another tool),
  capped at 3 rounds per turn.
- Out-of-tier auto-calls (writes) soft-block via 4A and surface a PIN prompt to the user.
- A feature toggle to disable autocall without uninstalling tools.

**Out of scope (deferred / later phases)**
- Local-first-escalate-on-miss routing (Option 3) — documented as the planned next
  refinement; 4B reuses Wave 0's existing task-tier routing unchanged.
- `[TOOL_NEED: …]` intent detection + npm discovery (Phase 4C).
- Browser automation (4D). Keeping tool-result synthesis local for privacy (a future
  refinement — see Privacy note).

## Decisions locked (this session)

- **Routing:** reuse Wave 0. Tool-using turns are task-shaped, so they already route to
  cloud (Opus) when cloud is enabled and run on local qwen3:8b otherwise. No new router.
  Option 3 (local tries, escalate to Opus on a "miss") is the documented future add.
- **Round cap:** 3 tool rounds per user turn, then Pike answers with what it has.
- **Autocall default:** ON. Gated behind `tooling.autocall_enabled` so it can be turned
  off without uninstalling tools. Tools only inject when the user has ≥1 installed, so the
  blast radius is limited to deliberately-installed tools.
- **PIN on auto-call:** an out-of-tier auto-call soft-blocks exactly like 4A. Pike never
  auto-bypasses; he surfaces "reply `/tools pin …`" and the op is stashed by 4A.

## Architecture

Four pieces, plus one small 4A touch. All tool execution stays in the 4A `service` layer;
4B only decides *when* to call and threads results back to the LLM.

### 1. Tool-schema injection — `ToolingProtocol.process_input`

In 4A this returned a no-op. Now, when `tooling.autocall_enabled` is true AND the user has
≥1 installed tool, it injects a compact **"Available tools"** context block:

```
Available tools — emit [TOOL: tool.method key=value] on its own line to use one:
  filesystem.list_directory path=<dir> — list files in an approved directory
  filesystem.read_file path=<file> — read a file's contents
  time.get_current_time timezone=<tz> — current time in a timezone
Only call a tool when the user's request needs live data or an action you can't do from
memory. After a tool runs you'll see its result and can answer or call another tool.
```

- The method list comes from the installed tools' **catalog method hints** (a new small
  `method_hints` map in each catalog entry) filtered to what's installed. Kept short (8B
  fragility): cap at the installed tools' primary methods.
- If the toggle is off or nothing is installed → empty injection (4A behavior).

### 2. `[TOOL: …]` parse without executing — `ToolingProtocol.process_output`

In 4A this was a no-op. Now it scans the LLM output for `[TOOL: tool.method key=value …]`
using the same bracket style as `bracket_commands.py`:

- Regex: `\[TOOL:\s*([a-z_]+)\.([a-z_]+)\s*(.*?)\]` → tool_id, method, raw args.
- Validate: tool_id is installed (via `registry.get`) and method is known for it
  (in the catalog's `method_tiers` or `method_hints`). Unknown → record a rejection note
  (so the loop can nudge Pike) and do NOT stash a call.
- Parse args with 4A's `_parse_kv(tokens, split_commas=False)` (shared helper — extract it
  to a module function reusable by both the 4A command path and here).
- Stash a structured pending call `{tool_id, method, args}` in a per-instance list.
- Strip the `[TOOL: …]` tag from the visible reply (same whitespace cleanup as
  `bracket_commands.process_output`).
- **Does NOT execute** — execution is blocking (drives the MCPManager sync API) and must
  run off the event loop, which `process_output` (sync, on the loop) can't do.
- Expose `get_pending_tool_calls()` and `get_rejections()`; both cleared at the start of
  each `process_output`.

### 3. Re-prompt loop — `server/chat_pipeline.py`

New logic after the existing `process_output` call (~line 187), reached only when the
tooling protocol reports pending calls. Pseudocode:

```
rounds = 0
pin_notes = []
while tooling.get_pending_tool_calls() and rounds < MAX_TOOL_ROUNDS:  # MAX = 3
    rounds += 1
    result_msgs = []
    for call in tooling.get_pending_tool_calls():
        res = await asyncio.to_thread(service.call_tool, user_id, call.tool_id,
                                      call.method, call.args)
        if res["status"] == "ok":
            result_msgs.append(f"Tool result for {call.tool_id}.{call.method}: "
                               f"{_format(res['result'])}")
        elif res["status"] == "needs_pin":
            pin_notes.append(f"🔒 {call.method} on {call.tool_id} needs your PIN — "
                             f"reply /tools pin <your vault PIN> to run it.")
            # op is already stashed by 4A trust; do NOT re-prompt this call
        else:  # error
            result_msgs.append(f"Tool {call.tool_id}.{call.method} failed: {res['message']}")
    for rej in tooling.get_rejections():
        result_msgs.append(f"(You tried {rej}, which isn't an available tool.)")
    if not result_msgs:      # only needs_pin / nothing to feed back → stop
        break
    # re-call the LLM with the tool results as authoritative system context
    reply = await asyncio.to_thread(router_chat_with_meta,
                                    messages + [system(result_msgs)], sensitivity, task_tag, ...)
    reply = clean_reply(reply)
    process_output(reply)    # may stash more tool calls → loop
final_reply = reply + ("\n\n" + "\n".join(pin_notes) if pin_notes else "")
```

- `MAX_TOOL_ROUNDS = 3`. On exhaustion, use the last reply (Pike answers with what he has)
  plus any pin_notes.
- Each re-prompt reuses the turn's existing `task_tag` and `sensitivity` (no new routing).
- The tool-result system messages are appended to `session.messages` context for the
  re-prompt but the raw `[TOOL:]`/result plumbing is not shown to the user — only Pike's
  final synthesized reply (plus pin_notes) is returned/persisted.
- The whole loop is wrapped in try/except; on any failure it falls back to the pre-loop
  reply so the turn never crashes.

### 4. Feature toggle

`core/config/core_config.json` → `tooling.autocall_enabled: true` (added to the existing
`tooling` block). Read via a helper with a safe default of true. When false: no schema
injection, no `[TOOL]` parsing, loop never entered. 4A `/tools` commands unaffected.

### 5. Small 4A touch — machine-readable `needs_pin`

`service.call_tool`'s `needs_pin` return currently carries only `status` + a human
`message`. Add `tool_id`, `method`, and `required_tier` fields so the loop builds the PIN
note without parsing prose. (This is the M4-lite item the 4A holistic review flagged for
4B.) The human `message` stays for the `/tools call` command path.

## Data flow

**Read:** "what's in my Documents?" → task turn → Pike emits
`[TOOL: filesystem.list_directory path=C:/Users/dusti/Documents]` → parsed + stripped →
off-loop `service.call_tool` → `ok` + file list → injected as system msg → re-prompt →
"You've got 3 files: a.txt, notes.md, budget.pdf." → no more brackets → done (1 user turn).

**Write:** "make a note foo.txt that says hi" → Pike emits
`[TOOL: filesystem.write_file path=…/foo.txt content=hi]` → `service.call_tool` →
`needs_pin` → reply gets "🔒 write_file on filesystem needs your PIN — reply /tools pin …"
appended; op stashed by 4A → user runs `/tools pin <PIN>` → 4A executes it (a second turn,
by design — Pike never auto-confirms a write).

## Error handling

- Tool `error` → fed back to Pike as a result message so he can recover or apologize.
- Unknown tool/method in a `[TOOL:]` → rejected at parse, nudged back so Pike stops
  retrying the phantom tool.
- 3-round cap prevents runaway loops; every real call is audited by 4A.
- Loop wrapped so a failure degrades to the pre-loop reply — the turn never crashes.
- Toggle off / no tools installed → feature fully dormant.

## Testing

No real LLM or subprocess (mock `router_chat_with_meta` + `service.call_tool`):
- **Injection:** appears only when a tool is installed AND toggle on; empty when toggle off
  or nothing installed (mock `registry`).
- **Parse:** `[TOOL: filesystem.list_directory path=X]` → one structured pending call with
  correct tool_id/method/args; tag stripped from visible reply; unknown tool/method →
  rejection recorded, no pending call; a non-TOOL bracket is ignored by this protocol.
- **Loop — ok:** mock router returns a reply with a `[TOOL:]`, mock service returns `ok`;
  assert result injected, re-prompt called once, final reply is the synthesized one, tag
  gone.
- **Loop — needs_pin:** service returns `needs_pin`; assert PIN note appended, NO re-prompt
  for that call, op-stash path exercised (mock).
- **Loop — error:** service returns `error`; assert error fed back and Pike re-prompted.
- **Round cap:** mock router that emits a `[TOOL:]` every time → assert exactly 3 executions
  then stop.
- **Toggle off:** a `[TOOL:]` in output is left intact / not executed; injection empty.
- **Fallback:** loop exception → returns the pre-loop reply, no crash.

## Routing & privacy notes

- **Routing (reused):** `classify()` → `route_task_tag()` already sends task-shaped turns
  to cloud when enabled. Tool turns ride that. **Future (Option 3):** when cloud is on but
  a tool-shaped turn on local qwen emits no/garbled `[TOOL:]`, silently re-run on Opus —
  deferred, noted here so the seam isn't designed against it.
- **Privacy:** when cloud is ON, tool-result re-prompts send results (including file
  contents from `filesystem.read_file`) to the cloud under the turn's `personal`
  sensitivity. Cloud OFF (default) keeps everything local. Future refinement: run the
  synthesis re-prompt locally regardless of the decision turn's routing, so tool-result
  data never leaves — deferred to keep 4B aligned with the chosen Option 1.

## Files touched

- `core/protocols/tooling.py` — real `process_input` (schema injection) + `process_output`
  (`[TOOL:]` parse/stash/strip) + `get_pending_tool_calls`/`get_rejections`; extract
  `_parse_kv` to a shared helper.
- `core/tooling/catalog.json` — add `method_hints` to each entry (arg hints for injection).
- `core/tooling/service.py` — add `tool_id`/`method`/`required_tier` to the `needs_pin`
  return.
- `server/chat_pipeline.py` — the bounded re-prompt loop.
- `core/config/core_config.json` — `tooling.autocall_enabled: true`.
- `tests/tooling/test_tool_autocall.py` (new), plus additions to
  `tests/tooling/test_tooling_protocol.py`.
