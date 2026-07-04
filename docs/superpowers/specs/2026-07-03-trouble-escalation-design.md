# Escalate-on-Trouble Mode — Design Spec

**Date:** 2026-07-03
**Status:** Approved (design), pending implementation plan
**Author:** Claude + Switch

## Goal

Give Pike a narrow, privacy-respecting way to reach the cloud (Opus) **only when the
local 8B is visibly struggling** — while everyday chat and personal data stay local by
default. This is the middle ground between "cloud off (Pike hallucinates/contradicts
itself under correction)" and "cloud on (routine data leaves the box)."

Motivating incident (2026-07-03): qwen3:8b confabulated ("July 7 is Wednesday in the
Pacific time zone"), self-contradicted across turns, and could not recover under repeated
user correction. Those turns are exactly when a bigger model would help — and only those.

## Non-Goals (v1)

- The **local-judge** detection layer (an extra qwen classification call). Deferred to a
  later iteration; v1 ships **fast-path detection only**. See "Future work."
- Changing the existing `cloud_enabled` main toggle or task-tier escalation behavior.
- Any new cloud provider or model. Reuses the existing `CloudBackend` (Opus 4.8).

## Configuration

Two new settings in `data/llm_router.json` (loaded by `core/llm/config.py`), both
independent of the existing `cloud_enabled`:

| Key | Default | Meaning |
|---|---|---|
| `cloud_trouble_escalation` | `false` | Master switch for this feature. When on (and an API key is configured), cloud may engage **only** on detected trouble. When off, behavior is exactly as today. |
| `trouble_private_consent` | `true` | When on, a `private`-tier trouble turn prompts for one-time consent before going to cloud. When off, `private` trouble turns auto-escalate (user has accepted the risk). |

`cloud_trouble_escalation` requires a resolvable API key (`config.resolve_api_key()`); if
none is present, the feature is inert regardless of the toggle (fail-closed, no error).

## Detection (`core/llm/trouble.py`)

New module, pure/deterministic for v1. Public entry:

```
detect_trouble(user_message: str, streak: int) -> TroubleResult
```

Returns `{ is_trouble: bool, reason: str, new_streak: int }`.

**Fast-path signals:**
1. **Correction/contradiction cues** — case-insensitive match against a curated phrase set:
   `no that's wrong`, `that's not right`, `that's incorrect`, `you made a mistake`,
   `fix your mistake`, `what are you talking about`, `you're confused`, `wrong again`,
   `you said ... but ...`, `that's not what i ...`, leading bare `no,`/`no.`/`nope`, etc.
   (Full list maintained in the module; conservative to avoid false positives on ordinary
   disagreement about content.)
2. **Correction streak** — a per-session counter. Each consecutive turn that matches a
   correction cue increments it; a non-correction turn resets it to 0. `is_trouble` also
   trips when `streak >= 2` even without a strong single-phrase match (escalating
   frustration).

Streak state lives on the session (see integration) and is passed in/out — the module
itself stays stateless and unit-testable.

## Routing decision (`core/llm/policy.py`)

`decide()` gains a `trouble: bool = False` argument and returns one of three outcomes
(today it returns a two-way local/cloud decision; this widens it):

- `"local"` — no trouble, toggle off, or fail-closed.
- `"cloud"` — escalate to Opus.
- `"needs_private_consent"` — private turn that would escalate but is gated on consent.

Decision table when `cloud_trouble_escalation` is on and an API key exists:

| sensitivity | trouble | `trouble_private_consent` | outcome |
|---|---|---|---|
| personal / public | yes | (n/a) | `cloud` |
| private | yes | true | `needs_private_consent` |
| private | yes | false | `cloud` |
| any | no | (n/a) | fall through to existing policy (normally `local`) |

`private` remains the hard-sensitive tier: it never *silently* escalates. It either asks
first (consent on) or escalates only because the user explicitly turned consent off.

The existing `cloud_enabled` path is unchanged and evaluated first: if `cloud_enabled`
already routes a turn to cloud, that still holds. Trouble escalation only *adds* cloud
routing to turns that would otherwise be local.

## Private consent flow (two-step, on the session)

Modeled on the existing email send-guard (`EmailOpsProtocol._pending`).

1. Turn classified `private`, trouble detected, consent toggle on →
   `decide()` returns `needs_private_consent`. The pipeline does **not** call the LLM
   normally. It stores `session._pending_escalation = { message, reason, ts }` and returns
   a ⚠ prompt:
   > "⚠ I'm struggling with this, and it looks like it involves private info (<reason>).
   > I can get better help from the cloud, but that sends this to Anthropic's servers.
   > Reply **'yes, use cloud'** to allow it just this once — otherwise I'll keep trying locally."
2. Next user message:
   - Affirmative (`yes`, `yes use cloud`, `go ahead`, `allowed`, `ok`) → re-run the
     **stored original message** with forced cloud routing, clear pending, ☁ announce.
   - Anything else → clear pending, process the new message normally (local).
3. Pending expires after 5 minutes to avoid a stale confirm hijacking a later unrelated
   turn (mirrors the existing vault-PIN token window).

## Announcements

Reuse the existing `BackendUsed` / ☁ mechanism (`core/llm/router.py`):
- ☁ prefix whenever cloud actually served a turn (trouble-escalated or consent-approved).
- ⚠ prefix on the private-consent prompt.
No new announcement infrastructure.

## Settings UI

Add to the existing Cloud settings panel (`ui/templates/index.html`, alongside the
`cloud_enabled` controls) and its settings API:
- Checkbox: **"Escalate to cloud when I'm struggling (trouble mode)"** → `cloud_trouble_escalation`.
- Sub-checkbox (enabled only when the above is on): **"Ask before sending private info to
  cloud"** → `trouble_private_consent` (default checked). Helper text: "When off, private
  turns escalate automatically — you accept the data leaving the box."
- Both persist via the existing `cloud_settings` write path (`set_cloud_enabled` sibling
  setters `set_trouble_escalation` / `set_trouble_private_consent`).

## Component layout & isolation

| Unit | Responsibility | Depends on |
|---|---|---|
| `core/llm/trouble.py` (new) | Stateless fast-path trouble detection | stdlib only |
| `core/llm/policy.py` (extend) | Pure decision incl. `trouble` → 3-way outcome | config shape only |
| `core/llm/config.py` (extend) | Load the two new flags | — |
| `core/llm/cloud_settings.py` (extend) | Setters for the two flags | config |
| `core/session.py` (integrate) | Compute streak + trouble, apply outcome, own pending-consent state | trouble, policy, router |
| `ui/templates/index.html` (extend) | Two checkboxes | settings API |

Detection and policy stay free of I/O so they're unit-testable without a model or network.

## Testing

- `trouble.py`: phrase matches, non-matches (ordinary content disagreement must NOT trip),
  streak increment/reset, `streak >= 2` trip.
- `policy.decide`: full decision table above, incl. fail-closed (no key), toggle off,
  each sensitivity × trouble × consent combination.
- Consent flow: private+trouble → `needs_private_consent` + pending set; affirmative next
  turn re-runs original on cloud; non-affirmative clears pending; expiry.
- `config` / `cloud_settings`: load defaults, round-trip the two flags.
- Regression: `cloud_enabled` off + trouble off → identical to current local routing.

## Future work

- **Judge path (deferred):** a short local qwen classification for cases the fast path
  misses, run only when fast path is negative and a prior assistant turn exists.
- Contradiction detection across Pike's own consecutive outputs.
- Tuning the correction phrase set from real logs.
