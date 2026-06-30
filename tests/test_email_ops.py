import core.email_assistant as ea
from core.protocols import google_tools as gt


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
