"""Tests for core/heartbeat/runtime.py (Task 13 — runtime assembly)."""

import asyncio
from datetime import datetime

from core.heartbeat.runtime import build_runtime, _EnsureSessionManager
from tests.heartbeat.conftest import FakeClock


class _FakeSM:
    """Minimal SessionManager stub: get() always returns None."""

    def get(self, user_id):
        return None


class _RecordingSM:
    """SessionManager stub exposing get_or_create so the runtime adapter wraps it."""

    def __init__(self):
        self.created = []
        self.touch_flags = []  # records the touch= value passed each call
        self.session = object()

    def get_or_create(self, user_id, touch=True):
        self.created.append(user_id)
        self.touch_flags.append(touch)
        return self.session

    def get(self, user_id):
        # Plain .get returns None (lazy sessions) — the adapter must NOT use this.
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


def test_ensure_session_manager_adapter_calls_get_or_create():
    """The adapter routes .get() through the real get_or_create."""
    real = _RecordingSM()
    adapter = _EnsureSessionManager(real)
    session = adapter.get("switch")
    assert real.created == ["switch"]
    assert session is real.session


def test_build_runtime_wraps_real_sm_with_get_or_create():
    """build_runtime wraps a get_or_create-capable sm so jobs get a live session."""
    real = _RecordingSM()
    rt = build_runtime(
        real,
        config=_all_disabled_config(),
        data_dir="ignored",
        get_telegram_app=lambda: None,
        get_chat_id=lambda uid: None,
    )
    # The scheduler-facing session manager must be the wrapping adapter.
    assert isinstance(rt._sm, _EnsureSessionManager)
    # And it resolves sessions via get_or_create (not the lazy .get()).
    assert rt._sm.get("switch") is real.session
    assert real.created == ["switch"]


def test_build_runtime_fake_sm_without_get_or_create_passed_through():
    """A fake sm lacking get_or_create is used as-is (tests keep working)."""
    fake = _FakeSM()
    rt = build_runtime(
        fake,
        config=_all_disabled_config(),
        data_dir="ignored",
        get_telegram_app=lambda: None,
        get_chat_id=lambda uid: None,
    )
    assert rt._sm is fake


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


def test_ensure_session_manager_adapter_calls_with_touch_false():
    """The adapter always calls get_or_create with touch=False (FIX I2)."""
    real = _RecordingSM()
    adapter = _EnsureSessionManager(real)
    adapter.get("switch")
    assert real.touch_flags == [False], (
        "adapter must pass touch=False so the heartbeat never resets the idle timer"
    )


def test_get_or_create_touch_false_does_not_reset_last_activity():
    """get_or_create(touch=False) must not update last_activity (FIX I2)."""
    from core.session import SessionManager
    from datetime import datetime, timedelta

    sm = SessionManager()
    # Create the session first (touch=True is the default — simulates first chat).
    session = sm.get_or_create("switch")
    # Wind back last_activity to simulate user being idle for 10 minutes.
    old_activity = datetime.now() - timedelta(minutes=10)
    session.last_activity = old_activity

    # Heartbeat access must NOT reset last_activity.
    sm.get_or_create("switch", touch=False)
    assert session.last_activity == old_activity, (
        "touch=False must not update last_activity"
    )


def test_get_or_create_touch_true_resets_last_activity():
    """get_or_create(touch=True, the default) must update last_activity (FIX I2 — chat path unchanged)."""
    from core.session import SessionManager
    from datetime import datetime, timedelta

    sm = SessionManager()
    session = sm.get_or_create("switch")
    old_activity = datetime.now() - timedelta(minutes=10)
    session.last_activity = old_activity

    sm.get_or_create("switch", touch=True)
    assert session.last_activity > old_activity, (
        "touch=True (default) must still update last_activity for real chat messages"
    )
