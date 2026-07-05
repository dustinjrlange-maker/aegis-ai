"""Silent heartbeat job: fire due recurring tasks every 60s.

Previously recurring tasks only fired when the user happened to send a
message (check_recurring was called inside the chat pipeline). This job
runs on the heartbeat loop so due items generate regardless of user activity.
"""

from core.heartbeat.job import JobResult


def run(ctx):
    """Check for due recurring tasks and report how many fired.

    Delegates to OperationsProtocol.check_recurring(now=ctx.now) so the
    time-of-day gate (Task 6) is honoured. Never notifies — the caller
    (heartbeat scheduler) decides whether to surface the result.

    Args:
        ctx: JobContext with .session (UserSession), .now (datetime), etc.

    Returns:
        JobResult with a silent_log summary; notify is always False.
    """
    ops = ctx.session.protocol_registry.get("operations")
    fired = ops.check_recurring(now=ctx.now)
    titles = ", ".join(t.get("text", "?") for t in fired) if fired else "none"
    return JobResult(silent_log=f"recurring fired: {len(fired)} ({titles})")
