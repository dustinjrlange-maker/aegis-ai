"""Assemble the heartbeat runtime from config + singletons.

server/app.py only needs to call build_runtime() and then
    asyncio.create_task(runtime.run())
to bring the full heartbeat online.

Data-dir layout note
--------------------
*data_dir* passed to build_runtime() should be the top-level data directory
(CONFIG["_paths"]["data_root"], i.e. ``data/``).

- State and log files (heartbeat.json, heartbeat_log.jsonl) live at the top
  of data_dir — they are system-level records, not per-user feature files.
- make_is_enabled receives data_dir/"users"/user_id (the per-user dir) so the
  daily_briefing feature toggle reads from the correct features.json.
"""

import asyncio
from datetime import datetime
from pathlib import Path

from core.heartbeat.hlog import HeartbeatLog
from core.heartbeat.notifier import Notifier
from core.heartbeat.registry import build_registry, make_is_enabled
from core.heartbeat.scheduler import run_heartbeat
from core.heartbeat.state import HeartbeatState


class _EnsureSessionManager:
    """Adapter whose ``.get()`` creates-or-returns a live session.

    The heartbeat runs unattended, so the primary user's session may not exist yet
    (sessions are created lazily on the user's first chat). The scheduler and
    Notifier both call ``session_manager.get(user_id)`` expecting a session;
    with a plain SessionManager that returns None until first chat, meaning
    recurring/briefing jobs would never fire proactively (and would crash on
    ``None.session``). Routing ``.get`` through ``get_or_create`` guarantees a
    live session on the first tick (created once, cached thereafter).
    """

    def __init__(self, real_sm):
        self._real = real_sm

    def get(self, user_id):
        # touch=False: heartbeat access must not reset the user's idle timer
        return self._real.get_or_create(user_id, touch=False)


class HeartbeatRuntime:
    """Ready-to-run heartbeat; wraps scheduler with all dependencies wired."""

    def __init__(self, *, session_manager, jobs, is_enabled, notifier, state,
                 hlog, tick_seconds, quiet_hours, user_id):
        self._sm = session_manager
        self._jobs = jobs
        self._is_enabled = is_enabled
        self._notifier = notifier
        self._state = state
        self._hlog = hlog
        self._tick_seconds = tick_seconds
        self._quiet_hours = quiet_hours
        self._user_id = user_id

    async def run(self, *, clock=None, sleep=None, max_ticks=None):
        """Start the scheduler loop. Runs forever unless max_ticks is set.

        clock and sleep are injectable for tests; production callers omit them.
        """
        clock = clock or (lambda: datetime.now())
        sleep = sleep or asyncio.sleep
        await run_heartbeat(
            jobs=self._jobs,
            clock=clock,
            notifier=self._notifier,
            state=self._state,
            hlog=self._hlog,
            is_enabled=self._is_enabled,
            tick_seconds=self._tick_seconds,
            user_id=self._user_id,
            quiet_hours=self._quiet_hours,
            session_manager=self._sm,
            sleep=sleep,
            max_ticks=max_ticks,
        )


def build_runtime(session_manager, *, config, data_dir, get_telegram_app,
                  get_chat_id, user_id):
    """Wire up and return a ready-to-run HeartbeatRuntime.

    Args:
        session_manager: The app-level SessionManager singleton.
        config: The heartbeat section from CONFIG (CONFIG["heartbeat"]).
        data_dir: Top-level data directory (Path or str).
            heartbeat.json and heartbeat_log.jsonl are created directly inside.
            The per-user subdir (data_dir/"users"/user_id) is derived here and
            injected into make_is_enabled so the daily_briefing toggle resolves
            from the correct features.json.
        get_telegram_app: Zero-argument callable returning the running
            python-telegram-bot Application, or None if Telegram is not up.
        get_chat_id: ``(user_id: str) -> int | None`` reverse-lookup from an
            Aegis username to its Telegram chat_id.
        user_id: Primary Aegis username this heartbeat instance runs for.
            REQUIRED — comes from CONFIG["heartbeat"]["primary_user"]; there is
            deliberately no default (a wrong default made Wave 3 run against an
            empty phantom user for two days).
    """
    data_dir = Path(data_dir)
    per_user_dir = data_dir / "users" / user_id
    qh = config.get("quiet_hours", {"start": "22:00", "end": "07:00"})

    # make_is_enabled checks config["data_dir"] for the daily_briefing toggle.
    # It must point to the PER-USER dir where features.json lives, not data/.
    enabled_config = dict(config)
    enabled_config["data_dir"] = str(per_user_dir)

    # Wrap the session manager so job/notifier session lookups create-or-return
    # a live session. Without this the unattended heartbeat sees None until the
    # user first chats (see _EnsureSessionManager). Tests may pass a fake sm
    # that already implements a create-on-get .get(); wrapping is only applied
    # when the real get_or_create method is present.
    sm = _EnsureSessionManager(session_manager) if hasattr(
        session_manager, "get_or_create") else session_manager

    return HeartbeatRuntime(
        session_manager=sm,
        jobs=build_registry(config),
        is_enabled=make_is_enabled(enabled_config),
        notifier=Notifier(sm, get_telegram_app, get_chat_id),
        state=HeartbeatState(data_dir / "heartbeat.json"),
        hlog=HeartbeatLog(data_dir / "heartbeat_log.jsonl"),
        tick_seconds=config.get("tick_seconds", 30),
        quiet_hours=(qh["start"], qh["end"]),
        user_id=user_id,
    )
