# Chat-driven Email (Phase 2: compose-new + forward) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extend the chat email agent so Pike can compose a brand-new email to an address and forward an inbox email to someone — both as draft-then-confirm, reusing the Phase 1 protocol.

**Architecture:** Adds two actions (`new`, `forward`) to `EmailOpsProtocol`: classifier learns them + a `TO=` field; `_do_new` calls the existing `email_assistant.draft_new`; `_do_forward` calls a new `email_assistant.draft_forward`; `_do_edit` becomes kind-aware so editing a new/forward draft re-drafts correctly. All safety (draft-then-confirm, literal send guard, fall-through) is inherited from Phase 1.

**Tech Stack:** Python 3.12, pytest. Builds on `core/protocols/email_ops.py`, `core/email_assistant.py`, `core/protocols/google_tools.py` (all exist).

**Reference spec:** `docs/superpowers/specs/2026-06-30-chat-driven-email-actions-design.md` (Phase 2 section).

**Out of scope:** mark-read/archive (Phase 3). Recipient resolution is by email address only (name→address lookup via contacts is future).

---

## File Structure
- **Modify** `core/email_assistant.py` — add `draft_forward`.
- **Modify** `core/protocols/email_ops.py` — extend classifier (`new`/`forward`/`TO=`), add `_do_new`, `_do_forward`, `_extract_recipient`, wire dispatch, make `_do_edit` kind-aware.
- **Modify** `tests/test_email_assistant.py` — test `draft_forward`.
- **Modify** `tests/test_email_ops.py` — tests for new/forward/edit-kind.

---

## Task 1: `draft_forward` in email_assistant

**Files:**
- Modify: `core/email_assistant.py` (add after `draft_new`)
- Test: `tests/test_email_assistant.py` (append, follow that file's existing mock style)

- [ ] **Step 1: Write the failing test** — Append to `tests/test_email_assistant.py` (it already mocks `core.protocols.google_tools as gt` and builds a fake session — match its existing pattern; if it uses a helper to build creds/session, reuse it):

```python
def test_draft_forward_builds_quoted_draft(monkeypatch):
    import core.email_assistant as ea
    from core.protocols import google_tools as gt

    class _G:
        def _get_creds(self): return "CREDS"
    class _R:
        def get(self, n): return _G() if n == "google" else None
    class _S:
        protocol_registry = _R()
        system_prompt_base = "SYS"
        user_id = "u"

    monkeypatch.setattr(gt, "gmail_get_message", lambda creds, mid: {
        "subject": "Quarterly numbers", "from": "Ann <ann@x.ca>",
        "date": "Mon, 1 Jun 2026", "body": "Here are the figures.",
    })
    captured = {}
    monkeypatch.setattr(gt, "gmail_create_draft",
                        lambda creds, to, subject, body, **kw: captured.update(
                            to=to, subject=subject, body=body) or {"success": True, "draft_id": "d9"})

    res = ea.draft_forward(_S(), "m1", "bob@x.ca", note="fyi")
    assert res["success"] is True
    assert res["draft_id"] == "d9"
    assert captured["to"] == "bob@x.ca"
    assert captured["subject"] == "Fwd: Quarterly numbers"
    assert "fyi" in captured["body"]
    assert "Forwarded message" in captured["body"]
    assert "Here are the figures." in captured["body"]
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_email_assistant.py::test_draft_forward_builds_quoted_draft -v` → FAIL (`AttributeError: ... 'draft_forward'`).

- [ ] **Step 3: Implement** — Add to `core/email_assistant.py` after `draft_new`:

```python
def draft_forward(session, message_id: str, to: str, note: str | None = None) -> dict:
    """Forward an inbox message to a new recipient. Saved as a draft, NOT sent.

    Returns: {success, draft_id, subject, to, body, error?}
    """
    creds = _creds_from_session(session)
    if not creds:
        return {"success": False, "error": "Email not authorized"}
    original = gt.gmail_get_message(creds, message_id)
    if not original:
        return {"success": False, "error": f"Could not load message {message_id}"}

    orig_subject = original.get("subject", "") or "(no subject)"
    subject = orig_subject if orig_subject.lower().startswith("fwd:") else f"Fwd: {orig_subject}"
    parts = []
    if note:
        parts.append(note.strip())
        parts.append("")
    parts.append("---------- Forwarded message ----------")
    parts.append(f"From: {original.get('from', '')}")
    parts.append(f"Date: {original.get('date', '')}")
    parts.append(f"Subject: {orig_subject}")
    parts.append("")
    parts.append(original.get("body", "") or "")
    body = "\n".join(parts)

    result = gt.gmail_create_draft(creds, to=to, subject=subject, body=body)
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "Draft creation failed"), "body": body}
    return {"success": True, "draft_id": result["draft_id"], "subject": subject, "to": to, "body": body}
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_email_assistant.py::test_draft_forward_builds_quoted_draft -v` → PASS. Then `pytest tests/test_email_assistant.py -v` → all pass.

- [ ] **Step 5: Commit**

```
git add core/email_assistant.py tests/test_email_assistant.py
git commit -m "feat: draft_forward — quote+forward an inbox message as a draft"
```

---

## Task 2: Classifier learns new/forward + TO field

**Files:**
- Modify: `core/protocols/email_ops.py` (`_ALLOWED_ACTIONS`, `_build_classifier_prompt`, `_parse_classification`)
- Test: `tests/test_email_ops.py` (append)

- [ ] **Step 1: Write failing tests** — Append to `tests/test_email_ops.py`:

```python
def test_parse_classification_new_with_to():
    p = _proto(_FakeSession())
    out = p._parse_classification(
        "ACTION=new | REF=- | TO=bob@x.ca | INSTRUCTION=ask about the schedule")
    assert out["action"] == "new"
    assert out["to"] == "bob@x.ca"
    assert out["instruction"] == "ask about the schedule"


def test_parse_classification_forward():
    p = _proto(_FakeSession())
    out = p._parse_classification("ACTION=forward | REF=3 | TO=sue@x.ca | INSTRUCTION=-")
    assert out["action"] == "forward"
    assert out["ref"] == "3"
    assert out["to"] == "sue@x.ca"
    assert "instruction" not in out
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_email_ops.py -k "new_with_to or forward" -v` → FAIL (action parsed as none because new/forward not allowed; or `to` missing).

- [ ] **Step 3: Implement** — In `core/protocols/email_ops.py`:

(a) Change `_ALLOWED_ACTIONS` to:
```python
    _ALLOWED_ACTIONS = ("reply", "new", "forward", "send", "edit", "discard")
```

(b) In `_build_classifier_prompt`, change the format line and rules to include new/forward + a TO field. Replace the existing format+rules block with:
```python
            "Reply with ONE line, exactly this format:\n"
            "ACTION=<reply|new|forward|send|edit|discard|none> | REF=<inbox number or -> "
            "| TO=<email address or -> | INSTRUCTION=<what to say, or ->\n\n"
            "Rules:\n"
            "- reply: replying to an inbox email. REF = the inbox number. "
            "INSTRUCTION = what the reply should say.\n"
            "- new: a brand-new email. TO = the recipient's email address. "
            "INSTRUCTION = what it should say.\n"
            "- forward: forward an inbox email. REF = the inbox number. "
            "TO = the recipient's email address.\n"
            "- send: send the pending draft. Only if a draft is pending.\n"
            "- edit: change the pending draft. INSTRUCTION = the change. "
            "Only if a draft is pending.\n"
            "- discard: cancel the pending draft. Only if a draft is pending.\n"
            "- none: anything that is not an email action.\n"
            "Output ONLY the one line. No explanation."
```

(c) In `_parse_classification`, after the `ref_m` block and before the `ins_m` block, add TO extraction:
```python
        to_m = re.search(r"TO\s*=\s*([^|]+)", text)
        if to_m:
            to_val = to_m.group(1).strip()
            if to_val and to_val != "-":
                out["to"] = to_val
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_email_ops.py -k "new_with_to or forward" -v` → PASS. Then full file `pytest tests/test_email_ops.py -v` → all pass (existing parser tests unaffected since they have no TO field).

- [ ] **Step 5: Commit**

```
git add core/protocols/email_ops.py tests/test_email_ops.py
git commit -m "feat: classifier supports new/forward actions + TO field"
```

---

## Task 3: `_do_new` + `_do_forward` + recipient extraction + dispatch

**Files:**
- Modify: `core/protocols/email_ops.py`
- Test: `tests/test_email_ops.py` (append)

- [ ] **Step 1: Write failing tests** — Append to `tests/test_email_ops.py`:

```python
def test_new_creates_pending(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=new | REF=- | TO=bob@x.ca | INSTRUCTION=say hi")
    monkeypatch.setattr(ea, "draft_new", lambda session, to, intent: {
        "success": True, "draft_id": "n1", "to": "bob@x.ca",
        "subject": "Hello", "body": "Hi Bob, ..."})
    result = p.process_input("email bob@x.ca and say hi", {})
    assert result["intercept"] is True
    assert "Hi Bob" in result["response"]
    assert p._pending["kind"] == "new"
    assert p._pending["draft_id"] == "n1"
    assert p._pending["message_id"] is None


def test_new_without_address_asks(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=new | REF=- | TO=- | INSTRUCTION=say hi to bob")
    result = p.process_input("email bob and say hi", {})
    assert result["intercept"] is True
    assert "email address" in result["response"].lower()
    assert p._pending is None


def test_forward_creates_pending(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [
        {"id": "m1", "sender": "Ann <ann@x.ca>", "subject": "Numbers"}])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=forward | REF=1 | TO=sue@x.ca | INSTRUCTION=-")
    monkeypatch.setattr(ea, "draft_forward", lambda session, message_id, to: {
        "success": True, "draft_id": "f1", "to": "sue@x.ca",
        "subject": "Fwd: Numbers", "body": "---------- Forwarded message ----------"})
    result = p.process_input("forward the Ann email to sue@x.ca", {})
    assert result["intercept"] is True
    assert p._pending["kind"] == "forward"
    assert p._pending["draft_id"] == "f1"
    assert p._pending["message_id"] == "m1"
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_email_ops.py -k "test_new or test_forward_creates" -v` → FAIL (handlers not wired; `new`/`forward` not in dispatch dict → falls through, no intercept).

- [ ] **Step 3: Implement** — In `core/protocols/email_ops.py`:

(a) Add a module-level email regex near `_SEND_PHRASE`:
```python
_EMAIL_ADDR = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
```

(b) Add `new` and `forward` to the dispatch dict in `process_input` (the `handler = {...}.get(act)` dict):
```python
            "reply": self._do_reply,
            "new": self._do_new,
            "forward": self._do_forward,
            "send": self._do_send,
            "edit": self._do_edit,
            "discard": self._do_discard,
```

(c) Add these methods (place after `_do_reply`):
```python
    def _extract_recipient(self, action, text):
        cand = (action.get("to") or "").strip()
        m = _EMAIL_ADDR.search(cand)
        if m:
            return m.group(0)
        m = _EMAIL_ADDR.search(text or "")
        return m.group(0) if m else None

    def _do_new(self, action, text):
        to = self._extract_recipient(action, text)
        if not to:
            return "Who should I send it to? Give me an email address."
        intent = action.get("instruction") or text
        res = ea.draft_new(self._session, to, intent=intent)
        if not res.get("success"):
            return f"I couldn't draft that email: {res.get('error', 'unknown error')}"
        self._pending = {
            "draft_id": res["draft_id"], "kind": "new", "message_id": None,
            "to": res.get("to", to), "subject": res.get("subject", ""), "intent": intent,
        }
        return (
            f"Here's your email to {res.get('to', to)} —\n"
            f"Subject: {res.get('subject', '')}\n\n"
            f"{res.get('body', '')}\n\n"
            "Send it, tweak it, or discard?"
        )

    def _do_forward(self, action, text):
        message_id = self._resolve_ref(action)
        if not message_id:
            return "Which email should I forward?"
        to = self._extract_recipient(action, text)
        if not to:
            return "Who should I forward it to? Give me an email address."
        res = ea.draft_forward(self._session, message_id, to)
        if not res.get("success"):
            return f"I couldn't draft that forward: {res.get('error', 'unknown error')}"
        self._pending = {
            "draft_id": res["draft_id"], "kind": "forward", "message_id": message_id,
            "to": res.get("to", to), "subject": res.get("subject", ""), "intent": text,
        }
        return (
            f"Here's the forward to {res.get('to', to)} —\n"
            f"Subject: {res.get('subject', '')}\n\n"
            f"{res.get('body', '')}\n\n"
            "Send it, tweak it, or discard?"
        )
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_email_ops.py -k "test_new or test_forward_creates" -v` → PASS. Then full file → all pass.

- [ ] **Step 5: Commit**

```
git add core/protocols/email_ops.py tests/test_email_ops.py
git commit -m "feat: chat new-email + forward handlers (draft-then-confirm)"
```

---

## Task 4: Make `_do_edit` kind-aware

**Files:**
- Modify: `core/protocols/email_ops.py` (`_do_edit`)
- Test: `tests/test_email_ops.py` (append)

- [ ] **Step 1: Write failing test** — Append to `tests/test_email_ops.py`:

```python
def test_edit_new_draft_uses_draft_new(monkeypatch):
    p = _proto(_FakeSession())
    p._pending = {"draft_id": "n1", "kind": "new", "message_id": None,
                  "to": "bob@x.ca", "subject": "Hello", "intent": "say hi"}
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=edit | REF=- | TO=- | INSTRUCTION=make it formal")
    monkeypatch.setattr(gt, "gmail_delete_draft", lambda creds, draft_id: None)
    used = {}
    monkeypatch.setattr(ea, "draft_new", lambda session, to, intent: used.update(
        to=to, intent=intent) or {"success": True, "draft_id": "n2", "to": to,
                                   "subject": "Hello", "body": "Dear Bob, ..."})
    # draft_reply must NOT be used for a 'new' draft
    monkeypatch.setattr(ea, "draft_reply",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("used draft_reply")))
    result = p.process_input("make it formal", {})
    assert result["intercept"] is True
    assert p._pending["draft_id"] == "n2"
    assert used["to"] == "bob@x.ca"
    assert "make it formal" in used["intent"]
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_email_ops.py::test_edit_new_draft_uses_draft_new -v` → FAIL (current `_do_edit` calls `draft_reply` for every kind → hits the AssertionError throw).

- [ ] **Step 3: Implement** — Replace the re-draft line in `_do_edit`. The current method computes `new_intent` then does `res = ea.draft_reply(self._session, p["message_id"], intent=new_intent)`. Replace that single `res = ...` line with kind-aware branching:

```python
        kind = p.get("kind", "reply")
        if kind == "new":
            res = ea.draft_new(self._session, p.get("to", ""), intent=new_intent)
        elif kind == "forward":
            res = ea.draft_forward(self._session, p["message_id"], p.get("to", ""), note=new_intent)
        else:
            res = ea.draft_reply(self._session, p["message_id"], intent=new_intent)
```

(Everything else in `_do_edit` — the failure check, the delete-old-then-update-pending, the return — stays exactly as-is.)

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_email_ops.py::test_edit_new_draft_uses_draft_new -v` → PASS. Then full file `pytest tests/test_email_ops.py -v` → all pass (the existing reply-edit test still passes since kind defaults to "reply").

- [ ] **Step 5: Commit**

```
git add core/protocols/email_ops.py tests/test_email_ops.py
git commit -m "feat: kind-aware edit (re-draft new/forward correctly)"
```

---

## Task 5: Full suite + manual smoke

**Files:** none (verification).

- [ ] **Step 1: Run feature suites** — `pytest tests/test_email_ops.py tests/test_email_assistant.py -v` → ALL PASS.

- [ ] **Step 2: Manual smoke** (restart Aegis, COMMS chat, Google connected):
  1. "email <your own address> a quick note that the new email actions work" → Pike shows a new-email draft + "send it?".
  2. "make it more upbeat" → updated draft (re-drafted via draft_new).
  3. "send it" → "Sent to <address>." (real send to yourself — safe).
  4. "forward the <some sender> email to <your address>" → Pike shows a Fwd: draft.
  5. "discard it" → draft deleted.

- [ ] **Step 3: Done** — no commit. Fix-and-add-regression-test if smoke finds a bug.

---

## Self-review notes
- **Spec coverage (Phase 2):** new (Task 3), forward (Tasks 1+3), TO/classifier (Task 2), edit for new/forward (Task 4). Safety (draft-then-confirm, send guard, fall-through) inherited from Phase 1 unchanged.
- **Signatures:** `draft_new(session, to, intent, ...) → {success, draft_id, body, subject, to}`; new `draft_forward(session, message_id, to, note=None) → {success, draft_id, subject, to, body}`; `gmail_get_message → {subject, from, date, body, to}`. `_pending` gains `kind in {reply,new,forward}` and `message_id=None` for new.
- **No placeholders.**
