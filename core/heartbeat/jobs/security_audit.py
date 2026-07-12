"""Silent heartbeat job: hourly security self-audit.

Silent-logs the full report every run; pushes (both channels) only when a
check fails. Each check is a function (ctx) -> str | None where None means
clean and a non-empty string is a failure message. Checks are independent:
one raising never stops the others. New checks are appended to the
module-level CHECKS list so Wave 6 can extend without a rewrite.

Cloud state is loaded live per call via ``_live_cloud_cfg()`` (so runtime
cloud toggles are caught). The Task 11 registry passes ONLY the static
``security_audit`` config block into ``ctx.config`` — it does not inject
cloud state — so production always takes the live-load path.

Tests may inject ``cloud_cfg`` and/or ``key_present`` into ``ctx.config`` to
override the live load deterministically:
  cloud_cfg   -- a RouterConfig-shaped object (real or stub) overriding
                 ``_live_cloud_cfg()`` for that call
  key_present -- bool overriding live ``resolve_api_key()`` in
                 check_cloud_misconfig
"""

import logging

from core.heartbeat.job import JobResult

logger = logging.getLogger("aegis.heartbeat")


def _live_cloud_cfg():
    """Load the current RouterConfig; returns None on any failure.

    Always performs a fresh load (reads core_config.json + data/llm_router.json
    override) so the audit catches runtime-toggle changes.  Failures are logged
    and never propagated — returning None causes checks to skip gracefully.
    """
    try:
        from core.llm.config import load_config
        return load_config()
    except Exception:
        logger.exception("security_audit: failed to load live RouterConfig")
        return None


def check_cloud_misconfig(ctx):
    """Flag cloud_enabled=True without a resolvable API key.

    A cloud backend that is switched on with no key silently fails on every
    cloud call, producing confusing fallback behaviour. The key is expected at
    data/anthropic_key (or ANTHROPIC_API_KEY env). Real attribute:
    RouterConfig.cloud_enabled (core/llm/config.py).
    """
    cfg = (ctx.config or {}).get("cloud_cfg")
    if cfg is None:
        cfg = _live_cloud_cfg()
    if cfg is None:
        return None
    if not cfg.cloud_enabled:
        return None
    # key_present may be pre-injected by the registry (Task 11) or resolved live.
    key_present = ctx.config.get("key_present")
    if key_present is None:
        try:
            from core.llm.config import resolve_api_key
            key_present = resolve_api_key() is not None
        except Exception:
            key_present = False
    if not key_present:
        return (
            "cloud backend is enabled (cloud_enabled=True) but no API key is "
            "present — cloud calls will fail silently; set ANTHROPIC_API_KEY or "
            "write data/anthropic_key, or disable cloud"
        )
    return None


def check_escalation_consent_invariant(ctx):
    """Flag the broken privacy invariant: trouble-escalation on, consent gate off.

    When cloud_trouble_escalation=True Pike may send a turn to the cloud API
    to recover from a correction loop. trouble_private_consent=True ensures the
    user is warned before any private-tagged payload leaves the machine.
    Disabling the consent gate while escalation is active breaks the core
    privacy contract shipped in Wave 3. Real attributes: RouterConfig fields
    cloud_trouble_escalation and trouble_private_consent (core/llm/config.py).
    """
    cfg = (ctx.config or {}).get("cloud_cfg")
    if cfg is None:
        cfg = _live_cloud_cfg()
    if cfg is None:
        return None
    if cfg.cloud_trouble_escalation and not cfg.trouble_private_consent:
        return (
            "trouble escalation is active (cloud_trouble_escalation=True) but the "
            "private-content consent gate is disabled (trouble_private_consent=False) "
            "— private content may be sent to cloud without user confirmation; "
            "re-enable the consent gate or disable trouble escalation"
        )
    return None


def check_escalation_without_cloud(ctx):
    """Flag trouble-escalation enabled while the cloud backend is off.

    If cloud_trouble_escalation=True but cloud_enabled=False the escalation
    path is wired but dead — correction loops attempt to escalate, find no
    cloud backend, and silently fall back. This is confusing state that usually
    means the user toggled escalation but forgot to enable cloud. Real
    attributes: RouterConfig.cloud_trouble_escalation and .cloud_enabled
    (core/llm/config.py).
    """
    cfg = (ctx.config or {}).get("cloud_cfg")
    if cfg is None:
        cfg = _live_cloud_cfg()
    if cfg is None:
        return None
    if cfg.cloud_trouble_escalation and not cfg.cloud_enabled:
        return (
            "trouble escalation is configured (cloud_trouble_escalation=True) but "
            "the cloud backend is off (cloud_enabled=False) — escalation will "
            "silently fail on every correction loop; either enable cloud or "
            "disable trouble escalation"
        )
    return None


CHECKS = [
    check_cloud_misconfig,
    check_escalation_consent_invariant,
    check_escalation_without_cloud,
]


def run(ctx):
    """Run all security checks and return a JobResult.

    Always silent-logs the outcome. Escalates to a push notification on both
    channels only when at least one check fails. A check that raises is treated
    as a finding (logged, reported) and never propagates to the caller.
    """
    failures = []
    for check in CHECKS:
        try:
            msg = check(ctx)
            if msg:
                failures.append(msg)
        except Exception:
            logger.exception("security check %s crashed", check.__name__)
            failures.append(f"{check.__name__} raised an error (see logs)")
    if failures:
        body = "\n".join(f"- {f}" for f in failures)
        n = len(failures)
        return JobResult(
            silent_log=f"security audit: {n} issue(s)",
            notify=True,
            title="⚠️ Security audit finding",
            body=body,
            # Full findings name config state (consent gate, cloud flags) —
            # keep those off Telegram's servers; send only a pointer there.
            telegram_body=(f"Security audit found {n} "
                           f"issue{'s' if n != 1 else ''} — open Aegis to review."),
            channels=["notification", "telegram"],
        )
    return JobResult(silent_log="security audit: clean (0 issues)")
