# Chat-driven email actions (EmailOps protocol) — design

**Date:** 2026-06-30
**Status:** approved design, pre-implementation
**Author:** Switch + Claude (Pike harness work)

## Problem

The Mail panel can draft, edit, and send email, but the conversational agent
("Pike" in the COMMS chat) cannot. When the user asks Pike in chat to "draft a
reply to the John Milton Carlson email saying I got the money, thanks," Pike
composes the text in the chat but cannot carry it into the mail tool — it
replies "I can't send emails directly." `core/email_assistant.py` is wired only
into the REST endpoints the Mail panel uses; it is not connected to the chat
pipeline or any protocol.

## Goal

Let Pike take real email actions from chat — reply, compose new, forward,
mark-read, archive — by routing the request to the existing `email_assistant` /
Gmail functions, with a safe draft-then-confirm flow for anything that sends.

## Non-goals

- No native LLM tool/function-calling (`tools=`) — unused in this codebase and
  unreliable on qwen3:8b.
- No cloud LLM calls in this build. Email stays local-first; see
  "Forward-compatibility with the hybrid brain."
- No new email-reading UI — the existing Mail panel is the surface; chat just
  drives actions, and the panel refreshes to reflect them.

## Architecture overview

A new per-session protocol, `EmailOpsProtocol` (`core/protocols/email_ops.py`),
sits in the existing chat `process_input` pipeline. For each user message it
runs **gate → classify → resolve → act**, and **intercepts** the message when it
handles an email action (the chat then skips the main LLM and returns the
protocol's response, using the existing intercept path in
`server/chat_pipeline.py:75-86`).

```
user message
   │
   ▼
EmailOpsProtocol.process_input
   ├─ gate: email cue present OR _pending exists?  ──no──▶ return (no intercept) → normal chat
   │  yes
   ▼
   ├─ classify (1 constrained local LLM call) → {action, target, instruction}
   ├─ resolve target (inbox message_id, or recipient address)
   ├─ act via email_assistant / google_tools
   └─ intercept with the result (draft shown, or confirmation)
```

### Why a protocol (not bracket commands, not regex-only)

- The chat pipeline has the `session` in scope, and a **per-session** protocol
  can hold a back-reference to it — which is exactly what `email_assistant`
  needs (it pulls Google creds via `session.protocol_registry.get("google")._get_creds()`).
- Intercepting lets email actions bypass the main persona LLM and return a
  deterministic, correct result (the draft, or "Sent").
- A constrained classification call is far more reliable on qwen3:8b than
  free-form tool-calling or brittle regex across 6+ action types.

## Components

### New: `core/protocols/email_ops.py` — `EmailOpsProtocol`
- Inherits the Protocol ABC (`core/protocols/base.py`).
- Priority: `PRIORITY_NORMAL + 5` (above communications so it evaluates and can
  intercept before the main LLM path; below Security/Wellness).
- Holds `self._session` (set by `session.py` after the registry is built) and
  `self._pending` (the draft awaiting confirmation, or `None`).
- `process_input(user_input, context)`:
  1. **Gate.** Lowercased message matches an email cue
     (`reply|respond|draft|compose|email|forward|inbox|archive|mark .*read|send`)
     OR `self._pending is not None`. Else return the untouched result (no
     intercept) — normal chat proceeds.
  2. **Auth check.** If no Google creds, intercept: "Connect Google in the Mail
     panel first." (Only when an email action was clearly requested.)
  3. **Classify.** One local LLM call (see Classifier) → structured action.
  4. **Dispatch.** Call the matching handler (see Action set). Each handler
     returns the intercept response string (Pike-flavored, plain text).
  5. On `action == none` or any parse failure → return without intercept
     (normal chat). This is the misfire safety net.
- `process_output` — not used (no output rewriting needed).
- `get_status` — exposes whether a draft is pending (for debugging/tests).

### Reused (unchanged)
- `email_assistant.draft_reply(session, message_id, intent)`
- `email_assistant.draft_new(session, to, intent, subject_hint, cc, bcc)`
- `email_assistant.send_draft(session, draft_id)` (via the REST layer today;
  reused directly here)
- `google_tools.gmail_list_messages(creds, max_results, categories)` — to load
  recent inbox for target resolution (use `categories=None` so any tab's mail is
  matchable).
- `google_tools.gmail_mark_read(creds, message_id)`

### New small functions
- `email_assistant.draft_forward(session, message_id, to, note=None)` — build a
  forward draft (original quoted + optional note), save via `gmail_create_draft`.
- `google_tools.gmail_archive(creds, message_id)` — remove the `INBOX` label via
  `messages.modify`.
- `email_assistant.send_draft` already exists; ensure it's importable here.

### Modified
- `core/session.py` — construct + register `EmailOpsProtocol`, then set its
  `_session` back-reference.
- `ui/templates/index.html` — `_refreshAfterChat` gains a Mail-panel refresh:
  if `#mailPanel` is open, reload the active tab (drafts and/or inbox) so a
  chat-driven draft/send/archive shows immediately.

## Action set, dispatch, and safety

| action | trigger | does | sends mail? | confirm first? |
|---|---|---|---|---|
| `reply` | "reply/respond to <X>…" | `draft_reply` → save draft, set `_pending` | no (draft) | — |
| `new` | "email <addr/name> about…" | `draft_new` → save draft, set `_pending` | no (draft) | — |
| `forward` | "forward <X> to <addr>" | `draft_forward` → save draft, set `_pending` | no (draft) | — |
| `send` | "send it / yes send" **and** `_pending` set | `send_draft(_pending.id)`, clear pending | **yes** | this *is* the confirm |
| `edit` | "change… / make it…" **and** `_pending` set | re-draft with new instruction, update `_pending` | no | — |
| `discard` | "discard / cancel" **and** `_pending` set | delete draft, clear `_pending` | no | — |
| `mark_read` | "mark <X> read" | `gmail_mark_read` | no | run directly |
| `archive` | "archive <X>" | `gmail_archive` | no | run directly |
| `none` | anything else | nothing (fall through to normal chat) | no | — |

**Safety model (approved):**
- Anything that **sends** mail — `reply`, `new`, `forward` — always produces a
  **draft** first and waits for an explicit `send`. Nothing leaves the outbox
  without the user saying "send it."
- `send` only fires when a `_pending` draft exists, so a stray "send it" with no
  pending draft falls through to normal chat (no accidental sends).
- `mark_read` / `archive` run directly — low-stakes and reversible.

### Draft-then-confirm flow (reply example)
1. User: "reply to the John Milton Carlson email saying I got the money, thanks."
2. Gate passes → classify → `{reply, target: <JMC msg id>, instruction: "confirm received the money, thank him"}`.
3. `draft_reply(session, msg_id, instruction)` composes the body + saves a Gmail
   draft; returns `{draft_id, subject, body, to}`.
4. `self._pending = {draft_id, kind: "reply", to: "John Milton Carlson", subject}`.
5. Intercept response:
   > "Here's your reply to **John Milton Carlson** —
   > *Subject: Re: …*
   > <body>
   > Send it, tweak it, or discard?"
6. Frontend shows it and (Mail panel open) the draft appears in DRAFTS.
7. User: "send it." → `_pending` set → classify `send` → `send_draft(draft_id)`
   → "Sent to John Milton Carlson." → clear `_pending` → panel refresh.

## Classifier

A single local LLM call, gated so it never runs on non-email messages.

- **Input:** the user message, a compact recent-inbox listing
  (`#<n> · <sender> · <subject>` with a hidden id map, ~15 items), and a flag
  for whether a draft is pending.
- **Output (constrained):** one line, `KEY=value` pairs, e.g.
  `ACTION=reply | REF=3 | INSTRUCTION=confirm received the money, thank him`
  where `REF` indexes the listing (or `to:<addr>` for `new`). Actions limited to
  the table above; `none` when nothing matches.
- **Robustness:** qwen3:8b, temp 0, `<think>` stripped; parse defensively;
  unknown/missing → `none`. The listing uses indices (not raw ids) so the model
  copies a small integer, not a long id — fewer transcription errors. The
  handler maps index → real `message_id`.
- **Latency:** one extra local call only on email-ish turns; acceptable for an
  email action.

## Target resolution

- `reply` / `forward` / `mark_read` / `archive`: resolve `REF` index → the
  message_id from the listing. Backup: fuzzy-match the sender/subject tokens in
  the user message against the listing if the model returns a name instead of an
  index.
- `new`: `to:` must be an email address, or a name that fuzzy-matches a recent
  correspondent's address; if unresolved, intercept asking for the address (no
  send, no guess).
- 0 matches → intercept "I couldn't find that email in your recent inbox —
  which one?" (+ top 3 candidates). >1 ambiguous → list candidates, take no
  action.

## Pipeline integration

- `EmailOpsProtocol` is registered in `session.py` alongside the others; its
  `_session` back-reference is set immediately after the registry is built.
- It participates in the existing `registry.process_input` loop. When it
  intercepts, `chat_pipeline.py` already returns the protocol response and skips
  the LLM (lines 75-86) — no pipeline changes needed beyond registration.
- The classifier/compose calls are synchronous inside `process_input`; the chat
  endpoint already awaits the pipeline.

## Frontend

- `_refreshAfterChat` (`index.html`) adds: if `#mailPanel` is open, refresh its
  active tab — `loadMailDrafts()` (drafts created/sent/edited) and/or
  `loadInboxDigest()` (mark-read/archive change the inbox). Mirrors how the task
  and calendar panels already refresh after chat.
- No new structured action channel required; the chat response carries the
  human-facing result, and the panel refresh reflects state.

## Forward-compatibility with the hybrid brain

Per the 2026-06-30 strategic decision (see memory `aegis_strategic_direction`),
Aegis is moving to **local-default with an opt-in, data-class-gated cloud
(Claude API) escalation** for horsepower, designed in its own later spec. To be
ready without taking on that scope now:

- **Single seam.** Every LLM call this feature makes — the classifier **and**
  any composition — goes through one function, `email_assistant._llm(messages,
  *, sensitivity="private", task=...)`. Today `_llm` calls Ollama only; the new
  `sensitivity` / `task` keyword args are accepted and currently ignored. When
  the hybrid router lands, `_llm` is the **one** place to swap, and email
  inherits cloud capability with no rewrite.
- **Sensitivity tagging.** Email LLM calls are tagged `sensitivity="private"`
  because they carry email bodies. The future router treats `private` as
  **local-only by default**; routing email to cloud will require an explicit,
  per-feature opt-in that is **OFF by default**. Email bodies must never
  silently leave the machine.
- **No behavior change now.** This build adds the seam + tags only; all calls
  remain local (qwen3:8b). This is purely making the architecture swappable.

## Error handling

- No Google creds → friendly "connect Google first" (only when an email action
  was requested).
- Classifier parse failure / `none` → no intercept, normal chat (never crash the
  pipeline, never act on a guess).
- Compose/send/Gmail API failure → intercept with a short apology + leave state
  consistent (don't clear `_pending` on a failed send; don't claim "sent" unless
  the API confirmed).
- `send`/`edit`/`discard` with no `_pending` → treat as `none` (normal chat).

## Testing

`tests/test_email_ops.py` (pytest), with `email_assistant`/`google_tools`/LLM
mocked:
- **Gate:** email-ish vs. plain messages route correctly.
- **Classifier parsing:** sample model outputs → expected structured actions;
  malformed → `none`.
- **Target resolution:** index map + fuzzy-match fallback; 0/1/many cases.
- **State machine:** draft → send / edit / discard transitions; send with no
  pending is a no-op; failed send keeps `_pending`.
- **Dispatch:** each action calls the right `email_assistant`/`google_tools`
  function with the resolved args.
- **Safety:** no handler that sends mail runs without an explicit `send` on a
  pending draft.
Live-LLM paths skipped (`@pytest.mark.skip`) per project convention. End-to-end
is a manual iris smoke test in the running app.

## Implementation phasing

- **Phase 1 — the exact use case:** `reply` + `send` + `edit`/`discard`,
  classifier, target resolution, session wiring, `_refreshAfterChat` mail
  refresh, the `_llm` seam + sensitivity tags. Ships the John-Milton-Carlson
  scenario end to end.
- **Phase 2:** `new` + `forward` (adds `draft_forward`, recipient resolution).
- **Phase 3:** `mark_read` + `archive` (adds `gmail_archive`).

## New/changed code surface

- New: `core/protocols/email_ops.py`, `tests/test_email_ops.py`.
- Changed: `core/email_assistant.py` (seam args on `_llm`, `draft_forward`),
  `core/protocols/google_tools.py` (`gmail_archive`), `core/session.py`
  (register + back-ref), `ui/templates/index.html` (`_refreshAfterChat`).
