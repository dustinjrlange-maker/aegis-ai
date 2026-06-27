# Email Assistant UI — Design Spec

**Date**: 2026-06-27
**Status**: Approved, ready for implementation plan
**Owner**: Switch

## Overview

Build an LCARS-themed Mail panel in the Aegis web UI that exposes the existing email-assistant backend (`core/email_assistant.py` + 7 `/api/email/*` endpoints) day-to-day, so Switch can triage, draft, and send email without going through chat. Drafts-only safety rule from the backend is preserved end-to-end.

The panel has three top-level tabs — **INBOX** (default), **COMPOSE**, **DRAFTS** — each using the same stacked-row inline-expand pattern for muscle-memory consistency. A two-step amber `Send` button plus a 5-second client-side deferred-send window provide two layers of safety against accidental sends.

## Goals

- A single panel where Switch handles email triage, composition, and draft management
- Pike-voiced inbox digest as the headline; quick action without leaving the row
- All three tabs feel like the same UI — same row pattern, same expand behavior, same action bar
- No accidental sends — explicit two-step confirmation + 5s undo
- One small set of additive backend extensions (CC/BCC + narrative cache)

## Non-goals

- Full email client (folders, labels, search, attachments handling, threading beyond the message in front of you)
- Recall after send (Gmail doesn't support it; the 5s "undo" is a deferred-send pattern)
- Multi-account support (Switch's `dustin.jr.lange@gmail.com` is the only connected account)
- Push notifications for new mail (handled separately by the existing notifications system)
- Calendar invitations or RSVP handling (out of scope)

## Existing backend (already shipped)

7 endpoints in `server/app.py`, 7 corresponding functions in `core/email_assistant.py`:

| Endpoint                                    | Function                                      | Notes                                                       |
|---------------------------------------------|-----------------------------------------------|-------------------------------------------------------------|
| `GET /api/email/inbox-digest`               | `get_inbox_digest(session, max_messages)`     | Returns `{narrative, unread_count, messages, error?}`        |
| `GET /api/email/drafts`                     | `list_drafts(session, max_results)`           | Returns list of draft summaries                              |
| `GET /api/email/drafts/{id}`                | `get_draft(session, draft_id)`                | Single draft with full body                                  |
| `POST /api/email/draft-reply`               | `draft_reply(session, message_id, intent?)`   | Pike drafts in user's voice                                  |
| `POST /api/email/draft`                     | `draft_new(session, to, intent, subject_hint?)` | Pike drafts from intent                                    |
| `POST /api/email/send-draft/{id}`           | `send_draft(session, draft_id)`               | Sends; requires `confirm: true` body                         |
| `DELETE /api/email/drafts/{id}`             | `discard_draft(session, draft_id)`            | Discards                                                     |

Drafts-only rule baked into the assistant layer: Pike never auto-sends — `send_draft` is a deliberate user action.

## Backend additions

Small, additive:

- `core/email_assistant.py`:
  - In-memory `_narrative_cache: dict[user_id, (timestamp_epoch_s, narrative_str)]`. 10-minute TTL by default (configurable).
  - `get_inbox_digest(session, max_messages=10, fresh=False)` — adds `fresh` kwarg. If `False` and cache is fresh, return cached `narrative` (still re-fetches the message list, which is cheap). If `True`, regenerate via LLM and update the cache.
  - `draft_new(session, to, intent, subject_hint=None, cc=None, bcc=None)` — adds `cc` and `bcc` kwargs.
  - New `mark_read(session, message_id) -> dict` — wraps the Gmail call below; returns `{"ok": True}` or error dict.
- `core/protocols/google_tools.py`:
  - The MIME builder used by `gmail_create_draft` (or equivalent) adds optional `Cc` and `Bcc` headers.
  - New `gmail_mark_read(creds, message_id)` — calls `users.messages.modify` with `removeLabelIds: ["UNREAD"]`.
- `server/app.py`:
  - `GET /api/email/inbox-digest` accepts `?fresh=1` query param and forwards as `fresh=True`.
  - The Pydantic model for `POST /api/email/draft` adds `cc: Optional[str] = None` and `bcc: Optional[str] = None`.
  - New endpoint `POST /api/email/mark-read/{message_id}` — calls `mark_read` on the session's assistant.
- No other endpoint changes. The deferred-send pattern is purely client-side.

## Frontend structure

All UI changes live in `ui/templates/index.html`. The Mail panel is one more LCARS panel sibling to `taskPanel` / `briefingPanel` / `calendarPanel`, registered with the same panel-init machinery.

### Panel chrome

- `id="mailPanel"`, classes `.lcars-panel .mail-panel`
- Header with title `MAIL`, gear button (settings dropdown), collapse `_` button, close `×` button
- Uses existing `initPanelCollapseButtons()` for collapse/settings/close handlers
- Settings dropdown registered with `togglePanelSettings('mailPanel')`
- Drag, resize, snap-grid via existing infrastructure
- Position + theme persist in `aegis_panel_positions` and `aegis_panel_themes` localStorage

### Tabs

Inside the panel body, a tab strip + three sections:

```html
<div class="mail-tabs">
  <button class="mail-tab active" data-tab="inbox" onclick="_mailSwitchTab('inbox')">INBOX</button>
  <button class="mail-tab" data-tab="compose" onclick="_mailSwitchTab('compose')">COMPOSE</button>
  <button class="mail-tab" data-tab="drafts" onclick="_mailSwitchTab('drafts')">DRAFTS</button>
</div>
<section class="mail-tab-body" data-tab="inbox">...</section>
<section class="mail-tab-body" data-tab="compose" style="display:none">...</section>
<section class="mail-tab-body" data-tab="drafts" style="display:none">...</section>
```

State: module-level `var _mailActiveTab = 'inbox'`. `_mailSwitchTab(name)` flips `active` on tab buttons and `display` on sections, then lazy-loads the tab's data if not already loaded. Settings persisted via `aegis_mail_active_tab` localStorage on switch.

### Sidebar entry

The right `SWITCH` cluster gets a new `MAIL` button alongside `LOGOUT`. Click toggles the panel.

## INBOX tab

### Narrative strip (top)

- `<div class="mail-narrative">` with `border-left: 3px solid var(--lcars-blue-1)`, low-opacity background
- Content: Pike's prose summary (3-5 sentences from the backend)
- Footer line in dim text: `Cached Xm ago · ↻ Refresh`
- `↻` button calls `loadInboxDigest(fresh=true)` which hits `/api/email/inbox-digest?fresh=1` and busts both the client and server caches
- Loading state: spinner with text `Pike is reading your inbox…`
- Empty state (narrative says "Inbox is clear" already, handled by backend)

### Message list

Stacked rows below the narrative:

```html
<div class="mail-row" data-message-id="..." data-unread="true">
  <div class="mail-row-dot"></div>         <!-- cyan filled if unread, hollow if read -->
  <div class="mail-row-meta">
    <div class="mail-row-from">Bill (FFL)</div>
    <div class="mail-row-subj">Question on form 4473</div>
  </div>
  <div class="mail-row-age">1h</div>
</div>
```

- Background tint differentiates unread vs read
- Click toggles `.expanded` class on the row + reveals a `.mail-row-detail` sibling
- `max-height: 0 → 600px` transition for smooth open/close
- Only one row expanded at a time (clicking a new row collapses the previous)

### Expanded message detail

When a row is expanded, immediately below it:

```html
<div class="mail-row-detail">
  <div class="mail-detail-meta">1 hour ago · 1 of 1 in thread</div>
  <div class="mail-detail-body">...rendered message body...</div>
  <div class="mail-action-bar">
    <button class="pill-btn" onclick="_mailStartReply(messageId)">Draft Reply</button>
    <button class="pill-btn" onclick="_mailOpenGmail(messageId)">Open in Gmail</button>
    <button class="pill-btn" onclick="_mailMarkRead(messageId)" hidden-if-read>Mark Read</button>
  </div>
</div>
```

- Message body sanitized via `DOMPurify` if available (loaded via existing CDN-cached script tag) — otherwise plain-text fallback with linkification
- `Open in Gmail`: `shell.openExternal('https://mail.google.com/mail/u/0/#inbox/' + messageId)` via the Electron IPC bridge (already used by `news-article` flow)

### Reply flow (per Q4 — optional inline intent + skip)

Replaces the action bar when `Draft Reply` is clicked:

**Step 1 — intent entry**:
```
<input class="mail-intent-input" placeholder="What's the gist? (optional)" autofocus>
<button class="pill-btn-primary" onclick="_mailDraftReply(intent)">Draft With Intent</button>
<button class="pill-btn-secondary" onclick="_mailDraftReply('')">skip and draft now</button>
```

**Step 2 — composing**:
```
<div class="mail-draft-loading">Pike is composing draft…</div>
```
Calls `POST /api/email/draft-reply` with `{message_id, intent}`. Typical wait 30-60s on local 8B model.

**Step 3 — editable draft**:
```html
<div class="mail-draft-editor">
  <div class="mail-draft-meta">
    <span>To: bill@example.com</span>
    <button class="mail-regen-icon" onclick="_mailRegenerateDraft(draftId)" title="Regenerate">↺</button>
  </div>
  <input class="mail-draft-subject" value="Re: Question on form 4473">
  <textarea class="mail-draft-body">{pike's draft text}</textarea>
  <div class="mail-action-bar">
    <button class="pill-btn" onclick="_mailSaveDraft(draftId)">Save Draft</button>
    <button class="pill-btn-send" onclick="_mailStartSend(draftId)">Send</button>
    <button class="pill-btn-danger" onclick="_mailDiscardDraft(draftId)">Discard</button>
  </div>
</div>
```

- `Save Draft` → PATCHes the draft body via `/api/email/draft-reply` (or whatever the save endpoint shape ends up being) and shows toast `Draft saved`
- `Send` → two-step amber confirmation flow (see "Send confirmation flow" below)
- `Discard` → `DELETE /api/email/drafts/{id}` + collapse the expanded row + toast `Discarded`
- `↺ Regenerate` → re-calls `POST /api/email/draft-reply` with the same `message_id` and current intent (preserved client-side), replaces draft body with the new output

## COMPOSE tab

Single inline form, replaced by the draft view on submit.

### Form

```html
<div class="mail-compose-form">
  <label>To <input class="mail-field-to" required placeholder="recipient@example.com"></label>
  <label>CC <input class="mail-field-cc" placeholder="(optional, comma-separated)"></label>
  <label>BCC <input class="mail-field-bcc" placeholder="(optional, comma-separated)"></label>
  <label>Subject hint <input class="mail-field-subject" placeholder="(optional — Pike writes it if blank)"></label>
  <label>Intent <textarea class="mail-field-intent" required rows="4"
    placeholder="What do you want to say? Pike will draft it in your voice."></textarea></label>
  <button class="pill-btn-primary mail-compose-submit"
    onclick="_mailComposeSubmit()" disabled>Draft</button>
</div>
```

- `Draft` button enabled when `To` matches a basic email regex AND `Intent` is non-empty
- On submit: form fields fade to `opacity:0.3`, spinner shown above the action bar saying `Pike is composing…`
- Calls `POST /api/email/draft` with `{to, cc, bcc, subject_hint, intent}` — backend extended to accept CC/BCC (see Backend additions)

### Draft view (replaces form on success)

Same structure as the reply flow's editable draft, plus:

- Recipients displayed as pill-style chips at the top: `To: bill@…` `CC: tyler@…`. Click a chip to revert that single field back to input (so you can edit the recipient list without losing the body).
- `↻ New Draft` button — resets the entire form to empty state for a fresh compose.
- All other action-bar behavior (`Save Draft`, `Send`, `Discard`, `↺ Regenerate`) matches the reply flow.

## DRAFTS tab

### List

Same stacked-row pattern as INBOX, populated from `GET /api/email/drafts`:

```html
<div class="mail-row" data-draft-id="...">
  <div class="mail-row-icon">◇</div>            <!-- cyan diamond — Pike's mark -->
  <div class="mail-row-meta">
    <div class="mail-row-from">To: bill@example.com</div>
    <div class="mail-row-subj">Re: Question on form 4473</div>
  </div>
  <div class="mail-row-age">saved 12m ago</div>
</div>
```

- Pike-drafted items always show the `◇` icon (Pike's voice signal); items not drafted via Pike (rare — manually-imported drafts) show no icon
- Empty state: *"No drafts yet. Pike will save drafts here when you ask him to compose anything, or when you save one from INBOX or COMPOSE."*

### Expanded detail

When a draft row is expanded, identical editor to the reply flow's editable draft, populated from `GET /api/email/drafts/{id}`. Includes all recipients (To / CC / BCC), Subject, Body. Action bar: `Save Changes`, `Send` (two-step amber), `Discard`, `↺ Regenerate` (re-runs the original draft endpoint with stored intent).

## Send confirmation flow (shared)

Triggered by `Send` button anywhere (reply, compose, drafts). Implemented in JS, no backend involvement until step 3.

1. **First click** — button label changes to `Confirm Send`, background flips to LCARS amber (`var(--lcars-amber)`), `box-shadow` pulse animation runs at 5-second total duration. A `setTimeout(_mailResetSendButton, 5000)` resets to `Send` if no second click.

2. **Second click within 5s** — the row collapses immediately, a toast appears: `Sent to bill@example.com · undo (5s)`. Toast has a clickable `undo` link. The actual `POST /api/email/send-draft/{id}` call is **deferred** — `setTimeout(actuallySend, 5000)` queues it. State stored: `var _mailPendingSend = {draftId, timeoutId}`.

3. **Within those 5s**, the `undo` link clears the timeout, re-shows the draft expanded with body intact, and shows toast `Send cancelled`.

4. **After 5s** with no undo, the deferred fetch fires (`POST /api/email/send-draft/{id}` with `{confirm: true}`), toast fades, draft is removed from the DRAFTS list.

This pattern matches Gmail's "Undo Send" — the safety guarantee is real, not cosmetic. The server only ever sees the actual send call when the 5s window elapses.

## Auth state (Google not connected)

The first time any of the email endpoints returns `{"error": "not_authorized"}`:

- The panel body shows a centered CTA block (replacing all tab content):
  ```
  Pike needs Google access to read your mail.
  [Authorize Google ▸]
  ```
- The button opens `/oauth/google/start` in a new tab via `shell.openExternal` (Electron) or `window.open` (browser)
- A polling loop checks `/api/google/status` every 3s; when auth succeeds, the panel re-fetches and renders normally

## Loading / error / empty states summary

| Surface           | Loading                           | Error                                              | Empty                                                                 |
|-------------------|-----------------------------------|----------------------------------------------------|----------------------------------------------------------------------|
| Inbox narrative   | `Pike is reading your inbox…`     | `[Pike unavailable — <error>] · ↻ retry`           | (Backend writes: "Inbox is clear. Nothing waiting.")                  |
| Inbox list        | spinner row                       | `Couldn't load messages · ↻ retry`                 | (Implied by narrative)                                                |
| Reply draft       | `Pike is composing draft…`        | `Couldn't draft right now. [Retry]`                | n/a                                                                  |
| Compose form      | `Pike is composing…` overlay      | Re-show form + toast with error                    | n/a                                                                  |
| Drafts list       | spinner row                       | `Couldn't load drafts · ↻ retry`                   | "No drafts yet. Pike will save drafts here when…"                    |
| Send              | Button text: `Sending…`           | Toast: `Couldn't send — saved as draft`            | n/a                                                                  |

## Settings (gear menu)

Three controls in the Mail panel's settings dropdown (via the standard `togglePanelSettings('mailPanel')` flow):

| Control                        | Type           | Default | Notes                                                          |
|--------------------------------|----------------|---------|----------------------------------------------------------------|
| Inbox messages to fetch        | number (1-50)  | 10      | Controls `max_messages` query param                            |
| Narrative cache TTL            | number (min)   | 10      | Persisted to `aegis_mail_settings.cacheTTL`; backend reads it via subsequent requests |
| Show Pike icon on Pike-drafts  | toggle         | ON      | Controls the `◇` icon on DRAFTS rows                          |

Persistence: `localStorage` under `aegis_mail_settings`.

## Polling and refresh policy

- **No automatic polling.** Opening the panel triggers a fetch; switching tabs lazy-loads.
- **Refresh button per tab** does manual refresh.
- **`_refreshAfterChat`** hook in `index.html` (added in the previous session) gets a new branch: when the Mail panel is open AND Pike likely created a draft via chat (we can detect this from the new ✶ system note pattern), trigger a DRAFTS refresh.
- **Tab switches** re-render the active section; cached data is used unless `lastFetchedAt` is more than 60s old, in which case a background refresh is triggered.

## Implementation surfaces

Files expected to change:

- `core/email_assistant.py` — narrative cache, `fresh` kwarg, CC/BCC kwargs on `draft_new`, `mark_read` wrapper
- `core/protocols/google_tools.py` — CC/BCC in MIME builder, `gmail_mark_read` helper
- `server/app.py` — `fresh` query param on inbox-digest endpoint; `cc`/`bcc` on draft-create Pydantic model; new `mark-read` endpoint
- `ui/templates/index.html` — Mail panel HTML, CSS, JS:
  - Panel markup (tabs + 3 sections)
  - `.mail-*` CSS class set
  - JS functions: `loadInboxDigest`, `loadDrafts`, `_mailSwitchTab`, `_mailStartReply`, `_mailDraftReply`, `_mailComposeSubmit`, `_mailStartSend`, `_mailActuallySend`, `_mailUndoSend`, `_mailSaveDraft`, `_mailDiscardDraft`, `_mailRegenerateDraft`, `_mailMarkRead`, `_mailOpenGmail`, `_mailResetSendButton`, `_mailMaybeRefreshOnChat`
  - localStorage helpers for `aegis_mail_settings` and `aegis_mail_active_tab`
  - Sidebar `MAIL` button
- No new endpoints, no migrations.

## Out of scope (for v1)

- Search across inbox or drafts
- Threading view (multi-message conversations)
- Attachments (read or compose)
- Labels / folders / archive
- Multi-account
- Recall after actual send
- HTML composition (drafts are plain-text only)
- Scheduled send (the deferred-send IS scheduled, but only by 5s)

## Defaults summary (first-run UX)

| Setting                        | Default      |
|--------------------------------|--------------|
| Active tab on first open       | INBOX        |
| Inbox messages to fetch        | 10           |
| Narrative cache TTL            | 10 minutes   |
| Show Pike icon on Pike-drafts  | ON           |
| Deferred-send delay            | 5 seconds    |
| Confirm Send window            | 5 seconds    |
