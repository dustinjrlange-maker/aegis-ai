# tests/llm/test_router.py
import pytest
import core.llm.router as router


class _FakeBackend:
    def __init__(self, name, available):
        self.name = name
        self._available = available
        self.calls = []

    def available(self):
        return self._available

    def chat(self, messages, *, model=None, options=None, format=None):
        self.calls.append({"messages": messages, "model": model,
                           "options": options, "format": format})
        return f"reply-from-{self.name}"


def _patch_backends(monkeypatch, local, cloud):
    monkeypatch.setattr(router, "_BACKENDS", {"local": local, "cloud": cloud})


def _cfg(monkeypatch, cloud_enabled, opt_in=()):
    class C:
        pass
    c = C()
    c.cloud_enabled = cloud_enabled
    c.cloud_opt_in_features = tuple(opt_in)
    c.cloud_model = "claude-opus-4-8"
    monkeypatch.setattr(router, "load_config", lambda: c)


def test_local_when_cloud_disabled(monkeypatch):
    local = _FakeBackend("local", True)
    cloud = _FakeBackend("cloud", False)
    _patch_backends(monkeypatch, local, cloud)
    _cfg(monkeypatch, cloud_enabled=False)

    out = router.chat([{"role": "user", "content": "hi"}], sensitivity="personal")
    assert out == "reply-from-local"
    assert len(local.calls) == 1
    assert len(cloud.calls) == 0


def test_cloud_decision_falls_back_to_local_when_unavailable(monkeypatch, caplog):
    local = _FakeBackend("local", True)
    cloud = _FakeBackend("cloud", False)  # unavailable -> fallback
    _patch_backends(monkeypatch, local, cloud)
    _cfg(monkeypatch, cloud_enabled=True)

    with caplog.at_level("INFO"):
        out = router.chat([{"role": "user", "content": "hi"}], sensitivity="public")
    assert out == "reply-from-local"        # executed locally
    assert len(cloud.calls) == 0            # cloud never actually called
    assert any("cloud escalation preview" in r.message for r in caplog.records)


def test_params_pass_through_to_backend(monkeypatch):
    local = _FakeBackend("local", True)
    cloud = _FakeBackend("cloud", False)
    _patch_backends(monkeypatch, local, cloud)
    _cfg(monkeypatch, cloud_enabled=False)

    router.chat([{"role": "user", "content": "x"}], sensitivity="private",
                model="m1", options={"temperature": 0.1}, format="json")
    call = local.calls[0]
    assert call["model"] == "m1"
    assert call["options"] == {"temperature": 0.1}
    assert call["format"] == "json"


def test_cloud_routes_to_cloud_when_available(monkeypatch):
    local = _FakeBackend("local", True)
    cloud = _FakeBackend("cloud", True)  # available this time
    _patch_backends(monkeypatch, local, cloud)
    _cfg(monkeypatch, cloud_enabled=True)

    out = router.chat([{"role": "user", "content": "hi"}], sensitivity="public")
    assert out == "reply-from-cloud"
    assert len(cloud.calls) == 1
    assert len(local.calls) == 0


def test_bad_sensitivity_raises(monkeypatch):
    _patch_backends(monkeypatch, _FakeBackend("local", True), _FakeBackend("cloud", False))
    _cfg(monkeypatch, cloud_enabled=False)
    with pytest.raises(ValueError):
        router.chat([{"role": "user", "content": "x"}], sensitivity="topsecret")


class _RaisingBackend:
    def __init__(self, name, exc):
        self.name = name
        self._exc = exc
        self.calls = []

    def available(self):
        return True

    def chat(self, messages, *, model=None, options=None, format=None):
        self.calls.append({"messages": messages})
        raise self._exc


def test_cloud_runtime_error_falls_back_to_local(monkeypatch, caplog):
    local = _FakeBackend("local", True)
    cloud = _RaisingBackend("cloud", RuntimeError("boom"))
    _patch_backends(monkeypatch, local, cloud)
    _cfg(monkeypatch, cloud_enabled=True)

    with caplog.at_level("WARNING"):
        out = router.chat([{"role": "user", "content": "hi"}], sensitivity="public")
    assert out == "reply-from-local"          # local answer returned
    assert len(cloud.calls) == 1              # cloud was attempted first
    assert len(local.calls) == 1
    assert any("cloud call failed" in r.message for r in caplog.records)


def test_cloud_refusal_falls_back_to_local(monkeypatch, caplog):
    from core.llm.backends import CloudRefusalError
    local = _FakeBackend("local", True)
    cloud = _RaisingBackend("cloud", CloudRefusalError("declined"))
    _patch_backends(monkeypatch, local, cloud)
    _cfg(monkeypatch, cloud_enabled=True)

    with caplog.at_level("WARNING"):
        out = router.chat([{"role": "user", "content": "guns"}], sensitivity="public")
    assert out == "reply-from-local"
    assert len(cloud.calls) == 1
    assert len(local.calls) == 1
    assert any("cloud call failed" in r.message for r in caplog.records)


from core.llm.router import chat_with_meta, RouteMeta
import core.llm.router as router_mod


class _MetaFakeBackend:
    def __init__(self, reply="ok", is_available=True, exc=None):
        self._reply, self._available, self._exc = reply, is_available, exc

    def available(self):
        return self._available

    def chat(self, messages, **kw):
        if self._exc:
            raise self._exc
        return self._reply


class _MetaCfg:
    cloud_enabled = True
    cloud_opt_in_features = ()
    deep_mode = False
    cloud_model = "claude-opus-4-8"


class TestChatWithMeta:
    def _patch(self, monkeypatch, local, cloud):
        monkeypatch.setattr(router_mod, "load_config", lambda: _MetaCfg())
        monkeypatch.setitem(router_mod._BACKENDS, "local", local)
        monkeypatch.setitem(router_mod._BACKENDS, "cloud", cloud)

    def test_cloud_pick_returns_cloud_meta(self, monkeypatch):
        self._patch(monkeypatch, _MetaFakeBackend("local-ans"), _MetaFakeBackend("cloud-ans"))
        content, meta = chat_with_meta(
            [{"role": "user", "content": "x"}], sensitivity="personal", task="chat_task",
        )
        assert content == "cloud-ans"
        assert meta.backend_used == "cloud"
        assert meta.cloud_model == "claude-opus-4-8"

    def test_local_pick_returns_local_meta(self, monkeypatch):
        self._patch(monkeypatch, _MetaFakeBackend("local-ans"), _MetaFakeBackend("cloud-ans"))
        content, meta = chat_with_meta(
            [{"role": "user", "content": "x"}], sensitivity="personal", task="chat_casual",
        )
        assert content == "local-ans"
        assert meta.backend_used == "local"
        assert meta.decision_reason == "personal_local_default"

    def test_cloud_failure_falls_back_with_local_meta(self, monkeypatch):
        self._patch(monkeypatch, _MetaFakeBackend("local-ans"),
                    _MetaFakeBackend(exc=RuntimeError("boom")))
        content, meta = chat_with_meta(
            [{"role": "user", "content": "x"}], sensitivity="personal", task="chat_task",
        )
        assert content == "local-ans"
        assert meta.backend_used == "local"
        assert meta.decision_reason == "cloud_failed_fallback"

    def test_cloud_unavailable_falls_back_with_local_meta(self, monkeypatch):
        self._patch(monkeypatch, _MetaFakeBackend("local-ans"),
                    _MetaFakeBackend(is_available=False))
        content, meta = chat_with_meta(
            [{"role": "user", "content": "x"}], sensitivity="personal", task="chat_task",
        )
        assert content == "local-ans"
        assert meta.backend_used == "local"
        assert meta.decision_reason == "cloud_unavailable_fallback"

    def test_plain_chat_still_returns_string(self, monkeypatch):
        self._patch(monkeypatch, _MetaFakeBackend("local-ans"), _MetaFakeBackend("cloud-ans"))
        out = router_mod.chat(
            [{"role": "user", "content": "x"}], sensitivity="personal", task="chat_casual",
        )
        assert out == "local-ans"


def test_trouble_flag_routes_to_cloud(monkeypatch):
    import core.llm.router as R

    class _Cfg:
        cloud_enabled = False
        cloud_opt_in_features = ()
        cloud_model = "claude-opus-4-8"
        cloud_max_tokens = 2048
        deep_mode = False
        cloud_trouble_escalation = True
        trouble_private_consent = True

    monkeypatch.setattr(R, "load_config", lambda: _Cfg())

    class _Cloud:
        def available(self): return True
        def chat(self, messages, *, model=None, options=None, format=None):
            return "cloud says hi"

    monkeypatch.setitem(R._BACKENDS, "cloud", _Cloud())
    content, meta = R.chat_with_meta(
        [{"role": "user", "content": "no that's wrong"}],
        sensitivity="personal", task="chat_casual", trouble=True)
    assert content == "cloud says hi"
    assert meta.backend_used == "cloud"
