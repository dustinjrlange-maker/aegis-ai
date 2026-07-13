# Email itemized summary + drill-down + length leeway — Design

**Date:** 2026-07-12
**Author:** Claude (with Switch)
**Status:** Approved (verbal), pre-implementation

## Problem

Chat-driven email summaries are unusable in practice (live test 2026-07-12):

- Asking for "5 unread emails" never returns more than **3 items**.
- Output is an editorialized narrative ("group them", "noise"), not an
  itemized per-email list. The 8B even hallucinated email content
  ("St. Clair festival shooting") that wasn't grounded.
- Asking for "more detail" returns the identical terse narrative.

### Root causes (verified in code)

1. `email_assistant.get_inbox_digest()` prompts for "3-5 sentences" (a
   narrative) and runs the result through `session.clean_reply(raw)` with **no
   mode** → defaults to `casual` → `reply_shaping.MODE_SENTENCE_BUDGETS["casual"]
   = 3` → hard-capped at 3 sentences. That is the "never more than 3" ceiling.
2. The digest emits `[1] [2]` bracket prose, which `reply_shaping`'s list
   detector (`^\s*(?:\d+\.|[-*])`) does not recognize, so the cap still applies.
3. `_do_summarize` reused the narrative digest instead of itemizing the message
   list — the per-message `sender/subject/snippet/unread` data
   (`gmail_list_messages`) was already available but collapsed.

## Goals

- Itemized, per-email summary that **respects the requested count**.
- Accurate — no hallucinated content.
- Tiered detail with single-email drill-down.
- Give Pike leeway on answer length when the user explicitly asks for detail,
  not just for email.

## Non-goals (explicit follow-ups)

- Filtering by topic/content ("the 5 unread that are commission requests") —
  that is a Gmail content search; separate feature.
- Changing the morning-briefing / Mail-panel narrative (`get_inbox_digest`
  stays as-is; it is a narrative surface by design).

## Design

### Part 1 — Deterministic itemized summary + AI triage (`core/protocols/email_ops.py`)

Rewrite `_do_summarize(action, text)`:

1. Resolve account (unchanged: `_explicit_from` + `_resolve_account`).
2. `N = _summary_count(text)` (default **5**, clamp 1–25).
3. `unread_only = bool(_UNREAD_CUE.search(text))` → pass
   `extra_query="is:unread"` to `gmail_list_messages`.
4. Fetch `msgs = gt.gmail_list_messages(creds, max_results=N,
   extra_query=...)`. Empty → "Your inbox is clear — nothing unread." (or
   non-unread equivalent).
5. Build a real numbered list. Per item:
   `f"{i}. {clean_sender} — {subject}"` plus a preview line.
   - default preview: first ~100 chars / first line of snippet.
   - detail requested (`response_length.wants_detailed_answer(text)`): full
     ~200-char snippet.
6. **One** LLM triage line via `ea._llm(..., sensitivity="private",
   task="email_triage")`: given the list, output ONE short line flagging the
   most urgent/important item(s); invent nothing. Prepend as a header. On LLM
   error, omit the triage line (list still returned).
7. Store `self._summary_map = {i: msg["id"]}` for drill-down.
8. Return via `_intercept` (bypasses reply-shaping cap; the numbered list would
   survive it anyway).

`clean_sender`: strip a trailing `<addr>` and surrounding quotes from the
`From` header, falling back to the raw value.

### New `read` action — single-email drill-down

- Add `read` to `_ALLOWED_ACTIONS`, the classifier ACTION enum, and a rule:
  "read: the user wants the full contents of ONE inbox email they referenced
  ('tell me more about #2', 'open #3', 'what does 1 say'). REF = the number."
- `_do_read_detail(action, text)`:
  - Resolve REF against `self._summary_map` first (the list the user is looking
    at), falling back to `self._id_map` (classifier's recent-inbox listing).
  - No match → "Which one? Give me the number from the list."
  - `msg = gt.gmail_get_message(creds, message_id)` →
    format `From / Subject / Date` then the body. Read-only, no cap.
- Handler wired in the `process_input` dispatch map.

`_summary_map` persists across turns (set only by `_do_summarize`); `_classify`
must not clobber it (it only writes `_id_map`).

### Part 2 — Length leeway on explicit detail requests

New module `core/response_length.py`:

```python
def wants_detailed_answer(text: str) -> bool:
    """True when the user explicitly asks for a longer/structured answer."""
```

Matches (case-insensitive, word-boundaried): `detailed`, `in detail`,
`details`, `itemize|itemized|itemise`, `full`/`in full`, `everything`,
`expand`, `elaborate`, `more detail`, `list them`/`list out`, `longer`,
`break it down`/`breakdown`, `complete list`, `long version`. Deliberately does
NOT match a bare "more" (too broad).

`server/chat_pipeline.py`: after `turn = classify(...)`, compute
`shaping_mode = "task" if (wants_detailed_answer(user_input) and turn.mode ==
"casual") else turn.mode`. Use `shaping_mode` for both the `_MODE_HINTS` lookup
and every `session.clean_reply(..., mode=...)` call. Leave `turn.mode` and
`route_task_tag(turn)` untouched, so routing/escalation are unaffected and
`emotional` turns are never lengthened.

## Testing (TDD)

`tests/test_email_ops.py`:
- summarize builds an N-item numbered list; count honored; `is:unread` query
  passed when "unread" present.
- default preview vs detailed (full snippet) tiers.
- `_summary_map` populated with displayed index → id.
- triage header present (mock `_llm`); triage LLM failure → list still returned.
- `read` drill-down resolves REF against `_summary_map`, calls
  `gmail_get_message`, returns body; unknown REF asks.
- parser: `ACTION=read` accepted.

`tests/test_response_length.py`:
- truth table for `wants_detailed_answer` (positives + "more"/plain negatives).

`tests/test_chat_pipeline_*`:
- a detail-requesting casual turn shapes with `mode="task"`; an ordinary casual
  turn stays `casual`; an emotional turn stays `emotional` even with a detail
  cue.

## Risks

- Triage line is the only hallucination surface; kept to one line, grounded on
  the listed facts, and omitted on error.
- `wants_detailed_answer` over-trigger only lengthens answers (low harm);
  under-trigger preserves current terse behavior. Bias toward precision.
