# Multi-Account Mail Inbox View (Design)

**Date:** 2026-07-07
**Status:** Approved design, pre-implementation
**Depends on:** Wave 3.5 multi-account identity (`8d31e62`) + account-linking (`c02bf5a`)

## Problem

The background email features are multi-account (briefing aggregates calendar +
unread across accounts; the heartbeat inbox_scan reads all accounts) and email
*compose* can pick an account. But the **interactive Mail panel** — browsing the
inbox, reading a message, and reply/forward/mark-read/archive — reads ONLY the
default account. `_recent_inbox()` and the read/action handlers in
`core/protocols/email_ops.py`, plus the UI's inbox endpoints, all use the default
account's credentials. So after linking a second account (SwitchStitch), its
actual inbox isn't viewable or actionable in the Mail panel.

Goal: make each linked account's inbox browsable and actionable in the Mail
panel, with the chat-driven actions ("reply to #2", "mark #3 read") operating on
the same account you're viewing.

## Decisions (brainstorm 2026-07-07)

1. **UX = account switcher, one inbox at a time.** A dropdown at the top of the
   Mail panel selects which account's inbox is shown; switching replaces the
   list. (Not a unified merged view; not per-account tabs.)
2. **Chat follows the selected account.** A shared session-level "active mail
   account" is the single source of truth; both the UI inbox view and the chat
   handlers read it, so `#N` indices and mark/archive/reply target the account
   you're viewing.
3. **Scope = Inbox + Drafts + Compose** all governed by the active account.
4. **Approach A — session is the single source of truth.** The switcher sets
   `session.current_mail_account` via a small endpoint; endpoints and chat read
   that one value (fallback: default account). localStorage only restores the
   choice on reload. (Not per-request account params; not client-only state.)

## Architecture

### 1. Active-account state + resolver

- **`core/session.py`**: add `self.current_mail_account = None` to `UserSession`
  (a string account id, or `None` = use the default account). Lives alongside
  the existing `_pending` session scratch state.
- **`core/email_assistant.py`**: add `active_account_id(session)` →
  `session.current_mail_account` if set, else the default account's id (via
  `session.accounts.default()`), else `None`. Single function every consumer
  calls to answer "which account are we acting on now?"

### 2. Set-active endpoint (`server/app.py`)

- **`POST /api/email/active-account`**, body `{account_id}`:
  - `account_id` null/empty → clears to default (`current_mail_account = None`).
  - Non-empty: must exist in `session.accounts` (`get(account_id)` not None) →
    set `session.current_mail_account = account_id`; else 400.
  - Returns the resolved active account summary `{id, label, email, status}`
    (reuse the `_account_summary` projection from the linking work) so the UI can
    update its header.

### 3. email_assistant account_id threading

Add `account_id=None` (mirroring the existing pattern —
`_creds_from_session(session, account_id)`) to the four still-default-only
functions:
- `get_inbox_digest(session, ..., account_id=None)`
- `mark_read(session, message_id, account_id=None)`
- `list_drafts(session, ..., account_id=None)`
- `get_draft(session, draft_id, account_id=None)`

(`draft_reply`/`draft_new`/`draft_forward`/`send_draft` already take `account_id`
from the linking work. The `gmail_*` functions already all take `creds` first —
no signature changes there.)

### 4. app.py endpoint threading

Each email endpoint resolves `account_id = active_account_id(session)` and passes
it to the email_assistant call. Because the switcher sets the session value
before the UI loads, endpoints read the session — no per-request account param.
Endpoints to thread:
`/api/email/inbox-digest`, `/api/email/messages/{id}`,
`/api/email/mark-read/{id}`, `/api/email/drafts` (GET),
`/api/email/drafts/{id}` (GET), and the draft-reply / draft (new) /
send-draft / draft PATCH / draft DELETE endpoints (pass the active account so
drafts + compose act on it; per-message explicit override still available in
chat via the classifier `ACCOUNT=` hint).

### 5. email_ops chat handlers (`core/protocols/email_ops.py`)

- **`_recent_inbox()`**: fetch with
  `_creds_from_session(self._session, active_account_id(self._session))` so the
  `#N` listing and `_id_map` (index → message_id) belong to the active account.
- **`_resolve_account(action)`**: change the no-hint fallback from
  `accounts.default()` to the active account (`active_account_id` → the record).
  Explicit classifier `ACCOUNT=` hint still wins.
- **`_do_mark_read` / `_do_archive`**: use the active account's creds (currently
  default) so "mark #3 read" / "archive #1" hit the viewed inbox.
- Result: the classifier's inbox listing, the index map, and the read/action
  targets are always the one account in the switcher. Only an explicit
  "from SwitchStitch…" acts on a different account.

### 6. UI (`ui/templates/index.html`)

- **Switcher**: a dropdown at the top of the Mail panel, populated from the
  existing `GET /api/google/accounts`, showing each account's label; governs
  Inbox, Drafts, and Compose.
- **Selection flow**: on change → `POST /api/email/active-account {account_id}`
  → reload the active tab (inbox digest or drafts list) → persist to
  `localStorage` (`aegis_mail_settings.selected_account`).
- **Panel open**: read localStorage → POST to sync the session → load.
- **Compose**: defaults its from-account to the active account (existing
  represent-as/classifier still allows per-message override).

## Error handling

- **Needs-reconnect account** (`status == "error"`, e.g. the weekly Gmail-token
  expiry in Google Testing mode): the switcher renders it with a
  "⚠ needs reconnect" marker. Selecting it shows a clear inbox state —
  "SwitchStitch needs reconnecting" + a **Reconnect** action that runs the same
  add/link flow (re-links by email, refreshes tokens in place) — instead of an
  empty/broken list.
- **`creds_for` yields None** on any email endpoint for the active account →
  return a clean "account not connected" response; UI shows the reconnect prompt;
  never crash.
- **Active account deleted/unlinked while selected** → fall back to default.
- **Chat with an errored active account**: `_recent_inbox` returns an empty
  listing and handlers report "that account needs reconnecting" rather than
  silently falling back to Personal.

## Testing

- **Session**: `current_mail_account` default None; setter round-trip.
- **`active_account_id`**: returns current when set, default id when unset, None
  when no accounts.
- **email_assistant**: `get_inbox_digest`/`mark_read`/`list_drafts`/`get_draft`
  route to the account_id's creds (fake creds keyed by account).
- **app.py**: set-active endpoint validates + sets session state (unknown id →
  400, null → default); inbox/mark-read/drafts endpoints act on the active
  account; an error-account request degrades gracefully (no crash).
- **email_ops**: `_recent_inbox` uses active-account creds; `_resolve_account`
  no-hint fallback is the active account (not default); `_do_mark_read` /
  `_do_archive` use active-account creds; explicit `ACCOUNT=` hint still
  overrides.
- **UI**: inline JS, no harness — verified live (switcher populates, switching
  reloads the inbox/drafts, selection persists, errored account shows reconnect).

## Out of scope

- Unified/merged cross-account inbox view (chose the switcher instead).
- Per-account tabs.
- Non-Google providers.
- Any change to the background features (briefing, heartbeat scan) — already
  multi-account.
