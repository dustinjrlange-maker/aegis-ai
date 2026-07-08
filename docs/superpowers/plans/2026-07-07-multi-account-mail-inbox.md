# Multi-Account Mail Inbox View — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the interactive Mail panel browse and act on each linked account's inbox via an account switcher, with chat actions ("reply to #2", "mark #3 read") following the account you're viewing.

**Architecture:** One session value, `session.current_mail_account`, is the single source of truth. A `POST /api/email/active-account` sets it; a resolver `active_account_id(session)` reads it (fallback = default account). Every email endpoint and every chat handler resolves creds through that value. gmail_* functions already take `creds`, so threading = passing different creds.

**Tech Stack:** Python 3.12, FastAPI + TestClient, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-07-multi-account-mail-inbox-design.md`
**Branch:** `feature/multi-account-mail-inbox` (checked out). Full-suite baseline: `python -m pytest -q` (777 on main at branch point).

---

## File structure

| File | Change |
|---|---|
| `core/session.py` | add `self.current_mail_account = None` to UserSession |
| `core/email_assistant.py` | `active_account_id(session)`; add `account_id=` to `get_inbox_digest` (+ cache key), `mark_read`, `list_drafts`, `get_draft`, `discard_draft` |
| `server/app.py` | `POST /api/email/active-account`; thread `active_account_id(session)` into every `/api/email/*` endpoint |
| `core/protocols/email_ops.py` | `_recent_inbox`, `_resolve_account` fallback, `_do_mark_read`, `_do_archive` use the active account |
| `ui/templates/index.html` | Mail-panel account switcher; reload-on-switch; localStorage; reconnect state |
| `tests/test_multi_account_mail.py` (new) | session/resolver/endpoint/chat tests |
| `tests/accounts/test_active_account.py` (new) | `active_account_id` + email_assistant threading |

---

### Task 1: Session active-account state + resolver

**Files:**
- Modify: `core/session.py` (UserSession.__init__), `core/email_assistant.py`
- Test: `tests/accounts/test_active_account.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/accounts/test_active_account.py
import json
from core.email_assistant import active_account_id


class _FakeAccounts:
    def __init__(self, accounts):
        self._a = accounts
    def get(self, aid):
        return next((x for x in self._a if x["id"] == aid), None)
    def default(self):
        return next((x for x in self._a if x.get("is_default")), self._a[0] if self._a else None)


class _S:
    pass


def test_active_id_none_when_no_accounts():
    s = _S(); s.current_mail_account = None; s.accounts = _FakeAccounts([])
    assert active_account_id(s) is None


def test_active_id_defaults_when_unset():
    s = _S(); s.current_mail_account = None
    s.accounts = _FakeAccounts([{"id": "google-personal", "is_default": True}])
    assert active_account_id(s) == "google-personal"


def test_active_id_returns_selected_when_set_and_exists():
    s = _S(); s.current_mail_account = "google-stitch"
    s.accounts = _FakeAccounts([{"id": "google-personal", "is_default": True},
                                {"id": "google-stitch"}])
    assert active_account_id(s) == "google-stitch"


def test_active_id_falls_back_to_default_when_selected_is_stale():
    # selected account was deleted/unlinked -> fall back to default
    s = _S(); s.current_mail_account = "google-deleted"
    s.accounts = _FakeAccounts([{"id": "google-personal", "is_default": True}])
    assert active_account_id(s) == "google-personal"
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/accounts/test_active_account.py -v`
Expected: FAIL — `ImportError: cannot import name 'active_account_id'`

- [ ] **Step 3: Implement**

`core/session.py` — in `UserSession.__init__`, near `self.accounts = AccountManager(...)` (the account-linking work added that), add:
```python
        # The account the interactive Mail panel is currently acting on.
        # None = use the default account. Set via POST /api/email/active-account;
        # read by the email endpoints and the chat email handlers.
        self.current_mail_account = None
```

`core/email_assistant.py` — add near `_creds_from_session` (top of the module's helpers):
```python
def active_account_id(session):
    """The account id the interactive Mail panel is currently acting on.

    Returns session.current_mail_account when it's set AND still exists, else
    the default account's id, else None (no accounts / not connected). A stale
    selection (account since deleted) falls back to the default.
    """
    accounts = getattr(session, "accounts", None)
    aid = getattr(session, "current_mail_account", None)
    if accounts is None:
        return None
    if aid and accounts.get(aid) is not None:
        return aid
    default = accounts.default()
    return default["id"] if default else None
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/accounts/test_active_account.py -v`
Expected: all PASS. Then `python -m pytest -q` (session change is additive; full suite green).

- [ ] **Step 5: Commit**

```bash
git add core/session.py core/email_assistant.py tests/accounts/test_active_account.py
git commit -m "mail multi-account: session current_mail_account + active_account_id resolver"
```

---

### Task 2: email_assistant account_id threading

**Files:**
- Modify: `core/email_assistant.py` (`get_inbox_digest`, `mark_read`, `list_drafts`, `get_draft`, `discard_draft`)
- Test: append to `tests/accounts/test_active_account.py`

**Context:** These four+ functions currently call `_creds_from_session(session)` (default). `_creds_from_session(session, account_id)` already routes by account (account-linking work). `get_inbox_digest` also caches its narrative under `cache_key = (user_id, categories)` — that MUST gain `account_id` or one account's summary bleeds into another.

- [ ] **Step 1: Write failing tests**

```python
# append to tests/accounts/test_active_account.py
from unittest.mock import patch
import core.email_assistant as ea


def test_get_inbox_digest_threads_account_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(ea, "_creds_from_session",
                        lambda session, account_id=None: captured.setdefault("aid", account_id) or "CREDS")
    monkeypatch.setattr(ea.gt, "gmail_unread_count", lambda creds, categories=(): 0)
    monkeypatch.setattr(ea.gt, "gmail_list_messages",
                        lambda creds, max_results=10, categories=(): [])
    ea.get_inbox_digest(_S(), account_id="google-stitch")
    assert captured["aid"] == "google-stitch"


def test_mark_read_threads_account_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(ea, "_creds_from_session",
                        lambda session, account_id=None: captured.setdefault("aid", account_id) or "CREDS")
    monkeypatch.setattr(ea.gt, "gmail_mark_read", lambda creds, mid: {"ok": True})
    ea.mark_read(_S(), "m1", account_id="google-stitch")
    assert captured["aid"] == "google-stitch"


def test_list_drafts_threads_account_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(ea, "_creds_from_session",
                        lambda session, account_id=None: captured.setdefault("aid", account_id) or "CREDS")
    monkeypatch.setattr(ea.gt, "gmail_list_drafts", lambda creds, max_results=20: [])
    ea.list_drafts(_S(), account_id="google-stitch")
    assert captured["aid"] == "google-stitch"


def test_inbox_digest_cache_is_per_account(monkeypatch):
    # Two accounts, same user, same categories -> DISTINCT cache entries.
    calls = {"n": 0}
    def fake_llm(msgs):
        calls["n"] += 1
        return f"summary {calls['n']}"
    monkeypatch.setattr(ea, "_creds_from_session", lambda session, account_id=None: "CREDS")
    monkeypatch.setattr(ea.gt, "gmail_unread_count", lambda creds, categories=(): 1)
    monkeypatch.setattr(ea.gt, "gmail_list_messages",
                        lambda creds, max_results=10, categories=(): [{"sender": "a", "subject": "s", "snippet": "x", "id": "1"}])
    monkeypatch.setattr(ea, "_llm", fake_llm)
    s = _S(); s.user_id = "u"; s.system_prompt_base = ""; s.clean_reply = lambda x: x
    r1 = ea.get_inbox_digest(s, account_id="acct-A")
    r2 = ea.get_inbox_digest(s, account_id="acct-B")
    # different accounts must NOT share the cached narrative
    assert calls["n"] == 2
    assert r1["narrative"] != r2["narrative"]
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/accounts/test_active_account.py -v -k threads or cache`
Expected: FAIL (functions don't accept account_id; cache shared).

- [ ] **Step 3: Implement** (in `core/email_assistant.py`)

`get_inbox_digest` — signature + creds + cache key:
```python
def get_inbox_digest(session, max_messages: int = 10, fresh: bool = False,
                     categories: tuple = ("primary",), account_id=None) -> dict:
```
- change `creds = _creds_from_session(session)` → `creds = _creds_from_session(session, account_id)`
- change the cache key line
  `cache_key = (user_id, tuple(categories) if categories else ())`
  to
  `cache_key = (user_id, account_id, tuple(categories) if categories else ())`

`mark_read`:
```python
def mark_read(session, message_id: str, account_id=None) -> dict:
    ...
    creds = _creds_from_session(session, account_id)
```

`list_drafts`:
```python
def list_drafts(session, max_results: int = 20, account_id=None) -> list[dict]:
    creds = _creds_from_session(session, account_id)
    ...
```

`get_draft`:
```python
def get_draft(session, draft_id: str, account_id=None) -> dict | None:
    creds = _creds_from_session(session, account_id)
    ...
```

`discard_draft`:
```python
def discard_draft(session, draft_id: str, account_id=None) -> dict:
    creds = _creds_from_session(session, account_id)
    ...
```

(All keep their existing bodies otherwise. `_creds_from_session`'s second positional/keyword arg already exists.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/accounts/test_active_account.py -v && python -m pytest -q`
Expected: PASS; full suite green (all new params optional/default None — existing callers unaffected).

- [ ] **Step 5: Commit**

```bash
git add core/email_assistant.py tests/accounts/test_active_account.py
git commit -m "mail multi-account: account_id on get_inbox_digest (per-account cache) + mark_read/list_drafts/get_draft/discard_draft"
```

---

### Task 3: Set-active endpoint + endpoint threading

**Files:**
- Modify: `server/app.py` (new `POST /api/email/active-account`; thread `active_account_id` into every `/api/email/*` endpoint)
- Test: `tests/test_multi_account_mail.py` (new)

**Context:** `_account_summary(acct)` already exists at module level in `server/app.py` (account-linking work) — reuse it. The existing email endpoints are at `server/app.py:1175-1349`. The auth-in-test pattern is `app.dependency_overrides[require_user] = lambda: "switch"` with `session_manager` patched to a MagicMock (see `tests/test_account_linking_endpoints.py`).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_multi_account_mail.py
# Reuse the TestClient + dependency_overrides[require_user] + session_manager
# MagicMock pattern from tests/test_account_linking_endpoints.py.
from unittest.mock import MagicMock


def test_set_active_account_valid(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock()
    sess.accounts.get.return_value = {"id": "google-stitch", "label": "SwitchStitch",
                                      "email": "s@x.com", "status": "ok", "is_default": False}
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    resp = client.post("/api/email/active-account", json={"account_id": "google-stitch"})
    assert resp.status_code == 200
    assert sess.current_mail_account == "google-stitch"
    assert resp.json()["active"]["id"] == "google-stitch"


def test_set_active_account_unknown_400(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock(); sess.accounts.get.return_value = None
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    resp = client.post("/api/email/active-account", json={"account_id": "nope"})
    assert resp.status_code == 400


def test_set_active_account_null_clears_to_default(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock()
    sess.accounts.default.return_value = {"id": "google-personal", "label": "Personal",
                                          "email": "p@x.com", "status": "ok", "is_default": True}
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    resp = client.post("/api/email/active-account", json={"account_id": None})
    assert resp.status_code == 200
    assert sess.current_mail_account is None


def test_inbox_digest_passes_active_account(client, monkeypatch):
    import server.app as app_mod
    sess = MagicMock()
    monkeypatch.setattr(app_mod.session_manager, "get_or_create", lambda u: sess)
    monkeypatch.setattr("core.email_assistant.active_account_id", lambda s: "google-stitch")
    captured = {}
    monkeypatch.setattr("core.email_assistant.get_inbox_digest",
                        lambda session, **kw: captured.update(kw) or {"messages": []})
    client.get("/api/email/inbox-digest")
    assert captured.get("account_id") == "google-stitch"
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_multi_account_mail.py -v`
Expected: FAIL — 404 on `/api/email/active-account`; inbox-digest doesn't pass account_id.

- [ ] **Step 3: Implement** (in `server/app.py`)

Add the set-active endpoint near the other `/api/email/*` routes:
```python
@app.post("/api/email/active-account")
async def email_set_active_account(body: dict, user_id: str = Depends(require_user)):
    """Set which linked account the Mail panel (and chat email actions) act on.

    Body: {account_id: str | null}. null/empty -> default account.
    """
    session = session_manager.get_or_create(user_id)
    account_id = (body.get("account_id") or "").strip() or None
    if account_id is not None and session.accounts.get(account_id) is None:
        return JSONResponse({"error": "Unknown account"}, status_code=400)
    session.current_mail_account = account_id
    eff = session.accounts.get(account_id) if account_id else session.accounts.default()
    return {"active": _account_summary(eff) if eff else None}
```

Then thread `active_account_id(session)` into each email endpoint. For each, add
`from core.email_assistant import active_account_id` alongside the existing import
and pass `account_id=active_account_id(session)`:

- `email_inbox_digest`: `return get_inbox_digest(session, max_messages=..., fresh=..., categories=cats, account_id=active_account_id(session))`
- `email_list_drafts`: `list_drafts(session, max_results=max_results, account_id=active_account_id(session))`
- `email_get_draft`: `get_draft(session, draft_id, account_id=active_account_id(session))`
- `email_draft_reply`: `draft_reply(session, message_id, intent=intent, account_id=active_account_id(session))`
- `email_draft_new`: `draft_new(session, to=to, intent=intent, subject_hint=subject_hint, cc=cc, bcc=bcc, account_id=active_account_id(session))`
- `email_send_draft`: `send_draft(session, draft_id, account_id=active_account_id(session))`
- `email_discard_draft`: `discard_draft(session, draft_id, account_id=active_account_id(session))`
- `email_mark_read`: `mark_read(session, message_id, account_id=active_account_id(session))`
- `email_get_message`: change `creds = _creds_from_session(session)` →
  `creds = _creds_from_session(session, active_account_id(session))`
- `email_update_draft` (PATCH): change the inline `creds = _creds_from_session(session)` →
  `creds = _creds_from_session(session, active_account_id(session))`

(Import `active_account_id` once at the top of `server/app.py` with the other
`core.email_assistant` imports if that's the file's style, OR locally in each
handler matching the existing local-import pattern — match what the file does.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_multi_account_mail.py -v && python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add server/app.py tests/test_multi_account_mail.py
git commit -m "mail multi-account: /api/email/active-account + thread active account through all email endpoints"
```

---

### Task 4: email_ops chat handlers follow the active account

**Files:**
- Modify: `core/protocols/email_ops.py` (`_recent_inbox`, `_resolve_account`, `_do_mark_read`, `_do_archive`)
- Test: append to `tests/test_multi_account_mail.py` (or the email_ops test file)

**Context (current code):**
- `_recent_inbox()` calls `ea._creds_from_session(self._session)` (default) then `gt.gmail_list_messages(creds, ...)`.
- `_resolve_account(action)` returns `(acct, note)`; the no-hint fallback is `accounts.default()`.
- `_do_mark_read` / `_do_archive` call `gt.gmail_mark_read/gmail_archive(ea._creds_from_session(self._session), message_id)` (default).

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_multi_account_mail.py
import core.protocols.email_ops as email_ops


def _proto_with_active(monkeypatch, active_id, captured):
    # Build an EmailOpsProtocol whose session reports a given active account,
    # capturing which account_id _creds_from_session is asked for.
    from core.protocols.email_ops import EmailOpsProtocol
    p = EmailOpsProtocol()
    sess = MagicMock()
    p._session = sess
    monkeypatch.setattr("core.email_assistant.active_account_id", lambda s: active_id)
    monkeypatch.setattr(email_ops.ea, "_creds_from_session",
                        lambda session, account_id=None: captured.setdefault("aid", account_id) or "CREDS")
    return p


def test_recent_inbox_uses_active_account(monkeypatch):
    captured = {}
    p = _proto_with_active(monkeypatch, "google-stitch", captured)
    monkeypatch.setattr(email_ops.gt, "gmail_list_messages",
                        lambda creds, max_results=15, categories=None: [])
    p._recent_inbox()
    assert captured["aid"] == "google-stitch"


def test_mark_read_uses_active_account(monkeypatch):
    captured = {}
    p = _proto_with_active(monkeypatch, "google-stitch", captured)
    p._id_map = {1: "m1"}
    monkeypatch.setattr(email_ops.gt, "gmail_mark_read", lambda creds, mid: {"ok": True})
    p._do_mark_read({"ref": "1"}, "mark 1 read")
    assert captured["aid"] == "google-stitch"


def test_resolve_account_no_hint_falls_back_to_active(monkeypatch):
    from core.protocols.email_ops import EmailOpsProtocol
    p = EmailOpsProtocol(); sess = MagicMock(); p._session = sess
    sess.accounts.get.return_value = {"id": "google-stitch", "label": "SwitchStitch"}
    monkeypatch.setattr("core.email_assistant.active_account_id", lambda s: "google-stitch")
    acct, note = p._resolve_account({})   # no ACCOUNT= hint
    assert acct["id"] == "google-stitch"
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_multi_account_mail.py -v -k active`
Expected: FAIL (handlers use default).

- [ ] **Step 3: Implement** (in `core/protocols/email_ops.py`)

At the top, ensure `active_account_id` is importable: it lives in `core.email_assistant` (already imported as `ea`), so call `ea.active_account_id(self._session)`.

`_recent_inbox()` — change the creds line:
```python
        creds = ea._creds_from_session(self._session,
                                       ea.active_account_id(self._session))
```

`_resolve_account(action)` — change the no-hint fallback. Current fallback returns `accounts.default()`; change it to the active account:
```python
        # no explicit ACCOUNT= hint -> the account the Mail panel is viewing
        active_id = ea.active_account_id(self._session)
        acct = accounts.get(active_id) if active_id else accounts.default()
        return (acct, "") if acct else (None, "")
```
(Keep the explicit-hint branch — `resolve(hint)` — unchanged; it still wins. Preserve the existing `(acct, note)` tuple return shape and the "couldn't match" note behavior for a bad explicit hint.)

`_do_mark_read` / `_do_archive` — thread the active account's creds:
```python
        creds = ea._creds_from_session(self._session,
                                       ea.active_account_id(self._session))
        res = gt.gmail_mark_read(creds, message_id)      # and gmail_archive in _do_archive
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_multi_account_mail.py -v && python -m pytest -q`
Expected: PASS; full suite green. (Existing email_ops tests: `_resolve_account` no-hint now returns the active account — which, with no `current_mail_account` set and a single account, is still the default, so existing single-account tests are unaffected. If any test asserted the default via a multi-account fixture with no active set, confirm it still holds — default is the active fallback.)

- [ ] **Step 5: Commit**

```bash
git add core/protocols/email_ops.py tests/test_multi_account_mail.py
git commit -m "mail multi-account: chat handlers (_recent_inbox/_resolve_account/mark_read/archive) follow the active account"
```

---

### Task 5: UI — Mail-panel account switcher

**Files:**
- Modify: `ui/templates/index.html`
- Test: none (inline JS, no harness — verified live)

**Context:** The Mail panel has a tab system (`_mailSwitchTab`: inbox/compose/drafts/accounts). Inbox loads via `loadInboxDigest(fresh)` → `GET /api/email/inbox-digest`. Message open → `GET /api/email/messages/{id}`, mark-read → `POST /api/email/mark-read/{id}`, reply → `POST /api/email/draft-reply`. Drafts list via `GET /api/email/drafts`. `authFetch(API + ...)` is the fetch helper; `_mailGetSetting/_mailSetSetting` read/write `localStorage.aegis_mail_settings`. The linked-accounts list endpoint is `GET /api/google/accounts`.

- [ ] **Step 1: Add the switcher markup + populate it**

Add a `<select id="mailAccountSwitcher">` at the top of the Mail panel body (above the tab content, visible on Inbox/Drafts/Compose). Populate it from `GET /api/google/accounts`:
```javascript
async function populateMailAccountSwitcher() {
    try {
        var res = await authFetch(API + '/google/accounts');
        if (!res.ok) return;
        var accounts = (await res.json()).accounts || [];
        var sel = document.getElementById('mailAccountSwitcher');
        if (!sel) return;
        var current = _mailGetSetting('selected_account', '');
        sel.innerHTML = accounts.map(function (a) {
            var warn = a.status === 'error' ? ' ⚠ needs reconnect' : '';
            var selAttr = (a.id === current) ? ' selected' : '';
            return '<option value="' + a.id + '"' + selAttr + '>' + a.label + warn + '</option>';
        }).join('');
    } catch (e) { /* non-fatal */ }
}
```

- [ ] **Step 2: Switch handler + session sync + reload**

```javascript
async function onMailAccountChange() {
    var sel = document.getElementById('mailAccountSwitcher');
    var accountId = sel ? sel.value : '';
    try {
        await authFetch(API + '/email/active-account', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account_id: accountId || null }),
        });
    } catch (e) { /* non-fatal; still reload */ }
    _mailSetSetting('selected_account', accountId);
    // reload whatever tab is active
    if (typeof loadInboxDigest === 'function') loadInboxDigest(true);
    if (typeof loadDrafts === 'function' && _mailCurrentTab === 'drafts') loadDrafts();
}
```
Wire `onchange="onMailAccountChange()"` on the select. (Use the file's actual current-tab variable name if `_mailCurrentTab` differs — check `_mailSwitchTab`.)

- [ ] **Step 3: Sync session on panel open**

Where the Mail panel open handler already runs (the one that calls `loadInboxDigest`), FIRST call `populateMailAccountSwitcher()`, then POST the stored selection to `/api/email/active-account` so the session matches the UI BEFORE the inbox loads:
```javascript
    await populateMailAccountSwitcher();
    var storedAcct = _mailGetSetting('selected_account', '');
    await authFetch(API + '/email/active-account', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_id: storedAcct || null }),
    });
    // ...then the existing loadInboxDigest() call
```

- [ ] **Step 4: Reconnect state for an errored account**

When the inbox digest response has `error === 'not_authorized'` (creds_for returned None for the active account), render a clear reconnect prompt in the inbox area instead of the empty/error narrative:
```javascript
    // inside the inbox render, when digest.error === 'not_authorized':
    //   show: "<label> needs reconnecting" + a button that calls submitAddAccount-style
    //   re-link for the CURRENT account (reuse the existing "Add account" flow;
    //   linking by the same email refreshes tokens in place via upsert dedupe).
```
Reuse the account-linking `submitAddAccount` flow for the reconnect button (a re-link of the same email updates tokens in place). Keep it minimal — a labeled button that opens the add-account form pre-filled with the account's label, or simply reveals the ACCOUNTS tab.

- [ ] **Step 5: Compose default**

Where the Compose tab builds its send/draft request, ensure it relies on the session's active account (the backend now uses `active_account_id`), so no explicit account field is needed in the compose request — it composes from the active account by default. Confirm the compose submit path doesn't hardcode a default; if it passes an account explicitly, default it to the switcher's value.

- [ ] **Step 6: Verify (manual static self-check — no JS harness)**
- Endpoints called match the backend: `GET /api/google/accounts`, `POST /api/email/active-account {account_id}`, and the existing inbox/message/mark-read/drafts routes.
- Element ids referenced (`mailAccountSwitcher`) exist in the markup.
- The panel-open handler populates + syncs BEFORE loading the inbox.
- No broken tags; additions are inside the existing script block.

- [ ] **Step 7: Commit**

```bash
git add ui/templates/index.html
git commit -m "mail multi-account: Mail-panel account switcher (sync session, reload, reconnect state)"
```

---

### Task 6: Holistic review + live verify (supervised) + merge

- [ ] **Step 1: Holistic whole-feature review** of `git diff main...feature/multi-account-mail-inbox`, focus:
  - Consistency: every email endpoint AND the chat handlers resolve through `active_account_id(session)` — no default-only reader left (grep `_creds_from_session(session)` with no 2nd arg in email paths).
  - Cache correctness: `get_inbox_digest` cache key includes account_id (no cross-account narrative bleed).
  - Stale-selection safety: `active_account_id` falls back to default when the selected account was deleted.
  - Chat↔UI sync: `#N` from `_recent_inbox` and mark/archive use the same active account the UI set.
  - Privacy/no cloud egress regressions; no token leak in the active-account response (`_account_summary` is token-free).
  - Drafts/send: sending a draft uses the account that owns it (active account = the one whose Drafts tab is shown).
  Fix findings; re-run full suite.

- [ ] **Step 2: Live verify (Switch, supervised)** — restart Aegis; open Mail; the switcher shows Personal + SwitchStitch; switch to SwitchStitch → its inbox loads; open/mark-read a message; in chat "mark #1 read" hits SwitchStitch; switch back to Personal → its inbox. An errored account shows the reconnect prompt.

- [ ] **Step 3: Merge + push** — merge `feature/multi-account-mail-inbox` → main, push, delete branch. Update Obsidian log + MEMORY.md.

---

## Self-review notes (plan-time)

- **Spec coverage:** session state + resolver (T1), email_assistant threading incl. per-account cache (T2), set-active endpoint + all endpoints (T3), chat handlers (T4), UI switcher + reconnect (T5), review + live + merge (T6). All spec sections mapped.
- **Beyond spec (justified):** `discard_draft` + the PATCH endpoint's inline creds were added to the threading list (spec said "drafts PATCH/DELETE" — these are the concrete functions); `get_inbox_digest` cache key gets account_id (spec implied per-account correctness; made explicit).
- **Type consistency:** `active_account_id(session)` signature identical across T1 definition and all T3/T4 callers. `account_id=None` param added consistently (mirrors the existing `_creds_from_session(session, account_id)` and the draft_* functions). `_account_summary` reused (not redefined). `current_mail_account` attribute name identical in session (T1), endpoint (T3), resolver (T1).
- **Harness dependency:** T3/T4 endpoint tests reuse the `dependency_overrides[require_user]` + `session_manager` MagicMock pattern from `tests/test_account_linking_endpoints.py` — implementer reads that file for the authed-client fixture.
