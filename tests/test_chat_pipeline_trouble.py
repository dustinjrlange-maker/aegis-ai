import asyncio

import server.chat_pipeline as cp
from server.chat_pipeline import evaluate_escalation


class _Cfg:
    def __init__(self, esc, consent):
        self.cloud_trouble_escalation = esc
        self.trouble_private_consent = consent


def test_non_private_trouble_escalates():
    out = evaluate_escalation("no that's wrong", streak=0,
                              cfg=_Cfg(True, True), key_present=True)
    assert out.action == "escalate"
    assert out.new_streak == 1


def test_private_trouble_with_consent_prompts():
    out = evaluate_escalation("no, my bank account number is wrong", streak=0,
                              cfg=_Cfg(True, True), key_present=True)
    assert out.action == "consent"
    assert "financial" in out.reason


def test_private_trouble_without_consent_escalates():
    out = evaluate_escalation("no, my bank account is wrong", streak=0,
                              cfg=_Cfg(True, False), key_present=True)
    assert out.action == "escalate"


def test_no_key_stays_local():
    out = evaluate_escalation("no that's wrong", streak=0,
                              cfg=_Cfg(True, True), key_present=False)
    assert out.action == "local"


def test_feature_off_stays_local():
    out = evaluate_escalation("no that's wrong", streak=0,
                              cfg=_Cfg(False, True), key_present=True)
    assert out.action == "local"


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
