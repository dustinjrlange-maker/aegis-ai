# tests/llm/test_backends.py
import pytest
import core.llm.backends as backends
from core.llm.backends import LocalBackend, CloudBackend


def test_cloud_backend_is_unavailable_and_refuses():
    cb = CloudBackend()
    assert cb.available() is False
    with pytest.raises(NotImplementedError):
        cb.chat([{"role": "user", "content": "hi"}], model="x")


def test_local_backend_is_available():
    assert LocalBackend().available() is True


def test_local_backend_passes_params_and_returns_content(monkeypatch):
    captured = {}

    def fake_ollama_chat(**kwargs):
        captured.update(kwargs)
        return {"message": {"content": "pong"}}

    monkeypatch.setattr(backends.ollama, "chat", fake_ollama_chat)
    out = LocalBackend().chat(
        [{"role": "user", "content": "ping"}],
        model="qwen3:8b", options={"temperature": 0.2}, format="json",
    )
    assert out == "pong"
    assert captured["model"] == "qwen3:8b"
    assert captured["messages"] == [{"role": "user", "content": "ping"}]
    assert captured["options"] == {"temperature": 0.2}
    assert captured["format"] == "json"


from core.llm.backends import _split_system, CloudRefusalError, CloudResponseError


def test_split_system_extracts_system_and_keeps_convo():
    system, convo = _split_system([
        {"role": "system", "content": "You are Pike."},
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "again"},
    ])
    assert system == "You are Pike.\n\nBe concise."
    assert convo == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "again"},
    ]


def test_split_system_empty_system_when_none():
    system, convo = _split_system([{"role": "user", "content": "hi"}])
    assert system == ""
    assert convo == [{"role": "user", "content": "hi"}]


def test_split_system_raises_when_no_leading_user():
    import pytest
    with pytest.raises(CloudResponseError):
        _split_system([{"role": "system", "content": "sys only"}])
    with pytest.raises(CloudResponseError):
        _split_system([{"role": "assistant", "content": "a"}])
