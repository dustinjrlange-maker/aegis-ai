"""Build the concrete job list + runtime accessors from the heartbeat config."""

import logging

from core.heartbeat.job import Job, every, daily_at
from core.heartbeat.jobs import (recurring_fire, morning_briefing,
                                 inbox_scan, security_audit)

logger = logging.getLogger("aegis.heartbeat")


def _daily_from_at(at):
    hh, mm = at.split(":")
    return daily_at(int(hh), int(mm))


def build_registry(config):
    """Return the four concrete Job instances configured from *config*.

    ``config`` is the heartbeat section of the master config (the dict under
    the ``"heartbeat"`` key).  Per-job settings live under ``config["jobs"]``
    keyed by job id.  Missing keys fall back to built-in defaults.
    """
    jobs_cfg = config.get("jobs", {})

    def jc(job_id):
        """Per-job config block (empty dict when absent)."""
        return jobs_cfg.get(job_id, {})

    def ch(job_id, default):
        """Per-job channel override; uses *default* when not configured."""
        return jc(job_id).get("channels", default)

    # Compute each interval once so schedule and cooldown can't drift apart.
    inbox_mins = jc("inbox_scan").get("every_minutes", 30)
    audit_mins = jc("security_audit").get("every_minutes", 60)

    return [
        Job(id="recurring_fire", kind="silent", schedule=every(60),
            cooldown_s=60, run=recurring_fire.run,
            config=jc("recurring_fire")),
        Job(id="morning_briefing", kind="notify",
            schedule=_daily_from_at(jc("morning_briefing").get("at", "07:00")),
            cooldown_s=12 * 3600, run=morning_briefing.run,
            # Privacy: default to in-app only so the briefing (tasks + wellness
            # data) never egresses to Telegram unless config explicitly opts in.
            channels=ch("morning_briefing", ["notification"]),
            config=jc("morning_briefing")),
        Job(id="inbox_scan", kind="silent",
            schedule=every(inbox_mins * 60), cooldown_s=inbox_mins * 60,
            run=inbox_scan.run,
            channels=ch("inbox_scan", ["notification"]),
            config=jc("inbox_scan")),
        Job(id="security_audit", kind="silent",
            schedule=every(audit_mins * 60), cooldown_s=audit_mins * 60,
            run=security_audit.run,
            # Channels hardcoded (not from per-job config) on purpose: a security
            # alert must not be silenceable via per-job channel config.
            channels=["notification", "telegram"],
            config=jc("security_audit")),
    ]


def make_is_enabled(config):
    """Return an ``is_enabled(job_id) -> bool`` guard built from *config*.

    Checks (in order):
      1. Global ``config["enabled"]`` (defaults True).
      2. Per-job ``config["jobs"][job_id]["enabled"]`` (defaults True).
      3. For ``morning_briefing``: the ``daily_briefing`` feature toggle from
         ``core.feature_toggles``.  When ``config["data_dir"]`` is set the live
         per-user file is consulted via ``is_feature_enabled(data_dir, ...)``;
         otherwise the module-level ``DEFAULT_FEATURES`` dict is used as the
         fallback so the function never requires disk I/O in tests that do not
         provide a data_dir.
    """
    jobs_cfg = config.get("jobs", {})

    def is_enabled(job_id):
        if not config.get("enabled", True):
            return False
        if not jobs_cfg.get(job_id, {}).get("enabled", True):
            return False
        if job_id == "morning_briefing":
            try:
                import core.feature_toggles as ft
                # data_dir must be the PER-USER data dir where features.json
                # lives (e.g. data/users/{user}/), NOT the top-level data/.
                # The T13 runtime is responsible for wiring the correct path.
                data_dir = config.get("data_dir")
                if data_dir:
                    return bool(ft.is_feature_enabled(data_dir, "daily_briefing"))
                return bool(ft.DEFAULT_FEATURES.get("daily_briefing", True))
            except Exception:
                logger.exception("registry: failed to read daily_briefing toggle")
                return True
        return True

    return is_enabled
