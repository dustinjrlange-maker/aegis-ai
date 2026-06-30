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
