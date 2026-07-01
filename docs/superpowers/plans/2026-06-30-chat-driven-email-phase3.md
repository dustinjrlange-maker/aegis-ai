# Chat-driven Email (Phase 3: mark-read + archive) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let Pike mark an inbox email as read or archive it (remove from inbox) directly from chat — low-stakes, reversible actions that run immediately (no draft-then-confirm).

**Architecture:** Adds two actions (`mark_read`, `archive`) to `EmailOpsProtocol`. Both resolve an inbox message by REF and act immediately via `google_tools` (`gmail_mark_read` exists; add `gmail_archive`). They do NOT set `_pending` — they're complete on the spot. Safety split per spec: reads/archives run directly; only *sending* mail needs confirmation.

**Tech Stack:** Python 3.12, pytest. Builds on `core/protocols/email_ops.py`, `core/protocols/google_tools.py`.

**Reference:** Phase 3 section of `docs/superpowers/specs/2026-06-30-chat-driven-email-actions-design.md`.

---

## File Structure
- **Modify** `core/protocols/google_tools.py` — add `gmail_archive` (mirror of `gmail_mark_read`).
- **Modify** `core/protocols/email_ops.py` — classifier learns `mark_read`/`archive`; add `_do_mark_read`, `_do_archive`; wire dispatch.
- **Modify** `tests/test_email_ops.py` — tests for archive helper + both actions.

---

## Task 1: `gmail_archive` in google_tools

**Files:**
- Modify: `core/protocols/google_tools.py` (add after `gmail_mark_read`, ~line 251)
- Test: `tests/test_email_ops.py` (append)

- [ ] **Step 1: Write the failing test** — Append to `tests/test_email_ops.py`:

```python
def test_gmail_archive_removes_inbox_label(monkeypatch):
    calls = {}
    class _Ex:
        def execute(self): return {}
    class _Msgs:
        def modify(self, userId, id, body):
            calls.update(userId=userId, id=id, body=body)
            return _Ex()
    class _Users:
        def messages(self): return _Msgs()
    class _Svc:
        def users(self): return _Users()
    monkeypatch.setattr(gt, "_get_gmail_service", lambda creds: _Svc())
    res = gt.gmail_archive("CREDS", "m1")
    assert res == {"ok": True}
    assert calls["id"] == "m1"
    assert calls["body"] == {"removeLabelIds": ["INBOX"]}
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_email_ops.py::test_gmail_archive_removes_inbox_label -v` → FAIL (`AttributeError: ... 'gmail_archive'`).

- [ ] **Step 3: Implement** — Add to `core/protocols/google_tools.py` immediately after `gmail_mark_read`:

```python
def gmail_archive(creds, message_id):
    """Archive an inbox message (removes the INBOX label).

    Returns {ok: True} on success, {ok: False, error: ...} on failure.
    """
    service = _get_gmail_service(creds)
    if not service:
        return {"ok": False, "error": "Gmail service unavailable"}
    try:
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["INBOX"]},
        ).execute()
        return {"ok": True}
    except Exception as e:
        logger.warning("Could not archive message: %s", e)
        return {"ok": False, "error": str(e)}
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_email_ops.py::test_gmail_archive_removes_inbox_label -v` → PASS. Then full file → all pass.

- [ ] **Step 5: Commit**

```
git add core/protocols/google_tools.py tests/test_email_ops.py
git commit -m "feat: gmail_archive — remove INBOX label"
```

---

## Task 2: mark_read + archive actions in the protocol

**Files:**
- Modify: `core/protocols/email_ops.py`
- Test: `tests/test_email_ops.py` (append)

- [ ] **Step 1: Write failing tests** — Append to `tests/test_email_ops.py`:

```python
def test_mark_read_action(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [
        {"id": "m5", "sender": "X", "subject": "Y"}])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=mark_read | REF=1 | TO=- | INSTRUCTION=-")
    marked = {}
    monkeypatch.setattr(gt, "gmail_mark_read",
                        lambda creds, mid: marked.update(id=mid) or {"ok": True})
    result = p.process_input("mark the first email as read", {})
    assert result["intercept"] is True
    assert "read" in result["response"].lower()
    assert marked["id"] == "m5"
    assert p._pending is None


def test_archive_action(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [
        {"id": "m5", "sender": "X", "subject": "Y"}])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=archive | REF=1 | TO=- | INSTRUCTION=-")
    archived = {}
    monkeypatch.setattr(gt, "gmail_archive",
                        lambda creds, mid: archived.update(id=mid) or {"ok": True})
    result = p.process_input("archive that email", {})
    assert result["intercept"] is True
    assert "archive" in result["response"].lower()
    assert archived["id"] == "m5"


def test_mark_read_bad_ref_asks(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=mark_read | REF=- | TO=- | INSTRUCTION=-")
    result = p.process_input("mark it read", {})
    assert result["intercept"] is True
    assert "which email" in result["response"].lower()
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_email_ops.py -k "mark_read or archive_action" -v` → FAIL (actions not allowed / not dispatched → fall through, no intercept).

- [ ] **Step 3: Implement** — Three edits in `core/protocols/email_ops.py`:

(a) Extend `_ALLOWED_ACTIONS` (currently `("reply", "new", "forward", "send", "edit", "discard")`) to:
```python
    _ALLOWED_ACTIONS = ("reply", "new", "forward", "mark_read", "archive",
                        "send", "edit", "discard")
```

(b) In `_build_classifier_prompt`, add two rules to the Rules block (insert right after the `- forward:` rule line, before the `- send:` rule):
```python
            "- mark_read: mark an inbox email as read. REF = the inbox number.\n"
            "- archive: remove an inbox email from the inbox. REF = the inbox number.\n"
```
Also update the ACTION list in the format line to include them — change `ACTION=<reply|new|forward|send|edit|discard|none>` to `ACTION=<reply|new|forward|mark_read|archive|send|edit|discard|none>`.

(c) Add `mark_read`/`archive` to the dispatch dict in `process_input`:
```python
            "reply": self._do_reply,
            "new": self._do_new,
            "forward": self._do_forward,
            "mark_read": self._do_mark_read,
            "archive": self._do_archive,
            "send": self._do_send,
            "edit": self._do_edit,
            "discard": self._do_discard,
```
And add the two handlers (place after `_do_forward`):
```python
    def _do_mark_read(self, action, text):
        message_id = self._resolve_ref(action)
        if not message_id:
            return "Which email should I mark as read?"
        res = gt.gmail_mark_read(ea._creds_from_session(self._session), message_id)
        if not res.get("ok"):
            return f"I couldn't mark it read: {res.get('error', 'unknown error')}"
        return "Marked it as read."

    def _do_archive(self, action, text):
        message_id = self._resolve_ref(action)
        if not message_id:
            return "Which email should I archive?"
        res = gt.gmail_archive(ea._creds_from_session(self._session), message_id)
        if not res.get("ok"):
            return f"I couldn't archive it: {res.get('error', 'unknown error')}"
        return "Archived it — it's out of your inbox."
```

Note: `mark_read`/`archive` are NOT added to the `act in ("send", "edit", "discard")` pending-guard, so they run whenever classified (after the creds check). They act immediately and do not touch `_pending`.

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_email_ops.py -k "mark_read or archive_action" -v` → PASS. Then full file `pytest tests/test_email_ops.py -v` → ALL pass.

- [ ] **Step 5: Commit**

```
git add core/protocols/email_ops.py tests/test_email_ops.py
git commit -m "feat: chat mark-read + archive actions (run directly, no confirm)"
```

---

## Task 3: Full suite + manual smoke

- [ ] **Step 1:** `pytest tests/test_email_ops.py tests/test_email_assistant.py -v` → ALL PASS.
- [ ] **Step 2: Manual smoke** (restart Aegis, COMMS chat, Google connected):
  1. "mark the top email as read" → Pike: "Marked it as read." (verify the unread dot clears in the Mail inbox).
  2. "archive the <some promo> email" → Pike: "Archived it…" (verify it leaves the inbox on refresh).
  3. A non-email message still gets a normal reply (no interception).
- [ ] **Step 3: Done** — no commit.

---

## Self-review notes
- **Spec coverage (Phase 3):** gmail_archive (Task 1), mark_read + archive actions + classifier (Task 2). Immediate-execute (no `_pending`), matching the spec's safety split (reads/archives direct; only sends confirm).
- **Return shapes:** `gmail_mark_read`/`gmail_archive` return `{ok: bool, error?}` (note `ok`, not `success`); handlers check `.get("ok")`. `_resolve_ref` (existing) maps REF→message_id.
- **No placeholders.**
