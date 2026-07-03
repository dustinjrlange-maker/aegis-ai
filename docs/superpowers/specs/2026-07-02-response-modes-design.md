# Response Modes + Task-Tier Escalation + Cloud Announcement — Design

**Date:** 2026-07-02
**Status:** Approved by Switch (conversation 2026-07-02), ready for planning
**Builds on:** hybrid LLM router (PRs #3/#4, merged) + Cloud Brain settings UI (PR #6, merged).
**Prerequisite:** merge PR #5 (`fix/security-hardening`) to `main`, then merge `main` into this branch before executing.
**Scope:** Pieces #2 and #3 of the remaining hybrid-brain plan (consent/announcement + task-tier escalation), unified with the response-length fix, because all three share the same two components: a turn classifier and a mode-aware reply shaper.

## Background — why these are one build

Two problems turned out to be the same subsystem:

1. **Escalation.** With the PR #6 toggle ON, *every* `personal` chat turn goes to Opus. The intended behavior ("hard turns → cloud, everyday chat local") needs a per-turn classification.
2. **The length muzzle.** Pike's replies are capped at 3 sentences by `clean_reply` (built in `core/agent.py::build_filler_cleaner`) — a blunt fix for a 3-part historical problem (roleplay drift, filler padding, genuine length) that also blocks emotional depth. Switch explicitly wants to be emotionally open with Pike and have him "feel real"; the blanket cap prevents that. Additionally, if escalated Opus output were piped through the 3-sentence cap, we'd pay for a detailed draft and keep two sentences of it.

Both need to know *what kind of turn this is*. One classifier drives both routing (local vs cloud) and reply shaping (length budget).

## The rule (must stay one sentence)

> **Task-shaped requests go to the big brain; conversation and feelings stay home.**

## Turn classification

Every chat turn is classified into a **style mode** and a **route force**:

- `mode`: `casual` | `emotional` | `task`
- `route`: `auto` | `force_local` | `force_cloud` (from explicit user override phrases)

Precedence:
1. **Override phrases** set `route` (never `mode`). Detection is a small fixed list, word-boundary matched, **negation-aware** (the EmailOps send-guard lesson: "don't overthink this" must not force cloud).
   - force_cloud: "think harder", "think hard", "big brain", "best answer", "use the cloud"
   - force_local: "just you", "keep it local", "no cloud", "keep it simple"
   - Negation guard: a match preceded (within ~12 chars) by "don't" / "dont" / "do not" / "never" / "no need" is ignored.
2. **Emotional veto** (beats task detection): the existing emotion detector (`core/voice/emotion.py`, distilbert, runs on every message already) returns `{label, score}`. If `label ∈ {sadness, fear, anger}` and `score ≥ 0.75` → `mode = emotional`. A grief message that happens to contain "figure out" stays emotional (and therefore local).
3. **Task detection**: deterministic work-request patterns (word-boundary regex): "help me", "can you", "could you", "i need you to", "write", "draft", "analyze", "plan", "summarize", "research", "compare", "review", "outline", "design", "debug", "break down", "figure out", "walk me through", "explain how". Message must be ≥ 4 words. **Length alone is never a trigger** — long heartfelt messages must not escalate.
4. Otherwise `mode = casual`.

Known limit (accepted): emotion detection returns `None` for messages under 5 words and when disabled — a 2-word heavy message classifies casual. It still stays local (casual never escalates), so the privacy consequence is nil; the tuning session may add a small disclosure-phrase list later.

## Routing

The classifier's output maps to the router's `task` tag; **policy consumes tags, pipeline computes them**:

| mode / route | task tag | routes to |
|---|---|---|
| task (or route=force_cloud) | `chat_task` | cloud, when `cloud_enabled` |
| emotional | `chat_emotional` | local — **unless `deep_mode` is on** |
| casual (or route=force_local) | `chat_casual` | local, always |

Policy change (`core/llm/policy.py`): for `sensitivity="personal"`, cloud is chosen **only** for `task == "chat_task"`, or `task == "chat_emotional"` when `cfg.deep_mode` is true. Any other `personal` task → local, reason `personal_local_default`. This is strictly *more* conservative than today (toggle-on currently sends all chat). `private` and `public` tiers are completely unchanged — the structural privacy invariant stands.

`force_local` is implemented purely by tagging `chat_casual`; `force_cloud` by tagging `chat_task`. The cloud master toggle still wins: force_cloud with `cloud_enabled=false` stays local (`cloud_disabled`).

**Deep Mode** (`deep_mode: bool`, default `false`): a new `RouterConfig` field + `data/llm_router.json` override key + settings toggle. When ON, emotional turns become cloud-eligible. This is Switch's explicit, off-by-default door for "Opus is better at emotional depth" — a heuristic never makes that call. Recommend enabling ZDR on the Anthropic account before flipping it.

## Router metadata (for the announcement)

`router.chat()` returns a bare string, so the caller can't know which brain answered. Add `chat_with_meta(...) -> tuple[str, RouteMeta]` where `RouteMeta = {backend_used: "local"|"cloud", decision_reason: str, cloud_model: str|None}`. `chat()` delegates to it and discards the meta (all existing call sites untouched). Fallback honesty: if cloud fails/refuses and local answers, `backend_used="local"` — **no marker on fallback replies**, because local answered.

## Reply shaping — mode-aware `clean_reply`

Extract `build_filler_cleaner` from `core/agent.py` into a new `core/reply_shaping.py` (pure, unit-testable; `core/agent.py` re-imports it so `core/session.py`'s existing usage is untouched). The returned `clean_reply(text, mode="casual")` keeps ALL current cleaning in every mode — `<think>` stripping, emoji, curly quotes, **asterisk-narration stripping (the anti-roleplay defense, now decoupled from length)**, exclamation→period, filler phrases, word replacements — and makes only the length handling mode-dependent:

| mode | sentence cap (non-list) | newline collapse |
|---|---|---|
| casual | 3 (today's exact behavior) | yes (non-list) |
| emotional | 6 | yes (non-list) |
| task | none | **no** — structure preserved |

The list-marker bypass (numbered/bulleted content keeps its lines) is unchanged.

## Prompt-side mode hint

The model can't know the server's mode, so the pipeline injects a one-line hint into the per-turn context (short, within qwen3:8b injection-fragility limits):

- emotional: `[Response mode: emotional support — you may take up to 5-6 sentences. Stay specific to their words, no advice, no cheerleading, no roleplay.]`
- task: `[Response mode: task — give the complete, structured answer; take the length it needs.]`
- casual: no hint (default persona rules apply).

Three consistent layers: classifier picks the mode → prompt hint licenses the style → `clean_reply` budget enforces it (because the 8B doesn't reliably obey prompts alone — that's why the cap existed).

## Personality pack changes (pike + default)

- Replace the blanket `Max 2 sentences. Always.` hard rule with: default 1–2 sentences, **"when a Response mode note gives you room, use it — more sentences, never more filler."**
- New `=== EMOTIONAL PRESENCE ===` section: when they open up, take room; reflect their specific words; sit with it; one real question at most; the existing bans stay absolute (no generic advice, no cheerleading, no "you've got this", no roleplay/scene-setting).
- All other persona rules unchanged. The anti-roleplay prompt rules stay.

## Announcement + consent (Piece #2)

- **Announce-after:** replies actually served by cloud get `\n\n☁ cloud brain` appended **after** output protocols run (so nothing strips it). Plain unicode — renders in the web UI and Telegram.
- **Preview on demand:** the last cloud call's payload is kept **in RAM only** on the session (`session.last_cloud_payload` — model, timestamp, message count, the final augmented user message). A `/cloud` slash command (handled deterministically in `chat_pipeline` before the registry) prints it. Nothing is persisted to disk; one payload retained, overwritten per cloud call.
- No ask-before-each: rejected by design — it would negate auto-detect. The marker + `/cloud` + Deep-Mode-off defaults are the consent surface Switch chose.

## Settings UI (extends PR #6's Cloud Brain section)

1. Update the main toggle caption to match new behavior: *"When on, task-shaped requests (drafts, analysis, planning) use Anthropic's Opus 4.8. Conversation and emotional support stay local. Your private data — email, journals, memory — always stays local."*
2. New **Deep Mode** toggle row (default off): *"Heavy emotional conversations may also use the cloud brain. Off = feelings never leave this machine."* → `POST /api/cloud/deep {enabled}` → new `cloud_settings.set_deep_mode()` (merge-safe, same pattern as `set_cloud_enabled`). `get_cloud_status()` gains `deep_mode`.

## Console (`core/agent.py` run loop)

Same classifier + task-tag mapping + mode-aware `clean_reply` call; marker appended to the printed reply. (Emotion result is already computed in the console loop's flow; if not available at that point, pass `None` — casual/task classification still applies.)

## Non-goals

- No ML/LLM classifier (the 8B judging "is this hard" is the documented weak link) — deterministic rules only.
- No per-message cost display, no cost caps (logged via router warnings only).
- No change to `private`/`public` tier behavior or any non-chat call site.
- No persistence of cloud payloads.
- The "feels real" ceiling — long-term memory continuity — is Wave 6 (dreaming/wiki memory), not this build.

## Testing

- Classifier: exhaustive unit tests (overrides, negation, veto-beats-task, task verbs, casual default, short-message edge).
- Policy: updated `personal` cases (tag-gated), `deep_mode` on/off, private/public regression unchanged.
- Router: `chat_with_meta` backend/meta on local pick, cloud pick, cloud-fallback.
- Reply shaping: per-mode budgets, roleplay stripping in all modes, list bypass, casual mode byte-identical to today's behavior on representative inputs.
- Pipeline: pure-helper test for tag mapping; marker only on `backend_used=="cloud"`.
- **Stage 6 (manual, with Switch): live emotional-tuning session** — scripted heavy/casual/task scenarios; tune the emotional budget (6 is a starting value), veto threshold (0.75 starting), and the EMOTIONAL PRESENCE wording until Pike feels right. This is the acceptance test for the feature's actual purpose and cannot be automated.

## Risks

- qwen3:8b may over-run the emotional budget → `clean_reply` cap catches it (by design).
- Classifier misfires are cheap and visible: false-cloud costs cents and never touches private-tier data; false-local shows no ☁ and one "think harder" fixes it.
- Emotion model disabled/short messages → no emotional mode (fails safe: local).
