# Chat-driven Email (Phase 1: reply / send / edit / discard) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Pike reply to an inbox email from chat, hold it as a draft, then send it on an explicit "send it" — the John-Milton-Carlson use case, end to end.

**Architecture:** A new per-session `EmailOpsProtocol` sits in the chat `process_input` pipeline. On an email-ish message it runs gate → classify (one local LLM call) → resolve target → act, and **intercepts** the chat with the result. `reply` composes + saves a Gmail draft and stores it as `_pending`; `send` (only when a draft is pending) sends it; `edit` re-drafts; `discard` deletes. All LLM calls route through `email_assistant._llm`, extended with a `sensitivity`/`task` seam so the future hybrid local/cloud router is a one-point swap (email stays local-only by default).

**Tech Stack:** Python 3.12, pytest, FastAPI (existing), Ollama qwen3:8b (existing), the existing `core/email_assistant.py` + `core/protocols/google_tools.py` Gmail helpers.

**Scope note:** This plan is Phase 1 only (reply/send/edit/discard). Phase 2 (new/forward) and Phase 3 (mark-read/archive) from the design spec get their own follow-up plans. Phase 1 is independently shippable.

**Reference spec:** `docs/superpowers/specs/2026-06-30-chat-driven-email-actions-design.md`

---

## File Structure

- **Create** `core/protocols/email_ops.py` — `EmailOpsProtocol`: gate, classifier, target resolution, action handlers, `_pending` state. Single responsibility: turn a chat message into an email action.
- **Create** `tests/test_email_ops.py` — unit tests with `email_assistant`/`google_tools`/LLM mocked and a fake session.
- **Modify** `core/email_assistant.py` — extend `_llm` with `sensitivity`/`task` kwargs (the cloud-ready seam).
- **Modify** `core/session.py` — register `EmailOpsProtocol` and attach the session back-reference.
- **Modify** `ui/templates/index.html` — `_refreshAfterChat` refreshes the Mail panel so chat-driven drafts/sends show immediately.

---

## Task 1: Add the cloud-ready `_llm` seam

**Files:**
- Modify: `core/email_assistant.py` (the `_llm` function, currently at line 48)
- Test: `tests/test_email_ops.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_email_ops.py` with:

```python
import core.email_assistant as ea


def test_llm_accepts_sensitivity_and_task_kwargs(monkeypatch):
    """The seam must accept sensitivity/task without changing behavior."""
    captured = {}

    def fake_chat(model, messages):
        captured["model"] = model
        captured["messages"] = messages
        return {"message": {"content": "ok"}}

    monkeypatch.setattr(ea.ollama, "chat", fake_chat)

    out = ea._llm(
        [{"role": "user", "content": "hi"}],
        sensitivity="private",
        task="email_classify",
    )
    assert out == "ok"
    # kwargs are accepted but do not alter the local call today
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_email_ops.py::test_llm_accepts_sensitivity_and_task_kwargs -v`
Expected: FAIL with `TypeError: _llm() got an unexpected keyword argument 'sensitivity'`.

- [ ] **Step 3: Write minimal implementation**

In `core/email_assistant.py`, change `_llm` from:

```python
def _llm(messages: list[dict]) -> str:
    """Call the chat model and return the response content."""
    response = ollama.chat(
        model=CONFIG["model"]["chat"],
        messages=messages,
    )
    return response["message"]["content"]
```

to:

```python
def _llm(messages: list[dict], *, sensitivity: str = "local",
         task: str | None = None) -> str:
    """Call the chat model and return the response content.

    sensitivity / task are forward-compat hints for the planned hybrid
    local/cloud router (see aegis_strategic_direction memory). Today every
    call runs locally on Ollama regardless; the future router will read these
    to decide local vs cloud, treating sensitivity="private" as local-only by
    default. This keeps the seam in ONE place.
    """
    response = ollama.chat(
        model=CONFIG["model"]["chat"],
        messages=messages,
    )
    return response["message"]["content"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_email_ops.py::test_llm_accepts_sensitivity_and_task_kwargs -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/email_assistant.py tests/test_email_ops.py
git commit -m "feat: add sensitivity/task seam to email_assistant._llm (cloud-ready, local-only today)"
```

---

## Task 2: EmailOpsProtocol skeleton + gate

**Files:**
- Create: `core/protocols/email_ops.py`
- Test: `tests/test_email_ops.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_email_ops.py`:

```python
from core.protocols.email_ops import EmailOpsProtocol


class _FakeGoogleProto:
    def __init__(self, creds):
        self._creds = creds

    def _get_creds(self):
        return self._creds


class _FakeRegistry:
    def __init__(self, google):
        self._g = google

    def get(self, name):
        return self._g if name == "google" else None


class _FakeSession:
    def __init__(self, creds="CREDS"):
        self.protocol_registry = _FakeRegistry(_FakeGoogleProto(creds))
        self.system_prompt_base = "SYS"
        self.user_id = "tester"


def _proto(session=None):
    p = EmailOpsProtocol()
    if session is not None:
        p.attach_session(session)
    return p


def test_gate_skips_non_email_message():
    """No email cue, no pending draft -> do nothing (no intercept)."""
    p = _proto(_FakeSession())
    result = p.process_input("what's the weather tomorrow?", {})
    assert result["intercept"] is False
    assert result["input"] == "what's the weather tomorrow?"


def test_no_session_is_inert():
    """Without a session back-ref, the protocol never intercepts."""
    p = _proto()  # no session attached
    result = p.process_input("reply to john saying thanks", {})
    assert result["intercept"] is False


def test_empty_message_is_inert():
    p = _proto(_FakeSession())
    assert p.process_input("   ", {})["intercept"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_email_ops.py -k "gate or session or empty" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.protocols.email_ops'`.

- [ ] **Step 3: Write minimal implementation**

Create `core/protocols/email_ops.py`:

```python
"""Email Ops Protocol — Aegis AI

Phase 1: lets the conversational agent reply to inbox email, hold the draft,
and send it on explicit confirmation (draft-then-confirm). Flow per message:
gate -> classify (one local LLM call) -> resolve target -> act, intercepting
the chat with the result. Phases 2/3 (new/forward, mark-read/archive) extend
the action set later.

All LLM calls route through email_assistant._llm with sensitivity="private"
so the planned hybrid local/cloud router is a one-point swap; email stays
local-only by default.
"""
import logging
import re

from core.protocols.base import Protocol
from core import email_assistant as ea
from core.protocols import google_tools as gt

logger = logging.getLogger(__name__)

# Cheap gate: engage only when the message looks email-ish OR a draft is pending
# (so follow-ups like "send it" route here too).
_EMAIL_CUE = re.compile(
    r"\b(reply|respond|draft|compose|e-?mail|forward|inbox|archive|"
    r"mark\b.*\bread|send)\b",
    re.IGNORECASE,
)


class EmailOpsProtocol(Protocol):
    """Turns chat requests into email actions (Phase 1: reply/send/edit/discard)."""

    def __init__(self):
        super().__init__(
            name="email_ops",
            description="Chat-driven email actions (reply/send/edit/discard)",
            priority=Protocol.PRIORITY_NORMAL + 5,
        )
        self._session = None   # UserSession back-ref, set by session.py
        self._pending = None   # {draft_id, kind, message_id, to, subject, intent} or None
        self._id_map = {}      # inbox listing index -> message_id (set per classify)

    def attach_session(self, session):
        """Give the protocol access to its UserSession (creds + LLM)."""
        self._session = session

    def process_input(self, user_input, context):
        result = {"input": user_input, "context_injection": "",
                  "intercept": False, "response": ""}
        if not self._session:
            return result
        text = (user_input or "").strip()
        if not text:
            return result
        if not (self._pending or _EMAIL_CUE.search(text)):
            return result
        # Classification + dispatch arrive in later tasks.
        return result

    def process_output(self, response, context):
        return {"response": response, "suppress": False, "append": ""}

    def _intercept(self, result, response):
        result["intercept"] = True
        result["response"] = response
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_email_ops.py -k "gate or session or empty" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add core/protocols/email_ops.py tests/test_email_ops.py
git commit -m "feat: EmailOpsProtocol skeleton + gate"
```

---

## Task 3: Classifier prompt + parser

**Files:**
- Modify: `core/protocols/email_ops.py`
- Test: `tests/test_email_ops.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_email_ops.py`:

```python
def test_parse_classification_reply():
    p = _proto(_FakeSession())
    out = p._parse_classification(
        "ACTION=reply | REF=#2 | INSTRUCTION=confirm I got the money, thank him")
    assert out == {"action": "reply", "ref": "2",
                   "instruction": "confirm I got the money, thank him"}


def test_parse_classification_send_no_ref():
    p = _proto(_FakeSession())
    out = p._parse_classification("ACTION=send | REF=- | INSTRUCTION=-")
    assert out == {"action": "send"}


def test_parse_classification_strips_think_block():
    p = _proto(_FakeSession())
    raw = "<think>the user wants to reply</think>\nACTION=reply | REF=1 | INSTRUCTION=hi"
    out = p._parse_classification(raw)
    assert out["action"] == "reply"
    assert out["ref"] == "1"
    assert out["instruction"] == "hi"


def test_parse_classification_unknown_is_none():
    p = _proto(_FakeSession())
    assert p._parse_classification("ACTION=banana | REF=- | INSTRUCTION=-") == {"action": "none"}
    assert p._parse_classification("garbage with no action") == {"action": "none"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_email_ops.py -k parse_classification -v`
Expected: FAIL with `AttributeError: 'EmailOpsProtocol' object has no attribute '_parse_classification'`.

- [ ] **Step 3: Write minimal implementation**

Add these methods to `EmailOpsProtocol` in `core/protocols/email_ops.py`:

```python
    # ---- classification ----

    _ALLOWED_ACTIONS = ("reply", "send", "edit", "discard")

    def _build_classifier_prompt(self, text, listing, pending):
        return (
            "You classify a user's email request into ONE action.\n\n"
            "Recent inbox (most recent first):\n"
            f"{listing or '(inbox empty)'}\n\n"
            f"A draft is currently pending: {'yes' if pending else 'no'}\n\n"
            f'User said: "{text}"\n\n'
            "Reply with ONE line, exactly this format:\n"
            "ACTION=<reply|send|edit|discard|none> | REF=<inbox number or -> "
            "| INSTRUCTION=<what to say, or ->\n\n"
            "Rules:\n"
            "- reply: replying to an inbox email. REF = the inbox number. "
            "INSTRUCTION = what the reply should say.\n"
            "- send: send the pending draft. Only if a draft is pending.\n"
            "- edit: change the pending draft. INSTRUCTION = the change. "
            "Only if a draft is pending.\n"
            "- discard: cancel the pending draft. Only if a draft is pending.\n"
            "- none: anything that is not an email action.\n"
            "Output ONLY the one line. No explanation."
        )

    def _parse_classification(self, raw):
        text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
        m = re.search(r"ACTION\s*=\s*([a-zA-Z]+)", text)
        if not m:
            return {"action": "none"}
        action = m.group(1).strip().lower()
        if action not in self._ALLOWED_ACTIONS:
            return {"action": "none"}
        out = {"action": action}
        ref_m = re.search(r"REF\s*=\s*#?(\d+)", text)
        if ref_m:
            out["ref"] = ref_m.group(1)
        ins_m = re.search(r"INSTRUCTION\s*=\s*(.+)", text)
        if ins_m:
            ins = ins_m.group(1).strip()
            # strip a trailing " | KEY=..." if the model crammed extra fields after
            ins = re.split(r"\s*\|\s*[A-Z]+\s*=", ins)[0].strip()
            if ins and ins != "-":
                out["instruction"] = ins
        return out

    def _classify(self, text):
        listing, self._id_map = self._recent_inbox()
        prompt = self._build_classifier_prompt(text, listing, self._pending is not None)
        try:
            raw = ea._llm(
                [{"role": "system",
                  "content": "You are an email-intent classifier. Output ONE line only."},
                 {"role": "user", "content": prompt}],
                sensitivity="private", task="email_classify",
            )
        except Exception:
            logger.exception("Email classify LLM call failed")
            return {"action": "none"}
        return self._parse_classification(raw)
```

Note: `_recent_inbox` is implemented in Task 4; `_classify` is not called until Task 5, so these tests only exercise `_parse_classification`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_email_ops.py -k parse_classification -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add core/protocols/email_ops.py tests/test_email_ops.py
git commit -m "feat: email classifier prompt + defensive parser"
```

---

## Task 4: Recent-inbox listing + ref resolution

**Files:**
- Modify: `core/protocols/email_ops.py`
- Test: `tests/test_email_ops.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_email_ops.py`:

```python
def test_recent_inbox_builds_listing_and_idmap(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [
        {"id": "m1", "sender": "John <j@x.ca>", "subject": "Money"},
        {"id": "m2", "sender": "Jane <ja@x.ca>", "subject": "Lunch"},
    ])
    listing, id_map = p._recent_inbox()
    assert "#1" in listing and "John" in listing
    assert "#2" in listing and "Jane" in listing
    assert id_map == {1: "m1", 2: "m2"}


def test_recent_inbox_no_creds_is_empty(monkeypatch):
    p = _proto(_FakeSession(creds=None))
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: None)
    listing, id_map = p._recent_inbox()
    assert listing == ""
    assert id_map == {}


def test_resolve_ref_uses_idmap():
    p = _proto(_FakeSession())
    p._id_map = {1: "m1", 2: "m2"}
    assert p._resolve_ref({"ref": "2"}) == "m2"
    assert p._resolve_ref({"ref": "9"}) is None
    assert p._resolve_ref({}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_email_ops.py -k "recent_inbox or resolve_ref" -v`
Expected: FAIL with `AttributeError: ... has no attribute '_recent_inbox'`.

- [ ] **Step 3: Write minimal implementation**

Add to `EmailOpsProtocol`:

```python
    # ---- target resolution ----

    def _recent_inbox(self):
        """Return (listing_text, {index: message_id}) for the last ~15 inbox msgs."""
        creds = ea._creds_from_session(self._session)
        if not creds:
            return "", {}
        try:
            msgs = gt.gmail_list_messages(creds, max_results=15, categories=None)
        except Exception:
            logger.exception("Could not list inbox for classification")
            return "", {}
        lines, id_map = [], {}
        for i, m in enumerate(msgs, 1):
            id_map[i] = m.get("id")
            sender = (m.get("sender") or "?").strip()
            subject = (m.get("subject") or "(no subject)").strip()
            lines.append(f"#{i} · {sender} · {subject}")
        return "\n".join(lines), id_map

    def _resolve_ref(self, action):
        ref = action.get("ref")
        if ref and str(ref).isdigit():
            return self._id_map.get(int(ref))
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_email_ops.py -k "recent_inbox or resolve_ref" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add core/protocols/email_ops.py tests/test_email_ops.py
git commit -m "feat: recent-inbox listing + ref resolution for email ops"
```

---

## Task 5: Reply handler + dispatch wiring

**Files:**
- Modify: `core/protocols/email_ops.py`
- Test: `tests/test_email_ops.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_email_ops.py`:

```python
def _wire_reply(monkeypatch, draft_result):
    """Patch classify->reply, inbox, and draft_reply for an end-to-end reply."""
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [
        {"id": "m1", "sender": "John Milton Carlson <j@x.ca>", "subject": "Money"},
    ])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=reply | REF=1 | INSTRUCTION=thank him")
    monkeypatch.setattr(ea, "draft_reply",
                        lambda session, message_id, intent: draft_result)


def test_reply_creates_pending_and_shows_draft(monkeypatch):
    p = _proto(_FakeSession())
    _wire_reply(monkeypatch, {
        "success": True, "draft_id": "d1", "to": "John Milton Carlson <j@x.ca>",
        "subject": "Re: Money", "body": "Hi John, got the payment - thanks!",
    })
    result = p.process_input("reply to the John Milton Carlson email, thank him", {})
    assert result["intercept"] is True
    assert "John Milton Carlson" in result["response"]
    assert "got the payment" in result["response"]
    assert p._pending["draft_id"] == "d1"
    assert p._pending["message_id"] == "m1"
    assert p._pending["kind"] == "reply"


def test_reply_unauthorized(monkeypatch):
    p = _proto(_FakeSession(creds=None))
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: None)
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=reply | REF=1 | INSTRUCTION=hi")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    result = p.process_input("reply to john", {})
    assert result["intercept"] is True
    assert "connect google" in result["response"].lower()


def test_reply_draft_failure_is_reported(monkeypatch):
    p = _proto(_FakeSession())
    _wire_reply(monkeypatch, {"success": False, "error": "Gmail down"})
    result = p.process_input("reply to john, thank him", {})
    assert result["intercept"] is True
    assert "Gmail down" in result["response"]
    assert p._pending is None


def test_classified_none_falls_through(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=none | REF=- | INSTRUCTION=-")
    # "send" matches the gate cue but classifier says none -> normal chat
    result = p.process_input("send my regards to the team in person", {})
    assert result["intercept"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_email_ops.py -k "reply or falls_through" -v`
Expected: FAIL (reply path not wired; `process_input` still returns no intercept).

- [ ] **Step 3: Write minimal implementation**

Replace the placeholder tail of `process_input` (the `# Classification + dispatch arrive in later tasks.` line and its `return result`) with the dispatch logic, and add the reply handler:

```python
    def process_input(self, user_input, context):
        result = {"input": user_input, "context_injection": "",
                  "intercept": False, "response": ""}
        if not self._session:
            return result
        text = (user_input or "").strip()
        if not text:
            return result
        if not (self._pending or _EMAIL_CUE.search(text)):
            return result

        action = self._classify(text)
        act = action.get("action", "none")
        if act == "none":
            return result  # fall through to normal chat

        # Actions that operate on a pending draft are no-ops without one.
        if act in ("send", "edit", "discard") and not self._pending:
            return result

        if ea._creds_from_session(self._session) is None:
            return self._intercept(
                result,
                "I can't reach your email yet — connect Google in the Mail panel first.")

        handler = {
            "reply": self._do_reply,
            "send": self._do_send,
            "edit": self._do_edit,
            "discard": self._do_discard,
        }.get(act)
        if handler is None:
            return result  # not wired in Phase 1 -> normal chat

        try:
            response = handler(action, text)
        except Exception as e:
            logger.exception("Email action '%s' failed", act)
            response = f"Something went wrong with that email action: {e}"
        if response is None:
            return result  # handler declined -> normal chat
        return self._intercept(result, response)

    # ---- action handlers ----

    def _do_reply(self, action, text):
        message_id = self._resolve_ref(action)
        if not message_id:
            return "I couldn't tell which email you mean — which one should I reply to?"
        intent = action.get("instruction") or text
        res = ea.draft_reply(self._session, message_id, intent=intent)
        if not res.get("success"):
            return f"I couldn't draft that reply: {res.get('error', 'unknown error')}"
        self._pending = {
            "draft_id": res["draft_id"],
            "kind": "reply",
            "message_id": message_id,
            "to": res.get("to", ""),
            "subject": res.get("subject", ""),
            "intent": intent,
        }
        return (
            f"Here's your reply to {res.get('to', 'them')} —\n"
            f"Subject: {res.get('subject', '')}\n\n"
            f"{res.get('body', '')}\n\n"
            "Send it, tweak it, or discard?"
        )
```

Note: `_do_send`, `_do_edit`, `_do_discard` are added in Task 6. Until then the dispatch dict references them; define lightweight versions now to avoid `AttributeError` — add these stubs too (they're fully implemented in Task 6, but must exist for the dispatch dict to resolve):

```python
    def _do_send(self, action, text):
        raise NotImplementedError

    def _do_edit(self, action, text):
        raise NotImplementedError

    def _do_discard(self, action, text):
        raise NotImplementedError
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_email_ops.py -k "reply or falls_through" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add core/protocols/email_ops.py tests/test_email_ops.py
git commit -m "feat: email reply handler + classify->dispatch wiring"
```

---

## Task 6: Send / edit / discard handlers

**Files:**
- Modify: `core/protocols/email_ops.py`
- Test: `tests/test_email_ops.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_email_ops.py`:

```python
def _pending_reply(p):
    p._pending = {"draft_id": "d1", "kind": "reply", "message_id": "m1",
                  "to": "John <j@x.ca>", "subject": "Re: Money", "intent": "thank him"}


def test_send_sends_pending_and_clears(monkeypatch):
    p = _proto(_FakeSession())
    _pending_reply(p)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=send | REF=- | INSTRUCTION=-")
    sent = {}
    monkeypatch.setattr(ea, "send_draft",
                        lambda session, draft_id: sent.setdefault("id", draft_id) or {"success": True})
    result = p.process_input("send it", {})
    assert result["intercept"] is True
    assert "sent to" in result["response"].lower()
    assert sent["id"] == "d1"
    assert p._pending is None


def test_send_with_no_pending_falls_through(monkeypatch):
    p = _proto(_FakeSession())  # no pending
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=send | REF=- | INSTRUCTION=-")
    result = p.process_input("send it", {})
    assert result["intercept"] is False


def test_send_failure_keeps_pending(monkeypatch):
    p = _proto(_FakeSession())
    _pending_reply(p)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=send | REF=- | INSTRUCTION=-")
    monkeypatch.setattr(ea, "send_draft",
                        lambda session, draft_id: {"success": False, "error": "no scope"})
    result = p.process_input("send it", {})
    assert "no scope" in result["response"]
    assert p._pending is not None  # not cleared on failure


def test_discard_deletes_and_clears(monkeypatch):
    p = _proto(_FakeSession())
    _pending_reply(p)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=discard | REF=- | INSTRUCTION=-")
    deleted = {}
    monkeypatch.setattr(gt, "gmail_delete_draft",
                        lambda creds, draft_id: deleted.setdefault("id", draft_id))
    result = p.process_input("discard that", {})
    assert "discard" in result["response"].lower()
    assert deleted["id"] == "d1"
    assert p._pending is None


def test_edit_redrafts_and_updates_pending(monkeypatch):
    p = _proto(_FakeSession())
    _pending_reply(p)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=edit | REF=- | INSTRUCTION=make it more formal")
    monkeypatch.setattr(gt, "gmail_delete_draft", lambda creds, draft_id: None)
    monkeypatch.setattr(ea, "draft_reply", lambda session, message_id, intent: {
        "success": True, "draft_id": "d2", "to": "John <j@x.ca>",
        "subject": "Re: Money", "body": "Dear John, I confirm receipt. Regards.",
    })
    result = p.process_input("make it more formal", {})
    assert result["intercept"] is True
    assert "Dear John" in result["response"]
    assert p._pending["draft_id"] == "d2"
    assert "more formal" in p._pending["intent"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_email_ops.py -k "send or discard or edit" -v`
Expected: FAIL with `NotImplementedError` (the Task 5 stubs).

- [ ] **Step 3: Write minimal implementation**

Replace the three stub methods in `core/protocols/email_ops.py` with:

```python
    def _do_send(self, action, text):
        res = ea.send_draft(self._session, self._pending["draft_id"])
        if not res.get("success"):
            return f"I couldn't send it: {res.get('error', 'unknown error')}"
        to = self._pending.get("to", "them")
        self._pending = None
        return f"Sent to {to}."

    def _do_discard(self, action, text):
        creds = ea._creds_from_session(self._session)
        try:
            gt.gmail_delete_draft(creds, self._pending["draft_id"])
        except Exception:
            logger.exception("Could not delete discarded draft")
        self._pending = None
        return "Discarded that draft."

    def _do_edit(self, action, text):
        p = self._pending
        change = action.get("instruction") or text
        new_intent = f"{p.get('intent', '')} | revision: {change}".strip(" |")
        creds = ea._creds_from_session(self._session)
        try:
            gt.gmail_delete_draft(creds, p["draft_id"])
        except Exception:
            logger.exception("Could not delete superseded draft")
        res = ea.draft_reply(self._session, p["message_id"], intent=new_intent)
        if not res.get("success"):
            return f"I couldn't revise it: {res.get('error', 'unknown error')}"
        self._pending = {
            **p,
            "draft_id": res["draft_id"],
            "to": res.get("to", p.get("to", "")),
            "subject": res.get("subject", p.get("subject", "")),
            "intent": new_intent,
        }
        return (
            f"Updated draft to {res.get('to', 'them')} —\n\n"
            f"{res.get('body', '')}\n\n"
            "Send it, tweak it, or discard?"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_email_ops.py -k "send or discard or edit" -v`
Expected: PASS (5 tests). Then run the whole file: `pytest tests/test_email_ops.py -v` — all green.

- [ ] **Step 5: Commit**

```bash
git add core/protocols/email_ops.py tests/test_email_ops.py
git commit -m "feat: email send/edit/discard handlers (draft-then-confirm)"
```

---

## Task 7: Register the protocol in the session

**Files:**
- Modify: `core/session.py` (import near line 30; register near line 104)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_email_ops.py`:

```python
def test_session_registers_email_ops_with_backref():
    """A real UserSession must register email_ops and attach itself."""
    from core.session import UserSession
    s = UserSession("plan_test_user")
    proto = s.protocol_registry.get("email_ops")
    assert proto is not None
    assert proto._session is s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_email_ops.py::test_session_registers_email_ops_with_backref -v`
Expected: FAIL — `get("email_ops")` returns `None`.

- [ ] **Step 3: Write minimal implementation**

In `core/session.py`, add the import alongside the other protocol imports (after line 29, `from core.protocols.creative import CreativeProtocol`):

```python
from core.protocols.email_ops import EmailOpsProtocol
```

Then, immediately after the `CreativeProtocol` registration (line 106, `self.protocol_registry.register(CreativeProtocol())`), add:

```python
        # Chat-driven email actions — needs a session back-ref for Gmail creds.
        email_ops = EmailOpsProtocol()
        email_ops.attach_session(self)
        self.protocol_registry.register(email_ops)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_email_ops.py::test_session_registers_email_ops_with_backref -v`
Expected: PASS.

(If this test is slow or pulls heavy deps, it still must pass; it exercises real session wiring, which is the point.)

- [ ] **Step 5: Commit**

```bash
git add core/session.py tests/test_email_ops.py
git commit -m "feat: register EmailOpsProtocol in the session with back-ref"
```

---

## Task 8: Refresh the Mail panel after chat actions

**Files:**
- Modify: `ui/templates/index.html` (the `_refreshAfterChat` function, ~line 9886)

- [ ] **Step 1: Read the current function**

Run: `grep -n "function _refreshAfterChat" ui/templates/index.html` and read the function body. Confirm it calls helpers like `_isPanelOpen('taskPanel')` and `loadTasks()`. Confirm the Mail helpers exist: `grep -n "function loadMailDrafts\|function loadInboxDigest\|_mailActiveTab" ui/templates/index.html`.

- [ ] **Step 2: Add the Mail refresh**

Inside `_refreshAfterChat`, after the existing panel refreshes (e.g. after the `contactPanel` block) and before the closing brace, insert:

```javascript
    // Mail panel: a chat-driven email action may have created/sent a draft or
    // changed the inbox. Refresh whichever mail tab is showing.
    if (typeof _isPanelOpen === 'function' && _isPanelOpen('mailPanel')) {
        if (_mailActiveTab === 'drafts' && typeof loadMailDrafts === 'function') {
            _mailDraftsLoaded = false;
            loadMailDrafts();
        } else if (_mailActiveTab === 'inbox' && typeof loadInboxDigest === 'function') {
            loadInboxDigest(true);
        }
    }
```

(If `_isPanelOpen` is not the helper name used in this file, use the same panel-open check the other branches in `_refreshAfterChat` use — match the existing pattern exactly.)

- [ ] **Step 3: Manual verification (no unit test — frontend)**

This is a renderer-only change; verify by reload, not pytest. After Task 9's smoke test, confirm: with the Mail panel open on DRAFTS, ask Pike in chat to draft a reply → the new draft appears in DRAFTS without manually switching tabs.

- [ ] **Step 4: Commit**

```bash
git add ui/templates/index.html
git commit -m "feat: refresh Mail panel after chat-driven email actions"
```

---

## Task 9: Full-suite test run + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit suite**

Run: `pytest tests/test_email_ops.py -v`
Expected: ALL PASS.

- [ ] **Step 2: Run the broader email tests to confirm no regressions**

Run: `pytest tests/test_email_assistant.py tests/test_email_ops.py -v`
Expected: ALL PASS.

- [ ] **Step 3: Manual smoke (the real use case)**

Restart Aegis (loads the new protocol). In the COMMS chat, with Google connected:
1. "reply to the John Milton Carlson email saying I received the money, thanks" → Pike shows a draft + "Send it, tweak it, or discard?" and the draft appears in Mail > DRAFTS.
2. "make it a bit warmer" → Pike shows an updated draft (old draft replaced).
3. "send it" → Pike: "Sent to John Milton Carlson." and the draft leaves DRAFTS.
4. Confirm a non-email message ("what's the weather?") still gets a normal Pike reply (no interception).

- [ ] **Step 4: Done**

No commit (verification task). If smoke reveals a bug, fix it in the relevant task's file and add a regression test to `tests/test_email_ops.py`.

---

## Self-review notes

- **Spec coverage (Phase 1):** gate (Task 2), classify (Task 3), resolve (Task 4), reply/send/edit/discard (Tasks 5–6), safety no-send-without-pending (Task 5 dispatch guard + Task 6 tests), session wiring (Task 7), frontend refresh (Task 8), `_llm` seam + `sensitivity="private"` (Task 1, used in Task 3 `_classify`). Phases 2/3 are explicitly out of scope (separate plans).
- **Function/return-shape consistency:** `draft_reply` returns `{success, draft_id, body, subject, to, ...}`; `send_draft` returns `{success, ...}`; `gmail_delete_draft(creds, draft_id)`, `gmail_list_messages(creds, max_results, categories)` match `core/protocols/google_tools.py`. `_pending` keys (`draft_id, kind, message_id, to, subject, intent`) are written in Task 5 and read consistently in Task 6.
- **No placeholders:** every code step is complete; no TODO/TBD.
