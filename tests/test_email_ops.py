import json

import core.email_assistant as ea
from core.accounts.manager import AccountManager
from core.protocols import google_tools as gt


def test_llm_accepts_sensitivity_and_task_kwargs(monkeypatch):
    """The seam must accept sensitivity/task and delegate to the LLM router."""
    captured = {}

    def fake_chat(messages, *, sensitivity, task=None, **kw):
        captured["messages"] = messages
        captured["sensitivity"] = sensitivity
        captured["task"] = task
        return "ok"

    # _llm now routes through core.llm.chat (imported as _router_chat), not
    # ollama directly — patch the router seam.
    monkeypatch.setattr(ea, "_router_chat", fake_chat)

    out = ea._llm(
        [{"role": "user", "content": "hi"}],
        sensitivity="private",
        task="email_classify",
    )
    assert out == "ok"
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["sensitivity"] == "private"
    assert captured["task"] == "email_classify"


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


def test_recent_inbox_builds_listing_and_idmap(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
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
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: None)
    listing, id_map = p._recent_inbox()
    assert listing == ""
    assert id_map == {}


def test_resolve_ref_uses_idmap():
    p = _proto(_FakeSession())
    p._id_map = {1: "m1", 2: "m2"}
    assert p._resolve_ref({"ref": "2"}) == "m2"
    assert p._resolve_ref({"ref": "9"}) is None
    assert p._resolve_ref({}) is None


def _wire_reply(monkeypatch, draft_result):
    """Patch classify->reply, inbox, and draft_reply for an end-to-end reply."""
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [
        {"id": "m1", "sender": "John Milton Carlson <j@x.ca>", "subject": "Money"},
    ])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=reply | REF=1 | INSTRUCTION=thank him")
    monkeypatch.setattr(ea, "draft_reply",
                        lambda session, message_id, intent, account_id=None: draft_result)


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
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: None)
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
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=none | REF=- | INSTRUCTION=-")
    # "send" matches the gate cue but classifier says none -> normal chat
    result = p.process_input("send my regards to the team in person", {})
    assert result["intercept"] is False


def _pending_reply(p):
    p._pending = {"draft_id": "d1", "kind": "reply", "message_id": "m1",
                  "to": "John <j@x.ca>", "subject": "Re: Money", "intent": "thank him"}


def test_send_sends_pending_and_clears(monkeypatch):
    p = _proto(_FakeSession())
    _pending_reply(p)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=send | REF=- | INSTRUCTION=-")
    sent = {}
    monkeypatch.setattr(ea, "send_draft",
                        lambda session, draft_id, account_id=None: sent.update({"id": draft_id}) or {"success": True})
    result = p.process_input("send it", {})
    assert result["intercept"] is True
    assert "sent to" in result["response"].lower()
    assert sent["id"] == "d1"
    assert p._pending is None


def test_send_with_no_pending_falls_through(monkeypatch):
    p = _proto(_FakeSession())  # no pending
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=send | REF=- | INSTRUCTION=-")
    result = p.process_input("send it", {})
    assert result["intercept"] is False


def test_send_failure_keeps_pending(monkeypatch):
    p = _proto(_FakeSession())
    _pending_reply(p)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=send | REF=- | INSTRUCTION=-")
    monkeypatch.setattr(ea, "send_draft",
                        lambda session, draft_id, account_id=None: {"success": False, "error": "no scope"})
    result = p.process_input("send it", {})
    assert "no scope" in result["response"]
    assert p._pending is not None  # not cleared on failure


def test_discard_deletes_and_clears(monkeypatch):
    p = _proto(_FakeSession())
    _pending_reply(p)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
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
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=edit | REF=- | INSTRUCTION=make it more formal")
    monkeypatch.setattr(gt, "gmail_delete_draft", lambda creds, draft_id: None)
    monkeypatch.setattr(ea, "draft_reply", lambda session, message_id, intent, account_id=None: {
        "success": True, "draft_id": "d2", "to": "John <j@x.ca>",
        "subject": "Re: Money", "body": "Dear John, I confirm receipt. Regards.",
    })
    result = p.process_input("make it more formal", {})
    assert result["intercept"] is True
    assert "Dear John" in result["response"]
    assert p._pending["draft_id"] == "d2"
    assert "more formal" in p._pending["intent"]


def test_session_registers_email_ops_with_backref():
    """A real UserSession must register email_ops and attach itself."""
    from core.session import UserSession
    s = UserSession("plan_test_user")
    proto = s.protocol_registry.get("email_ops")
    assert proto is not None
    assert proto._session is s


def test_edit_failure_preserves_old_draft(monkeypatch):
    p = _proto(_FakeSession())
    _pending_reply(p)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=edit | REF=- | INSTRUCTION=make it formal")
    deleted = {"called": False}
    monkeypatch.setattr(gt, "gmail_delete_draft",
                        lambda creds, draft_id: deleted.update(called=True))
    monkeypatch.setattr(ea, "draft_reply",
                        lambda session, message_id, intent, account_id=None: {"success": False, "error": "LLM down"})
    result = p.process_input("make it formal", {})
    assert "LLM down" in result["response"]
    assert deleted["called"] is False          # old draft NOT deleted on failure
    assert p._pending["draft_id"] == "d1"        # pending unchanged


def test_send_requires_literal_phrase(monkeypatch):
    p = _proto(_FakeSession())
    _pending_reply(p)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=send | REF=- | INSTRUCTION=-")
    called = {"sent": False}
    monkeypatch.setattr(ea, "send_draft",
                        lambda session, draft_id, account_id=None: called.update(sent=True) or {"success": True})
    result = p.process_input("yeah ok do it", {})   # affirmative but no send word
    assert called["sent"] is False                   # must NOT send
    assert "confirm" in result["response"].lower()
    assert p._pending is not None                     # draft still held


def _wire_send_classifier(monkeypatch, p):
    """Pending draft + a classifier that (wrongly) says ACTION=send."""
    _pending_reply(p)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=send | REF=- | INSTRUCTION=-")
    called = {"sent": False}
    monkeypatch.setattr(ea, "send_draft",
                        lambda session, draft_id, account_id=None: called.update(sent=True) or {"success": True})
    return called


def test_send_not_fired_by_a_question(monkeypatch):
    """A send word inside a QUESTION must re-confirm, never transmit — even if
    the classifier maps the message to ACTION=send."""
    p = _proto(_FakeSession())
    called = _wire_send_classifier(monkeypatch, p)
    result = p.process_input("should I send it to my boss or wait?", {})
    assert called["sent"] is False          # must NOT send while deliberating
    assert "confirm" in result["response"].lower()
    assert p._pending is not None


def test_send_not_fired_by_negation(monkeypatch):
    """'don't send that' contains the send word but must NOT transmit."""
    p = _proto(_FakeSession())
    called = _wire_send_classifier(monkeypatch, p)
    result = p.process_input("actually don't send that yet", {})
    assert called["sent"] is False
    assert p._pending is not None


def test_classify_skips_inbox_when_pending(monkeypatch):
    p = _proto(_FakeSession())
    _pending_reply(p)
    called = {"inbox": False}
    def _boom(self):
        called["inbox"] = True
        return "", {}
    monkeypatch.setattr(EmailOpsProtocol, "_recent_inbox", _boom)
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=discard | REF=- | INSTRUCTION=-")
    p._classify("discard that")
    assert called["inbox"] is False   # inbox NOT fetched while a draft is pending


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


def test_new_creates_pending(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=new | REF=- | TO=bob@x.ca | INSTRUCTION=say hi")
    monkeypatch.setattr(ea, "draft_new", lambda session, to, intent, account_id=None: {
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
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=new | REF=- | TO=- | INSTRUCTION=say hi to bob")
    result = p.process_input("email bob and say hi", {})
    assert result["intercept"] is True
    assert "email address" in result["response"].lower()
    assert p._pending is None


def test_forward_creates_pending(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [
        {"id": "m1", "sender": "Ann <ann@x.ca>", "subject": "Numbers"}])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=forward | REF=1 | TO=sue@x.ca | INSTRUCTION=-")
    monkeypatch.setattr(ea, "draft_forward", lambda session, message_id, to, account_id=None: {
        "success": True, "draft_id": "f1", "to": "sue@x.ca",
        "subject": "Fwd: Numbers", "body": "---------- Forwarded message ----------"})
    result = p.process_input("forward the Ann email to sue@x.ca", {})
    assert result["intercept"] is True
    assert p._pending["kind"] == "forward"
    assert p._pending["draft_id"] == "f1"
    assert p._pending["message_id"] == "m1"


def test_edit_new_draft_uses_draft_new(monkeypatch):
    p = _proto(_FakeSession())
    p._pending = {"draft_id": "n1", "kind": "new", "message_id": None,
                  "to": "bob@x.ca", "subject": "Hello", "intent": "say hi"}
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=edit | REF=- | TO=- | INSTRUCTION=make it formal")
    monkeypatch.setattr(gt, "gmail_delete_draft", lambda creds, draft_id: None)
    used = {}
    monkeypatch.setattr(ea, "draft_new", lambda session, to, intent, account_id=None: used.update(
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


def test_edit_forward_is_blocked(monkeypatch):
    p = _proto(_FakeSession())
    p._pending = {"draft_id": "f1", "kind": "forward", "message_id": "m1",
                  "to": "sue@x.ca", "subject": "Fwd: X", "intent": "forward the ann email to sue@x.ca"}
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=edit | REF=- | TO=- | INSTRUCTION=make it formal")
    called = {"redrafted": False}
    monkeypatch.setattr(ea, "draft_forward",
                        lambda *a, **k: called.update(redrafted=True) or {"success": True})
    result = p.process_input("make it formal", {})
    assert "reword" in result["response"].lower() or "forward again" in result["response"].lower()
    assert called["redrafted"] is False
    assert p._pending["draft_id"] == "f1"


def test_new_ignores_address_in_body(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [])
    # classifier returns no recipient (TO=-) though the body mentions an address
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=new | REF=- | TO=- | INSTRUCTION=tell boss spam@x.com keeps emailing")
    called = {"drafted": False}
    monkeypatch.setattr(ea, "draft_new", lambda *a, **k: called.update(drafted=True) or {"success": True})
    result = p.process_input("email my boss that spam@x.com keeps emailing me", {})
    assert result["intercept"] is True
    assert "email address" in result["response"].lower()   # asks, does not target spam@x.com
    assert called["drafted"] is False
    assert p._pending is None


def test_mark_read_action(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
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
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
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
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages", lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=mark_read | REF=- | TO=- | INSTRUCTION=-")
    result = p.process_input("mark it read", {})
    assert result["intercept"] is True
    assert "which email" in result["response"].lower()


# --- Task 10: ACCOUNT= classifier field + account resolution -------------

from tests.test_briefing_accounts import FakeAccounts


def _session_with_accounts(accounts):
    s = _FakeSession()
    s.accounts = accounts
    return s


def test_classifier_prompt_lists_accounts():
    accounts = FakeAccounts(
        accounts=[
            {"id": "google-personal", "label": "Personal",
             "email": "dustin.jr.lange@gmail.com"},
            {"id": "google-stitch", "label": "SwitchStitch",
             "email": "TheSwitchStitch@gmail.com"},
        ],
        creds_by_id={},
    )
    p = _proto(_session_with_accounts(accounts))
    prompt = p._build_classifier_prompt("hi", "", False)
    assert "ACCOUNT=<account id or ->" in prompt
    assert "google-personal" in prompt
    assert "SwitchStitch" in prompt
    # Privacy: email addresses must NOT be shipped into the classifier prompt —
    # the classifier only picks an id/label; resolve() matches emails locally.
    assert "dustin.jr.lange@gmail.com" not in prompt
    assert "TheSwitchStitch@gmail.com" not in prompt


def test_classifier_prompt_no_accounts_unchanged():
    """Un-migrated session (no accounts attr) gets no account block."""
    p = _proto(_FakeSession())
    prompt = p._build_classifier_prompt("hi", "", False)
    assert "Linked accounts" not in prompt


def test_classifier_prompt_empty_registry_unchanged():
    """Accounts layer present but EMPTY -> original prompt (no ACCOUNT token)."""
    p = _proto(_session_with_accounts(FakeAccounts(accounts=[], creds_by_id={})))
    prompt = p._build_classifier_prompt("hi", "", False)
    assert "ACCOUNT=" not in prompt
    assert "Linked accounts" not in prompt


def test_parse_classification_account():
    p = _proto(_FakeSession())
    out = p._parse_classification(
        "ACTION=new | REF=- | TO=bob@x.com | ACCOUNT=google-stitch | INSTRUCTION=say hi")
    assert out["account"] == "google-stitch"
    assert out["instruction"] == "say hi"
    assert out["to"] == "bob@x.com"


def test_parse_classification_account_dash_absent():
    p = _proto(_FakeSession())
    out = p._parse_classification(
        "ACTION=new | REF=- | TO=bob@x.com | ACCOUNT=- | INSTRUCTION=say hi")
    assert "account" not in out


def test_parse_classification_account_out_of_order():
    """ACCOUNT after INSTRUCTION: still parsed, instruction stays clean."""
    p = _proto(_FakeSession())
    out = p._parse_classification(
        "ACTION=new | INSTRUCTION=say hi | ACCOUNT=google-stitch")
    assert out["account"] == "google-stitch"
    assert out["instruction"] == "say hi"


def _real_accounts(tmp_path):
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": [
        {"id": "google-personal", "label": "Personal",
         "email": "dustin.jr.lange@gmail.com", "is_default": True},
        {"id": "google-stitch", "label": "SwitchStitch",
         "email": "TheSwitchStitch@gmail.com"},
    ]}), encoding="utf-8")
    return AccountManager(tmp_path)


def test_resolve_account_explicit_hint(tmp_path):
    p = _proto(_session_with_accounts(_real_accounts(tmp_path)))
    acct, _ = p._resolve_account({"account": "stitch"})
    assert acct["id"] == "google-stitch"


def test_resolve_account_unknown_falls_back_to_default(tmp_path):
    p = _proto(_session_with_accounts(_real_accounts(tmp_path)))
    acct, _ = p._resolve_account({"account": "nonsense"})
    assert acct["id"] == "google-personal"


def test_resolve_account_absent_uses_default(tmp_path):
    p = _proto(_session_with_accounts(_real_accounts(tmp_path)))
    acct, _ = p._resolve_account({})
    assert acct["id"] == "google-personal"


def test_resolve_account_no_layer_returns_none():
    p = _proto(_FakeSession())  # session has no .accounts
    acct, note = p._resolve_account({"account": "stitch"})
    assert acct is None
    assert note == ""


def test_resolve_account_note_fires_on_unmatched_hint(tmp_path):
    """A hint that doesn't resolve produces a non-empty note mentioning the hint
    and the fallback label; matching hint or absent hint yields note == ''."""
    p = _proto(_session_with_accounts(_real_accounts(tmp_path)))
    # unmatched hint -> fallback to default with a note
    acct, note = p._resolve_account({"account": "nonexistent-xyz"})
    assert acct is not None
    assert acct["id"] == "google-personal"
    assert note  # non-empty
    assert "nonexistent-xyz" in note
    assert "Personal" in note

    # matching hint -> no note
    acct2, note2 = p._resolve_account({"account": "stitch"})
    assert acct2["id"] == "google-stitch"
    assert note2 == ""

    # absent hint -> no note
    acct3, note3 = p._resolve_account({})
    assert acct3["id"] == "google-personal"
    assert note3 == ""


# --- Task 11: represent-as / account named in draft + send threading -----


def _stitch_accounts(tmp_path):
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": [
        {"id": "google-stitch", "label": "SwitchStitch",
         "email": "TheSwitchStitch@gmail.com", "is_default": True,
         "represent_as": {"name": "Switch", "signoff": "Switch",
                          "tone_hint": "maker-brand"}},
    ]}), encoding="utf-8")
    return AccountManager(tmp_path)


def test_do_new_preview_states_account_and_stores_id(tmp_path, monkeypatch):
    p = _proto(_session_with_accounts(_stitch_accounts(tmp_path)))
    monkeypatch.setattr(ea, "draft_new",
                        lambda session, to, intent, account_id=None: {
                            "success": True, "draft_id": "n1", "to": to,
                            "subject": "Hi", "body": "Hi Bob"})
    resp = p._do_new({"to": "bob@x.com", "instruction": "say hi"},
                     "email bob@x.com say hi")
    assert "From: SwitchStitch (TheSwitchStitch@gmail.com)" in resp
    assert p._pending["account_id"] == "google-stitch"


def test_do_send_threads_composing_account_id(tmp_path, monkeypatch):
    p = _proto(_session_with_accounts(_stitch_accounts(tmp_path)))
    monkeypatch.setattr(ea, "draft_new",
                        lambda session, to, intent, account_id=None: {
                            "success": True, "draft_id": "n1", "to": to,
                            "subject": "Hi", "body": "Hi Bob"})
    p._do_new({"to": "bob@x.com", "instruction": "say hi"}, "email bob@x.com say hi")
    assert p._pending["account_id"] == "google-stitch"
    captured = {}
    monkeypatch.setattr(ea, "send_draft",
                        lambda session, draft_id, account_id=None: captured.update(
                            draft_id=draft_id, account_id=account_id) or {"success": True})
    p._do_send({}, "send it")
    assert captured["account_id"] == "google-stitch"


def test_discard_deletes_with_composing_account_creds(monkeypatch):
    """A draft made under a non-default account must be deleted with THAT
    account's creds, not the default's."""
    p = _proto(_FakeSession())
    p._pending = {"draft_id": "d1", "kind": "reply", "message_id": "m1",
                  "to": "x@y.com", "subject": "Re", "intent": "hi",
                  "account_id": "google-stitch"}
    # creds are keyed to the account_id so we can prove routing.
    monkeypatch.setattr(ea, "_creds_from_session",
                        lambda s, account_id=None: f"CREDS::{account_id}")
    captured = {}
    monkeypatch.setattr(gt, "gmail_delete_draft",
                        lambda creds, draft_id: captured.update(creds=creds, draft_id=draft_id))
    p._do_discard({}, "discard it")
    assert captured["creds"] == "CREDS::google-stitch"
    assert captured["draft_id"] == "d1"
    assert p._pending is None


def test_edit_deletes_old_draft_with_composing_account_creds(monkeypatch):
    """The superseded draft delete in _do_edit uses the pending account's creds."""
    p = _proto(_FakeSession())
    p._pending = {"draft_id": "d1", "kind": "reply", "message_id": "m1",
                  "to": "x@y.com", "subject": "Re", "intent": "hi",
                  "account_id": "google-stitch"}
    monkeypatch.setattr(ea, "_creds_from_session",
                        lambda s, account_id=None: f"CREDS::{account_id}")
    monkeypatch.setattr(ea, "draft_reply",
                        lambda session, message_id, intent, account_id=None: {
                            "success": True, "draft_id": "d2", "to": "x@y.com",
                            "subject": "Re", "body": "redone"})
    captured = {}
    monkeypatch.setattr(gt, "gmail_delete_draft",
                        lambda creds, draft_id: captured.update(creds=creds, draft_id=draft_id))
    p._do_edit({"instruction": "make it formal"}, "make it formal")
    assert captured["creds"] == "CREDS::google-stitch"   # old draft deleted under its account
    assert captured["draft_id"] == "d1"
    assert p._pending["draft_id"] == "d2"
    assert p._pending["account_id"] == "google-stitch"   # account preserved across edit


# --- summarize/read inbox from chat (2026-07-12 false-refusal fix) --------


def test_parse_classification_summarize():
    p = _proto(_FakeSession())
    out = p._parse_classification("ACTION=summarize | REF=- | TO=- | INSTRUCTION=-")
    assert out == {"action": "summarize"}


def test_parse_classification_treats_arrow_placeholder_as_blank():
    """The model often echoes the template placeholder '->' (dash + the '>'
    from 'or ->'). It must be read as absent, not stored as a real value —
    else '->' pollutes a draft's recipient/intent."""
    p = _proto(_FakeSession())
    out = p._parse_classification(
        "ACTION=summarize | REF=-> | TO=-> | ACCOUNT=-> | INSTRUCTION=->")
    assert out == {"action": "summarize"}
    # and a reply whose fields are all placeholders keeps no garbage intent
    out2 = p._parse_classification("ACTION=reply | REF=2 | TO=-> | INSTRUCTION=->")
    assert out2 == {"action": "reply", "ref": "2"}


def _msgs(n, *, unread=True):
    return [
        {"id": f"m{i}", "sender": f"Person{i} <p{i}@x.ca>",
         "subject": f"Subject {i}", "snippet": f"Body preview {i}", "unread": unread}
        for i in range(1, n + 1)
    ]


def test_summarize_builds_itemized_numbered_list(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results=10, categories=None, extra_query=None:
                        _msgs(5)[:max_results])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "Person1's Subject 1 looks most urgent.")
    resp = p._do_summarize({}, "summarize my 5 unread emails")
    for i in range(1, 6):
        assert f"{i}. Person{i} — Subject {i}" in resp   # itemized, sender cleaned
    assert "most urgent" in resp                           # triage header present
    assert p._summary_map == {1: "m1", 2: "m2", 3: "m3", 4: "m4", 5: "m5"}


def test_summarize_honors_count_and_unread_query(monkeypatch):
    p = _proto(_FakeSession())
    captured = {}
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    def fake_list(creds, max_results=10, categories=None, extra_query=None):
        captured.update(n=max_results, q=extra_query)
        return _msgs(1)
    monkeypatch.setattr(gt, "gmail_list_messages", fake_list)
    monkeypatch.setattr(ea, "_llm", lambda messages, **kw: "routine.")
    p._do_summarize({}, "give me a summary of the 5 unread emails")
    assert captured["n"] == 5
    assert captured["q"] == "is:unread"


def test_summarize_no_unread_filter_when_not_requested(monkeypatch):
    p = _proto(_FakeSession())
    captured = {}
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    def fake_list(creds, max_results=10, categories=None, extra_query=None):
        captured.update(q=extra_query)
        return _msgs(1, unread=False)
    monkeypatch.setattr(gt, "gmail_list_messages", fake_list)
    monkeypatch.setattr(ea, "_llm", lambda messages, **kw: "routine.")
    p._do_summarize({}, "summarize my inbox")
    assert captured["q"] is None


def test_summarize_default_count_is_five(monkeypatch):
    p = _proto(_FakeSession())
    captured = {}
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    def fake_list(creds, max_results=10, categories=None, extra_query=None):
        captured.update(n=max_results)
        return _msgs(1)
    monkeypatch.setattr(gt, "gmail_list_messages", fake_list)
    monkeypatch.setattr(ea, "_llm", lambda messages, **kw: "routine.")
    p._do_summarize({}, "summarize my unread emails")
    assert captured["n"] == 5


def test_summarize_detail_tier_expands_preview(monkeypatch):
    p = _proto(_FakeSession())
    long_snip = "START " + ("filler " * 20) + "ENDMARKER"   # ENDMARKER sits ~146 chars in
    msgs = [{"id": "m1", "sender": "A <a@x>", "subject": "S",
             "snippet": long_snip, "unread": True}]
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results=10, categories=None, extra_query=None: msgs)
    monkeypatch.setattr(ea, "_llm", lambda messages, **kw: "routine.")
    plain = p._do_summarize({}, "summarize my unread")
    detailed = p._do_summarize({}, "give me a detailed summary of my unread")
    assert "ENDMARKER" not in plain      # default preview truncated
    assert "ENDMARKER" in detailed       # detail request shows the full snippet


def test_summarize_triage_failure_still_lists(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results=10, categories=None, extra_query=None:
                        [{"id": "m1", "sender": "A <a@x>", "subject": "Hello",
                          "snippet": "hi", "unread": True}])
    def boom(messages, **kw):
        raise RuntimeError("llm down")
    monkeypatch.setattr(ea, "_llm", boom)
    resp = p._do_summarize({}, "summarize my unread")
    assert "1. A — Hello" in resp        # list survives a triage LLM failure


def test_summarize_empty_inbox_says_clear(monkeypatch):
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results=10, categories=None, extra_query=None: [])
    resp = p._do_summarize({}, "summarize my unread")
    assert "clear" in resp.lower()


def test_summarize_respects_named_account(tmp_path, monkeypatch):
    p = _proto(_session_with_accounts(_real_accounts(tmp_path)))
    captured = {}
    monkeypatch.setattr(ea, "_creds_from_session",
                        lambda s, account_id=None: captured.update(acct=account_id) or "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results=10, categories=None, extra_query=None: _msgs(1))
    monkeypatch.setattr(ea, "_llm", lambda messages, **kw: "routine.")
    p._do_summarize({"account": "google-stitch"},
                    "summarize the unread in my switchstitch email")
    assert captured["acct"] == "google-stitch"   # fetched from the RIGHT account


# --- drill-down: read one email in full -----------------------------------


def test_parse_classification_read():
    p = _proto(_FakeSession())
    out = p._parse_classification("ACTION=read | REF=2 | TO=- | INSTRUCTION=-")
    assert out == {"action": "read", "ref": "2"}


def test_read_drilldown_uses_summary_map(monkeypatch):
    p = _proto(_FakeSession())
    p._summary_map = {1: "m1", 2: "m2"}     # what the user is looking at
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_get_message",
                        lambda creds, mid: {"from": "Ann <ann@x.ca>", "subject": "Numbers",
                                            "date": "Mon", "body": "Here are the full numbers."}
                        if mid == "m2" else None)
    resp = p._do_read_detail({"action": "read", "ref": "2"}, "tell me more about #2")
    assert "Numbers" in resp and "full numbers" in resp and "Ann" in resp


def test_read_drilldown_uses_summary_account(tmp_path, monkeypatch):
    """The message ids in a summary are account-scoped; drilling into one must
    reuse the account the summary was drawn from, not the default (else a
    stitch-inbox id is fetched against the personal account -> 404)."""
    p = _proto(_session_with_accounts(_real_accounts(tmp_path)))  # default = personal
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results=10, categories=None, extra_query=None:
                        [{"id": "stitch-msg", "sender": "A", "subject": "S",
                          "snippet": "x", "unread": True}])
    monkeypatch.setattr(ea, "_llm", lambda messages, **kw: "routine.")
    monkeypatch.setattr(ea, "_creds_from_session",
                        lambda s, account_id=None: f"CREDS::{account_id}")
    p._do_summarize({"account": "google-stitch"}, "summarize my 5 unread")
    assert p._summary_map == {1: "stitch-msg"}
    got = {}
    monkeypatch.setattr(gt, "gmail_get_message",
                        lambda creds, mid: got.update(creds=creds, mid=mid) or {
                            "from": "A", "subject": "S", "date": "", "body": "full"})
    p._do_read_detail({"action": "read", "ref": "1"}, "tell me more about #1")
    assert got["mid"] == "stitch-msg"
    assert got["creds"] == "CREDS::google-stitch"   # SAME account as the summary


def test_read_unknown_ref_asks(monkeypatch):
    p = _proto(_FakeSession())
    p._summary_map = {}
    resp = p._do_read_detail({"action": "read", "ref": "9"}, "tell me more about #9")
    assert "number" in resp.lower() or "which" in resp.lower()


def test_read_routes_through_gate_after_summary(monkeypatch):
    """'#2' has no email cue, but once a summary is on screen it must route to
    the read handler (via _summary_map context), not fall through to chat."""
    p = _proto(_FakeSession())
    p._summary_map = {1: "m1", 2: "m2"}
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results=15, categories=None, extra_query=None: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=read | REF=2 | TO=- | INSTRUCTION=-")
    monkeypatch.setattr(gt, "gmail_get_message",
                        lambda creds, mid: {"from": "Ann", "subject": "Numbers",
                                            "date": "", "body": "full body"})
    result = p.process_input("tell me more about #2", {})
    assert result["intercept"] is True
    assert "full body" in result["response"]


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
