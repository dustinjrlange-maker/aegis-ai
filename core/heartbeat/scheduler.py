"""The heartbeat loop. Dependency-injected (jobs, clock, notifier, state, hlog,
is_enabled, sleep) so it runs under a fake clock with no real sleeping.

Quiet hours defers pushes, never runs: a notify-kind job due inside the window
is held; a silent job runs and any escalated push is queued for window end."""

import asyncio
import logging
from datetime import timedelta

from core.heartbeat import job as J

logger = logging.getLogger("aegis.heartbeat")


async def run_heartbeat(*, jobs, clock, notifier, state, hlog, is_enabled,
                        tick_seconds, user_id, quiet_hours, session_manager,
                        sleep=asyncio.sleep, max_ticks=None):
    """Main scheduler loop; iterates until max_ticks (or forever if None).

    Each tick: flush any deferred pushes (if outside quiet hours), then
    evaluate every job in order. Uses dependency injection throughout so
    tests can drive time and notifications without real I/O.

    Args:
        jobs: Sequence of Job instances to evaluate each tick.
        clock: Zero-argument callable returning the current datetime.
        notifier: Object with ``async push(user_id, title, body, channels)``.
        state: HeartbeatState for persistent job records and deferred pushes.
        hlog: HeartbeatLog for structured run records.
        is_enabled: ``(job_id) -> bool`` guard; disabled jobs are skipped.
        tick_seconds: Seconds passed to ``sleep`` between ticks.
        user_id: Identifier of the user owning this heartbeat instance.
        quiet_hours: ``(start_hhmm, end_hhmm)`` window; wraps midnight.
        session_manager: Optional session manager; may be None in tests.
        sleep: Async callable replacing ``asyncio.sleep`` for tests.
        max_ticks: Stop after this many ticks (None = run forever).
    """
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        now = clock()
        try:
            await _flush_deferred(now, quiet_hours, state, notifier)
        except Exception:
            logger.exception("heartbeat: _flush_deferred crashed; continuing tick")
        for job in jobs:
            try:
                await _evaluate_job(job, now, is_enabled, state, hlog, notifier,
                                    quiet_hours, user_id, session_manager)
            except Exception:
                logger.exception("heartbeat job %s crashed", job.id)
                hlog.write(now, job.id, job.kind, "error", "unhandled exception", 0)
        await sleep(tick_seconds)


async def _flush_deferred(now, quiet_hours, state, notifier):
    """Deliver any pushes queued during quiet hours, once outside the window.

    Does nothing if still inside quiet hours; drains and delivers all
    pending push dicts otherwise. When a push dict carries ``job_id`` and
    ``cooldown_s`` (written by ``_evaluate_job``), the job's ``last_fired_at``
    is updated to ``now`` so ``is_due`` won't re-trigger the same job on
    the same tick.
    """
    if J.in_quiet_hours(now, quiet_hours):
        return
    for push in state.drain_pushes():
        await notifier.push(push["user_id"], push["title"], push["body"],
                            push["channels"])
        if "job_id" in push:
            # stamp as fired now so the same-tick evaluate doesn't re-run and double-push
            state.mark_fired(push["job_id"], now,
                             now + timedelta(seconds=push["cooldown_s"]))


async def _evaluate_job(job, now, is_enabled, state, hlog, notifier,
                        quiet_hours, user_id, session_manager):
    """Evaluate one job for the current tick and act accordingly.

    Gate order (each gate returns early if not satisfied):
      1. is_enabled — disabled jobs log skipped_disabled and return.
      2. is_due — not-due jobs return silently (no log, avoids per-tick spam).
      3. cooldown — past-cooldown jobs log skipped_cooldown and return.
      4. quiet-hours / notify-kind — notify jobs held in window; silent jobs
         run and any escalated push is queued rather than sent immediately.

    Jobs execute via asyncio.to_thread since job.run is sync.

    Args:
        job: The Job to evaluate.
        now: Current datetime from the clock.
        is_enabled: Guard callable ``(job_id) -> bool``.
        state: HeartbeatState for reading/writing job records.
        hlog: HeartbeatLog for structured outcomes.
        notifier: Async push delivery.
        quiet_hours: ``(start_hhmm, end_hhmm)`` window.
        user_id: User identifier for notifications and session lookup.
        session_manager: Optional session manager; may be None.
    """
    if not is_enabled(job.id):
        hlog.write(now, job.id, job.kind, "skipped_disabled")
        return

    rec = state.get(job.id)
    last = rec["last_fired_at"] if rec else None

    # Not due — return without logging to avoid per-tick spam.
    if not J.is_due(job.schedule, now, last):
        return

    if rec and rec["next_eligible_at"] and now < rec["next_eligible_at"]:
        hlog.write(now, job.id, job.kind, "skipped_cooldown")
        return

    # Notify-kind jobs are fully held during quiet hours (not run at all).
    if job.kind == "notify" and J.in_quiet_hours(now, quiet_hours):
        hlog.write(now, job.id, job.kind, "skipped_quiet")
        return

    session = session_manager.get(user_id) if session_manager else None
    ctx = J.JobContext(user_id=user_id, session=session, now=now, config=job.config)
    result = await asyncio.to_thread(job.run, ctx)

    state.mark_fired(job.id, now, now + timedelta(seconds=job.cooldown_s))

    if result.notify:
        channels = result.channels or job.channels or ["notification"]
        if J.in_quiet_hours(now, quiet_hours):
            # Silent job with escalated push — queue for window end.
            state.queue_push({"user_id": user_id, "title": result.title,
                              "body": result.body, "channels": channels,
                              "job_id": job.id, "cooldown_s": job.cooldown_s})
            hlog.write(now, job.id, job.kind, "deferred", result.silent_log, 0)
        else:
            await notifier.push(user_id, result.title, result.body, channels)
            hlog.write(now, job.id, job.kind, "notified", result.silent_log, 0)
    else:
        hlog.write(now, job.id, job.kind, "silent_log", result.silent_log, 0)
