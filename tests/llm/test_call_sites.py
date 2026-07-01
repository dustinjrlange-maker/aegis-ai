# tests/llm/test_call_sites.py
"""Assert each refactored site calls router.chat with the right sensitivity.

These patch the router so no Ollama call happens. They target the module-level
`_router_chat` name the call sites import.
"""
import core.email_assistant as email_assistant


def test_email_llm_tags_private(monkeypatch):
    captured = {}

    def fake_chat(messages, *, sensitivity, task=None, **kw):
        captured["sensitivity"] = sensitivity
        captured["task"] = task
        return "ok"

    monkeypatch.setattr(email_assistant, "_router_chat", fake_chat)
    out = email_assistant._llm([{"role": "user", "content": "hi"}], task="draft")
    assert out == "ok"
    assert captured["sensitivity"] == "private"
    assert captured["task"] == "draft"
