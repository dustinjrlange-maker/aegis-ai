import core.email_assistant as ea
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
                        lambda session, draft_id: sent.update({"id": draft_id}) or {"success": True})
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
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=edit | REF=- | INSTRUCTION=make it formal")
    deleted = {"called": False}
    monkeypatch.setattr(gt, "gmail_delete_draft",
                        lambda creds, draft_id: deleted.update(called=True))
    monkeypatch.setattr(ea, "draft_reply",
                        lambda session, message_id, intent: {"success": False, "error": "LLM down"})
    result = p.process_input("make it formal", {})
    assert "LLM down" in result["response"]
    assert deleted["called"] is False          # old draft NOT deleted on failure
    assert p._pending["draft_id"] == "d1"        # pending unchanged


def test_send_requires_literal_phrase(monkeypatch):
    p = _proto(_FakeSession())
    _pending_reply(p)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=send | REF=- | INSTRUCTION=-")
    called = {"sent": False}
    monkeypatch.setattr(ea, "send_draft",
                        lambda session, draft_id: called.update(sent=True) or {"success": True})
    result = p.process_input("yeah ok do it", {})   # affirmative but no send word
    assert called["sent"] is False                   # must NOT send
    assert "confirm" in result["response"].lower()
    assert p._pending is not None                     # draft still held


def _wire_send_classifier(monkeypatch, p):
    """Pending draft + a classifier that (wrongly) says ACTION=send."""
    _pending_reply(p)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=send | REF=- | INSTRUCTION=-")
    called = {"sent": False}
    monkeypatch.setattr(ea, "send_draft",
                        lambda session, draft_id: called.update(sent=True) or {"success": True})
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


def test_edit_forward_is_blocked(monkeypatch):
    p = _proto(_FakeSession())
    p._pending = {"draft_id": "f1", "kind": "forward", "message_id": "m1",
                  "to": "sue@x.ca", "subject": "Fwd: X", "intent": "forward the ann email to sue@x.ca"}
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
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
    monkeypatch.setattr(ea, "_creds_from_session", lambda s: "CREDS")
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


# --- Task 10: ACCOUNT= classifier field + account resolution -------------

import json
from core.accounts.manager import AccountManager
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


def test_classifier_prompt_no_accounts_unchanged():
    """Un-migrated session (no accounts attr) gets no account block."""
    p = _proto(_FakeSession())
    prompt = p._build_classifier_prompt("hi", "", False)
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
    acct = p._resolve_account({"account": "stitch"})
    assert acct["id"] == "google-stitch"


def test_resolve_account_unknown_falls_back_to_default(tmp_path):
    p = _proto(_session_with_accounts(_real_accounts(tmp_path)))
    acct = p._resolve_account({"account": "nonsense"})
    assert acct["id"] == "google-personal"


def test_resolve_account_absent_uses_default(tmp_path):
    p = _proto(_session_with_accounts(_real_accounts(tmp_path)))
    acct = p._resolve_account({})
    assert acct["id"] == "google-personal"


def test_resolve_account_no_layer_returns_none():
    p = _proto(_FakeSession())  # session has no .accounts
    assert p._resolve_account({"account": "stitch"}) is None


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
