import asyncio

import server.chat_pipeline as cp
from server.chat_pipeline import evaluate_escalation, payload_has_private_content


class _Cfg:
    def __init__(self, esc, consent):
        self.cloud_trouble_escalation = esc
        self.trouble_private_consent = consent


# --- evaluate_escalation is now TROUBLE-ONLY (no private-content decision) ---


def test_trouble_escalates():
    out = evaluate_escalation("no that's wrong", streak=0,
                              cfg=_Cfg(True, True), key_present=True)
    assert out.action == "escalate"
    assert out.new_streak == 1


def test_no_key_stays_local():
    out = evaluate_escalation("no that's wrong", streak=0,
                              cfg=_Cfg(True, True), key_present=False)
    assert out.action == "local"


def test_feature_off_stays_local():
    out = evaluate_escalation("no that's wrong", streak=0,
                              cfg=_Cfg(False, True), key_present=True)
    assert out.action == "local"


def test_no_trouble_stays_local():
    out = evaluate_escalation("what time is it", streak=0,
                              cfg=_Cfg(True, True), key_present=True)
    assert out.action == "local"


# --- payload_has_private_content scans the full assembled payload ---


def test_payload_finds_private_in_middle_message():
    payload = [
        {"role": "system", "content": "you are Pike"},
        {"role": "user", "content": "hello there"},
        {"role": "system", "content": "Relevant memory:\nmy bank account number is 1234"},
        {"role": "user", "content": "no that's wrong"},
    ]
    is_priv, reason = payload_has_private_content(payload)
    assert is_priv is True
    assert reason == "financial"


def test_payload_all_clean_returns_false():
    payload = [
        {"role": "system", "content": "you are Pike"},
        {"role": "user", "content": "what time is it"},
        {"role": "assistant", "content": "it is noon"},
    ]
    is_priv, reason = payload_has_private_content(payload)
    assert is_priv is False
    assert reason == ""


def test_payload_ignores_non_string_content():
    # Multimodal / structured content must not crash the scan.
    payload = [{"role": "user", "content": [{"type": "image"}]},
               {"role": "user", "content": "hi"}]
    assert payload_has_private_content(payload) == (False, "")


# --- Integration: prove the wiring through process_chat, not just the helper ---


class _FakeMemory:
    def __init__(self):
        self._fact_store = None
        self.user_data_dir = None

    def build_session_context(self):
        return ""

    def get_relevant_memories(self, text):
        return ""

    def periodic_save(self, messages):
        pass

    def extract_recent_facts(self, messages, since_index=0):
        pass


class _FakeCharMemory:
    def get_core_context(self, message_count=0):
        return ""

    def get_relevant_memories(self, text, max_results=2):
        return ""


class _FakeRegistry:
    def process_input(self, user_input, context):
        return {"intercept": False, "input": user_input,
                "context_injections": [], "full_context_injections": []}

    def process_output(self, response, context):
        return {"suppress": False, "response": response}

    def get(self, name):
        return None

    def handle_command(self, command, args=""):
        return (False, "")


class _RouteMeta:
    backend_used = "local"
    cloud_model = None


class _FakeSession:
    def __init__(self):
        self.agent_name = "Pike"
        self.system_prompt_base = ""
        self.messages = [{"role": "system", "content": ""}]
        self.memory = _FakeMemory()
        self.char_memory = _FakeCharMemory()
        self.protocol_registry = _FakeRegistry()
        self.notification_service = None
        self.last_fact_extraction_index = 0
        self._pending_escalation = None
        self._correction_streak = 0

    def clean_reply(self, content, mode=None):
        return content


class _FakeManager:
    def __init__(self, session):
        self._s = session

    def get_or_create(self, user_id):
        return self._s


def _install_stubs(monkeypatch, calls):
    def _router_stub(messages, sensitivity=None, task=None, model=None, trouble=False):
        calls.append({"sensitivity": sensitivity, "task": task,
                      "model": model, "trouble": trouble})
        return ("stub reply", _RouteMeta())

    monkeypatch.setattr(cp, "router_chat_with_meta", _router_stub)
    monkeypatch.setattr(cp, "_load_router_config", lambda: _Cfg(True, True))
    monkeypatch.setattr(cp, "_resolve_key", lambda: "key-present")
    # Avoid loading the emotion model in tests; None is a valid emotion_result.
    monkeypatch.setattr(cp.emotion, "detect_emotion", lambda text: None)


def test_gate_blocks_router_then_affirmative_escalates(monkeypatch):
    calls = []
    _install_stubs(monkeypatch, calls)
    session = _FakeSession()
    mgr = _FakeManager(session)

    # (a) private-content trouble turn: consent prompt returned, router NOT called
    out = asyncio.run(cp.process_chat(mgr, "u", "no, my bank account number is wrong"))
    assert "⚠" in out["response"]
    assert calls == []                       # gate held — nothing left the machine
    assert session._pending_escalation is not None

    # (b) affirmative follow-up on the same session: router called with trouble=True
    out2 = asyncio.run(cp.process_chat(mgr, "u", "yes, use cloud"))
    assert len(calls) == 1
    assert calls[0]["trouble"] is True
    assert session._pending_escalation is None


def test_prior_turn_private_content_blocks_silent_escalation(monkeypatch):
    """Cross-turn leak: Turn 1 leaks private data into history while staying
    local; Turn 2 is a clean correction. Escalating Turn 2 would ship Turn 1's
    private data to the cloud — so the gate must fire on the WHOLE history."""
    calls = []
    _install_stubs(monkeypatch, calls)
    session = _FakeSession()
    mgr = _FakeManager(session)

    # Turn 1: private, NON-correction → stays local, appended to history.
    asyncio.run(cp.process_chat(mgr, "u", "my bank account number is 1234"))
    assert len(calls) == 1
    assert calls[0]["trouble"] is False
    assert any("1234" in m["content"] for m in session.messages)

    # Turn 2: clean correction (current msg NOT private) with consent ON.
    # Prior turn IS private → must return the consent prompt, router NOT called.
    out = asyncio.run(cp.process_chat(mgr, "u", "no that's wrong"))
    assert "⚠" in out["response"]
    assert len(calls) == 1                    # router NOT called — no leak
    assert session._pending_escalation is not None

    # Affirmative consent: now the escalated cloud call is allowed.
    asyncio.run(cp.process_chat(mgr, "u", "yes"))
    assert len(calls) == 2
    assert calls[1]["trouble"] is True


def test_injected_memory_private_content_blocks_escalation(monkeypatch):
    """Memory vector: retrieved memories are assembled INTO the payload after the
    trouble decision. A clean correction turn that pulls a private stored fact
    must be gated on that assembled payload — the router must NOT be called."""
    calls = []
    _install_stubs(monkeypatch, calls)
    session = _FakeSession()
    # Vector-store returns a private stored fact for this turn's retrieval.
    session.memory.get_relevant_memories = (
        lambda text: "stored fact: user's bank account number is 1234")
    mgr = _FakeManager(session)

    # Clean correction (not private itself), consent ON, feature on. The private
    # data enters ONLY via the injected memory → gate must still fire.
    out = asyncio.run(cp.process_chat(mgr, "u", "no that's wrong"))
    assert "⚠" in out["response"]
    assert calls == []                       # router NOT called — memory did not leak
    assert session._pending_escalation is not None

    # Affirmative consent → now the escalated cloud call proceeds.
    asyncio.run(cp.process_chat(mgr, "u", "yes"))
    assert len(calls) == 1
    assert calls[0]["trouble"] is True


def test_consent_rerun_with_new_private_category_reprompts(monkeypatch):
    """2026-07-09 audit: the consented re-run skipped the private-content
    rescan entirely. The user consents to a payload containing FINANCIAL
    content; before the re-run a HEALTH item lands in the history. The
    consent no longer covers the payload -> must re-prompt, not egress."""
    calls = []
    _install_stubs(monkeypatch, calls)
    session = _FakeSession()
    mgr = _FakeManager(session)

    asyncio.run(cp.process_chat(mgr, "u", "no, my bank account number is wrong"))
    assert calls == []
    assert session._pending_escalation is not None

    # A new private category appears in history before the user answers
    # (interleaved turn, memory retrieval, file context...).
    session.messages.append({"role": "system",
                             "content": "Relevant memory: my diagnosis came back"})

    out = asyncio.run(cp.process_chat(mgr, "u", "yes, use cloud"))
    assert calls == []                       # nothing left the machine
    assert "⚠" in out["response"]            # re-prompted instead
    assert session._pending_escalation is not None

    # Consenting to the updated prompt (now covering both categories) proceeds.
    asyncio.run(cp.process_chat(mgr, "u", "yes, use cloud"))
    assert len(calls) == 1
    assert calls[0]["trouble"] is True
