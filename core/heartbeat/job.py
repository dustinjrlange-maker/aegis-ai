"""Heartbeat job model + pure scheduling predicates (no I/O)."""

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Schedule:
    """When a job fires; `kind` is the schedule type ("every" | "daily_at")."""
    kind: str                       # "every" | "daily_at"
    seconds: Optional[int] = None   # for "every"
    hh: Optional[int] = None        # for "daily_at"
    mm: Optional[int] = None


def every(seconds: int) -> Schedule:
    """Build an interval schedule firing every `seconds` seconds."""
    return Schedule("every", seconds=seconds)


def daily_at(hh: int, mm: int) -> Schedule:
    """Build a schedule firing once per day at `hh`:`mm`."""
    return Schedule("daily_at", hh=hh, mm=mm)


@dataclass
class JobResult:
    """What a job's run() returns. silent_log is always recorded; notify
    requests a user push (a silent job may set notify=True to escalate)."""
    silent_log: str
    notify: bool = False
    title: str = ""
    body: str = ""
    channels: Optional[list[str]] = None   # override Job.channels when set


@dataclass
class JobContext:
    """Everything a job needs to run. session exposes notification_service,
    ops (operations protocol), event_manager, etc."""
    user_id: str
    session: Any
    now: datetime
    config: dict                      # this job's config block


@dataclass
class Job:
    """A schedulable unit of work.

    `kind` is the job's *disposition* ("silent" | "notify") — its normal
    behaviour when it runs — and is distinct from `Schedule.kind`, which is the
    schedule *type* ("every" | "daily_at"). The two `kind`s are unrelated.

    `config` is the per-job configuration block from the heartbeat config; it is
    passed verbatim into ``JobContext.config`` at run time so jobs can read their
    own settings without a global config import.
    """
    id: str
    kind: str                         # "silent" | "notify" (normal disposition)
    schedule: Schedule
    cooldown_s: int
    run: Callable[[JobContext], JobResult]
    channels: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)


def is_due(schedule: Schedule, now: datetime,
           last_fired_at: Optional[datetime]) -> bool:
    """Return True if `schedule` is due at `now` given its last fire time."""
    if schedule.kind == "every":
        if last_fired_at is None:
            return True
        return (now - last_fired_at).total_seconds() >= schedule.seconds
    if schedule.kind == "daily_at":
        target = time(schedule.hh, schedule.mm)
        if now.time() < target:
            return False
        if last_fired_at is None:
            return True
        # Invariant (upheld by Task 2 state-writing): a daily_at job's
        # last_fired_at is only ever stamped at/after its target time, so
        # last_fired_at.date() < now.date() means "hasn't fired since today's target."
        return last_fired_at.date() < now.date()
    raise ValueError(f"unknown schedule kind: {schedule.kind}")


def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def in_quiet_hours(now: datetime, window: tuple[str, str]) -> bool:
    """window is (start, end) as 'HH:MM' strings; wraps midnight. End exclusive."""
    start, end = _parse_hhmm(window[0]), _parse_hhmm(window[1])
    t = now.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end     # wraps midnight
