"""Notify heartbeat job: push the narrative morning briefing (local/private).

Runs at 07:00. Calls generate_narrative_briefing with period=None so the
time-of-day auto-detection yields the morning briefing. The call is tagged
sensitivity="private" inside generate_narrative_briefing, so the LLM payload
never leaves the machine.
"""

from core.briefing import generate_narrative_briefing
from core.heartbeat.job import JobResult

_FALLBACK = "Good morning. Nothing pressing on the schedule."


def run(ctx):
    """Return a NOTIFY JobResult carrying the narrative morning briefing.

    Falls back to a default line if the briefing text is empty so a
    notification is always pushed.
    """
    result = generate_narrative_briefing(ctx.session, period=None)
    text = (result.get("narrative") or "" if isinstance(result, dict) else str(result or "")).strip()
    if not text:
        text = _FALLBACK
    return JobResult(
        silent_log="morning briefing pushed",
        notify=True,
        title="Morning briefing",
        body=text,
    )
