# tests/llm/test_policy.py
import pytest
from core.llm.policy import decide, RouteDecision, VALID_SENSITIVITIES


class _Cfg:
    """Minimal stand-in for RouterConfig (policy reads cloud_enabled,
    cloud_opt_in_features, and deep_mode)."""
    def __init__(self, cloud_enabled=True, cloud_opt_in_features=(), deep_mode=False):
        self.cloud_enabled = cloud_enabled
        self.cloud_opt_in_features = tuple(cloud_opt_in_features)
        self.deep_mode = deep_mode


def test_cloud_disabled_forces_local_for_every_tier():
    cfg = _Cfg(cloud_enabled=False)
    for tier in VALID_SENSITIVITIES:
        d = decide(tier, cfg)
        assert d.backend == "local"
        assert d.reason == "cloud_disabled"
        assert d.would_send_cloud is False


def test_offline_forces_local_even_when_cloud_enabled():
    cfg = _Cfg(cloud_enabled=True)
    d = decide("public", cfg, offline=True)
    assert d.backend == "local"
    assert d.reason == "offline"
    assert d.would_send_cloud is False


def test_private_stays_local_by_default_when_cloud_enabled():
    cfg = _Cfg(cloud_enabled=True)
    d = decide("private", cfg, task="summarize")
    assert d.backend == "local"
    assert d.reason == "private_local_default"
    assert d.would_send_cloud is False


def test_private_escalates_only_when_task_opted_in():
    cfg = _Cfg(cloud_enabled=True, cloud_opt_in_features=("summarize",))
    d = decide("private", cfg, task="summarize")
    assert d.backend == "cloud"
    assert d.would_send_cloud is True


def test_personal_and_public_are_cloud_eligible_when_enabled():
    cfg = _Cfg(cloud_enabled=True)
    for tier in ("personal", "public"):
        d = decide(tier, cfg, task="chat_task")
        assert d.backend == "cloud"
        assert d.reason == "cloud_eligible"
        assert d.would_send_cloud is True


def test_invalid_sensitivity_raises():
    with pytest.raises(ValueError):
        decide("secret", _Cfg())


class TestPersonalTaskGating:
    def test_personal_chat_task_goes_cloud(self):
        d = decide("personal", _Cfg(), task="chat_task")
        assert d.backend == "cloud"
        assert d.would_send_cloud is True

    def test_personal_casual_stays_local_even_when_enabled(self):
        d = decide("personal", _Cfg(), task="chat_casual")
        assert (d.backend, d.reason) == ("local", "personal_local_default")

    def test_personal_emotional_local_without_deep_mode(self):
        d = decide("personal", _Cfg(deep_mode=False), task="chat_emotional")
        assert (d.backend, d.reason) == ("local", "personal_local_default")

    def test_personal_emotional_cloud_with_deep_mode(self):
        d = decide("personal", _Cfg(deep_mode=True), task="chat_emotional")
        assert (d.backend, d.reason) == ("cloud", "deep_mode")

    def test_legacy_chat_tag_stays_local(self):
        d = decide("personal", _Cfg(), task="chat")
        assert d.backend == "local"

    def test_cloud_disabled_beats_chat_task(self):
        d = decide("personal", _Cfg(cloud_enabled=False), task="chat_task")
        assert (d.backend, d.reason) == ("local", "cloud_disabled")

    def test_public_tier_unchanged(self):
        d = decide("public", _Cfg(), task="summarize")
        assert d.backend == "cloud"

    def test_private_tier_unchanged(self):
        d = decide("private", _Cfg(), task="draft")
        assert (d.backend, d.reason) == ("local", "private_local_default")
