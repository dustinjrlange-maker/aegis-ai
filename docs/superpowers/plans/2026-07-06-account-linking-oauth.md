# Account-Aware Google OAuth (Link Nth Account) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user link a second/Nth Google account (starting with TheSwitchStitch@gmail.com) through the normal browser-consent flow, creating a distinct `accounts.json` entry instead of overwriting the default account.

**Architecture:** Carry a "pending account" (label + present-as name) through the OAuth `state` token; the callback captures the connected email via Gmail `getProfile`, upserts an `accounts.json` entry (deduped by email), and saves tokens to that account's dir. Uses `prompt=select_account consent` so the user explicitly picks the account. A minimal Mail-panel button + linked-accounts list drive it.

**Tech Stack:** Python 3.12, FastAPI, pytest (+ FastAPI TestClient), google-auth (existing). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-05-account-linking-oauth-design.md`

**Branch:** `feature/account-linking-oauth` (already checked out). Run tests from repo root: `python -m pytest <path> -v`. Full suite baseline: `python -m pytest -q` (759 passing on main at branch point).

---

## File structure

| File | Responsibility (change) |
|---|---|
| `core/accounts/manager.py` | add `upsert_account()` + module `_slugify_account_id()` helper |
| `core/protocols/google_tools.py` | add `get_account_email()`; `build_auth_url()` gains `prompt=` param |
| `server/app.py` | `_oauth_states` value becomes a dict; `/api/google/auth` stores dict; new `POST /api/google/accounts/add` + `GET /api/google/accounts`; `/api/google/callback` pending-branch |
| `ui/templates/index.html` | "Add another Google account" button + inline form + linked-accounts list |
| `tests/accounts/test_upsert.py` (new) | upsert_account unit tests |
| `tests/accounts/test_account_email.py` (new) | get_account_email unit tests |
| `tests/test_account_linking_endpoints.py` (new) | endpoint + callback tests (TestClient) |

---

### Task 1: `AccountManager.upsert_account` + slug helper

**Files:**
- Modify: `core/accounts/manager.py`
- Test: `tests/accounts/test_upsert.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/accounts/test_upsert.py
import json
from core.accounts.manager import AccountManager, _slugify_account_id


def _seed(tmp_path, accounts):
    (tmp_path / "accounts.json").write_text(
        json.dumps({"accounts": accounts}), encoding="utf-8")


def test_slugify():
    assert _slugify_account_id("SwitchStitch") == "google-switchstitch"
    assert _slugify_account_id("HBO Max!!") == "google-hbo-max"
    assert _slugify_account_id("") == "google-account"


def test_upsert_creates_new_account(tmp_path):
    am = AccountManager(tmp_path)
    acct_id = am.upsert_account("SwitchStitch", "TheSwitchStitch@gmail.com",
                                {"name": "Switch"})
    assert acct_id == "google-switchstitch"
    a = am.get("google-switchstitch")
    assert a["email"] == "TheSwitchStitch@gmail.com"
    assert a["label"] == "SwitchStitch"
    assert a["is_default"] is False
    assert a["provider"] == "google"
    assert a["features"] == {"briefing_calendar": True, "inbox_scan": True}
    assert a["status"] == "ok"
    # signoff defaults to name; tone blank
    assert a["represent_as"] == {"name": "Switch", "signoff": "Switch", "tone_hint": ""}


def test_upsert_dedupes_by_email_case_insensitive(tmp_path):
    _seed(tmp_path, [{
        "id": "google-personal", "provider": "google",
        "email": "dustin.jr.lange@gmail.com", "label": "Personal",
        "is_default": True, "represent_as": {"name": "Dustin", "signoff": "Dustin", "tone_hint": ""},
        "features": {"briefing_calendar": True, "inbox_scan": True}, "status": "error",
    }])
    am = AccountManager(tmp_path)
    # same email, different case -> updates existing, no new account
    acct_id = am.upsert_account("Dustin Personal", "DUSTIN.JR.LANGE@gmail.com",
                                {"name": "Dustin"})
    assert acct_id == "google-personal"
    assert len(am.list()) == 1
    assert am.get("google-personal")["label"] == "Dustin Personal"
    assert am.get("google-personal")["status"] == "ok"     # reset from error


def test_upsert_slug_collision_appends_number(tmp_path):
    _seed(tmp_path, [{"id": "google-work", "provider": "google", "email": "a@x.com",
                      "label": "Work", "is_default": False,
                      "represent_as": {"name": "", "signoff": "", "tone_hint": ""},
                      "features": {"briefing_calendar": True, "inbox_scan": True},
                      "status": "ok"}])
    am = AccountManager(tmp_path)
    # different email, label slugs to the same base -> deduped id
    acct_id = am.upsert_account("Work", "b@y.com", {"name": "Me"})
    assert acct_id == "google-work-2"
    assert len(am.list()) == 2


def test_upsert_blank_email_still_creates(tmp_path):
    am = AccountManager(tmp_path)
    acct_id = am.upsert_account("SwitchStitch", "", {"name": "Switch"})
    assert acct_id == "google-switchstitch"
    assert am.get("google-switchstitch")["email"] == ""


def test_upsert_leaves_no_tmp_file(tmp_path):
    am = AccountManager(tmp_path)
    am.upsert_account("SwitchStitch", "s@x.com", {"name": "Switch"})
    assert not (tmp_path / "accounts.json.tmp").exists()
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/accounts/test_upsert.py -v`
Expected: FAIL — `ImportError: cannot import name '_slugify_account_id'`

- [ ] **Step 3: Implement**

In `core/accounts/manager.py`, add `import re` to the imports if not present, then add the module-level helper (below the constants, near `_REGISTRY_LOCK`):

```python
import re


def _slugify_account_id(label):
    """Derive a stable account id from a human label: 'SwitchStitch' ->
    'google-switchstitch'. Empty/garbage -> 'google-account'."""
    base = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")
    return f"google-{base}" if base else "google-account"


def _normalize_represent_as(rep):
    """Build a represent_as block from partial input: signoff defaults to name,
    tone_hint blank when absent."""
    rep = rep or {}
    name = (rep.get("name") or "").strip()
    return {
        "name": name,
        "signoff": (rep.get("signoff") or name),
        "tone_hint": rep.get("tone_hint", "") or "",
    }
```

Add the method to `AccountManager`:

```python
    def upsert_account(self, label, email, represent_as=None):
        """Create a new Google account record, or update the existing one that
        already has *email* (dedupe by email — the account's true identity).

        New id is a slug of *label* (deduped for uniqueness). New accounts are
        non-default with both features on and status 'ok'. Returns the id.
        Thread-safe: whole read-modify-write under the registry lock.
        """
        with _REGISTRY_LOCK:
            data = self._read()
            accounts = data.setdefault("accounts", [])
            email_l = (email or "").strip().lower()

            if email_l:
                for a in accounts:
                    if (a.get("email") or "").strip().lower() == email_l:
                        if label:
                            a["label"] = label
                        if represent_as and (represent_as.get("name") or "").strip():
                            a["represent_as"] = _normalize_represent_as(represent_as)
                        a["status"] = "ok"
                        self._write(data)
                        return a["id"]

            base = _slugify_account_id(label)
            existing_ids = {a.get("id") for a in accounts}
            acct_id, n = base, 2
            while acct_id in existing_ids:
                acct_id, n = f"{base}-{n}", n + 1

            accounts.append({
                "id": acct_id,
                "provider": "google",
                "email": email or "",
                "label": label or acct_id,
                "is_default": False,
                "represent_as": _normalize_represent_as(represent_as),
                "features": {"briefing_calendar": True, "inbox_scan": True},
                "status": "ok",
            })
            self._write(data)
            return acct_id
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/accounts/test_upsert.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add core/accounts/manager.py tests/accounts/test_upsert.py
git commit -m "account linking: AccountManager.upsert_account (dedupe by email) + slug helper"
```

---

### Task 2: `get_account_email` + `build_auth_url` prompt override

**Files:**
- Modify: `core/protocols/google_tools.py`
- Test: `tests/accounts/test_account_email.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/accounts/test_account_email.py
from core.protocols import google_tools as gt


class _FakeService:
    def __init__(self, email=None, raises=False):
        self._email = email
        self._raises = raises

    def users(self):
        return self

    def getProfile(self, userId=None):
        return self

    def execute(self):
        if self._raises:
            raise RuntimeError("boom")
        return {"emailAddress": self._email}


def test_get_account_email_ok(monkeypatch):
    monkeypatch.setattr(gt, "_get_gmail_service",
                        lambda creds: _FakeService(email="x@y.com"))
    assert gt.get_account_email(object()) == "x@y.com"


def test_get_account_email_no_service(monkeypatch):
    monkeypatch.setattr(gt, "_get_gmail_service", lambda creds: None)
    assert gt.get_account_email(object()) == ""


def test_get_account_email_swallows_errors(monkeypatch):
    monkeypatch.setattr(gt, "_get_gmail_service",
                        lambda creds: _FakeService(raises=True))
    assert gt.get_account_email(object()) == ""


def test_build_auth_url_default_prompt_is_consent():
    # signature-level: prompt defaults to "consent", accepts override.
    import inspect
    sig = inspect.signature(gt.build_auth_url)
    assert sig.parameters["prompt"].default == "consent"
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/accounts/test_account_email.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'get_account_email'` (and the prompt param test fails until Step 3)

- [ ] **Step 3: Implement**

In `core/protocols/google_tools.py`, add `get_account_email` next to the other Gmail functions (after `_get_gmail_service`):

```python
def get_account_email(creds):
    """Return the connected Google account's email via Gmail getProfile.

    Covered by the existing gmail.modify scope (no extra scope needed). Returns
    "" on any failure (logged) so account linking is never blocked by it.
    """
    service = _get_gmail_service(creds)
    if not service:
        return ""
    try:
        return service.users().getProfile(userId="me").execute().get("emailAddress", "")
    except Exception as e:
        logger.warning("Could not fetch account email: %s", e)
        return ""
```

Change `build_auth_url` to accept a `prompt` override. Current signature is
`def build_auth_url(redirect_uri, state=None):` and the kwargs dict hardcodes
`"prompt": "consent"`. Change to:

```python
def build_auth_url(redirect_uri, state=None, prompt="consent"):
```

and in the kwargs dict, replace the hardcoded `"prompt": "consent"` line with:

```python
        "prompt": prompt,
```

(Leave `access_type` and `include_granted_scopes` unchanged.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/accounts/test_account_email.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add core/protocols/google_tools.py tests/accounts/test_account_email.py
git commit -m "account linking: get_account_email via getProfile + build_auth_url prompt override"
```

---

### Task 3: OAuth state shape + add-account and list endpoints

**Files:**
- Modify: `server/app.py` (`_oauth_states` value shape; `/api/google/auth`; new `POST /api/google/accounts/add`, `GET /api/google/accounts`)
- Test: `tests/test_account_linking_endpoints.py` (new)

**Context:** The state store `_oauth_states: dict[str, str]` currently maps `state -> user_id`.
This task changes the VALUE to a dict `{"user_id": str, "pending": {...} | absent}` and
updates the one existing producer (`/api/google/auth`) to store `{"user_id": user_id}`.
The callback is updated in Task 4 to read the dict — do BOTH the auth producer here and the
callback in Task 4 so the shape stays consistent; the full suite is only asserted green at
the end of Task 4.

**Auth in tests:** endpoints use `Depends(require_user)`. Read
`tests/test_server_security.py` and `tests/tooling/test_tools_endpoints.py` FIRST to copy
the exact TestClient auth setup this project uses (how it obtains a valid session token or
overrides the dependency). Mirror that pattern in the new test file.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_account_linking_endpoints.py
# NOTE: reuse the TestClient + auth helper pattern from tests/test_server_security.py.
# The helper below is a placeholder name — replace `authed_client` with whatever that
# file's fixture/utility provides (e.g. a client with a valid session cookie/header).
from unittest.mock import patch


def test_add_account_returns_auth_url_and_uses_select_account(authed_client, monkeypatch):
    import server.app as app_mod
    captured = {}

    def fake_build(redirect_uri, state=None, prompt="consent"):
        captured["prompt"] = prompt
        captured["state"] = state
        return "https://accounts.google.com/o/oauth2/auth?fake=1"

    monkeypatch.setattr("core.protocols.google_tools.build_auth_url", fake_build)
    # ensure Google is considered enabled for the endpoint
    monkeypatch.setattr("integrations.google_config.is_enabled", lambda: True)

    resp = authed_client.post("/api/google/accounts/add",
                              json={"label": "SwitchStitch", "name": "Switch"})
    assert resp.status_code == 200
    assert resp.json()["auth_url"].startswith("https://accounts.google.com")
    # the add flow MUST force the account chooser
    assert captured["prompt"] == "select_account consent"
    # the pending descriptor is stashed under the returned state token
    assert app_mod._oauth_states[captured["state"]]["pending"] == {
        "label": "SwitchStitch", "name": "Switch"}


def test_add_account_rejects_empty_label(authed_client, monkeypatch):
    monkeypatch.setattr("integrations.google_config.is_enabled", lambda: True)
    resp = authed_client.post("/api/google/accounts/add",
                              json={"label": "  ", "name": "Switch"})
    assert resp.status_code == 400


def test_list_accounts_returns_metadata(authed_client, tmp_path, monkeypatch):
    # Point the authed user's account registry at a seeded accounts.json.
    # (Use the same mechanism the other tests use to redirect data/users/<user>.)
    # Seed two accounts, then assert the endpoint returns id/label/email/status/is_default
    # WITHOUT tokens.
    resp = authed_client.get("/api/google/accounts")
    assert resp.status_code == 200
    body = resp.json()["accounts"]
    assert all(set(a.keys()) == {"id", "label", "email", "status", "is_default"}
               for a in body)
```

(If wiring `test_list_accounts_returns_metadata` to a seeded registry is awkward with the
existing fixtures, assert the shape against the authed user's real empty registry — an empty
list is a valid pass — and cover the populated case in a unit test on the helper that builds
the list. Keep the endpoint's projection logic in a tiny module function so it's unit-testable.)

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_account_linking_endpoints.py -v`
Expected: FAIL — 404 on the new routes.

- [ ] **Step 3: Implement**

In `server/app.py`:

Change the state store type/comment (line ~65):
```python
# OAuth state store: maps state_token -> {"user_id": str, "pending": {...}?}
_oauth_states: dict[str, dict] = {}
```

Update the existing producer in `/api/google/auth` — replace
`_oauth_states[state] = user_id` with:
```python
    _oauth_states[state] = {"user_id": user_id}
```

Add the two new endpoints near the other Google routes:

```python
from pydantic import BaseModel


class AddAccountRequest(BaseModel):
    label: str
    name: str = ""


@app.post("/api/google/accounts/add")
async def google_add_account(req: AddAccountRequest, request: Request,
                             user_id: str = Depends(require_user)):
    """Start OAuth to LINK a new Google account (not overwrite the default)."""
    try:
        from integrations.google_config import is_enabled
        from core.protocols.google_tools import build_auth_url
    except ImportError:
        return JSONResponse({"error": "Google integration not installed"}, status_code=500)

    if not is_enabled():
        return JSONResponse({"error": "Google integration not configured"}, status_code=400)

    label = (req.label or "").strip()
    if not label:
        return JSONResponse({"error": "Label is required"}, status_code=400)

    host = request.headers.get("host", "localhost:8484").replace("127.0.0.1", "localhost")
    scheme = request.headers.get("x-forwarded-proto", "http")
    redirect_uri = f"{scheme}://{host}/api/google/callback"

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "user_id": user_id,
        "pending": {"label": label, "name": (req.name or "").strip()},
    }
    # select_account forces Google's chooser so the user picks the NEW account
    # instead of silently reusing the browser's active session.
    auth_url = build_auth_url(redirect_uri, state=state, prompt="select_account consent")
    if not auth_url:
        _oauth_states.pop(state, None)
        return JSONResponse({"error": "Could not generate auth URL"}, status_code=500)
    return {"auth_url": auth_url}


def _account_summary(acct):
    """Project an account record to UI-safe metadata (no tokens)."""
    return {
        "id": acct.get("id", ""),
        "label": acct.get("label", ""),
        "email": acct.get("email", ""),
        "status": acct.get("status", "ok"),
        "is_default": bool(acct.get("is_default")),
    }


@app.get("/api/google/accounts")
async def google_list_accounts(user_id: str = Depends(require_user)):
    """List the user's linked Google accounts (metadata only)."""
    session = session_manager.get_or_create(user_id)
    return {"accounts": [_account_summary(a) for a in session.accounts.list()]}
```

Confirm the imports used here already exist at the top of `server/app.py`
(`secrets`, `Request`, `Depends`, `JSONResponse`, `session_manager`, `require_user`). Add any
that are missing (match the existing import style). `BaseModel` may already be imported —
reuse the existing import rather than duplicating.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_account_linking_endpoints.py -v`
Expected: the add-account + list tests PASS. (Callback test comes in Task 4.)

- [ ] **Step 5: Commit**

```bash
git add server/app.py tests/test_account_linking_endpoints.py
git commit -m "account linking: add-account + list endpoints; OAuth state carries pending account"
```

---

### Task 4: Callback pending-branch

**Files:**
- Modify: `server/app.py` (`/api/google/callback`)
- Test: extend `tests/test_account_linking_endpoints.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_account_linking_endpoints.py
def test_callback_links_new_account(authed_client, monkeypatch):
    import server.app as app_mod
    from core.config import PROJECT_ROOT

    # Arrange a pending state as the add endpoint would have created it.
    state = "teststate123"
    app_mod._oauth_states[state] = {
        "user_id": "<the authed test user id>",   # match the fixture's user
        "pending": {"label": "SwitchStitch", "name": "Switch"},
    }

    monkeypatch.setattr("core.protocols.google_tools.exchange_code",
                        lambda code, redirect_uri: object())     # fake creds
    monkeypatch.setattr("core.protocols.google_tools.get_account_email",
                        lambda creds: "TheSwitchStitch@gmail.com")
    saved = {}
    monkeypatch.setattr("core.protocols.google_tools.save_credentials",
                        lambda d, creds, account_id=None: saved.update(
                            {"dir": str(d), "account_id": account_id}))

    resp = authed_client.get(f"/api/google/callback?code=abc&state={state}")
    assert resp.status_code == 200
    # tokens saved under the NEW account's id
    assert saved["account_id"] == "google-switchstitch"
    # registry entry created for the authed user
    # (load the user's AccountManager the same way the app does and assert the account exists,
    #  email captured, presents as Switch)
```

(Adjust the `user_id` and the registry-load assertion to the test harness's authed user +
its `data/users/<user>` location. The load-and-assert mirrors how other tests inspect a
user's on-disk state.)

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_account_linking_endpoints.py::test_callback_links_new_account -v`
Expected: FAIL — callback ignores `pending`, saves to default (account_id is None).

- [ ] **Step 3: Implement**

In `/api/google/callback` (`server/app.py`), the state recovery currently is
`user_id = _oauth_states.pop(state, None)` where the value was a bare string. Update it to
read the dict and branch on `pending`:

```python
    state_data = _oauth_states.pop(state, None)
    if not state_data:
        return HTMLResponse("<h2>Invalid or expired state</h2><p>Please try connecting again.</p>")
    user_id = state_data.get("user_id")
    pending = state_data.get("pending")
    if not user_id:
        return HTMLResponse("<h2>Invalid or expired state</h2><p>Please try connecting again.</p>")
```

Then, after `credentials = exchange_code(...)` and the existing None-guard, replace the
save block. Current code:
```python
    from core.config import PROJECT_ROOT
    user_data_dir = PROJECT_ROOT / "data" / "users" / user_id
    user_data_dir.mkdir(parents=True, exist_ok=True)
    save_credentials(user_data_dir, credentials)
```
becomes:
```python
    from core.config import PROJECT_ROOT
    from core.protocols.google_tools import get_account_email, save_credentials
    from core.accounts.manager import AccountManager

    user_data_dir = PROJECT_ROOT / "data" / "users" / user_id
    user_data_dir.mkdir(parents=True, exist_ok=True)

    if pending:
        email = get_account_email(credentials)
        accounts = AccountManager(user_data_dir)
        acct_id = accounts.upsert_account(pending.get("label", ""), email,
                                          {"name": pending.get("name", "")})
        save_credentials(user_data_dir, credentials, account_id=acct_id)
        logger.info("Linked Google account '%s' (%s) for user '%s'",
                    acct_id, email or "email unknown", user_id)
    else:
        save_credentials(user_data_dir, credentials)
        logger.info("Google account connected for user '%s'", user_id)
```

Keep the existing success HTMLResponse. Make sure the `save_credentials` /
`get_account_email` imports don't collide with an existing top-of-function import (the
original imports `exchange_code, save_credentials` earlier in the handler — consolidate so
`save_credentials` isn't imported twice; import `exchange_code, save_credentials,
get_account_email` together).

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_account_linking_endpoints.py -v && python -m pytest -q`
Expected: linking test PASS; full suite green (the default-connect path is unchanged in
behavior; only the state value shape changed, updated in both producer and consumer).

- [ ] **Step 5: Commit**

```bash
git add server/app.py tests/test_account_linking_endpoints.py
git commit -m "account linking: callback creates account + saves tokens under it on pending link"
```

---

### Task 5: Mail-panel UI — Add button, form, linked-accounts list

**Files:**
- Modify: `ui/templates/index.html`
- Test: none (inline JS, no harness — verified live)

**Context:** Find the existing Google connect/disconnect controls in the Mail (or settings)
panel. Add the new UI adjacent to them. Match the panel's existing styling
(use the same CSS classes the surrounding controls use — do not invent a new style system;
per the user's palette preference, prefer the existing blue/slate accents already in the file).

- [ ] **Step 1: Add the button + form + list markup**

Near the existing Google connect control, add:
- A button `Add another Google account`.
- A hidden inline form with two text inputs: `Label` (placeholder e.g. `SwitchStitch`) and
  `Present as` (placeholder e.g. `Switch`), plus a `Connect` button and a `Cancel` button.
- A container `<div id="linkedAccountsList">` to render the linked accounts.

- [ ] **Step 2: Add the JS**

Add functions (mirror the existing fetch/DOM idioms in the file — same auth header/cookie
approach the other `/api/...` calls use):

```javascript
async function refreshLinkedAccounts() {
    const res = await fetch('/api/google/accounts', { credentials: 'same-origin' });
    if (!res.ok) return;
    const { accounts } = await res.json();
    const el = document.getElementById('linkedAccountsList');
    el.innerHTML = accounts.map(a =>
        `<div class="linked-account">${a.label}${a.email ? ' · ' + a.email : ''}` +
        `${a.is_default ? ' · default' : ''}` +
        `${a.status === 'error' ? ' · <span class="acct-error">needs reconnect</span>' : ''}</div>`
    ).join('');
}

async function submitAddAccount() {
    const label = document.getElementById('addAcctLabel').value.trim();
    const name = document.getElementById('addAcctName').value.trim();
    if (!label) { alert('Enter a label'); return; }
    const res = await fetch('/api/google/accounts/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ label, name }),
    });
    const data = await res.json();
    if (data.auth_url) {
        window.open(data.auth_url, '_blank');   // Google consent in a new tab
        // user returns after the success page; poll/refresh the list
        setTimeout(refreshLinkedAccounts, 1500);
    } else {
        alert(data.error || 'Could not start account linking');
    }
}
```

Wire the `Add another Google account` button to reveal the form; `Connect` to
`submitAddAccount()`; `Cancel` to hide the form. Call `refreshLinkedAccounts()` when the
Mail/settings panel opens (add the call wherever that panel's open handler already refreshes
its Google status).

- [ ] **Step 3: Verify live (manual)**

Restart Aegis. Open the Mail/settings panel → the button and the linked-accounts list show
(the list already includes your `Personal` account). Do NOT click Connect yet — that's the
supervised Task 6.

- [ ] **Step 4: Commit**

```bash
git add ui/templates/index.html
git commit -m "account linking: Mail-panel Add-account button, form, and linked-accounts list"
```

---

### Task 6: Live-link SwitchStitch (supervised) + holistic review

**Files:** none committed (live linking) — plus a holistic review of the diff.

- [ ] **Step 1: Holistic whole-feature review** — dispatch a review of `git diff main...feature/account-linking-oauth`, focus:
  - Privacy: the label/name/email now flow through the callback + `upsert_account` — confirm none of it reaches a cloud LLM payload (it doesn't touch `_llm`; verify).
  - Wrong-account guard: confirm `select_account consent` is actually sent, and dedupe-by-email lands a mis-picked account back on the correct existing entry rather than creating a bogus one.
  - State store: `_oauth_states` value shape is a dict at BOTH producers (`/auth`, `/accounts/add`) and the consumer (callback) — no bare-string reader remains (grep).
  - Concurrency: `upsert_account` uses the registry lock (it does) — a link during a heartbeat `set_status` won't corrupt the file.
  Fix anything found; re-run full suite.

- [ ] **Step 2: Live link (Switch, supervised)** — with Aegis running: open the Mail panel → Add another Google account → Label `SwitchStitch`, Present as `Switch` → Connect → at Google's chooser pick **TheSwitchStitch@gmail.com** → consent → success page.

- [ ] **Step 3: Verify** — `data/users/dustin/accounts.json` now has a second entry `google-switchstitch` with the real email + tokens at `data/users/dustin/accounts/google-switchstitch/google_tokens.json`. The panel's linked-accounts list shows both. Ask Pike for a briefing / trigger an inbox scan and confirm items tag by account.

- [ ] **Step 4: Merge + push** — merge `feature/account-linking-oauth` → main, push, delete the branch. Update Obsidian log + MEMORY.md (account #2 linked; the linking flow shipped).

---

## Self-review notes (plan-time)

- **Spec coverage:** upsert_account (T1), get_account_email + prompt override (T2), add/list endpoints + state shape (T3), callback pending-branch (T4), UI button/form/list (T5), live-link + holistic review (T6). Account-chooser `select_account consent` (T3 endpoint + T6 review). Dedupe-by-email (T1). Email-capture-free-via-getProfile (T2). All spec sections mapped.
- **Deviations:** none material. The spec's "GET /api/google/accounts" projection is implemented as `_account_summary` (a unit-testable module function) to keep the endpoint thin.
- **Type consistency:** `_oauth_states` value is `{"user_id", "pending"?}` in the producer (T3) and consumer (T4). `upsert_account(label, email, represent_as)` signature identical across T1 definition and T4 caller. `save_credentials(dir, creds, account_id=)` matches the Wave 3.5 signature. `build_auth_url(redirect_uri, state=, prompt=)` consistent T2↔T3.
- **Known harness dependency:** the endpoint/callback tests (T3/T4) depend on the project's existing TestClient auth pattern (`tests/test_server_security.py`) — the implementer must read that file to wire the authed client + locate the authed user's `data/users/<user>` dir; the plan flags this explicitly rather than guessing the fixture.
