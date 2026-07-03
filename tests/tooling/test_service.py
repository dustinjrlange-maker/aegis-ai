"""Service-layer tests — manager mocked, registry/trust real (temp dirs)."""
import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Real registry+audit on tmp dirs, mocked manager, clean trust stash."""
    from core.tooling import registry, audit, trust, service

    monkeypatch.setattr(registry, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_DATA_ROOT", tmp_path)
    trust._pending.clear()

    class FakeManager:
        def __init__(self):
            self.started = []
            self.calls = []
        def ensure_started(self, u, t, command, args, env=None, timeout=60):
            self.started.append((u, t, command, tuple(args)))
        def call(self, u, t, method, arguments=None, timeout=10):
            self.calls.append((u, t, method, arguments))
            return [f"{method}-result"]
        def list_tools(self, u, t, timeout=10):
            return [{"name": "read_file", "description": "", "input_schema": {}}]
        def is_running(self, u, t):
            return True
        def stop(self, u, t):
            pass

    fake = FakeManager()
    monkeypatch.setattr(service, "MANAGER", fake)
    return service, fake


def test_install_time_no_config(env):
    service, fake = env
    msg = service.install_tool("switch", "time", {})
    assert "time" in msg.lower() and "read_scoped" in msg
    assert fake.started                       # warm-up spawned the server
    from core.tooling import registry
    assert registry.get("switch", "time")["trust_tier"] == "read_scoped"


def test_install_filesystem_requires_dirs(env):
    service, _ = env
    msg = service.install_tool("switch", "filesystem", {})
    assert "approved_dirs" in msg             # missing config -> guidance, not install
    from core.tooling import registry
    assert registry.get("switch", "filesystem") is None


def test_install_unknown_tool(env):
    service, _ = env
    msg = service.install_tool("switch", "nope", {})
    assert "catalog" in msg.lower()


def test_call_within_tier_executes_and_audits(env):
    service, fake = env
    service.install_tool("switch", "time", {})
    result = service.call_tool("switch", "time", "get_current_time", {"timezone": "UTC"})
    assert result["status"] == "ok"
    assert result["result"] == ["get_current_time-result"]
    from core.tooling import audit
    entries = audit.recent("switch")
    assert entries[-1]["outcome"] == "ok"


def test_call_uninstalled_tool(env):
    service, _ = env
    result = service.call_tool("switch", "filesystem", "read_file", {})
    assert result["status"] == "error" and "not installed" in result["message"]


def test_out_of_tier_write_soft_blocks(env):
    service, fake = env
    service.install_tool("switch", "filesystem", {"approved_dirs": ["C:/safe"]})
    result = service.call_tool("switch", "filesystem", "write_file",
                               {"path": "C:/safe/a.txt", "content": "hi"})
    assert result["status"] == "needs_pin"
    assert result["tool_id"] == "filesystem"
    assert result["method"] == "write_file"
    assert result["required_tier"] == "write_destructive"
    assert not any(c[2] == "write_file" for c in fake.calls)   # NOT executed
    from core.tooling import audit
    assert audit.recent("switch")[-1]["outcome"] == "denied"


def test_pin_confirm_executes_once(env, monkeypatch):
    service, fake = env
    from core.tooling import trust
    monkeypatch.setattr(trust, "verify_vault_pin", lambda u, p: p == "1234")
    monkeypatch.setattr(trust, "has_vault_pin", lambda u: True)

    service.install_tool("switch", "filesystem", {"approved_dirs": ["C:/safe"]})
    service.call_tool("switch", "filesystem", "write_file", {"path": "C:/safe/a.txt"})

    result = service.confirm_pending("switch", "1234")
    assert result["status"] == "ok"
    assert any(c[2] == "write_file" for c in fake.calls)       # executed now
    from core.tooling import audit
    assert audit.recent("switch")[-1]["outcome"] == "pin_escalated"

    again = service.confirm_pending("switch", "1234")
    assert again["status"] == "error"                           # consumed


def test_install_warmup_failure_keeps_tool_installed(env, monkeypatch):
    service, fake = env
    def boom(*a, **k):
        raise RuntimeError("spawn failed")
    monkeypatch.setattr(fake, "ensure_started", boom)
    msg = service.install_tool("switch", "time", {})
    assert "failed to start" in msg.lower()
    from core.tooling import registry
    assert registry.get("switch", "time") is not None      # still installed


def test_resolve_launch_npx_missing_is_graceful(env, monkeypatch):
    service, _ = env
    import core.tooling.service as svc
    monkeypatch.setattr(svc.shutil, "which", lambda name: None)
    service.install_tool("switch", "filesystem", {"approved_dirs": ["C:/safe"]})
    result = service.call_tool("switch", "filesystem", "read_file", {"path": "C:/safe/x"})
    assert result["status"] == "error"                     # graceful, not a crash


def test_append_config_scalar_is_coerced(env):
    service, fake = env
    service.install_tool("switch", "filesystem", {"approved_dirs": "C:/only"})  # STRING, not list
    args = fake.started[-1][3]                              # tuple(args) from ensure_started
    assert "C:/only" in args                               # kept whole
    assert "C" not in args                                 # NOT char-split


def test_confirm_pending_with_nothing_pending(env):
    service, _ = env
    result = service.confirm_pending("switch", "1234")
    assert result["status"] == "error"                     # (False, msg) branch
