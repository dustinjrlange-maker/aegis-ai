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
