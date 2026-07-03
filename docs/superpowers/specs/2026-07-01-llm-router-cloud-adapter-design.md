# LLM Router — Cloud Adapter (Claude API) Design

**Date:** 2026-07-01
**Status:** Approved, ready for planning
**Builds on:** `docs/superpowers/specs/2026-06-30-hybrid-llm-router-design.md` (the local-only router, now on `main`)
**Scope of this build:** Option A — make cloud escalation actually work. Real `CloudBackend` (Anthropic SDK + message translation + error/refusal→local fallback), key storage, cloud model/token config. **Deferred to follow-ups:** task-tier escalation (the inert `task` param), a settings-UI toggle, a user-facing consent/announcement flow.

## Background

The 2026-06-30 build shipped the `core/llm/` router with a stubbed `CloudBackend` (`available()` → `False`), so everything ran local. This build turns the stub into a working Anthropic-backed adapter. The policy layer, the 3-tier sensitivity taxonomy, and all 7 tagged call sites are unchanged — they already route correctly. Turning cloud on is now a matter of (a) filling in `CloudBackend`, (b) resolving a key, (c) a runtime error/refusal fallback in the router.

**Privacy posture is unchanged and remains the whole point:** global cloud toggle defaults OFF (fully offline); `private` data (email, journals, memory/vault, profile, briefing, fact-extraction) stays local by default even with the toggle on, escalating only if its feature is in the (empty-by-default) `cloud_opt_in_features` list; only `personal` (chat) and `public` (news/web) tiers are cloud-eligible, and only with the toggle on. Regression tests already enforce every `private` tag.

## Non-goals (deferred)

- Task-tier escalation — the `task` param stays accepted but inert for routing.
- Settings-UI toggle and API-key entry field.
- User-facing (beyond-log) cloud-escalation announcement / consent prompt.
- Streaming, prompt caching, thinking/effort tuning, batching — none needed for Aegis's short chat/summarize traffic.

## Model choice

Default cloud model: **`claude-opus-4-8`** ($5/$25 per M tokens), configured in `core_config.json` so it can change. Rationale: escalation exists because local qwen3:8b wasn't enough, so the most capable Opus-tier model is the right target; escalations are occasional and gated. **Fable 5 was assessed and rejected** for this traffic — 2× cost, minutes-long turns, no zero-data-retention option (conflicts with privacy-first), and higher refusal risk on firearms-adjacent content, all for zero benefit on short chat/summarize calls. The model being config-driven leaves Fable 5 available for a future task-tier that genuinely needs it.

## Architecture

No new modules. Three existing files change:

- `core/llm/backends.py` — `CloudBackend` stub → real Anthropic adapter.
- `core/llm/router.py` — add a runtime try/except around the cloud call that falls back to local on any failure.
- `core/llm/config.py` — add API-key resolution + `cloud_model` / `cloud_max_tokens` to `RouterConfig`.

Plus: `core/config/core_config.json` (config defaults), `requirements.txt` (`anthropic`), and `CLAUDE.md` (supersede the "no cloud APIs" rule).

### `CloudBackend` — the real adapter

```python
class CloudBackend:
    """Anthropic Claude API adapter. Enabled only when a key is resolvable
    and the `anthropic` package is importable; otherwise available() is False
    and the router falls back to local."""
    name = "cloud"

    def available(self) -> bool:
        # True iff an API key resolves AND `import anthropic` succeeds.

    def chat(self, messages, *, model=None, options=None, format=None) -> str:
        # 1. Translate ollama-style messages -> Anthropic shape.
        # 2. Call client.messages.create with the CONFIGURED cloud model
        #    (NOT the passed `model`, which is a local model id).
        # 3. Detect refusal -> raise CloudRefusalError.
        # 4. Return the first text block's text.
```

Behavioral requirements:

1. **Message translation** — Anthropic takes the system prompt as a top-level `system=` string, not a `{"role": "system"}` message. `CloudBackend` must:
   - Collect every `role == "system"` message's `content`, join with `\n\n`, and pass as `system=` (omit the kwarg if empty).
   - Pass only `user`/`assistant` messages in `messages=`.
   - Ensure the first remaining message is `role == "user"`. All Aegis call sites already satisfy this after system extraction; if somehow no user message exists, raise (router falls back to local).
2. **Cloud model, not local** — ignore the incoming `model` (a local id like `qwen3:8b`); use `cfg.cloud_model`. Use `cfg.cloud_max_tokens` for `max_tokens`.
3. **No `thinking` param** — Opus 4.8 runs without thinking; correct for short chat/summarize. (Deferred: effort/thinking tuning.)
4. **Lazy import** — `import anthropic` inside the method/`available()`, not at module top. A missing package makes `available()` return False (graceful local fallback), so local-only installs don't need `anthropic`.
5. **Refusal detection** — if `response.stop_reason == "refusal"`, raise `CloudRefusalError` (a new exception in `backends.py`). The router treats it like any failure and falls back to local. This matters for firearms/ammo-adjacent prompts, where Opus's safety classifier can decline — local qwen3:8b has no such classifier, so the user still gets an answer.
6. **Text extraction** — return the text of the first `block` where `block.type == "text"`; if none, raise (→ local fallback).

The Anthropic client is constructed once (lazily) and reused: `anthropic.Anthropic(api_key=<resolved key>)`.

### `router.chat` — runtime fallback (the safety net)

Currently the router only falls back when `CloudBackend.available()` is False *before* the call. This build adds a fallback when the cloud call *fails at runtime*:

```python
if decision.backend == "cloud" and backend.available():
    try:
        return backend.chat(messages, model=model, options=options, format=format)
    except Exception:
        logger.warning("[llm-router] cloud call failed (%s) — falling back to local", ...)
        backend = _BACKENDS["local"]
# (existing) cloud selected but unavailable -> preview log -> local
return backend.chat(messages, model=model, options=options, format=format)
```

Net guarantee: **cloud can never make Aegis worse than local-only.** Network error, rate-limit, auth failure, or safety refusal all transparently yield the local answer. The existing "cloud unavailable → preview log → local" path is preserved for the pre-call case (no key / package missing).

### `config.py` — key resolution + cloud settings

`RouterConfig` gains two fields:

```python
@dataclass
class RouterConfig:
    cloud_enabled: bool = False
    cloud_opt_in_features: tuple = field(default_factory=tuple)
    cloud_model: str = "claude-opus-4-8"
    cloud_max_tokens: int = 2048
```

`load_config()` reads `cloud_model` / `cloud_max_tokens` from the `llm_router` config block (with the defaults above), honoring the same `data/llm_router.json` override mechanism.

**API-key resolution (option C — env var first, file fallback):**

```python
_KEY_FILE = PROJECT_ROOT / "data" / "anthropic_key"

def resolve_api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key and key.strip():
        return key.strip()
    if _KEY_FILE.exists():
        try:
            text = _KEY_FILE.read_text(encoding="utf-8").strip()
            return text or None
        except Exception:
            logger.exception("Bad %s", _KEY_FILE)
    return None
```

The key is never stored in `core_config.json` or committed. `data/` is gitignored. `CloudBackend.available()` and its client construction both call `resolve_api_key()`.

## Config storage

- `core/config/core_config.json` `llm_router` block adds `"cloud_model": "claude-opus-4-8"` and `"cloud_max_tokens": 2048` (alongside existing `cloud_enabled: false`, `cloud_opt_in_features: []`).
- Runtime overrides continue through `data/llm_router.json`.
- API key: `ANTHROPIC_API_KEY` env var, or `data/anthropic_key` (gitignored). Not in config.

## Zero-data-retention (deployment note, not code)

ZDR is an **account-level setting in the Anthropic org console**, not a per-request parameter — the code does not (cannot) set it. To make cloud calls non-retained, enable ZDR on the Anthropic account; the calls then inherit it. Opus 4.8 supports ZDR (Fable 5 does not — part of why it was rejected). This is documented for the operator; no code hook.

## Dependencies & docs

- `requirements.txt` — add `anthropic` (note it in the file per CLAUDE.md's dependency rule).
- `CLAUDE.md` — the "Do NOT add cloud API calls (OpenAI, Anthropic, etc.) — this is local-only" line is superseded by the 2026-06-30 strategic shift. Reword to: local-first is the default; cloud is opt-in and gated, and every LLM call must still go through the `core/llm` router seam (never a direct provider call outside `core/llm/backends.py`).

## Error handling

- Every cloud failure mode (connection, rate-limit, auth, status, refusal, empty content, missing package) surfaces as an exception that the router catches → logs → runs local. No cloud failure is fatal.
- Corrupt `data/anthropic_key` → logged, treated as no key → `available()` False → local.
- `resolve_api_key()` never raises.

## Testing

- **`CloudBackend`** (inject a fake Anthropic client via a module-level seam or constructor param — no real API, not mocking Ollama):
  - system messages are extracted into `system=` and joined; only user/assistant remain in `messages=`.
  - the configured `cloud_model` is used, not the passed local `model`.
  - `cloud_max_tokens` is passed as `max_tokens`.
  - a `stop_reason == "refusal"` response raises `CloudRefusalError`.
  - a response with no text block raises.
  - first-text-block extraction returns the right string.
  - `available()` is False when no key resolves; True when a key resolves and a fake `anthropic` module is importable.
- **`router`**:
  - cloud selected + available → routes to cloud (fake cloud backend returns a marker).
  - cloud call raises → falls back to local + logs a warning; local marker returned.
  - refusal (CloudRefusalError) → local fallback.
  - existing local/preview-fallback tests still pass.
- **`config`**:
  - `cloud_model` / `cloud_max_tokens` load from config and from the override file.
  - `resolve_api_key()`: env var wins → `data/anthropic_key` fallback → None; corrupt file → None without raising. (Use monkeypatch + tmp_path; never read a real key.)
- **Live smoke test (manual, gated):** one real `client.messages.create` against `claude-opus-4-8`, run only when a key is present (`@pytest.mark.skipif` on no key), asserting a non-empty string — mirrors how the local seam was live-verified.

## Open items for later builds (not this spec)

- Task-tier escalation: activate `task` in `policy.decide` (e.g. `task="heavy"` + non-`private` → cloud).
- Settings-UI: a cloud on/off toggle and an API-key entry field writing to `data/anthropic_key`.
- User-facing cloud-escalation announcement + consent prompt (beyond the current log line).
- Effort/thinking tuning, prompt caching, streaming — only if a future tier needs them.
