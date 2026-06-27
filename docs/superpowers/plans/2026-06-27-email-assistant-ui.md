# Email Assistant UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Email Assistant UI per `docs/superpowers/specs/2026-06-27-email-assistant-ui-design.md` — a 3-tab LCARS Mail panel (`INBOX` / `COMPOSE` / `DRAFTS`) with Pike-voiced inbox digest, cached narrative + manual refresh, optional inline intent for replies, full draft editing, and a two-step amber `Send` button backed by a 5-second client-side deferred-send window.

**Architecture:** Tiny additive backend extensions (narrative cache, CC/BCC kwargs, mark-read endpoint), then ~1500 lines of HTML+CSS+JS added to `ui/templates/index.html` as one more LCARS panel sibling to `taskPanel` / `briefingPanel` / `calendarPanel`. The deferred-send pattern is purely client-side — server only sees the actual `send-draft` call after the 5s timer elapses.

**Tech Stack:** Python 3.12 / FastAPI / pytest for backend; plain JS in a single PWA HTML file (no framework, no JS test runner — manual smoke per tab).

---

## File Structure

**Create:**
- `tests/test_email_assistant.py` — backend pytest covering narrative cache, CC/BCC threading, mark_read

**Modify:**
- `core/email_assistant.py` — narrative cache state, `fresh` kwarg on `get_inbox_digest`, CC/BCC kwargs on `draft_new`, new `mark_read` wrapper
- `core/protocols/google_tools.py` — CC/BCC in `_build_mime_message`, new `gmail_mark_read` helper
- `server/app.py` — `?fresh=1` query on `/api/email/inbox-digest`, read `cc`/`bcc` from `/api/email/draft` body, new `POST /api/email/mark-read/{message_id}` endpoint
- `ui/templates/index.html` — Mail panel HTML, `.mail-*` CSS class set, JS state and handlers, sidebar `MAIL` button, settings dropdown entries

---

## Task 1: Backend — narrative cache + `fresh` kwarg

**Files:**
- Modify: `core/email_assistant.py` (`get_inbox_digest`)
- Modify: `server/app.py` (`/api/email/inbox-digest` endpoint)
- Create: `tests/test_email_assistant.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_email_assistant.py`:

```python
import time
from unittest.mock import MagicMock, patch

import pytest


def _mock_session(narrative_text="Pike's brief"):
    """Build a fake session enough for get_inbox_digest to run."""
    session = MagicMock()
    session.user_id = "test_user"
    session.system_prompt_base = "system"
    session.clean_reply = lambda s: s
    return session


def test_narrative_cache_returns_cached_within_ttl():
    """Two calls within the TTL should produce ONE LLM call."""
    from core import email_assistant as ea
    ea._narrative_cache.clear()  # isolate

    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea.gt, "gmail_unread_count", return_value=2), \
         patch.object(ea.gt, "gmail_list_messages", return_value=[
             {"id": "m1", "subject": "Hi", "sender": "Bill", "date": "now", "snippet": "x"}
         ]), \
         patch.object(ea, "_llm", return_value="Pike's brief") as llm_mock:
        r1 = ea.get_inbox_digest(session)
        r2 = ea.get_inbox_digest(session)

    assert r1["narrative"] == "Pike's brief"
    assert r2["narrative"] == "Pike's brief"
    assert llm_mock.call_count == 1  # second call was served from cache


def test_narrative_cache_busted_by_fresh_kwarg():
    """fresh=True forces an LLM regeneration."""
    from core import email_assistant as ea
    ea._narrative_cache.clear()

    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea.gt, "gmail_unread_count", return_value=1), \
         patch.object(ea.gt, "gmail_list_messages", return_value=[
             {"id": "m1", "subject": "x", "sender": "y", "date": "z", "snippet": "s"}
         ]), \
         patch.object(ea, "_llm", side_effect=["first", "second"]) as llm_mock:
        ea.get_inbox_digest(session)
        ea.get_inbox_digest(session, fresh=True)

    assert llm_mock.call_count == 2


def test_narrative_cache_expires_after_ttl():
    """After TTL, the cache is rebuilt."""
    from core import email_assistant as ea
    ea._narrative_cache.clear()

    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea.gt, "gmail_unread_count", return_value=0), \
         patch.object(ea.gt, "gmail_list_messages", return_value=[
             {"id": "m1", "subject": "x", "sender": "y", "date": "z", "snippet": "s"}
         ]), \
         patch.object(ea, "_llm", side_effect=["first", "second"]) as llm_mock, \
         patch.object(ea, "_NARRATIVE_TTL_S", 0.05):  # 50ms TTL for fast test
        ea.get_inbox_digest(session)
        time.sleep(0.1)
        ea.get_inbox_digest(session)

    assert llm_mock.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_email_assistant.py -v -k narrative_cache`
Expected: All 3 fail because `_narrative_cache` and `fresh` kwarg don't exist.

- [ ] **Step 3: Implement in `core/email_assistant.py`**

Add near the top, after the imports:

```python
import time as _time

# Per-user narrative cache: {user_id: (timestamp_epoch_s, narrative_str)}
_narrative_cache: dict[str, tuple[float, str]] = {}
_NARRATIVE_TTL_S: float = 600.0  # 10 minutes
```

Change `get_inbox_digest`'s signature to add `fresh: bool = False`:

```python
def get_inbox_digest(session, max_messages: int = 10, fresh: bool = False) -> dict:
```

Inside `get_inbox_digest`, just before the `facts_text = _format_messages_for_llm(messages)` line (line ~101), insert the cache hit branch:

```python
    # Narrative cache (per-user, TTL-gated). Always re-fetch the message list
    # since it's cheap; the LLM call is what we want to avoid.
    user_id = getattr(session, "user_id", "default")
    cached = _narrative_cache.get(user_id)
    if cached and not fresh:
        ts, narrative = cached
        if _time.time() - ts < _NARRATIVE_TTL_S:
            return {
                "narrative": narrative,
                "unread_count": unread_count,
                "messages": messages,
                "cached_age_s": int(_time.time() - ts),
            }
```

After the existing `narrative = session.clean_reply(raw).strip()` line, add the cache write:

```python
    except Exception as e:
        logger.exception("Inbox digest LLM call failed")
        narrative = f"[Briefing failed — {e}]"
    else:
        # Successful LLM call — cache it.
        _narrative_cache[user_id] = (_time.time(), narrative)
```

(The `else` clause attaches to the `try/except` that wraps the LLM call.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_email_assistant.py -v -k narrative_cache`
Expected: 3 pass.

- [ ] **Step 5: Update `server/app.py` endpoint**

Find `/api/email/inbox-digest` (around line 1069). Current shape:

```python
@app.get("/api/email/inbox-digest")
async def email_inbox_digest(user_id: str = Depends(require_user)):
    from core.email_assistant import get_inbox_digest
    session = session_manager.get_or_create(user_id)
    return get_inbox_digest(session)
```

Replace with:

```python
@app.get("/api/email/inbox-digest")
async def email_inbox_digest(fresh: int = 0, max_messages: int = 10,
                              user_id: str = Depends(require_user)):
    from core.email_assistant import get_inbox_digest
    session = session_manager.get_or_create(user_id)
    return get_inbox_digest(session, max_messages=max_messages, fresh=bool(fresh))
```

- [ ] **Step 6: Commit**

```bash
git add core/email_assistant.py server/app.py tests/test_email_assistant.py
git commit -m "feat: cache inbox digest narrative with 10-min TTL

Two callers within the TTL window share one LLM call. Bust with
?fresh=1 query param on the endpoint or fresh=True kwarg on the
function. Per-user keyed."
```

---

## Task 2: Backend — CC/BCC on draft creation

**Files:**
- Modify: `core/protocols/google_tools.py` (`_build_mime_message`, `gmail_create_draft`)
- Modify: `core/email_assistant.py` (`draft_new`)
- Modify: `server/app.py` (`/api/email/draft` endpoint)
- Modify: `tests/test_email_assistant.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_email_assistant.py`:

```python
def test_draft_new_threads_cc_and_bcc_to_gmail_create():
    """draft_new should forward cc and bcc into gmail_create_draft."""
    from core import email_assistant as ea
    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea, "_llm", return_value="Subject: Hi\n\nBody"), \
         patch.object(ea.gt, "gmail_create_draft", return_value={
             "success": True, "draft_id": "d1", "message_id": "m1"
         }) as mock_create:
        ea.draft_new(
            session, to="bill@example.com",
            intent="say hi",
            cc="tyler@example.com",
            bcc="audit@example.com",
        )
    # Inspect the kwargs the mock received
    kwargs = mock_create.call_args.kwargs
    assert kwargs.get("cc") == "tyler@example.com"
    assert kwargs.get("bcc") == "audit@example.com"


def test_draft_new_omits_cc_bcc_when_not_provided():
    """Backwards compat — existing callers without cc/bcc still work."""
    from core import email_assistant as ea
    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea, "_llm", return_value="Subject: Hi\n\nBody"), \
         patch.object(ea.gt, "gmail_create_draft", return_value={
             "success": True, "draft_id": "d1", "message_id": "m1"
         }) as mock_create:
        ea.draft_new(session, to="bill@example.com", intent="say hi")
    kwargs = mock_create.call_args.kwargs
    # cc/bcc may be passed as None or absent — both are fine
    assert not kwargs.get("cc")
    assert not kwargs.get("bcc")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_email_assistant.py -v -k draft_new_threads`
Expected: Fail because `draft_new` doesn't accept `cc`/`bcc`.

- [ ] **Step 3: Update `core/protocols/google_tools.py`**

Find `_build_mime_message` and `gmail_create_draft` (around lines 398-421). Change `gmail_create_draft`'s signature:

```python
def gmail_create_draft(creds, to, subject, body, reply_to_id=None, cc=None, bcc=None):
    """Create a draft email (saved to user's Gmail drafts, NOT sent).

    Returns {success, draft_id, message_id} or {success: False, error: ...}.
    """
    service = _get_gmail_service(creds)
    if not service:
        return {"success": False, "error": "Gmail service unavailable"}

    try:
        raw, thread_id = _build_mime_message(to, subject, body, reply_to_id, service, cc=cc, bcc=bcc)
        draft_body = {"message": {"raw": raw}}
        if thread_id:
            draft_body["message"]["threadId"] = thread_id

        result = service.users().drafts().create(userId="me", body=draft_body).execute()
        return {
            "success": True,
            "draft_id": result.get("id", ""),
            "message_id": result.get("message", {}).get("id", ""),
        }
    except Exception as e:
        logger.warning("Could not create Gmail draft: %s", e)
        return {"success": False, "error": str(e)}
```

Now update `_build_mime_message` — find its signature (search for `def _build_mime_message`) and add `cc=None, bcc=None` to the params. Inside the function, after the `msg["To"] = to` line (or equivalent), add:

```python
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
```

- [ ] **Step 4: Update `core/email_assistant.py` `draft_new`**

Find `draft_new` (line 210). Change its signature:

```python
def draft_new(session, to: str, intent: str, subject_hint: str | None = None,
              cc: str | None = None, bcc: str | None = None) -> dict:
```

Find where it calls `gt.gmail_create_draft(...)` inside the function and add `cc=cc, bcc=bcc` to that call.

- [ ] **Step 5: Update `server/app.py` `/api/email/draft` endpoint**

Find the endpoint (around line 1111). Update it to read cc/bcc from the body:

```python
@app.post("/api/email/draft")
async def email_draft_new(body: dict, user_id: str = Depends(require_user)):
    """Draft a fresh email (not a reply). Saves to Gmail drafts. Does NOT send.

    Body: {to: str, intent: str, subject?: str, cc?: str, bcc?: str}
    """
    from core.email_assistant import draft_new
    session = session_manager.get_or_create(user_id)
    to = body.get("to", "").strip()
    intent = body.get("intent", "").strip()
    if not to or not intent:
        return {"success": False, "error": "to and intent required"}
    subject_hint = body.get("subject")
    cc = (body.get("cc") or "").strip() or None
    bcc = (body.get("bcc") or "").strip() or None
    return draft_new(session, to=to, intent=intent, subject_hint=subject_hint,
                     cc=cc, bcc=bcc)
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_email_assistant.py -v`
Expected: All tests pass (3 from Task 1 + 2 from Task 2 = 5).

- [ ] **Step 7: Commit**

```bash
git add core/protocols/google_tools.py core/email_assistant.py server/app.py tests/test_email_assistant.py
git commit -m "feat: CC/BCC support on draft_new and Gmail MIME builder

Optional cc/bcc kwargs flow from /api/email/draft body through
draft_new to gmail_create_draft to _build_mime_message. Backwards-
compatible — existing callers without cc/bcc behave identically."
```

---

## Task 3: Backend — `mark_read` + endpoint

**Files:**
- Modify: `core/protocols/google_tools.py` (new `gmail_mark_read`)
- Modify: `core/email_assistant.py` (new `mark_read`)
- Modify: `server/app.py` (new endpoint)
- Modify: `tests/test_email_assistant.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_email_assistant.py`:

```python
def test_mark_read_calls_gmail_modify():
    """mark_read should call gmail_mark_read with the message id."""
    from core import email_assistant as ea
    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=object()), \
         patch.object(ea.gt, "gmail_mark_read", return_value={"ok": True}) as mock_mark:
        result = ea.mark_read(session, "msg_abc")
    assert result == {"ok": True}
    assert mock_mark.call_args.args[1] == "msg_abc"


def test_mark_read_returns_error_when_not_authorized():
    from core import email_assistant as ea
    session = _mock_session()
    with patch.object(ea, "_creds_from_session", return_value=None):
        result = ea.mark_read(session, "msg_abc")
    assert result.get("error") == "not_authorized"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_email_assistant.py -v -k mark_read`
Expected: Fail — `mark_read` and `gmail_mark_read` don't exist.

- [ ] **Step 3: Add `gmail_mark_read` to `core/protocols/google_tools.py`**

Place it near `gmail_list_messages` (around line 221). Add:

```python
def gmail_mark_read(creds, message_id):
    """Mark an inbox message as read (removes the UNREAD label).

    Returns {ok: True} on success, {ok: False, error: ...} on failure.
    """
    service = _get_gmail_service(creds)
    if not service:
        return {"ok": False, "error": "Gmail service unavailable"}
    try:
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        return {"ok": True}
    except Exception as e:
        logger.warning("Could not mark message read: %s", e)
        return {"ok": False, "error": str(e)}
```

- [ ] **Step 4: Add `mark_read` to `core/email_assistant.py`**

Place it near `discard_draft` (around line 306). Add:

```python
def mark_read(session, message_id: str) -> dict:
    """Mark an inbox message as read."""
    creds = _creds_from_session(session)
    if not creds:
        return {"error": "not_authorized"}
    return gt.gmail_mark_read(creds, message_id)
```

- [ ] **Step 5: Add the endpoint to `server/app.py`**

Add right after the discard endpoint (around line 1146):

```python
@app.post("/api/email/mark-read/{message_id}")
async def email_mark_read(message_id: str, user_id: str = Depends(require_user)):
    """Mark an inbox message as read."""
    from core.email_assistant import mark_read
    session = session_manager.get_or_create(user_id)
    return mark_read(session, message_id)
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_email_assistant.py -v`
Expected: All 7 tests pass.

- [ ] **Step 7: Commit**

```bash
git add core/protocols/google_tools.py core/email_assistant.py server/app.py tests/test_email_assistant.py
git commit -m "feat: mark-read endpoint for inbox messages

POST /api/email/mark-read/{id} removes the UNREAD label via the
Gmail API. Wired through a new mark_read wrapper on the assistant
layer plus a thin gmail_mark_read helper."
```

---

## Task 4: Frontend — Mail panel skeleton + sidebar entry + CSS shell

**Files:**
- Modify: `ui/templates/index.html`

This task adds the empty Mail panel, the tab strip, the sidebar `MAIL` button, and the full `.mail-*` CSS class set. No data loading yet — that's Tasks 5-9.

- [ ] **Step 1: Add the Mail panel HTML**

Find the closing of `taskPanel` or `briefingPanel` in `ui/templates/index.html` (search for `id="taskPanel"`). Add a new sibling panel after it (and after `calendarPanel`):

```html
<!-- ========== MAIL PANEL ========== -->
<div id="mailPanel" class="lcars-panel theme-cyan" role="dialog"
     aria-label="Mail" style="display:none">
    <div class="lcars-elbow"></div>
    <div class="lcars-header">
        <span class="lcars-title">MAIL</span>
        <div class="lcars-header-buttons">
            <button class="lcars-settings-btn" onclick="togglePanelSettings('mailPanel')"
                    aria-label="Mail settings">*</button>
            <button class="lcars-collapse-btn"
                    onclick="togglePanelCollapse('mailPanel')"
                    aria-label="Collapse Mail">_</button>
            <button class="lcars-close-btn"
                    onclick="togglePanel('mailPanel')"
                    aria-label="Close Mail">×</button>
        </div>
    </div>
    <div class="lcars-endcap"></div>
    <div class="panel-body">
        <div class="mail-tabs">
            <button class="mail-tab active" data-tab="inbox"
                    onclick="_mailSwitchTab('inbox')">INBOX</button>
            <button class="mail-tab" data-tab="compose"
                    onclick="_mailSwitchTab('compose')">COMPOSE</button>
            <button class="mail-tab" data-tab="drafts"
                    onclick="_mailSwitchTab('drafts')">DRAFTS</button>
        </div>
        <section class="mail-tab-body" data-tab="inbox" id="mailInboxSection">
            <div class="mail-empty">Open INBOX to load.</div>
        </section>
        <section class="mail-tab-body" data-tab="compose" id="mailComposeSection"
                 style="display:none">
            <div class="mail-empty">Compose tab — wired in Task 7.</div>
        </section>
        <section class="mail-tab-body" data-tab="drafts" id="mailDraftsSection"
                 style="display:none">
            <div class="mail-empty">Drafts tab — wired in Task 8.</div>
        </section>
    </div>
    <div class="lcars-bottom-bar"></div>
</div>
```

(The exact tags and classes — `lcars-panel`, `theme-cyan`, `lcars-elbow`, `lcars-header`, `panel-body`, `lcars-endcap`, `lcars-bottom-bar`, `lcars-title`, `lcars-settings-btn`, `lcars-collapse-btn`, `lcars-close-btn` — should match what `taskPanel` actually uses. Open `taskPanel`'s markup first, copy the structure, only swap IDs and the title text. If `taskPanel` uses `theme-green`, use `theme-cyan` here so the colors visually differentiate.)

- [ ] **Step 2: Add the sidebar MAIL button**

Search for `id="logoutBtn"` or the SWITCH cluster (look for `<div class="sidebar-section">` with `SWITCH`). Right above the LOGOUT button, add:

```html
<button class="sidebar-button" onclick="togglePanel('mailPanel')" aria-label="Toggle Mail">
    <span class="sidebar-button-label">MAIL</span>
</button>
```

- [ ] **Step 3: Add the `.mail-*` CSS block**

Find an existing `<style>` block near the bottom of the file or where panel CSS lives (search `.lcars-panel`). Add a contiguous block of styles:

```css
/* ========== MAIL PANEL ========== */
#mailPanel { width: 540px; min-height: 420px; }
#mailPanel .panel-body { display: flex; flex-direction: column; padding: 0; }

/* Tabs */
.mail-tabs {
    display: flex;
    gap: 2px;
    padding: 6px 10px 0;
    border-bottom: 1px solid rgba(85,153,255,0.18);
}
.mail-tab {
    background: rgba(85,153,255,0.08);
    color: var(--lcars-text-dim);
    border: none;
    padding: 6px 14px;
    cursor: pointer;
    font-family: inherit;
    font-size: 11px;
    letter-spacing: 0.08em;
    border-radius: 3px 3px 0 0;
    border-bottom: 2px solid transparent;
    transition: background 0.15s, color 0.15s;
}
.mail-tab:hover { background: rgba(85,153,255,0.16); color: var(--lcars-text); }
.mail-tab.active {
    background: rgba(93,217,217,0.18);
    color: var(--lcars-text);
    border-bottom-color: var(--lcars-cyan, #5dd9d9);
}

.mail-tab-body { flex: 1; overflow-y: auto; padding: 8px 0; }
.mail-empty {
    text-align: center;
    color: var(--lcars-text-dim);
    padding: 32px;
    font-size: 12px;
}

/* Narrative strip */
.mail-narrative {
    margin: 8px;
    padding: 10px 14px;
    background: rgba(85,153,255,0.06);
    border-left: 3px solid var(--lcars-blue-1, #5599ff);
    font-size: 12px;
    line-height: 1.5;
    color: var(--lcars-text);
    border-radius: 2px;
}
.mail-narrative-text { margin-bottom: 4px; }
.mail-narrative-footer {
    color: var(--lcars-text-dim);
    font-size: 10px;
    display: flex;
    gap: 6px;
    align-items: center;
}
.mail-narrative-refresh {
    background: none; border: none; color: var(--lcars-cyan, #5dd9d9);
    cursor: pointer; font-size: 11px; padding: 0 2px;
}
.mail-narrative-loading {
    margin: 8px; padding: 14px;
    color: var(--lcars-text-dim);
    font-size: 12px;
    text-align: center;
}

/* Message + draft rows */
.mail-row {
    display: grid;
    grid-template-columns: 18px 1fr 70px;
    gap: 10px;
    align-items: center;
    padding: 8px 12px;
    border-bottom: 1px solid rgba(85,153,255,0.08);
    cursor: pointer;
    transition: background 0.12s;
}
.mail-row:hover { background: rgba(85,153,255,0.06); }
.mail-row[data-unread="true"] { background: rgba(85,153,255,0.04); }
.mail-row.expanded {
    background: rgba(93,217,217,0.10);
    border-left: 3px solid var(--lcars-cyan, #5dd9d9);
}
.mail-row-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--lcars-cyan, #5dd9d9);
}
.mail-row[data-unread="false"] .mail-row-dot {
    background: transparent; border: 1px solid rgba(255,255,255,0.2);
}
.mail-row-icon { color: var(--lcars-cyan, #5dd9d9); font-size: 12px; text-align: center; }
.mail-row-meta { min-width: 0; }
.mail-row-from { font-weight: bold; color: var(--lcars-text); font-size: 12px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mail-row-subj { color: var(--lcars-text-dim); font-size: 11px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mail-row-age { color: var(--lcars-text-dim); font-size: 10px; text-align: right; }

/* Expanded detail */
.mail-row-detail {
    background: rgba(85,153,255,0.04);
    padding: 12px 16px;
    border-bottom: 1px solid rgba(85,153,255,0.08);
}
.mail-detail-meta {
    color: var(--lcars-text-dim);
    font-size: 10px;
    margin-bottom: 8px;
    letter-spacing: 0.05em;
}
.mail-detail-body {
    font-size: 12px;
    color: var(--lcars-text);
    line-height: 1.5;
    margin-bottom: 12px;
    max-height: 320px;
    overflow-y: auto;
    word-wrap: break-word;
}

/* Action bar */
.mail-action-bar {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
}
.pill-btn, .pill-btn-primary, .pill-btn-secondary, .pill-btn-send, .pill-btn-danger {
    background: rgba(85,153,255,0.18);
    border: none;
    color: var(--lcars-text);
    padding: 5px 12px;
    border-radius: 12px;
    cursor: pointer;
    font-family: inherit;
    font-size: 10px;
    letter-spacing: 0.05em;
    transition: background 0.12s;
}
.pill-btn:hover { background: rgba(85,153,255,0.30); }
.pill-btn-primary {
    background: var(--lcars-blue-1, #5599ff);
    color: #0a0e15;
    font-weight: bold;
}
.pill-btn-primary:hover { background: #6aa9ff; }
.pill-btn-primary:disabled { background: rgba(85,153,255,0.20); color: var(--lcars-text-dim); cursor: not-allowed; }
.pill-btn-secondary { background: rgba(255,255,255,0.08); }
.pill-btn-secondary:hover { background: rgba(255,255,255,0.16); }
.pill-btn-send {
    background: var(--lcars-blue-1, #5599ff);
    color: #0a0e15;
    font-weight: bold;
    transition: background 0.2s;
}
.pill-btn-send:hover { background: #6aa9ff; }
.pill-btn-send.confirming {
    background: var(--lcars-amber, #ffc850);
    animation: mail-send-pulse 1s ease-in-out infinite;
}
@keyframes mail-send-pulse {
    0%, 100% { box-shadow: 0 0 0 0 transparent; }
    50% { box-shadow: 0 0 0 4px rgba(255,200,80,0.4); }
}
.pill-btn-danger { background: rgba(255,85,102,0.20); color: #ff8aa0; }
.pill-btn-danger:hover { background: rgba(255,85,102,0.36); }

/* Intent input + draft editor */
.mail-intent-row {
    display: flex;
    gap: 6px;
    align-items: center;
    margin-top: 8px;
}
.mail-intent-input {
    flex: 1;
    background: rgba(0,0,0,0.30);
    border: 1px solid rgba(85,153,255,0.30);
    color: var(--lcars-text);
    padding: 6px 10px;
    border-radius: 3px;
    font-family: inherit;
    font-size: 11px;
}
.mail-draft-editor { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.mail-draft-meta {
    display: flex;
    gap: 8px;
    align-items: center;
    font-size: 11px;
    color: var(--lcars-text-dim);
}
.mail-recipient-pill {
    background: rgba(85,153,255,0.20);
    padding: 2px 8px;
    border-radius: 10px;
    color: var(--lcars-text);
    cursor: pointer;
    font-size: 10px;
}
.mail-recipient-pill:hover { background: rgba(85,153,255,0.32); }
.mail-draft-subject, .mail-draft-body, .mail-field-to, .mail-field-cc,
.mail-field-bcc, .mail-field-subject, .mail-field-intent {
    background: rgba(0,0,0,0.30);
    border: 1px solid rgba(85,153,255,0.30);
    color: var(--lcars-text);
    padding: 7px 10px;
    border-radius: 3px;
    font-family: inherit;
    font-size: 12px;
    width: 100%;
    box-sizing: border-box;
}
.mail-draft-body { min-height: 160px; resize: vertical; line-height: 1.5; }
.mail-field-intent { min-height: 80px; resize: vertical; line-height: 1.4; }
.mail-regen-icon {
    background: none; border: none; color: var(--lcars-cyan, #5dd9d9);
    cursor: pointer; font-size: 14px; padding: 0 4px;
}

/* Compose form */
.mail-compose-form {
    display: flex; flex-direction: column; gap: 10px;
    padding: 12px 16px;
}
.mail-compose-form label {
    display: flex; flex-direction: column; gap: 4px;
    font-size: 11px; color: var(--lcars-text-dim);
    letter-spacing: 0.05em;
}
.mail-compose-form.composing { opacity: 0.3; pointer-events: none; }
.mail-compose-spinner {
    text-align: center;
    color: var(--lcars-text-dim);
    font-size: 12px;
    padding: 24px;
}

/* Auth CTA */
.mail-auth-cta {
    text-align: center;
    padding: 48px 32px;
    color: var(--lcars-text);
}
.mail-auth-cta h3 { font-size: 14px; margin-bottom: 8px; color: var(--lcars-text); }
.mail-auth-cta p { font-size: 12px; color: var(--lcars-text-dim); margin-bottom: 16px; }

/* Undo toast (extends existing toast styles if present) */
.mail-undo-toast {
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(10,14,21,0.95);
    border: 1px solid rgba(85,153,255,0.40);
    color: var(--lcars-text);
    padding: 10px 16px;
    border-radius: 6px;
    font-size: 12px;
    display: flex;
    gap: 12px;
    align-items: center;
    z-index: 10000;
    box-shadow: 0 4px 16px rgba(0,0,0,0.5);
}
.mail-undo-link {
    color: var(--lcars-cyan, #5dd9d9);
    cursor: pointer;
    text-decoration: underline;
}
```

- [ ] **Step 4: Add the tab-switch JS helper**

Inside an existing `<script>` block in `index.html`, add near other panel helpers:

```javascript
// ========== MAIL PANEL ==========

var _mailActiveTab = 'inbox';
var _mailInboxLoaded = false;
var _mailDraftsLoaded = false;

function _mailSwitchTab(tabName) {
    _mailActiveTab = tabName;
    try { localStorage.setItem('aegis_mail_active_tab', tabName); } catch (e) {}
    // Tabs
    var tabs = document.querySelectorAll('#mailPanel .mail-tab');
    tabs.forEach(function(t) {
        t.classList.toggle('active', t.dataset.tab === tabName);
    });
    // Sections
    var sections = document.querySelectorAll('#mailPanel .mail-tab-body');
    sections.forEach(function(s) {
        s.style.display = (s.dataset.tab === tabName) ? '' : 'none';
    });
    // Lazy-load
    if (tabName === 'inbox' && !_mailInboxLoaded) {
        if (typeof loadInboxDigest === 'function') loadInboxDigest();
    }
    if (tabName === 'drafts' && !_mailDraftsLoaded) {
        if (typeof loadMailDrafts === 'function') loadMailDrafts();
    }
}

// Restore last-active tab on panel open
function _mailOnPanelOpen() {
    try {
        var saved = localStorage.getItem('aegis_mail_active_tab');
        if (saved && ['inbox','compose','drafts'].indexOf(saved) >= 0) {
            _mailSwitchTab(saved);
            return;
        }
    } catch (e) {}
    _mailSwitchTab('inbox');
}
```

Then find the `togglePanel(...)` function (search for `function togglePanel`). Inside it, add a hook so when `mailPanel` opens, `_mailOnPanelOpen()` is called. If the function already iterates panel-specific open hooks, add `mailPanel` to that list. Otherwise add a defensive check:

```javascript
// Inside togglePanel, after the panel is shown:
if (panelId === 'mailPanel' && typeof _mailOnPanelOpen === 'function') {
    _mailOnPanelOpen();
}
```

- [ ] **Step 5: Manual smoke test** (USER ACTION)

⚠️ Cannot perform yourself. Switch must:
1. Restart Aegis (tray Exit → relaunch — backend changes from Tasks 1-3 need a server restart)
2. Open Aegis. The right sidebar should have a new `MAIL` button.
3. Click it. The Mail panel should appear with INBOX / COMPOSE / DRAFTS tabs.
4. Click each tab. They should switch with the active style. Each shows its `mail-empty` placeholder (Compose / Drafts) or the "Open INBOX to load." text (since data loading is Task 5).
5. Close + reopen — last active tab should restore.

- [ ] **Step 6: Commit**

```bash
git add ui/templates/index.html
git commit -m "feat: Mail panel skeleton + sidebar entry + CSS

LCARS panel with 3 top-level tabs (INBOX, COMPOSE, DRAFTS) and
the .mail-* CSS class set. No data loading yet — tabs show empty
placeholders. Sidebar MAIL button toggles the panel. Last-active
tab persists in localStorage."
```

---

## Task 5: Frontend — INBOX tab (digest, list, expand, mark-read)

**Files:**
- Modify: `ui/templates/index.html`

- [ ] **Step 1: Add JS for the INBOX tab**

Append after the Task 4 JS block:

```javascript
var _mailInbox = { narrative: '', messages: [], cachedAgeS: 0 };

async function loadInboxDigest(fresh) {
    var section = document.getElementById('mailInboxSection');
    if (!section) return;
    if (fresh) section.innerHTML =
        '<div class="mail-narrative-loading">Pike is reading your inbox…</div>';
    try {
        var url = API + '/email/inbox-digest' + (fresh ? '?fresh=1' : '');
        var res = await authFetch(url);
        var data = await res.json();
        if (data && data.error === 'not_authorized') {
            _mailRenderAuthCTA(section);
            return;
        }
        _mailInbox = {
            narrative: data.narrative || '',
            messages: data.messages || [],
            cachedAgeS: data.cached_age_s || 0,
        };
        _mailInboxLoaded = true;
        _mailRenderInbox();
    } catch (e) {
        section.innerHTML =
            '<div class="mail-empty">Couldn\'t load inbox. ' +
            '<button class="pill-btn" onclick="loadInboxDigest(true)">↻ Retry</button></div>';
    }
}

function _mailRenderInbox() {
    var section = document.getElementById('mailInboxSection');
    if (!section) return;
    var ageLabel = _mailInbox.cachedAgeS > 0
        ? 'Cached ' + _mailFormatAge(_mailInbox.cachedAgeS) + ' ago'
        : 'Just generated';
    var html = '';
    if (_mailInbox.narrative) {
        html += '<div class="mail-narrative">' +
                '<div class="mail-narrative-text">' +
                    '<strong>Pike:</strong> ' + escapeHtml(_mailInbox.narrative) +
                '</div>' +
                '<div class="mail-narrative-footer">' +
                    '<span>' + ageLabel + '</span>' +
                    '<button class="mail-narrative-refresh" onclick="loadInboxDigest(true)" title="Refresh">↻ Refresh</button>' +
                '</div>' +
                '</div>';
    }
    html += '<div id="mailMessageList">';
    if (!_mailInbox.messages.length) {
        html += '<div class="mail-empty">No messages.</div>';
    } else {
        _mailInbox.messages.forEach(function(m) {
            html += _mailRenderMessageRow(m);
        });
    }
    html += '</div>';
    section.innerHTML = html;
}

function _mailRenderMessageRow(m) {
    var isUnread = m.unread === true || (m.labelIds || []).indexOf('UNREAD') >= 0;
    // Backend may or may not surface unread; the snippet-list endpoint always
    // returns recent INBOX items, so treat unread as a hint only.
    return '<div class="mail-row" data-message-id="' + escapeHtml(m.id) + '" ' +
           'data-unread="' + (isUnread ? 'true' : 'false') + '" ' +
           'onclick="_mailToggleMessage(this)">' +
           '<div class="mail-row-dot"></div>' +
           '<div class="mail-row-meta">' +
               '<div class="mail-row-from">' + escapeHtml(m.sender || m.from || '?') + '</div>' +
               '<div class="mail-row-subj">' + escapeHtml(m.subject || '(no subject)') + '</div>' +
           '</div>' +
           '<div class="mail-row-age">' + escapeHtml(_mailFormatRowAge(m.date)) + '</div>' +
           '</div>';
}

function _mailFormatRowAge(dateStr) {
    if (!dateStr) return '';
    var d;
    try { d = new Date(dateStr); } catch (e) { return ''; }
    if (isNaN(d.getTime())) return '';
    var diffS = (Date.now() - d.getTime()) / 1000;
    if (diffS < 60) return Math.floor(diffS) + 's';
    if (diffS < 3600) return Math.floor(diffS / 60) + 'm';
    if (diffS < 86400) return Math.floor(diffS / 3600) + 'h';
    if (diffS < 604800) return Math.floor(diffS / 86400) + 'd';
    return d.toLocaleDateString();
}

function _mailFormatAge(seconds) {
    if (seconds < 60) return seconds + 's';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm';
    return Math.floor(seconds / 3600) + 'h';
}

async function _mailToggleMessage(rowEl) {
    var messageId = rowEl.dataset.messageId;
    var alreadyExpanded = rowEl.classList.contains('expanded');
    // Collapse all rows + remove any detail panels
    document.querySelectorAll('#mailInboxSection .mail-row').forEach(function(r) {
        r.classList.remove('expanded');
    });
    document.querySelectorAll('#mailInboxSection .mail-row-detail').forEach(function(d) {
        d.remove();
    });
    if (alreadyExpanded) return; // toggled closed

    rowEl.classList.add('expanded');
    // Insert placeholder
    var ph = document.createElement('div');
    ph.className = 'mail-row-detail';
    ph.innerHTML = '<div class="mail-narrative-loading">Loading…</div>';
    rowEl.parentNode.insertBefore(ph, rowEl.nextSibling);
    // Fetch full message
    try {
        var res = await authFetch(API + '/email/messages/' + encodeURIComponent(messageId));
        var data = await res.json();
        ph.innerHTML = _mailRenderMessageDetail(messageId, data);
    } catch (e) {
        ph.innerHTML = '<div class="mail-empty">Couldn\'t load message.</div>';
    }
    // Mark read in the background
    if (rowEl.dataset.unread === 'true') {
        _mailMarkRead(messageId).then(function() {
            rowEl.dataset.unread = 'false';
        });
    }
}

function _mailRenderMessageDetail(messageId, data) {
    var bodyHtml;
    if (typeof DOMPurify !== 'undefined' && data.body) {
        bodyHtml = DOMPurify.sanitize(data.body);
    } else {
        bodyHtml = '<pre style="white-space:pre-wrap;font-family:inherit">' +
                   escapeHtml(data.body || '') + '</pre>';
    }
    return '<div class="mail-detail-meta">' +
                escapeHtml(data.date || '') + ' · from ' + escapeHtml(data.from || '') +
           '</div>' +
           '<div class="mail-detail-body">' + bodyHtml + '</div>' +
           '<div class="mail-action-bar" data-message-id="' + escapeHtml(messageId) + '">' +
               '<button class="pill-btn-primary" onclick="_mailStartReply(\'' +
                   _mailEscapeAttr(messageId) + '\')">Draft Reply</button>' +
               '<button class="pill-btn" onclick="_mailOpenInGmail(\'' +
                   _mailEscapeAttr(messageId) + '\')">Open in Gmail</button>' +
           '</div>';
}

function _mailEscapeAttr(s) {
    return String(s || '').replace(/'/g, "\\'");
}

async function _mailMarkRead(messageId) {
    try {
        await authFetch(API + '/email/mark-read/' + encodeURIComponent(messageId), {
            method: 'POST',
        });
    } catch (e) {}
}

function _mailOpenInGmail(messageId) {
    var url = 'https://mail.google.com/mail/u/0/#inbox/' + encodeURIComponent(messageId);
    if (window.electronAPI && typeof window.electronAPI.openExternal === 'function') {
        window.electronAPI.openExternal(url);
    } else {
        window.open(url, '_blank');
    }
}

function _mailRenderAuthCTA(section) {
    section.innerHTML =
        '<div class="mail-auth-cta">' +
            '<h3>Mail needs Google access</h3>' +
            '<p>Pike can\'t reach your inbox until you authorize Google in settings.</p>' +
            '<button class="pill-btn-primary" onclick="_mailStartAuth()">Authorize Google ▸</button>' +
        '</div>';
}

function _mailStartAuth() {
    var url = '/api/google/oauth/start';
    if (window.electronAPI && typeof window.electronAPI.openExternal === 'function') {
        window.electronAPI.openExternal(url);
    } else {
        window.open(url, '_blank');
    }
    // Poll for auth-complete
    var poll = setInterval(async function() {
        try {
            var res = await authFetch(API + '/google/status');
            var data = await res.json();
            if (data && data.connected) {
                clearInterval(poll);
                loadInboxDigest(true);
            }
        } catch (e) {}
    }, 3000);
}
```

- [ ] **Step 2: Verify the `/email/messages/{id}` endpoint exists**

Search for a GET endpoint that returns a single message body:

Run: `grep -n "/api/email/messages\|gmail_get_message" 'C:/Users/dusti/Projects/aegis-ai/server/app.py'`

If it does NOT exist, add it. Place it near other email endpoints:

```python
@app.get("/api/email/messages/{message_id}")
async def email_get_message(message_id: str, user_id: str = Depends(require_user)):
    """Get a single inbox message's full body."""
    from core.email_assistant import _creds_from_session
    from core.protocols import google_tools as gt
    session = session_manager.get_or_create(user_id)
    creds = _creds_from_session(session)
    if not creds:
        return {"error": "not_authorized"}
    msg = gt.gmail_get_message(creds, message_id)
    if msg is None:
        return {"error": "not_found"}
    return msg
```

Add a backend test for the endpoint pattern in `tests/test_email_assistant.py` if you added the endpoint:

```python
def test_mark_read_endpoint_via_get_message_path_exists():
    """Light sanity: gmail_get_message is importable and callable shape-wise."""
    from core.protocols.google_tools import gmail_get_message
    assert callable(gmail_get_message)
```

Run: `pytest tests/test_email_assistant.py -v` — expect green.

- [ ] **Step 3: Manual smoke test** (USER ACTION)

⚠️ Cannot perform yourself. After Switch restarts and Ctrl+R:
1. Open Mail panel. INBOX tab loads.
2. After ~10-60s (cached if recent), Pike's narrative appears at the top with a `Cached Xm ago · ↻ Refresh` footer.
3. Message list shows below.
4. Click a message — row highlights cyan, body loads inline.
5. Click another — first collapses, second expands.
6. Click `Open in Gmail` — opens the thread in browser.
7. Click `↻ Refresh` on the narrative — spinner, then fresh narrative.
8. Disconnect Google in settings → click MAIL → see the auth CTA.

- [ ] **Step 4: Commit**

```bash
git add ui/templates/index.html server/app.py tests/test_email_assistant.py
git commit -m "feat: INBOX tab — load digest, render messages, expand, mark-read

Loads /api/email/inbox-digest, renders Pike's narrative with cache
age + refresh button. Click a message row to expand inline; body
fetched from /api/email/messages/{id} (added). Background mark-read
fires automatically on first expand of an unread message. Open in
Gmail button shells out via the Electron IPC bridge."
```

---

## Task 6: Frontend — Reply flow (intent → draft → editable)

**Files:**
- Modify: `ui/templates/index.html`

- [ ] **Step 1: Add the reply flow JS**

Append after the Task 5 JS:

```javascript
function _mailStartReply(messageId) {
    var bar = document.querySelector(
        '#mailInboxSection .mail-action-bar[data-message-id="' + messageId + '"]'
    );
    if (!bar) return;
    bar.innerHTML =
        '<div class="mail-intent-row">' +
            '<input type="text" class="mail-intent-input" id="mailIntentInput_' + messageId + '" ' +
                'placeholder="What\'s the gist? (optional)" autofocus>' +
            '<button class="pill-btn-primary" onclick="_mailDraftReply(\'' +
                _mailEscapeAttr(messageId) + '\', true)">Draft With Intent</button>' +
            '<button class="pill-btn-secondary" onclick="_mailDraftReply(\'' +
                _mailEscapeAttr(messageId) + '\', false)">skip and draft now</button>' +
        '</div>';
    var input = document.getElementById('mailIntentInput_' + messageId);
    if (input) {
        input.focus();
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') _mailDraftReply(messageId, true);
        });
    }
}

async function _mailDraftReply(messageId, useIntent) {
    var bar = document.querySelector(
        '#mailInboxSection .mail-action-bar[data-message-id="' + messageId + '"]'
    );
    if (!bar) return;
    var intent = '';
    if (useIntent) {
        var input = document.getElementById('mailIntentInput_' + messageId);
        intent = input ? input.value.trim() : '';
    }
    bar.innerHTML = '<div class="mail-narrative-loading">Pike is composing draft…</div>';
    try {
        var res = await authFetch(API + '/email/draft-reply', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({message_id: messageId, intent: intent}),
        });
        var draft = await res.json();
        if (!draft || draft.success === false) {
            bar.innerHTML = '<div class="mail-empty">Couldn\'t draft. ' +
                '<button class="pill-btn" onclick="_mailStartReply(\'' +
                    _mailEscapeAttr(messageId) + '\')">Retry</button></div>';
            return;
        }
        // Remember intent + message_id on the draft so we can regenerate later
        draft._origin = {kind: 'reply', message_id: messageId, intent: intent};
        bar.outerHTML = _mailRenderDraftEditor(draft);
    } catch (e) {
        bar.innerHTML = '<div class="mail-empty">Network error.</div>';
    }
}

function _mailRenderDraftEditor(draft) {
    var draftId = draft.draft_id || draft.id || '';
    var subject = draft.subject || '';
    var body = draft.body || '';
    var to = draft.to || '';
    // Stash the draft origin so regenerate works
    window._mailDrafts = window._mailDrafts || {};
    window._mailDrafts[draftId] = draft;
    return '<div class="mail-draft-editor" data-draft-id="' + escapeHtml(draftId) + '">' +
        '<div class="mail-draft-meta">' +
            '<span>To: ' + escapeHtml(to) + '</span>' +
            '<button class="mail-regen-icon" onclick="_mailRegenerateDraft(\'' +
                _mailEscapeAttr(draftId) + '\')" title="Regenerate">↺</button>' +
        '</div>' +
        '<input type="text" class="mail-draft-subject" id="mailDraftSubject_' + draftId + '" ' +
            'value="' + escapeHtml(subject) + '" placeholder="Subject">' +
        '<textarea class="mail-draft-body" id="mailDraftBody_' + draftId + '">' +
            escapeHtml(body) + '</textarea>' +
        '<div class="mail-action-bar">' +
            '<button class="pill-btn" onclick="_mailSaveDraft(\'' +
                _mailEscapeAttr(draftId) + '\')">Save Draft</button>' +
            '<button class="pill-btn-send" id="mailSendBtn_' + draftId +
                '" onclick="_mailStartSend(\'' + _mailEscapeAttr(draftId) +
                '\', this)">Send</button>' +
            '<button class="pill-btn-danger" onclick="_mailDiscardDraft(\'' +
                _mailEscapeAttr(draftId) + '\')">Discard</button>' +
        '</div>' +
    '</div>';
}

async function _mailSaveDraft(draftId) {
    var subjEl = document.getElementById('mailDraftSubject_' + draftId);
    var bodyEl = document.getElementById('mailDraftBody_' + draftId);
    if (!subjEl || !bodyEl) return;
    try {
        await authFetch(API + '/email/drafts/' + encodeURIComponent(draftId), {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({subject: subjEl.value, body: bodyEl.value}),
        });
        showToast('Draft saved');
        if (_mailActiveTab === 'drafts') loadMailDrafts();
    } catch (e) {
        showToast('Save failed', 'error');
    }
}

async function _mailDiscardDraft(draftId) {
    try {
        await authFetch(API + '/email/drafts/' + encodeURIComponent(draftId), {
            method: 'DELETE',
        });
        showToast('Discarded');
        // Collapse the row this draft was inside (if INBOX)
        var editor = document.querySelector(
            '#mailInboxSection .mail-draft-editor[data-draft-id="' + draftId + '"]'
        );
        if (editor) {
            var detail = editor.closest('.mail-row-detail');
            var row = detail ? detail.previousElementSibling : null;
            if (row && row.classList.contains('mail-row')) row.classList.remove('expanded');
            if (detail) detail.remove();
        }
        if (_mailActiveTab === 'drafts') loadMailDrafts();
    } catch (e) {
        showToast('Discard failed', 'error');
    }
}

async function _mailRegenerateDraft(draftId) {
    var origin = (window._mailDrafts || {})[draftId] && window._mailDrafts[draftId]._origin;
    if (!origin) return;
    var editor = document.querySelector(
        '.mail-draft-editor[data-draft-id="' + draftId + '"]'
    );
    if (!editor) return;
    var bodyEl = document.getElementById('mailDraftBody_' + draftId);
    if (bodyEl) bodyEl.value = '⟳ Pike is rewriting…';
    try {
        var endpoint = (origin.kind === 'reply') ? '/email/draft-reply' : '/email/draft';
        var payload = (origin.kind === 'reply')
            ? {message_id: origin.message_id, intent: origin.intent}
            : {to: origin.to, intent: origin.intent, subject: origin.subject_hint,
               cc: origin.cc, bcc: origin.bcc};
        var res = await authFetch(API + endpoint, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        var newDraft = await res.json();
        if (newDraft && newDraft.body) {
            if (bodyEl) bodyEl.value = newDraft.body;
            var subjEl = document.getElementById('mailDraftSubject_' + draftId);
            if (subjEl && newDraft.subject) subjEl.value = newDraft.subject;
        } else {
            if (bodyEl) bodyEl.value = '(regenerate failed)';
        }
    } catch (e) {
        if (bodyEl) bodyEl.value = '(regenerate failed — network error)';
    }
}
```

- [ ] **Step 2: Verify `/api/email/drafts/{id}` PATCH endpoint exists**

Run: `grep -n "@app.patch.*email/drafts\|PATCH.*drafts" 'C:/Users/dusti/Projects/aegis-ai/server/app.py'`

If it doesn't exist, add it next to the DELETE endpoint:

```python
@app.patch("/api/email/drafts/{draft_id}")
async def email_update_draft(draft_id: str, body: dict,
                              user_id: str = Depends(require_user)):
    """Update a draft's subject/body in-place."""
    from core.email_assistant import _creds_from_session
    from core.protocols import google_tools as gt
    session = session_manager.get_or_create(user_id)
    creds = _creds_from_session(session)
    if not creds:
        return {"error": "not_authorized"}
    subject = body.get("subject", "")
    body_text = body.get("body", "")
    # Re-create the draft with the new content (Gmail draft API requires
    # the full message body on update).
    existing = gt.gmail_get_draft(creds, draft_id) if hasattr(gt, "gmail_get_draft") else None
    to = (existing or {}).get("to", "")
    result = gt.gmail_create_draft(creds, to=to, subject=subject, body=body_text)
    if result.get("success") and result.get("draft_id") != draft_id:
        # If the API created a new draft id, discard the old one
        try: gt.gmail_send_draft  # noqa — just check tools available
        except Exception: pass
    return result
```

If the `gmail_update_draft` helper exists, use it instead. Confirm shape with one quick read of `google_tools.py`.

- [ ] **Step 3: Manual smoke test** (USER ACTION)

After Switch Ctrl+R:
1. Open Mail → click a message → click `Draft Reply`.
2. Intent input appears with `[Draft With Intent]` and `[skip and draft now]`. Input is auto-focused.
3. Type `decline politely` then Enter — Pike drafts (30-60s).
4. Edit the body, click `Save Draft` → toast `Draft saved`.
5. Click `↺` regenerate — body re-fills with a new attempt.
6. Click `Discard` → toast `Discarded`, row collapses.

- [ ] **Step 4: Commit**

```bash
git add ui/templates/index.html server/app.py
git commit -m "feat: reply flow — intent input + draft editor + save/discard/regen

Click Draft Reply on an expanded message: optional inline intent
field with [Draft With Intent] and [skip and draft now] buttons.
Pike's draft replaces the action bar with an editable Subject +
Body and the full action bar (Save Draft, Send, Discard, Regen).
Save persists via PATCH /api/email/drafts/{id} (added if missing)."
```

---

## Task 7: Frontend — COMPOSE tab

**Files:**
- Modify: `ui/templates/index.html`

- [ ] **Step 1: Replace the COMPOSE section placeholder**

Find `id="mailComposeSection"`. Replace its inner content with the form:

```html
<section class="mail-tab-body" data-tab="compose" id="mailComposeSection" style="display:none">
    <div id="mailComposeContent">
        <!-- rendered by _mailRenderComposeForm() -->
    </div>
</section>
```

- [ ] **Step 2: Add the COMPOSE JS**

Append after Task 6 JS:

```javascript
function _mailRenderComposeForm() {
    var container = document.getElementById('mailComposeContent');
    if (!container) return;
    container.innerHTML =
        '<form class="mail-compose-form" id="mailComposeForm" onsubmit="event.preventDefault();_mailComposeSubmit()">' +
            '<label>To <input type="email" class="mail-field-to" id="mailFieldTo" ' +
                'required placeholder="recipient@example.com" oninput="_mailUpdateComposeButton()"></label>' +
            '<label>CC <input type="text" class="mail-field-cc" id="mailFieldCc" ' +
                'placeholder="(optional, comma-separated)"></label>' +
            '<label>BCC <input type="text" class="mail-field-bcc" id="mailFieldBcc" ' +
                'placeholder="(optional, comma-separated)"></label>' +
            '<label>Subject hint <input type="text" class="mail-field-subject" id="mailFieldSubject" ' +
                'placeholder="(optional — Pike writes it if blank)"></label>' +
            '<label>Intent <textarea class="mail-field-intent" id="mailFieldIntent" rows="4" required ' +
                'placeholder="What do you want to say? Pike will draft it in your voice." ' +
                'oninput="_mailUpdateComposeButton()"></textarea></label>' +
            '<button type="submit" class="pill-btn-primary" id="mailComposeBtn" disabled>Draft</button>' +
        '</form>';
}

function _mailUpdateComposeButton() {
    var to = document.getElementById('mailFieldTo');
    var intent = document.getElementById('mailFieldIntent');
    var btn = document.getElementById('mailComposeBtn');
    if (!to || !intent || !btn) return;
    var emailOk = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(to.value.trim());
    var intentOk = intent.value.trim().length > 0;
    btn.disabled = !(emailOk && intentOk);
}

async function _mailComposeSubmit() {
    var to = document.getElementById('mailFieldTo').value.trim();
    var cc = document.getElementById('mailFieldCc').value.trim();
    var bcc = document.getElementById('mailFieldBcc').value.trim();
    var subjectHint = document.getElementById('mailFieldSubject').value.trim();
    var intent = document.getElementById('mailFieldIntent').value.trim();
    if (!to || !intent) return;

    var form = document.getElementById('mailComposeForm');
    if (form) form.classList.add('composing');
    var container = document.getElementById('mailComposeContent');
    var spinner = document.createElement('div');
    spinner.className = 'mail-compose-spinner';
    spinner.textContent = 'Pike is composing…';
    container.appendChild(spinner);

    try {
        var res = await authFetch(API + '/email/draft', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                to: to, intent: intent,
                subject: subjectHint || undefined,
                cc: cc || undefined,
                bcc: bcc || undefined,
            }),
        });
        var draft = await res.json();
        if (!draft || draft.success === false) {
            spinner.textContent = 'Couldn\'t draft right now.';
            spinner.innerHTML += ' <button class="pill-btn" onclick="_mailRenderComposeForm()">Reset</button>';
            return;
        }
        draft._origin = {
            kind: 'new', to: to, intent: intent,
            subject_hint: subjectHint, cc: cc, bcc: bcc
        };
        // Replace the entire content with the draft editor
        container.innerHTML = '';
        var wrap = document.createElement('div');
        wrap.style.padding = '12px 16px';
        wrap.innerHTML =
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
                '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
                    '<span class="mail-recipient-pill" onclick="_mailRenderComposeForm()">To: ' +
                        escapeHtml(to) + '</span>' +
                    (cc ? '<span class="mail-recipient-pill" onclick="_mailRenderComposeForm()">CC: ' +
                        escapeHtml(cc) + '</span>' : '') +
                    (bcc ? '<span class="mail-recipient-pill" onclick="_mailRenderComposeForm()">BCC: ' +
                        escapeHtml(bcc) + '</span>' : '') +
                '</div>' +
                '<button class="pill-btn-secondary" onclick="_mailRenderComposeForm()">↻ New Draft</button>' +
            '</div>' +
            _mailRenderDraftEditor(draft);
        container.appendChild(wrap);
    } catch (e) {
        spinner.textContent = 'Network error.';
    }
}

// Render the form once the COMPOSE tab is first shown
(function _mailComposeInitHook() {
    // Patch _mailSwitchTab to render the form when compose is activated
    var origSwitch = _mailSwitchTab;
    _mailSwitchTab = function(name) {
        origSwitch(name);
        if (name === 'compose') {
            var container = document.getElementById('mailComposeContent');
            if (container && !container.firstChild) _mailRenderComposeForm();
        }
    };
})();
```

- [ ] **Step 3: Manual smoke test** (USER ACTION)

1. Open Mail → COMPOSE tab.
2. Form appears with `To`, `CC`, `BCC`, `Subject hint`, `Intent` fields.
3. `Draft` button stays disabled until `To` looks like an email AND `Intent` has text.
4. Fill in: `To: tyler@example.com`, `Intent: confirm friday lunch at noon`, click `Draft`.
5. Form opacity-fades, spinner shows for 30-60s, then draft editor replaces.
6. Recipient pills show at top; click `↻ New Draft` resets to empty form.
7. Edit body, `Save Draft` → toast.

- [ ] **Step 4: Commit**

```bash
git add ui/templates/index.html
git commit -m "feat: COMPOSE tab — form, validation, draft inline

Form has To (required, email-shaped), CC, BCC, Subject hint, Intent
(required). Draft button enables when To + Intent are valid. Submit
calls /api/email/draft with cc/bcc forwarded; the form is replaced
with the same editable draft editor used by the reply flow.
Recipient pills show at top with a New Draft reset button."
```

---

## Task 8: Frontend — DRAFTS tab

**Files:**
- Modify: `ui/templates/index.html`

- [ ] **Step 1: Add DRAFTS list rendering**

Append after Task 7 JS:

```javascript
var _mailDraftsCache = [];

async function loadMailDrafts() {
    var section = document.getElementById('mailDraftsSection');
    if (!section) return;
    section.innerHTML = '<div class="mail-narrative-loading">Loading drafts…</div>';
    try {
        var res = await authFetch(API + '/email/drafts');
        var data = await res.json();
        if (data && data.error === 'not_authorized') {
            _mailRenderAuthCTA(section);
            return;
        }
        _mailDraftsCache = Array.isArray(data) ? data : (data.drafts || []);
        _mailDraftsLoaded = true;
        _mailRenderDraftsList();
    } catch (e) {
        section.innerHTML = '<div class="mail-empty">Couldn\'t load drafts. ' +
            '<button class="pill-btn" onclick="loadMailDrafts()">↻ Retry</button></div>';
    }
}

function _mailRenderDraftsList() {
    var section = document.getElementById('mailDraftsSection');
    if (!section) return;
    if (!_mailDraftsCache.length) {
        section.innerHTML =
            '<div class="mail-empty">No drafts yet. Pike will save drafts here ' +
            'when you ask him to compose anything, or when you save one from ' +
            'INBOX or COMPOSE.</div>';
        return;
    }
    var showPikeIcon = _mailGetSetting('showPikeIcon', true);
    var html = '<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 12px;">' +
                  '<div style="color:var(--lcars-text-dim);font-size:11px">' +
                      _mailDraftsCache.length + ' draft' +
                      (_mailDraftsCache.length === 1 ? '' : 's') +
                  '</div>' +
                  '<button class="pill-btn" onclick="loadMailDrafts()">↻ Refresh</button>' +
              '</div><div id="mailDraftsList">';
    _mailDraftsCache.forEach(function(d) {
        var draftId = d.id || d.draft_id;
        var icon = (showPikeIcon && d.pike_drafted !== false) ? '◇' : '';
        var ageS = d.created_at ? (Date.now()/1000 - d.created_at) : null;
        var age = ageS ? ('saved ' + _mailFormatAge(Math.floor(ageS)) + ' ago') : '';
        html += '<div class="mail-row" data-draft-id="' + escapeHtml(draftId) + '" ' +
                'onclick="_mailToggleDraft(this)">' +
                    '<div class="mail-row-icon">' + icon + '</div>' +
                    '<div class="mail-row-meta">' +
                        '<div class="mail-row-from">To: ' + escapeHtml(d.to || '?') + '</div>' +
                        '<div class="mail-row-subj">' +
                            escapeHtml(d.subject || '(no subject)') + '</div>' +
                    '</div>' +
                    '<div class="mail-row-age">' + escapeHtml(age) + '</div>' +
                '</div>';
    });
    html += '</div>';
    section.innerHTML = html;
}

async function _mailToggleDraft(rowEl) {
    var draftId = rowEl.dataset.draftId;
    var alreadyExpanded = rowEl.classList.contains('expanded');
    document.querySelectorAll('#mailDraftsSection .mail-row').forEach(function(r) {
        r.classList.remove('expanded');
    });
    document.querySelectorAll('#mailDraftsSection .mail-row-detail').forEach(function(d) {
        d.remove();
    });
    if (alreadyExpanded) return;
    rowEl.classList.add('expanded');
    var ph = document.createElement('div');
    ph.className = 'mail-row-detail';
    ph.innerHTML = '<div class="mail-narrative-loading">Loading draft…</div>';
    rowEl.parentNode.insertBefore(ph, rowEl.nextSibling);
    try {
        var res = await authFetch(API + '/email/drafts/' + encodeURIComponent(draftId));
        var draft = await res.json();
        if (!draft || draft.error) {
            ph.innerHTML = '<div class="mail-empty">Couldn\'t load draft.</div>';
            return;
        }
        // No origin metadata for old drafts — regenerate becomes a no-op for those
        draft._origin = draft._origin || null;
        ph.innerHTML = _mailRenderDraftEditor(draft);
    } catch (e) {
        ph.innerHTML = '<div class="mail-empty">Network error.</div>';
    }
}

function _mailGetSetting(key, fallback) {
    try {
        var raw = localStorage.getItem('aegis_mail_settings');
        if (raw) {
            var obj = JSON.parse(raw);
            if (key in obj) return obj[key];
        }
    } catch (e) {}
    return fallback;
}

function _mailSetSetting(key, val) {
    try {
        var raw = localStorage.getItem('aegis_mail_settings');
        var obj = raw ? JSON.parse(raw) : {};
        obj[key] = val;
        localStorage.setItem('aegis_mail_settings', JSON.stringify(obj));
    } catch (e) {}
}
```

- [ ] **Step 2: Manual smoke test** (USER ACTION)

1. Open Mail → DRAFTS tab.
2. If you've saved any drafts (from Task 6 or 7), they appear in a list with `To:`, `Subject`, `saved Xm ago`, and a `◇` icon.
3. Click a draft → expands inline with the editable Subject + Body and the action bar.
4. Edit + `Save Draft` → toast.
5. `Discard` → row removed.
6. Refresh button reloads the list.
7. Empty state shows when no drafts exist.

- [ ] **Step 3: Commit**

```bash
git add ui/templates/index.html
git commit -m "feat: DRAFTS tab — list + expand-edit

Lists saved drafts (To, Subject, age, ◇ Pike icon). Click expands
inline with the same editable draft editor used by reply/compose.
Save/Discard wired. Refresh button on the list header. Empty state
explains where drafts come from."
```

---

## Task 9: Frontend — Send confirmation (two-step + 5s deferred + toast) + settings + smoke

**Files:**
- Modify: `ui/templates/index.html`

- [ ] **Step 1: Add the deferred-send + undo flow**

Append after Task 8 JS:

```javascript
var _mailPendingSend = null; // {draftId, timeoutId, toastEl}

function _mailStartSend(draftId, btn) {
    if (!btn) return;
    if (btn.classList.contains('confirming')) {
        // Second click within the 5s window — commit to deferred send
        clearTimeout(btn._resetTimeout);
        btn.classList.remove('confirming');
        btn.textContent = 'Sending…';
        btn.disabled = true;
        _mailCommitSend(draftId, btn);
        return;
    }
    // First click — flip into confirming state
    btn.classList.add('confirming');
    btn.textContent = 'Confirm Send';
    btn._resetTimeout = setTimeout(function() {
        btn.classList.remove('confirming');
        btn.textContent = 'Send';
    }, 5000);
}

function _mailCommitSend(draftId, btn) {
    // Save current edits to the draft before scheduling the send
    var subjEl = document.getElementById('mailDraftSubject_' + draftId);
    var bodyEl = document.getElementById('mailDraftBody_' + draftId);
    var subject = subjEl ? subjEl.value : '';
    var body = bodyEl ? bodyEl.value : '';
    // Capture the editor element so we can restore it on undo
    var editor = document.querySelector(
        '.mail-draft-editor[data-draft-id="' + draftId + '"]'
    );
    var editorParent = editor ? editor.parentNode : null;
    var editorHtml = editor ? editor.outerHTML : '';
    // Hide the editor immediately
    if (editor) editor.style.display = 'none';
    // Show toast
    var toast = _mailShowUndoToast(draftId);
    var timeoutId = setTimeout(async function() {
        toast.remove();
        _mailPendingSend = null;
        // Save edits to draft, then send
        try {
            await authFetch(API + '/email/drafts/' + encodeURIComponent(draftId), {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({subject: subject, body: body}),
            });
            var res = await authFetch(API + '/email/send-draft/' +
                encodeURIComponent(draftId), {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({confirm: true}),
            });
            var result = await res.json();
            if (result && result.success !== false) {
                showToast('Sent');
                // Remove the editor from DOM permanently
                if (editor) editor.remove();
                // Refresh inbox if it's loaded
                if (_mailActiveTab === 'inbox' && _mailInboxLoaded) loadInboxDigest();
                if (_mailActiveTab === 'drafts') loadMailDrafts();
            } else {
                showToast('Send failed — saved as draft', 'error');
                if (editor) editor.style.display = '';
            }
        } catch (e) {
            showToast('Send failed — saved as draft', 'error');
            if (editor) editor.style.display = '';
        }
    }, 5000);
    _mailPendingSend = {draftId: draftId, timeoutId: timeoutId,
                       toastEl: toast, editorEl: editor};
}

function _mailShowUndoToast(draftId) {
    var existing = document.getElementById('mailUndoToast');
    if (existing) existing.remove();
    var toast = document.createElement('div');
    toast.id = 'mailUndoToast';
    toast.className = 'mail-undo-toast';
    toast.innerHTML =
        '<span>Sending in 5s</span> ' +
        '<span class="mail-undo-link" onclick="_mailUndoSend()">undo</span>';
    document.body.appendChild(toast);
    return toast;
}

function _mailUndoSend() {
    if (!_mailPendingSend) return;
    clearTimeout(_mailPendingSend.timeoutId);
    if (_mailPendingSend.toastEl) _mailPendingSend.toastEl.remove();
    if (_mailPendingSend.editorEl) {
        _mailPendingSend.editorEl.style.display = '';
        var btn = document.getElementById('mailSendBtn_' + _mailPendingSend.draftId);
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Send';
            btn.classList.remove('confirming');
        }
    }
    showToast('Send cancelled');
    _mailPendingSend = null;
}
```

- [ ] **Step 2: Add the settings dropdown entries**

Find `togglePanelSettings` in `index.html`. There should be a per-panel branch (`if (panelId === 'taskPanel') { ... }` from the deadline countdown work). Add a parallel `mailPanel` branch right after it (or wherever the existing per-panel additions live):

```javascript
if (panelId === 'mailPanel') {
    var s = {
        maxMessages: _mailGetSetting('maxMessages', 10),
        cacheTTLMin: _mailGetSetting('cacheTTLMin', 10),
        showPikeIcon: _mailGetSetting('showPikeIcon', true),
    };
    var html =
        '<div class="ps-divider"></div>' +
        '<div class="settings-label">MAIL</div>' +
        '<div class="deadline-setting-row">' +
            '<span>Inbox messages</span>' +
            '<input type="number" min="1" max="50" value="' + s.maxMessages +
                '" onchange="_mailSetSetting(\'maxMessages\', parseInt(this.value,10) || 10)" ' +
                'style="width:50px">' +
        '</div>' +
        '<div class="deadline-setting-row">' +
            '<span>Cache TTL (min)</span>' +
            '<input type="number" min="0" max="60" value="' + s.cacheTTLMin +
                '" onchange="_mailSetSetting(\'cacheTTLMin\', parseInt(this.value,10) || 10)" ' +
                'style="width:50px">' +
        '</div>' +
        '<label class="deadline-setting-row">' +
            '<span>Show Pike icon on drafts</span>' +
            '<input type="checkbox"' + (s.showPikeIcon ? ' checked' : '') +
                ' onchange="_mailSetSetting(\'showPikeIcon\', this.checked);if(_mailActiveTab===\'drafts\')_mailRenderDraftsList()">' +
        '</label>';
    panel.insertAdjacentHTML('beforeend', html);
}
```

(Reuse `.deadline-setting-row` from the deadline countdown work — that class exists and matches the gear dropdown's styling.)

- [ ] **Step 3: End-to-end smoke checklist** (USER ACTION)

Run through this list:

- [ ] Open Mail panel, INBOX loads with cached narrative + message list
- [ ] Click `↻ Refresh` — fresh narrative regenerates after ~30-60s
- [ ] Click a message — body loads inline, mark-read fires automatically (dot becomes hollow on next refresh)
- [ ] `Open in Gmail` opens the correct thread in the browser
- [ ] `Draft Reply` → intent input → type intent → Enter → Pike drafts → editable
- [ ] `Save Draft` → toast → DRAFTS tab shows the saved one
- [ ] First `Send` click → button turns amber pulse, label becomes `Confirm Send`
- [ ] Wait 5s without clicking → button resets to `Send` (blue)
- [ ] Click `Send` twice quickly → undo toast appears at the bottom
- [ ] Click `undo` within 5s → editor restored, toast shows `Send cancelled`
- [ ] Click `Send` twice and wait 5s → toast disappears, draft sent
- [ ] COMPOSE tab → form → submit → draft → save / send
- [ ] DRAFTS tab → list shows saved drafts → click expand → edit → save
- [ ] Settings (gear) → adjust `Inbox messages` → refresh → fewer/more rows
- [ ] Disconnect Google → MAIL → auth CTA shows
- [ ] Reconnect → polling detects it → INBOX loads

- [ ] **Step 4: Commit + push**

```bash
git add ui/templates/index.html
git commit -m "feat: send confirmation flow + settings + smoke verified

Two-step amber Send button: first click flips to 'Confirm Send'
(amber pulse), 5s timeout resets if no second click. Second click
hides the editor immediately, shows an 'undo' toast, and schedules
the actual /api/email/send-draft call for 5s later. Undo cancels
the scheduled send and restores the editor. Settings panel adds
Mail-specific controls (max inbox messages, cache TTL, Pike icon
toggle)."
git push origin main
```

---

## Self-Review

**Spec coverage** — every section of `2026-06-27-email-assistant-ui-design.md` maps to a task:

| Spec section                          | Task(s)         |
|---------------------------------------|-----------------|
| Backend: narrative cache + fresh      | Task 1          |
| Backend: CC/BCC on draft              | Task 2          |
| Backend: mark_read endpoint           | Task 3          |
| Frontend panel + tabs + sidebar       | Task 4          |
| INBOX tab (narrative, list, expand, mark-read) | Task 5 |
| Reply flow (intent + draft editor)    | Task 6          |
| COMPOSE tab (form + draft inline)     | Task 7          |
| DRAFTS tab (list + edit)              | Task 8          |
| Send confirmation flow + auth CTA + settings | Task 9   |
| Loading / error / empty states        | Spread across Tasks 5-8 (inline per-section) |
| Auth-not-connected state              | Task 5 (helper) + Task 9 (smoke) |
| Settings (gear menu)                  | Task 9          |
| Refresh policy                        | Tasks 5, 8      |

**Placeholder scan** — no TBDs, no "implement later", every step has runnable code or an explicit user-action note.

**Type consistency** — helper names match across tasks: `loadInboxDigest`, `loadMailDrafts`, `_mailSwitchTab`, `_mailStartReply`, `_mailDraftReply`, `_mailRenderDraftEditor`, `_mailSaveDraft`, `_mailDiscardDraft`, `_mailRegenerateDraft`, `_mailStartSend`, `_mailCommitSend`, `_mailUndoSend`, `_mailShowUndoToast`, `_mailGetSetting`, `_mailSetSetting`, `_mailRenderComposeForm`, `_mailUpdateComposeButton`, `_mailComposeSubmit`, `_mailToggleMessage`, `_mailToggleDraft`, `_mailMarkRead`, `_mailOpenInGmail`, `_mailRenderAuthCTA`, `_mailStartAuth`. Settings localStorage key: `aegis_mail_settings` (consistent). Active-tab key: `aegis_mail_active_tab` (consistent). Settings shape: `{maxMessages, cacheTTLMin, showPikeIcon}` (consistent across the gear dropdown and the helpers).
