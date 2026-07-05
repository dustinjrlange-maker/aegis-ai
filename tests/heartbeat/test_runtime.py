"""Tests for core/heartbeat/runtime.py (Task 13 — runtime assembly)."""

import asyncio
from datetime import datetime

from core.heartbeat.runtime import build_runtime
from tests.heartbeat.conftest import FakeClock


class _FakeSM:
    """Minimal SessionManager stub: get() always returns None."""

    def get(self, user_id):
        return None


def _all_disabled_config():
    return {
        "enabled": True,
        "tick_seconds": 30,
        "quiet_hours": {"start": "22:00", "end": "07:00"},
        "jobs": {
            "recurring_fire":   {"enabled": False},
            "morning_briefing": {"enabled": False, "at": "07:00"},
            "inbox_scan":       {"enabled": False, "every_minutes": 30},
            "security_audit":   {"enabled": False, "every_minutes": 60},
        },
    }


def test_build_runtime_produces_runnable(tmp_path):
    """build_runtime returns a HeartbeatRuntime that completes one tick cleanly."""
    cfg = _all_disabled_config()
    rt = build_runtime(
        _FakeSM(),
        config=cfg,
        data_dir=tmp_path,
        get_telegram_app=lambda: None,
        get_chat_id=lambda uid: None,
    )
    clock = FakeClock(datetime(2026, 7, 4, 12, 0))

    async def _noop_sleep(_):
        pass

    asyncio.run(rt.run(clock=clock, sleep=_noop_sleep, max_ticks=1))
    # All jobs disabled — one clean tick, no crash.
    assert True


def test_build_runtime_creates_state_and_log_files(tmp_path):
    """State and log files are created inside data_dir (not per-user subdir)."""
    cfg = _all_disabled_config()
    rt = build_runtime(
        _FakeSM(),
        config=cfg,
        data_dir=tmp_path,
        get_telegram_app=lambda: None,
        get_chat_id=lambda uid: None,
    )
    clock = FakeClock(datetime(2026, 7, 4, 12, 0))

    async def _noop_sleep(_):
        pass

    asyncio.run(rt.run(clock=clock, sleep=_noop_sleep, max_ticks=1))

    # heartbeat.json is created by HeartbeatState on first write.
    # heartbeat_log.jsonl is created by HeartbeatLog on first write.
    # With all jobs disabled the scheduler logs skipped_disabled for each,
    # which triggers hlog.write — so the log file should exist after one tick.
    assert (tmp_path / "heartbeat_log.jsonl").exists(), (
        "heartbeat_log.jsonl not created after first tick"
    )


def test_build_runtime_per_user_data_dir_wired(tmp_path):
    """make_is_enabled receives the per-user data dir, not the top-level dir."""
    user_id = "testuser"
    cfg = dict(_all_disabled_config())
    # Enable morning_briefing at the job level so make_is_enabled will try to
    # read the daily_briefing toggle from the per-user features.json.
    cfg["jobs"]["morning_briefing"] = {"enabled": True, "at": "07:00"}

    rt = build_runtime(
        _FakeSM(),
        config=cfg,
        data_dir=tmp_path,
        get_telegram_app=lambda: None,
        get_chat_id=lambda uid: None,
        user_id=user_id,
    )
    clock = FakeClock(datetime(2026, 7, 4, 12, 0))

    async def _noop_sleep(_):
        pass

    # Should not raise even though features.json doesn't exist (falls back to
    # DEFAULT_FEATURES inside make_is_enabled).
    asyncio.run(rt.run(clock=clock, sleep=_noop_sleep, max_ticks=1))
    assert True


def test_build_runtime_global_disabled(tmp_path):
    """When config["enabled"] is False the runtime still constructs cleanly."""
    cfg = dict(_all_disabled_config())
    cfg["enabled"] = False

    rt = build_runtime(
        _FakeSM(),
        config=cfg,
        data_dir=tmp_path,
        get_telegram_app=lambda: None,
        get_chat_id=lambda uid: None,
    )
    clock = FakeClock(datetime(2026, 7, 4, 12, 0))

    async def _noop_sleep(_):
        pass

    asyncio.run(rt.run(clock=clock, sleep=_noop_sleep, max_ticks=1))
    assert True
