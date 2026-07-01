# Hybrid Local/Cloud LLM Router — Design

**Date:** 2026-06-30
**Status:** Approved, ready for planning
**Scope of this build:** Option A — seam + policy, **local-only** (cloud adapter stubbed). No real Anthropic call, no key storage. Flipping cloud on is a later, focused follow-up.

## Background

Aegis is local-first (Ollama `qwen3:8b`). The 2026-06-30 strategic shift greenlit an **opt-in, gated, transparent** cloud escalation (Claude API) for horsepower "when needed" — local stays the default, sensitive data stays local by default even after cloud exists.

Today there are **7 direct `ollama.chat` call sites** scattered across the codebase. Only `email_assistant._llm` is already a seam (carries inert `sensitivity`/`task` kwargs). This build makes the router the single seam every call funnels through, implements the routing policy, and wires all 7 sites — while every call still executes locally.

Non-goals this build: real Anthropic adapter, API-key storage, task-tier-based escalation, user-facing UI toggle. Each is a later step; the machinery here leaves clean hooks for them.

## Principles (from strategy memory, carried forward)

- ONE LLM seam every call routes through.
- Local is the default. Global cloud toggle defaults **OFF**.
- Sensitive personal data (email bodies, journals, memory/vault, profile) stays **local by default** even after cloud exists — cloud for those is explicit per-feature opt-in, off by default.
- Transparency: the router announces (logs, this build) when it *would* use cloud and what it would send.
- No cloud data leaves in this build — cloud backend is a stub.

## Architecture — `core/llm/` (new infra layer, below protocols)

The router is infrastructure, not a Protocol. Protocols intercept chat turns; the router sits *under* every LLM call (chat, extraction, summarization, briefing). New package:

```
core/llm/
  __init__.py        # exports chat(), the public entry point
  router.py          # chat(...) — selects backend, executes, returns content
  policy.py          # decide(...) — PURE decision function, no I/O
  backends.py        # LocalBackend (real), CloudBackend (stub this build)
  config.py          # loads router settings (defaults + runtime overrides)
```

### `router.chat` — the single entry point

```python
def chat(messages: list[dict], *, sensitivity: str, task: str | None = None,
         model: str | None = None, options: dict | None = None,
         format: str | None = None) -> str:
```

- `sensitivity` — required, one of `"private" | "personal" | "public"` (see taxonomy).
- `task` — accepted and logged, but **inert for routing this build** (reserved for later task-tier escalation).
- `model` / `options` / `format` — faithful passthrough to `ollama.chat` so no call site loses behavior (temperature, JSON format, model override).
- Returns the response content string (same shape call sites consume today).

Flow: build config → `policy.decide(...)` → if decision is cloud but `CloudBackend.available()` is `False`, log the escalation preview and execute on `LocalBackend` → return content.

### `policy.decide` — pure, testable

```python
def decide(sensitivity: str, cfg: RouterConfig, *, offline: bool = False) -> RouteDecision:
```

Returns `RouteDecision(backend: "local"|"cloud", reason: str, would_send_cloud: bool)`. No I/O, no Ollama — a lookup table over inputs. Rules:

1. `cfg.cloud_enabled` is `False` → `local` (reason `"cloud_disabled"`).
2. `offline` is `True` → `local` (reason `"offline"`).
3. `sensitivity == "private"` and feature not in `cfg.cloud_opt_in_features` → `local` (reason `"private_local_default"`).
4. Otherwise (`personal`/`public`, or opted-in `private`) → `cloud` (reason `"cloud_eligible"`).

`would_send_cloud` records whether rule 4 fired even when the backend later falls back — this is what drives the transparency log.

### Backends

- `LocalBackend.chat(...)` — wraps `ollama.chat`, real, faithful passthrough. `available()` → `True`.
- `CloudBackend` — **stub this build**: `available()` → `False`; `chat(...)` raises `NotImplementedError` (never reached, because the router checks `available()` first and falls back). The later cloud build fills this in.

### Transparency = the fallback path

Whenever `policy.decide` returns `cloud` but `CloudBackend.available()` is `False`, the router logs a **cloud escalation preview**: chosen backend, sensitivity, task, and that it is executing locally instead. This single code path serves three purposes at once:

- the transparency "announce what it would send" requirement (this build),
- the offline fallback (`cloud unreachable → local`) in the later cloud build,
- an observable signal for verifying the policy without any data leaving.

## Sensitivity taxonomy (3-tier)

| Tier | Meaning | Default routing |
|------|---------|-----------------|
| `private` | Personal content — email bodies, journals/personal logs, memory/vault, profile facts | **Local always**; cloud only via explicit per-feature opt-in |
| `personal` | User's words, lower-stakes — general chat with Pike | Local by default; cloud-eligible only when global toggle ON |
| `public` | No personal data — summarizing fetched news/web content | Cloud-eligible when global toggle ON |

Sensitivity gates the *maximum* reach; the global toggle + per-feature opt-in decide actual routing.

## Config storage

- **Defaults** in `core/config/core_config.json` (IP-free, single source of truth):
  ```json
  "llm_router": { "cloud_enabled": false, "cloud_opt_in_features": [] }
  ```
- **Runtime overrides** in `data/llm_router.json` (gitignored, created lazily) — where the toggle actually flips at runtime. Merged over the config defaults by `core/llm/config.py`.
- API-key storage is **out of scope** — the later cloud build adds it, Google-creds style.

## Call-site refactor

Replace each direct `ollama.chat` with `router.chat`, tagged:

| Site | sensitivity | task |
|------|-------------|------|
| `core/email_assistant.py` (`_llm`) | `private` | (existing tags) |
| `core/agent.py` main loop (~L385) | `personal` | `chat` |
| `server/chat_pipeline.py` (~L140, async) | `personal` | `chat` |
| `core/memory/fact_extractor.py` (~L76) | `private` | `extract` |
| `core/memory/journal.py` (~L70) | `private` | `summarize` |
| `core/briefing.py` (~L250) | `private` | `summarize` |
| `core/protocols/web.py` news summary (~L703) | `public` | `summarize` |

Notes:
- `email_assistant._llm` stays as the email-tagged wrapper; its body delegates to `router.chat` (single line change), preserving the existing `sensitivity`/`task` kwargs it already carries.
- `chat_pipeline.py` runs the LLM call in a thread to avoid blocking the event loop — keep that offload; call `router.chat` inside the thread (it is synchronous like `ollama.chat`).
- Each site must preserve any `model` / `options` / `format` it passes today — audit exact args during implementation and thread them through the passthrough params.

## Error handling

- Router wraps backend calls in try/except; on backend failure, log and re-raise the same way call sites handle `ollama.chat` today (they each already `try/except` and degrade). No new swallowing.
- Missing/invalid `sensitivity` → raise `ValueError` (fail fast; every call site must tag).
- `data/llm_router.json` unreadable/corrupt → log and fall back to config defaults (never crash on a bad override file).

## Testing

- **`policy.py`** — table-driven pure unit tests: every `(sensitivity, cloud_enabled, opt_in, offline)` combination → expected `RouteDecision`. No Ollama, no mocking.
- **`router.py`** — inject a fake backend to verify: cloud decision + unavailable cloud → local fallback + preview logged; param passthrough (`model`/`options`/`format`) reaches the backend; bad sensitivity raises.
- **`config.py`** — defaults load; `data/llm_router.json` override merges; corrupt override falls back to defaults.
- **Per-site smoke** — each refactored site still returns a string; LLM-dependent assertions `@pytest.mark.skip` per CLAUDE.md (don't mock Ollama).

## Open items for the later cloud build (not this spec)

- Real `CloudBackend` (Anthropic adapter, re-derived not pasted).
- API-key storage + settings UI toggle.
- Task-tier escalation (activate the `task` param in `policy.decide`).
- User-facing (not just log) cloud-escalation announcement + consent prompt.
