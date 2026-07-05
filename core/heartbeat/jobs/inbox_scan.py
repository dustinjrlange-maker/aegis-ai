"""Silent heartbeat job: scan unread mail, escalate to a push only when
important messages cross a threshold. Importance = known sender OR keyword hit.

Architecture note
-----------------
``fetch_unread`` is kept in this module so ranking logic is unit-testable without
a live mailbox. The real EmailOps seam is wired in Task 13 by attaching a
``fetch_unread_emails`` callable to the session object; until then the job
self-disables gracefully (logs once, no notification, never raises).

Contract: the ``fetch_unread_emails`` callable may raise once real mailbox I/O
is wired; we treat any exception as "unavailable" and return None (self-disable).
"""

import logging
from core.heartbeat.job import JobResult

logger = logging.getLogger("aegis.heartbeat")

_DEFAULT_KEYWORDS = ["urgent", "invoice", "overdue", "call sheet", "contract"]


def fetch_unread(session):
    """Return unread emails as ``[{"from": str, "subject": str}, ...]`` or None.

    Returns None when email is not configured for the current user (the
    ``fetch_unread_emails`` accessor is absent from *session*). Task 13 wires
    the real EmailOps accessor here; no change to this function is needed.

    The ``fetch_unread_emails`` callable may raise (real mailbox I/O); any
    exception is treated as "unavailable" and returns None (self-disable).
    """
    getter = getattr(session, "fetch_unread_emails", None)
    if getter is None:
        return None
    try:
        return getter()
    except Exception:
        logger.exception("inbox fetch_unread failed")
        return None


def _is_important(email, senders, keywords):
    """Return True if *email* matches a known sender or a subject keyword.

    Both comparisons are case-insensitive. Sender match is an exact address
    comparison; keyword match is a substring search on the subject line.
    """
    if email.get("from", "").lower() in {s.lower() for s in senders}:
        return True
    subj = email.get("subject", "").lower()
    return any(k.lower() in subj for k in keywords)


def run(ctx):
    """Scan unread mail and escalate to a push notification when important
    messages meet or exceed *notify_threshold*.

    Config keys (all optional):
      important_senders  -- list of email addresses that always count (default [])
      keywords           -- subject substrings that count (default _DEFAULT_KEYWORDS)
      notify_threshold   -- minimum important-count to push (default 1)

    Returns a silent JobResult when below threshold or email is unconfigured.
    """
    unread = fetch_unread(ctx.session)
    if unread is None:
        logger.debug("inbox_scan: email not configured; self-disabling")
        return JobResult(silent_log="inbox scan: email not configured; skipping")

    senders = ctx.config.get("important_senders", [])
    keywords = ctx.config.get("keywords", _DEFAULT_KEYWORDS)
    threshold = ctx.config.get("notify_threshold", 1)

    important = [e for e in unread if _is_important(e, senders, keywords)]

    log_line = (
        f"inbox scan: {len(unread)} unread, {len(important)} important"
    )

    if len(important) >= threshold:
        lines = "\n".join(
            f"- {e.get('from', '?')}: {e.get('subject', '')}" for e in important
        )
        logger.info("inbox_scan: escalating — %d important email(s)", len(important))
        return JobResult(
            silent_log=log_line,
            notify=True,
            title=f"{len(important)} important email(s)",
            body=lines,
        )

    return JobResult(silent_log=log_line)
