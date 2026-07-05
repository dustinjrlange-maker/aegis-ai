"""Notify heartbeat job: deliver the narrative morning briefing.

Runs at 07:00. Calls generate_narrative_briefing with period=None so the
time-of-day auto-detection yields the morning briefing.

Privacy note:
- LLM *inference* is local: the call is tagged sensitivity="private" inside
  generate_narrative_briefing, so the model payload never leaves the machine.
- The *assembled briefing text* (tasks, wellness/mood data) is delivered to
  whatever channels config specifies (heartbeat.jobs.morning_briefing.channels).
  The default is ["notification"] — in-app only, nothing leaves the machine.
  If the user adds "telegram" to that list the full briefing text, including
  personal/wellness data, will be sent to Telegram (an external service).
"""

from core.briefing import generate_narrative_briefing
from core.heartbeat.job import JobResult

_FALLBACK = "Good morning. Nothing pressing on the schedule."


def run(ctx):
    """Return a NOTIFY JobResult carrying the narrative morning briefing.

    Falls back to a default line if the briefing text is empty so a
    notification is always pushed. If the LLM is unavailable generate_narrative_briefing
    returns a "[Briefing unavailable — ...]" sentinel; in that case the job returns
    silently (notify=False) rather than pushing an error string as the briefing.
    """
    if ctx.session is None:
        return JobResult(silent_log="morning_briefing: no active session")
    result = generate_narrative_briefing(ctx.session, period=None)
    text = (result.get("narrative") or "" if isinstance(result, dict) else str(result or "")).strip()
    if text.startswith("[Briefing unavailable"):
        return JobResult(silent_log=f"morning_briefing: skipped — {text}")
    if not text:
        text = _FALLBACK
    return JobResult(
        silent_log="morning briefing pushed",
        notify=True,
        title="Morning briefing",
        body=text,
    )
