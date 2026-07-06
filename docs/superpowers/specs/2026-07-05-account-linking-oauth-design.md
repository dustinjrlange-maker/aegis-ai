# Account-Aware Google OAuth — Link a Second/Nth Account (Design)

**Date:** 2026-07-05
**Status:** Approved design, pre-implementation
**Depends on:** Wave 3.5 multi-account identity (merged main `8d31e62`)
**Immediate goal:** link TheSwitchStitch@gmail.com today; reusable for HBO / Reel Ballistics / Husky Blue.

## Problem

Wave 3.5 built the multi-account model (per-user `accounts.json` registry + per-account
token dirs; `save_credentials(account_id=)` is account-aware). But the OAuth *connect*
flow was left single-account: `/api/google/callback` saves tokens with no account id,
which resolves to the **default** account. Running "connect Google" a second time would
**overwrite the Personal account's tokens**, not create a second account. Linking account
#2 needs the connect flow to be account-aware.

Groundwork already present: `save_credentials(user_data_dir, creds, account_id=)`
(Wave 3.5), `accounts.json` registry + `AccountManager`, OAuth client secrets at
`data/google_client.json`, the `/api/google/callback` redirect URI already registered in
the Google Cloud Console, and existing scopes (`gmail.modify`, `gmail.compose`,
`calendar.readonly`, `calendar.events`) — `gmail.modify` already covers Gmail
`getProfile`, so the connected account's email can be captured with **no new scope**.

## Decisions (brainstorm 2026-07-05)

1. **Scope = account-aware backend + a minimal "Add another Google account" UI button.**
   Fully reusable/repeatable (click per future account), not a throwaway script.
2. **Add form captures Label + Present-as name.** Sign-off defaults to the name; tone hint
   blank (editable in `accounts.json` later). Email auto-captured from Google.
3. **Approach A — entry created on success.** The `accounts.json` entry is written only in
   the callback, after the code exchange succeeds — no orphan/dangling entries if the user
   abandons consent.

## Architecture

### New backend components

**`AccountManager.upsert_account(label, email, represent_as) -> account_id`**
(`core/accounts/manager.py`)
- If an account with `email` already exists → update it (label/represent_as refreshed,
  tokens re-saved by the caller); return its id. (Dedupe by EMAIL — the account's true
  identity — not by label.)
- Else → create a new record: `id` = slug of label (`"SwitchStitch"` →
  `google-switchstitch`), deduped for uniqueness (append `-2`, `-3`, … on collision);
  `provider="google"`, `email`, `label`, `represent_as`
  (`{name, signoff: name, tone_hint: ""}` when signoff/tone not supplied),
  `features={"briefing_calendar": true, "inbox_scan": true}`, `is_default=false`,
  `status="ok"`. Return the new id.
- Thread-safe: reuses the module `_REGISTRY_LOCK` + atomic write from Wave 3.5.
- Slug rule: lowercase, non-alphanumeric → `-`, collapse repeats, prefix `google-`.

**`google_tools.get_account_email(creds) -> str`** (`core/protocols/google_tools.py`)
- Calls Gmail `users().getProfile(userId="me")`, returns `emailAddress` (covered by the
  existing `gmail.modify` scope). Returns `""` on any failure (logged, non-fatal).

**`POST /api/google/accounts/add`** (`server/app.py`)
- Body: `{label: str, name: str}`. Validates non-empty label.
- Generates a state token; stashes `{"user_id": user_id, "pending": {"label", "name"}}`
  in `_oauth_states`; returns `{auth_url}` built with **`prompt="select_account consent"`**
  (forces Google's account chooser so the user explicitly picks the new account rather than
  silently reusing the active browser session).

**`GET /api/google/accounts`** (`server/app.py`)
- Returns the linked-account list for the UI: `[{id, label, email, status, is_default}]`
  (metadata only, no tokens). Drives the panel's "what's connected" view.

### Modified components

**`_oauth_states`** (`server/app.py`) — now maps `state → {"user_id", "pending"?}` instead
of `state → user_id`. The existing default-connect (`/api/google/auth`) omits `pending`,
so it is backward-compatible.

**`/api/google/callback`** (`server/app.py`) — recover the state dict; exchange the code.
- If `pending` present: obtain the user's manager as `accounts = AccountManager(user_data_dir)`
  (the callback already computes `user_data_dir = PROJECT_ROOT/"data"/"users"/user_id`);
  `email = get_account_email(creds)`;
  `acct_id = accounts.upsert_account(label, email, {"name": name})`;
  `save_credentials(user_data_dir, creds, account_id=acct_id)`.
- Else: existing default-connect behavior, unchanged
  (`save_credentials(user_data_dir, creds)`).

**`build_auth_url`** (`core/protocols/google_tools.py`) — gains an optional
`prompt=` override so the add flow can pass `"select_account consent"` while the default
flow keeps `"consent"`.

### UI

A **"Add another Google account"** button near the existing Google connect control in the
Mail/settings panel (`ui/templates/index.html`). Click → small inline form (Label,
Present-as name) → "Connect" `POST`s to `/api/google/accounts/add` → open the returned
`auth_url` → after the success page, refresh the panel via `GET /api/google/accounts` to
show the newly linked account. A minimal linked-accounts list (label · email · status) is
rendered from that endpoint so the user can see and verify what's connected.

## Data flow (linking SwitchStitch)

```
[Add account] → form {label:"SwitchStitch", name:"Switch"}
  → POST /api/google/accounts/add  → stash {user_id, pending} @ state; return auth_url
  → browser: Google account chooser (select_account) → pick TheSwitchStitch@gmail.com → consent
  → GET /api/google/callback?code&state
       exchange_code → get_account_email() = "theswitchstitch@gmail.com"
       upsert_account("SwitchStitch", email, {name:"Switch"}) = "google-switchstitch"
       save_credentials(user_dir, creds, account_id="google-switchstitch")
  → success page → panel refresh → both accounts listed
```

## Correctness details

- **Account chooser:** add flow uses `prompt="select_account consent"`. Without it Google
  silently reuses the active session (Dustin) and would mislink it under the new entry.
- **Dedupe by email:** `upsert_account` matches on the captured email. Re-linking an
  existing address updates that account instead of creating a duplicate — this is also the
  safety net if the user accidentally picks the wrong Google account at the chooser
  (tokens land back on the correct existing account, not a bogus new entry).

## Error handling

- `get_account_email` fails → email saved blank; entry still created (editable later);
  logged. Never blocks the link.
- User abandons consent → no entry written (Approach A); nothing to clean up.
- Invalid/expired state → existing error page, unchanged.
- `upsert_account` write failure → propagates the Wave 3.5 atomic-write error (logged,
  raised); the callback surfaces a generic error page.

## Testing

- **`upsert_account`**: new-account slug generation + collision dedupe; dedupe-by-email
  updates the existing record (no duplicate); represent_as defaults (signoff=name); atomic
  write leaves no `.tmp`.
- **`get_account_email`**: returns the address from a mocked `getProfile`; returns `""` on
  exception.
- **`POST /api/google/accounts/add`**: validates label; stashes `pending` state; returns an
  auth URL built with `select_account consent`.
- **`/api/google/callback` pending branch**: with fakes for `exchange_code` +
  `get_account_email`, creates the entry and saves tokens to the new account's dir; default
  branch (no `pending`) still saves to the default account.
- **UI**: inline JS, no harness — verified live by clicking the button (consistent with the
  rest of the frontend).

## Out of scope

- Editing an existing account's represent-as/label from the UI (edit `accounts.json`
  directly for now; a per-account edit UI is a later polish).
- Non-Google providers (the HBO account may be Microsoft — a separate provider
  implementation, not this flow).
- Removing/unlinking a non-default account from the UI (disconnect currently targets the
  default; multi-account disconnect is a follow-up).
