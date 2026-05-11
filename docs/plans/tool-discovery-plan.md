# Tool Discovery Primitive — Plan

**Task #4 in current cycle.** Drafted 2026-04-28.

## Vision

Pike recognizes when he needs a capability he doesn't have, proposes a tool that provides it, gets your approval, installs it, and uses it — with you as ultimate gatekeeper. Built on the **MCP standard** (Model Context Protocol) so Pike inherits the entire MCP ecosystem instead of us writing one-off integrations forever.

This is the difference between a chatbot and a real assistant: the chatbot says "I can't do that"; the assistant says "I'll need a tool for that — here's one, want me to install it?"

## The Five Conceptual Layers

Any tool-discovery system has these layers. Naming them up front so we don't conflate them in design.

1. **Intent detection** — recognizing that a request needs a tool Pike doesn't currently have.
2. **Discovery** — finding a tool that provides the needed capability.
3. **Approval & install** — getting your consent, configuring credentials, registering the tool.
4. **Invocation** — Pike calling the tool's functions during conversation.
5. **Feedback & audit** — results returned, logged, error-handled, undone if needed.

Each phase below builds these layers in order.

---

## The Five Critical Decisions

### 1. MCP vs. native integrations

**Decision: Hybrid — MCP for the discovery/expansion layer, native for core stuff already built.**

Pros of MCP standard: massive existing ecosystem (filesystem, Gmail, Slack, GitHub, Postgres, browser, etc.), one integration unlocks many tools, future-proof.

Cons: designed primarily for frontier models (Claude/GPT) — qwen3:8b's tool-use reliability is weaker. Most servers are Node/Python processes that have to run on the user's machine.

**What we keep native**: Google (already done), web search, weather. Those are baked-in capabilities, not "discoverable" tools.

**What goes through MCP**: filesystem, github, slack, browser automation, future stuff we haven't thought of. Anything that benefits from being a swappable, community-maintained piece.

### 2. Where intent detection happens

**Decision: Phased — start user-initiated, then add LLM-driven, then patterns for common cases.**

- **Phase 4A**: User-initiated only. Slash commands: `/tools find`, `/tools install`, `/tools call`. No magic. Build the foundation.
- **Phase 4B**: Pike emits `[TOOL_NEED: description]` bracket commands when he can't fulfill a request. Less reliable but "magical" when it works.
- **Phase 4C**: Pattern detection (regex/keywords) for the most common 10-20 tool needs — skip the LLM round-trip, fire instantly.

qwen3:8b at 8B parameters is not reliable at "deciding I need a tool" — frontier models do this well, smaller ones hallucinate that they CAN do something they can't. So Phase 4B will need lots of tuning. Don't skip 4A.

### 3. Trust model

**Decision: Per-tool trust profile, four tiers.**

| Tier | Examples | Approval flow |
|---|---|---|
| **read-only, scoped** | weather, web search, time, calendar read | auto-approve |
| **read-only, broad** | filesystem read, repo browse | approve once per tool, then trusted |
| **write, scoped, undoable** | create draft email, add calendar event, write to scoped folder | approve once per session, full audit log |
| **write, broad or destructive** | delete files, send messages, run shell, browser automation | approve every single call with explicit "are you sure" |

Each installed tool gets classified into one tier when installed. You can downgrade trust later but never silently upgrade — privilege escalation always requires explicit consent.

Stored in `data/users/<user>/mcp_tools/registry.json`:
```json
{
  "filesystem": {
    "trust_tier": "read_broad",
    "approved_paths": ["~/Documents", "~/Projects"],
    "installed": "2026-04-28T...",
    "last_used": "...",
    "call_count": 0
  }
}
```

### 4. How Pike actually calls tools

**Decision: Pike-as-router (Phase 4B), evolving toward Pike-as-orchestrator (Phase 4D+).**

- **Router** (4B): Pike says "use filesystem.read_file with path X". System executes, displays result. Pike narrates. Single round-trip per tool call.
- **Orchestrator** (4D+): Pike emits Claude-style structured tool calls, gets results fed back as conversation context, plans multi-step workflows. Requires either a smarter brain or careful prompt engineering with qwen3:8b.

For 4B, the bracket protocol already exists in Aegis. We add a new `TOOL` bracket: `[TOOL: filesystem.read_file path=/Users/dusti/Documents/foo.txt]`. The handler executes via MCP client, returns result as "tool_result" message that Pike sees on the next turn.

### 5. Discovery source — where tools come from

**Decision: Curated catalog first, fallback to npm/pypi search with prominent "unverified" warning.**

- **Curated catalog** at `core/tooling/catalog.json` — vetted MCP servers we ship with Aegis, pre-approved by us. Officially Anthropic-maintained ones (filesystem, github, postgres, brave, time) plus well-known community ones.
- **npm/pypi search** — for tools not in the catalog, we can search for `mcp-server-X` packages. But these come with a banner: *"This tool is not vetted by Aegis. Read its README, check its author, and only install if you trust the source."*

Catalog entries include: name, description, what permissions it needs, recommended trust tier, install command, author, source URL, version compatibility.

---

## Phase Breakdown

### Phase 4A — Foundation (1-2 evenings)

**Goal**: Tools can be installed, listed, and manually called via slash commands. No automatic intent detection. No LLM tool-calling. Just plumbing.

**New files:**
- `core/tooling/__init__.py`
- `core/tooling/mcp_client.py` — MCP protocol client wrapper (uses official `mcp` Python SDK)
- `core/tooling/registry.py` — manages installed-tools registry, per-user
- `core/tooling/catalog.py` — loads `catalog.json`, search/filter
- `core/tooling/catalog.json` — vetted MCP servers (start with: filesystem, time)
- `core/tooling/trust.py` — trust tier definitions, approval flow logic
- `core/protocols/tooling.py` — Aegis protocol that exposes slash commands
- `data/users/<user>/mcp_tools/registry.json` — created on first use

**Server endpoints:**
- `GET /api/tools/catalog` — browse vetted tools
- `GET /api/tools/installed` — list user's installed tools
- `POST /api/tools/install` — install a tool from catalog (body: `{tool_id, trust_tier?}`)
- `POST /api/tools/uninstall/{tool_id}` — remove a tool
- `POST /api/tools/call` — manually invoke (body: `{tool_id, method, args}`)
- `GET /api/tools/audit` — call log

**Slash commands** (in chat):
- `/tools list` — what's installed
- `/tools find <query>` — search catalog
- `/tools install <tool_id>` — install (UI shows trust tier, prompts confirm)
- `/tools call <tool_id> <method> <args>` — manual test

**Starter tools shipped in catalog:**
1. `time` — `now()`, `timezone()`, `format(iso, format)`. Read-only, auto-approve.
2. `filesystem` — `list_files`, `read_file` (read-only-broad tier, you approve directory list at install).

**Test plan**: install filesystem, run `/tools call filesystem list_files path=~/Documents`, verify file list appears.

### Phase 4B — Pike calls tools (1 evening)

**Goal**: Pike learns to use installed tools naturally during chat. Bracket protocol routes tool calls through MCP.

**New files / changes:**
- `core/protocols/bracket_command.py` (existing) — register `TOOL` handler
- `core/tooling/executor.py` — bracket handler logic, calls MCP client, formats result for Pike to see
- `core/personality/pack_loader.py` (modify) — extend `build_system_prompt` to inject available-tool list
- `packs/personalities/pike/config_overlay.json` — Pike-flavored language for tool calls

**Mechanism:**
1. Pike's system prompt includes `Available tools: filesystem.list_files(path), filesystem.read_file(path), time.now(), ...`
2. User asks "what's in my Documents folder?"
3. Pike responds (in his voice) with `[TOOL: filesystem.list_files path=~/Documents]` somewhere in his message
4. Bracket protocol intercepts, calls MCP, gets result
5. Result injected as system message: `Tool result for filesystem.list_files: ['file1.txt', 'file2.pdf', ...]`
6. Pike's NEXT turn synthesizes the result into a reply

**Trust enforcement**: each tool call checks the registry's trust profile. Auto-approve, approve-once-per-session, or per-call confirm dialog (UI prompt or chat-line confirmation depending on context).

**Test plan**: Ask Pike "what's in my Documents folder?" — he should call filesystem.list_files and answer.

### Phase 4C — Intent detection & discovery (1-2 evenings)

**Goal**: Pike notices when he needs a tool he doesn't have. User says "find me one." Pike searches and proposes.

**New files / changes:**
- Pattern detector in `core/tooling/intent.py` — regex/keyword for common tool needs
- `[TOOL_NEED: description]` bracket handler — Pike emits when he can't fulfill
- Catalog search → npm/pypi fallback in `core/tooling/discovery.py`
- New endpoint `POST /api/tools/discover` — body `{description}`, returns ranked candidates

**Flow:**
1. User: "rename all .jpg files in my Pictures to lowercase"
2. Pike (via pattern detection OR LLM bracket): emits `[TOOL_NEED: file rename / batch file operations]`
3. Aegis searches catalog → finds nothing → searches npm for `mcp-server-files` etc.
4. UI shows top 3 candidates with trust info, asks which to install
5. User picks, approves, tool installs, Pike resumes the original task

**npm search safety**: only search for packages matching `mcp-server-*` or tagged `model-context-protocol`. Show download count, last update, author. Never auto-install — always require explicit user pick.

### Phase 4D — Polish & expansion (ongoing)

- More catalog entries: github, brave-search, postgres, puppeteer (browser automation)
- Dedicated **Tools panel** in LCARS UI (list installed, view audit log, manage trust)
- Audit log viewer with filters (per-tool, per-day, success/failure)
- Trust profile editor (downgrade trust, revoke a tool, edit approved paths)
- Background tool process management (restart on crash, health checks)
- Rate limiting per tool (no runaway loops)

---

## File-level Architecture Summary

```
core/tooling/                    # NEW — all tool-discovery infrastructure
  __init__.py
  mcp_client.py                  # MCP protocol wrapper
  registry.py                    # installed-tools registry
  catalog.py                     # catalog loader + search
  catalog.json                   # vetted MCP servers
  trust.py                       # trust tiers & approval logic
  executor.py                    # bracket command -> MCP call -> result
  intent.py                      # pattern + LLM intent detection (Phase 4C)
  discovery.py                   # catalog + npm search (Phase 4C)

core/protocols/
  tooling.py                     # NEW — Aegis protocol, slash commands
  bracket_command.py             # MODIFY — add TOOL and TOOL_NEED handlers

core/personality/
  pack_loader.py                 # MODIFY — inject tool schemas into system prompt

server/
  app.py                         # MODIFY — add /api/tools/* endpoints

data/users/<user>/mcp_tools/    # NEW per-user
  registry.json                   # what's installed + trust profiles
  audit.jsonl                     # append-only call log
  <tool_id>/                      # per-tool config
```

---

## Open Questions for Switch

1. **Catalog trust authority**: I'd vet the initial catalog entries. Are you OK with that, or do you want to review every tool we ship before it lands in the catalog?

2. **npm search scope**: Comfortable with Aegis searching npm/pypi for unverified MCP servers (with prominent warnings)? Or strictly catalog-only?

3. **Tool processes**: MCP servers are separate processes. Comfortable with Aegis spawning Node/Python child processes when tools are invoked? They'd be kid-glove'd (only when called, killed when done) but it's a real footprint.

4. **Browser automation later**: Phase 4D could include Puppeteer/Playwright MCP for "Pike, book me a flight" type tasks. That's a major capability jump but also a major risk surface. Want it on the roadmap or off?

5. **Trust escalation**: If a tool is in `read_broad` tier and Pike tries to use it for a write operation (because the MCP server supports both), what happens? My default: hard-block, tell the user the tool needs trust upgrade.

6. **Phase 4A scope cut**: If 4A feels too big for one session, the smallest possible cut is: just `time` tool + slash commands + registry. Filesystem can come in 4A.5. Want me to plan it that way?

---

## What "Worth Putting Energy Into" Looks Like

If we ship Phase 4A only, you get:
- A clean tooling subsystem you can extend
- Two working tools (time, filesystem)
- Slash-command interface for tool management
- Foundation for everything else

That alone makes Pike meaningfully more capable than today (he can read files), and it's the smallest phase. **Phase 4A is the right "ship and stop, evaluate" point** — don't commit to 4B-4D until you've used 4A and know the abstraction works.

If 4A reveals that qwen3:8b is too weak to use tools reliably, that's the moment we revisit the local-only decision. The brain question and the tools question are linked: tools are what make a smaller brain useful, but tools also expose a smaller brain's weaknesses.

---

## Recommended Next Step

Review this plan from your phone. Pick answers to the 6 open questions. When you're at the desk and have tested the morning's work, we either:
- Start Phase 4A (1-2 evening commitment), or
- Adjust scope based on your answers, or
- Defer until later if you want to live with what's already shipped first.
