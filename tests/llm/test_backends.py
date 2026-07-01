# tests/llm/test_backends.py
import pytest
import core.llm.backends as backends
from core.llm.backends import LocalBackend, CloudBackend, CloudRefusalError, CloudResponseError
from core.llm.config import RouterConfig


class _FakeBlock:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _FakeResp:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._resp


class _FakeClient:
    def __init__(self, resp):
        self.messages = _FakeMessages(resp)


def _cfg(**kw):
    return RouterConfig(**kw)


def test_cloud_available_false_without_key(monkeypatch):
    monkeypatch.setattr(backends, "resolve_api_key", lambda: None)
    assert CloudBackend().available() is False


def test_cloud_available_true_with_key_and_package(monkeypatch):
    monkeypatch.setattr(backends, "resolve_api_key", lambda: "sk-x")
    monkeypatch.setattr(backends, "_anthropic_installed", lambda: True)
    assert CloudBackend().available() is True


def test_cloud_available_false_when_package_missing(monkeypatch):
    monkeypatch.setattr(backends, "resolve_api_key", lambda: "sk-x")
    monkeypatch.setattr(backends, "_anthropic_installed", lambda: False)
    assert CloudBackend().available() is False


def test_cloud_chat_translates_uses_cloud_model_and_returns_text(monkeypatch):
    cb = CloudBackend()
    cb._client = _FakeClient(_FakeResp([_FakeBlock("text", "hello from cloud")]))
    monkeypatch.setattr(backends, "load_config",
                        lambda: _cfg(cloud_model="claude-opus-4-8", cloud_max_tokens=999))

    out = cb.chat(
        [{"role": "system", "content": "You are Pike."},
         {"role": "user", "content": "hi"}],
        model="qwen3:8b",  # local id — must be ignored
    )
    assert out == "hello from cloud"
    call = cb._client.messages.calls[0]
    assert call["model"] == "claude-opus-4-8"   # cloud model, not qwen3:8b
    assert call["max_tokens"] == 999
    assert call["system"] == "You are Pike."
    assert call["messages"] == [{"role": "user", "content": "hi"}]


def test_cloud_chat_raises_on_refusal(monkeypatch):
    cb = CloudBackend()
    cb._client = _FakeClient(_FakeResp([], stop_reason="refusal"))
    monkeypatch.setattr(backends, "load_config",
                        lambda: _cfg(cloud_model="claude-opus-4-8", cloud_max_tokens=100))
    with pytest.raises(CloudRefusalError):
        cb.chat([{"role": "user", "content": "..."}])


def test_cloud_chat_raises_when_no_text_block(monkeypatch):
    cb = CloudBackend()
    cb._client = _FakeClient(_FakeResp([_FakeBlock("thinking", "")]))
    monkeypatch.setattr(backends, "load_config",
                        lambda: _cfg(cloud_model="claude-opus-4-8", cloud_max_tokens=100))
    with pytest.raises(CloudResponseError):
        cb.chat([{"role": "user", "content": "..."}])


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
