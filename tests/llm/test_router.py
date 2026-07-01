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


def test_bad_sensitivity_raises(monkeypatch):
    _patch_backends(monkeypatch, _FakeBackend("local", True), _FakeBackend("cloud", False))
    _cfg(monkeypatch, cloud_enabled=False)
    with pytest.raises(ValueError):
        router.chat([{"role": "user", "content": "x"}], sensitivity="topsecret")
