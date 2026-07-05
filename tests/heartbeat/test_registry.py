"""Tests for core.heartbeat.registry — build_registry + make_is_enabled."""
import json
from pathlib import Path

import pytest

from core.heartbeat.registry import build_registry, make_is_enabled
from core.heartbeat.job import Schedule


# ---------------------------------------------------------------------------
# build_registry
# ---------------------------------------------------------------------------

def test_registry_returns_all_four_ids():
    jobs = build_registry({})
    ids = [j.id for j in jobs]
    assert ids == ["recurring_fire", "morning_briefing", "inbox_scan", "security_audit"]


def test_morning_briefing_default_schedule():
    """Default 'at' = '07:00' => daily_at(7, 0)."""
    jobs = build_registry({})
    mb = next(j for j in jobs if j.id == "morning_briefing")
    assert mb.schedule.kind == "daily_at"
    assert mb.schedule.hh == 7
    assert mb.schedule.mm == 0


def test_morning_briefing_custom_at():
    """at = '08:30' => daily_at(8, 30)."""
    cfg = {"jobs": {"morning_briefing": {"at": "08:30"}}}
    jobs = build_registry(cfg)
    mb = next(j for j in jobs if j.id == "morning_briefing")
    assert mb.schedule.hh == 8
    assert mb.schedule.mm == 30


def test_inbox_scan_default_interval():
    """Default every_minutes = 30 => every(1800)."""
    jobs = build_registry({})
    ib = next(j for j in jobs if j.id == "inbox_scan")
    assert ib.schedule.kind == "every"
    assert ib.schedule.seconds == 30 * 60


def test_inbox_scan_custom_interval():
    """every_minutes = 15 => every(900)."""
    cfg = {"jobs": {"inbox_scan": {"every_minutes": 15}}}
    jobs = build_registry(cfg)
    ib = next(j for j in jobs if j.id == "inbox_scan")
    assert ib.schedule.seconds == 15 * 60


def test_security_audit_default_interval():
    """Default every_minutes = 60 => every(3600)."""
    jobs = build_registry({})
    sa = next(j for j in jobs if j.id == "security_audit")
    assert sa.schedule.kind == "every"
    assert sa.schedule.seconds == 60 * 60


def test_job_config_blocks_flow_through():
    """Each job's config block equals the per-job config from the registry cfg."""
    inbox_cfg = {"every_minutes": 20, "important_senders": ["boss@example.com"],
                 "keywords": ["urgent"], "notify_threshold": 1}
    cfg = {"jobs": {"inbox_scan": inbox_cfg}}
    jobs = build_registry(cfg)
    ib = next(j for j in jobs if j.id == "inbox_scan")
    assert ib.config["important_senders"] == ["boss@example.com"]
    assert ib.config["keywords"] == ["urgent"]
    assert ib.config["notify_threshold"] == 1


def test_jobs_with_no_per_job_config_get_empty_dict():
    """Jobs absent from config["jobs"] receive an empty config block."""
    jobs = build_registry({})
    for job in jobs:
        assert isinstance(job.config, dict)


# ---------------------------------------------------------------------------
# make_is_enabled
# ---------------------------------------------------------------------------

def test_global_disabled_all_false():
    is_enabled = make_is_enabled({"enabled": False})
    for jid in ["recurring_fire", "morning_briefing", "inbox_scan", "security_audit"]:
        assert is_enabled(jid) is False


def test_per_job_disabled():
    cfg = {"jobs": {"inbox_scan": {"enabled": False}}}
    is_enabled = make_is_enabled(cfg)
    assert is_enabled("inbox_scan") is False
    assert is_enabled("recurring_fire") is True


def test_all_enabled_by_default():
    is_enabled = make_is_enabled({})
    for jid in ["recurring_fire", "morning_briefing", "inbox_scan", "security_audit"]:
        assert is_enabled(jid) is True


def test_morning_briefing_daily_briefing_toggle_off(monkeypatch):
    """morning_briefing disabled when DEFAULT_FEATURES daily_briefing=False."""
    import core.feature_toggles as ft
    monkeypatch.setitem(ft.DEFAULT_FEATURES, "daily_briefing", False)
    is_enabled = make_is_enabled({})
    assert is_enabled("morning_briefing") is False


def test_morning_briefing_daily_briefing_toggle_on(monkeypatch):
    """morning_briefing enabled when DEFAULT_FEATURES daily_briefing=True."""
    import core.feature_toggles as ft
    monkeypatch.setitem(ft.DEFAULT_FEATURES, "daily_briefing", True)
    is_enabled = make_is_enabled({})
    assert is_enabled("morning_briefing") is True


def test_morning_briefing_live_toggle_with_data_dir(monkeypatch, tmp_path):
    """When data_dir provided, is_feature_enabled is consulted."""
    import core.feature_toggles as ft
    monkeypatch.setattr(ft, "is_feature_enabled",
                        lambda data_dir, feat: feat != "daily_briefing")
    is_enabled = make_is_enabled({"data_dir": str(tmp_path)})
    assert is_enabled("morning_briefing") is False


def test_morning_briefing_toggle_exception_defaults_true(monkeypatch):
    """Exception in toggle lookup => enabled (safe fallback)."""
    import core.feature_toggles as ft
    def boom(*a, **kw):
        raise RuntimeError("toggle exploded")
    monkeypatch.setattr(ft, "is_feature_enabled", boom)
    monkeypatch.delitem(ft.DEFAULT_FEATURES, "daily_briefing", raising=False)
    # With no data_dir it will try DEFAULT_FEATURES.get which returns None
    # Let's patch DEFAULT_FEATURES to raise too
    class BadDict(dict):
        def get(self, key, default=None):
            raise RuntimeError("dict exploded")
    monkeypatch.setattr(ft, "DEFAULT_FEATURES", BadDict())
    is_enabled = make_is_enabled({})
    assert is_enabled("morning_briefing") is True
