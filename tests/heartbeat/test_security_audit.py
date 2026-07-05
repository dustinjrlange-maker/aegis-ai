"""Tests for the security_audit heartbeat job.

Orchestration tests monkeypatch SA.CHECKS so they exercise run() in isolation.
Real-check tests use a stub _Cfg that matches the actual RouterConfig field
names from core/llm/config.py (cloud_enabled, cloud_trouble_escalation,
trouble_private_consent).
"""
from datetime import datetime

from core.heartbeat.job import JobContext
from core.heartbeat.jobs import security_audit as SA


def _ctx(config=None):
    return JobContext("switch", object(), datetime(2026, 7, 4, 10, 0), config or {})


# ---------------------------------------------------------------------------
# run() orchestration
# ---------------------------------------------------------------------------

def test_all_clean_is_silent(monkeypatch):
    monkeypatch.setattr(SA, "CHECKS", [lambda ctx: None, lambda ctx: None])
    result = SA.run(_ctx())
    assert result.notify is False
    assert "clean" in result.silent_log.lower() or "0 issue" in result.silent_log.lower()


def test_failure_escalates(monkeypatch):
    monkeypatch.setattr(SA, "CHECKS", [
        lambda ctx: None,
        lambda ctx: "cloud escalation enabled without consent",
    ])
    result = SA.run(_ctx())
    assert result.notify is True
    assert "cloud escalation" in result.body


def test_failure_uses_both_channels(monkeypatch):
    monkeypatch.setattr(SA, "CHECKS", [lambda ctx: "a finding"])
    result = SA.run(_ctx())
    assert result.channels is not None
    assert "notification" in result.channels
    assert "telegram" in result.channels


def test_check_exception_is_reported_not_fatal(monkeypatch):
    """A crashing check must not propagate, and must NOT echo the raw exception
    repr in the finding (FIX M6 — don't leak config internals to external channels)."""
    def boom(ctx):
        raise RuntimeError("bad check — secret internal value")
    monkeypatch.setattr(SA, "CHECKS", [boom, lambda ctx: None])
    result = SA.run(_ctx())           # must not raise
    assert result.notify is True
    # Finding must name the check and say "raised an error" — not echo the exception.
    assert "boom" in result.body
    assert "raised an error" in result.body
    # Raw exception text must NOT appear (M6 privacy fix).
    assert "bad check" not in result.body
    assert "secret internal value" not in result.body


def test_multiple_failures_all_appear_in_body(monkeypatch):
    monkeypatch.setattr(SA, "CHECKS", [
        lambda ctx: "finding one",
        lambda ctx: "finding two",
    ])
    result = SA.run(_ctx())
    assert "finding one" in result.body
    assert "finding two" in result.body


# ---------------------------------------------------------------------------
# check_cloud_misconfig — real check
# ---------------------------------------------------------------------------

class _CfgCloudOnNoKey:
    cloud_enabled = True
    cloud_trouble_escalation = False
    trouble_private_consent = True


class _CfgCloudOnWithKey:
    cloud_enabled = True
    cloud_trouble_escalation = False
    trouble_private_consent = True


class _CfgCloudOff:
    cloud_enabled = False
    cloud_trouble_escalation = False
    trouble_private_consent = True


def test_check_cloud_misconfig_flags_when_cloud_on_no_key():
    """cloud_enabled=True + key_present=False must return a failure message."""
    ctx = _ctx({"cloud_cfg": _CfgCloudOnNoKey(), "key_present": False})
    msg = SA.check_cloud_misconfig(ctx)
    assert msg is not None
    assert "key" in msg.lower() or "enabled" in msg.lower()


def test_check_cloud_misconfig_clean_when_key_present():
    """cloud_enabled=True + key_present=True must return None."""
    ctx = _ctx({"cloud_cfg": _CfgCloudOnWithKey(), "key_present": True})
    assert SA.check_cloud_misconfig(ctx) is None


def test_check_cloud_misconfig_clean_when_cloud_off():
    """cloud_enabled=False skips the check regardless of key state."""
    ctx = _ctx({"cloud_cfg": _CfgCloudOff(), "key_present": False})
    assert SA.check_cloud_misconfig(ctx) is None


def test_check_cloud_misconfig_skips_when_no_cfg():
    """No cloud_cfg in config — check returns None (skip gracefully)."""
    assert SA.check_cloud_misconfig(_ctx()) is None


def test_check_cloud_misconfig_live_resolve_none_flags(monkeypatch):
    """key_present NOT injected -> live resolve_api_key() returns None -> flag.

    Exercises the function-local `from core.llm.config import resolve_api_key`
    fallback branch deterministically, without touching the real key file.
    """
    monkeypatch.setattr("core.llm.config.resolve_api_key", lambda: None)
    ctx = _ctx({"cloud_cfg": _CfgCloudOnNoKey()})   # no key_present injected
    msg = SA.check_cloud_misconfig(ctx)
    assert msg is not None
    assert "key" in msg.lower() or "enabled" in msg.lower()


def test_check_cloud_misconfig_live_resolve_key_clean(monkeypatch):
    """key_present NOT injected -> live resolve_api_key() returns a key -> clean."""
    monkeypatch.setattr("core.llm.config.resolve_api_key", lambda: "sk-ant-sentinel")
    ctx = _ctx({"cloud_cfg": _CfgCloudOnWithKey()})   # no key_present injected
    assert SA.check_cloud_misconfig(ctx) is None


# ---------------------------------------------------------------------------
# check_escalation_consent_invariant — real check
# ---------------------------------------------------------------------------

class _CfgEscalationBreached:
    """Invariant violated: escalation on, consent gate off."""
    cloud_enabled = True
    cloud_trouble_escalation = True
    trouble_private_consent = False


class _CfgEscalationSafe:
    """Safe: escalation on, consent gate on."""
    cloud_enabled = True
    cloud_trouble_escalation = True
    trouble_private_consent = True


class _CfgEscalationOff:
    """Escalation off — consent gate state irrelevant."""
    cloud_enabled = True
    cloud_trouble_escalation = False
    trouble_private_consent = False


def test_check_escalation_consent_flags_invariant_violation():
    """cloud_trouble_escalation=True + trouble_private_consent=False must flag."""
    ctx = _ctx({"cloud_cfg": _CfgEscalationBreached()})
    msg = SA.check_escalation_consent_invariant(ctx)
    assert msg is not None
    assert "consent" in msg.lower() or "private" in msg.lower()


def test_check_escalation_consent_clean_when_consent_on():
    """Escalation on but consent gate on — clean state."""
    ctx = _ctx({"cloud_cfg": _CfgEscalationSafe()})
    assert SA.check_escalation_consent_invariant(ctx) is None


def test_check_escalation_consent_clean_when_escalation_off():
    """Escalation off — consent gate state doesn't matter."""
    ctx = _ctx({"cloud_cfg": _CfgEscalationOff()})
    assert SA.check_escalation_consent_invariant(ctx) is None


def test_check_escalation_consent_skips_when_no_cfg():
    assert SA.check_escalation_consent_invariant(_ctx()) is None


# ---------------------------------------------------------------------------
# check_escalation_without_cloud — real check
# ---------------------------------------------------------------------------

class _CfgDeadEscalation:
    """Trouble escalation on but cloud disabled — dead path."""
    cloud_enabled = False
    cloud_trouble_escalation = True
    trouble_private_consent = True


class _CfgEscalationLive:
    """Both escalation and cloud enabled — live path."""
    cloud_enabled = True
    cloud_trouble_escalation = True
    trouble_private_consent = True


class _CfgBothOff:
    cloud_enabled = False
    cloud_trouble_escalation = False
    trouble_private_consent = True


def test_check_escalation_without_cloud_flags_dead_path():
    """cloud_trouble_escalation=True + cloud_enabled=False must flag."""
    ctx = _ctx({"cloud_cfg": _CfgDeadEscalation()})
    msg = SA.check_escalation_without_cloud(ctx)
    assert msg is not None
    assert "escalation" in msg.lower() or "cloud" in msg.lower()


def test_check_escalation_without_cloud_clean_when_cloud_on():
    ctx = _ctx({"cloud_cfg": _CfgEscalationLive()})
    assert SA.check_escalation_without_cloud(ctx) is None


def test_check_escalation_without_cloud_clean_when_escalation_off():
    ctx = _ctx({"cloud_cfg": _CfgBothOff()})
    assert SA.check_escalation_without_cloud(ctx) is None


def test_check_escalation_without_cloud_skips_when_no_cfg():
    assert SA.check_escalation_without_cloud(_ctx()) is None


# ---------------------------------------------------------------------------
# CHECKS list structure
# ---------------------------------------------------------------------------

def test_checks_is_list_of_callables():
    assert isinstance(SA.CHECKS, list)
    assert len(SA.CHECKS) >= 2
    for fn in SA.CHECKS:
        assert callable(fn)


# ---------------------------------------------------------------------------
# _live_cloud_cfg fallback — no ctx.config["cloud_cfg"] injection
# ---------------------------------------------------------------------------

class _LiveBadCfg:
    """Stub: escalation on, consent gate off — invariant violated."""
    cloud_enabled = True
    cloud_trouble_escalation = True
    trouble_private_consent = False


class _LiveCleanCfg:
    """Stub: all safe — no issues."""
    cloud_enabled = False
    cloud_trouble_escalation = False
    trouble_private_consent = True


def test_check_uses_live_cfg_bad_state(monkeypatch):
    """No cloud_cfg injection => _live_cloud_cfg() is called; bad state flagged."""
    monkeypatch.setattr(SA, "_live_cloud_cfg", lambda: _LiveBadCfg())
    ctx = _ctx({})   # no cloud_cfg key
    msg = SA.check_escalation_consent_invariant(ctx)
    assert msg is not None
    assert "consent" in msg.lower() or "private" in msg.lower()


def test_check_uses_live_cfg_clean_state(monkeypatch):
    """No cloud_cfg injection => _live_cloud_cfg() is called; clean state is None."""
    monkeypatch.setattr(SA, "_live_cloud_cfg", lambda: _LiveCleanCfg())
    ctx = _ctx({})
    assert SA.check_escalation_consent_invariant(ctx) is None


def test_injected_cloud_cfg_overrides_live(monkeypatch):
    """Explicit cloud_cfg in ctx.config takes precedence; _live_cloud_cfg NOT called."""
    called = []
    monkeypatch.setattr(SA, "_live_cloud_cfg", lambda: (called.append(1) or _LiveBadCfg()))

    class InjectedCfg:
        cloud_enabled = False
        cloud_trouble_escalation = False
        trouble_private_consent = True

    ctx = _ctx({"cloud_cfg": InjectedCfg()})
    assert SA.check_escalation_without_cloud(ctx) is None
    assert called == []   # live loader was NOT touched


def test_live_cloud_cfg_loader_failure_skips_check(monkeypatch):
    """_live_cloud_cfg() returning None causes check to skip (return None)."""
    monkeypatch.setattr(SA, "_live_cloud_cfg", lambda: None)
    ctx = _ctx({})
    assert SA.check_cloud_misconfig(ctx) is None
    assert SA.check_escalation_consent_invariant(ctx) is None
    assert SA.check_escalation_without_cloud(ctx) is None
