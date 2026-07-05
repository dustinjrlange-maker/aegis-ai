"""Narrative briefing generator.

Pulls structured data from session managers, formats it as a facts package,
and asks the active personality's LLM to deliver it as a short in-character brief.
Time-of-day aware: morning brief, midday status, evening recap, late-night minimal.
"""
from datetime import datetime, timedelta
import logging

from core.config import CONFIG
from core.llm import chat as router_chat

logger = logging.getLogger(__name__)


def _time_of_day(now: datetime | None = None) -> str:
    h = (now or datetime.now()).hour
    if 4 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 23:
        return "evening"
    return "late"


_PROMPTS = {
    "morning": (
        "Deliver a morning briefing. 3-5 sentences. Acknowledge the day, surface "
        "the most important items only (overdue, due today, today's events), close "
        "with a forward-looking line. Stay in character. Do not list every field — "
        "synthesize."
    ),
    "afternoon": (
        "Deliver a midday status. 2-4 sentences. What's still pending and what's "
        "coming next. Stay in character."
    ),
    "evening": (
        "Deliver an evening recap. 3-5 sentences. What was on the docket today, "
        "what's still open, and what's on tomorrow if there are events. Stay in "
        "character."
    ),
    "late": (
        "Deliver a brief late-night status. 2-3 sentences. Don't suggest tasks "
        "unless something is overdue. Stay in character."
    ),
}


def collect_briefing_facts(session, period: str | None = None) -> dict:
    """Gather all data sources into a structured dict."""
    now = datetime.now()
    period = period or _time_of_day(now)
    today_str = now.strftime("%Y-%m-%d")
    tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    end_3d = (now + timedelta(days=3)).strftime("%Y-%m-%d")

    # Tasks
    overdue, due_today, high_pri, all_pending = [], [], [], []
    ops = session.protocol_registry.get("operations")
    if ops:
        for task in ops.get_pending_tasks():
            all_pending.append(task)
            if task.get("priority") == "high":
                high_pri.append(task)
            due_dt = ops.task_due_datetime(task)
            if due_dt is not None:
                if due_dt < now:
                    overdue.append(task)
                elif due_dt.strftime("%Y-%m-%d") == today_str:
                    due_today.append(task)

    # Local events
    events_today = session.event_manager.list_events(today_str, today_str)
    events_upcoming = session.event_manager.list_events(tomorrow_str, end_3d)

    # Google calendars — all linked accounts with the feature (best-effort)
    google_today, google_upcoming = [], []
    try:
        accounts = getattr(session, "accounts", None)
        acct_list = accounts.list(feature="briefing_calendar") if accounts else []
        label_items = len(acct_list) > 1   # prefix only when ambiguous
        from core.protocols.google_tools import calendar_upcoming
        for acct in acct_list:
            creds = accounts.creds_for(acct["id"])
            if creds is None:
                continue   # creds_for already marked status="error"
            label = acct.get("label") or acct["id"]
            for ev in calendar_upcoming(creds, days=4):
                ev_date = ev.get("start", "")[:10]
                title = ev.get("summary", "(no title)")
                if label_items:
                    title = f"[{label}] {title}"
                item = {
                    "title": title,
                    "date": ev_date,
                    "time_start": ev.get("start", "")[11:16] or None,
                    "source": "google",
                    "account": label,
                }
                if ev_date == today_str:
                    google_today.append(item)
                elif tomorrow_str <= ev_date <= end_3d:
                    google_upcoming.append(item)
    except Exception as e:
        logger.debug("Google calendar fetch failed in briefing: %s", e)

    # Weather (None if not configured / errored)
    weather = session.weather_service.get_weather()
    if isinstance(weather, dict) and "error" in weather:
        weather = None

    # Unread email count — summed across linked accounts (best-effort)
    unread_email_count = 0
    try:
        accounts = getattr(session, "accounts", None)
        from core.protocols.google_tools import gmail_unread_count
        for acct in (accounts.list() if accounts else []):
            creds = accounts.creds_for(acct["id"])
            if creds is None:
                continue
            unread_email_count += gmail_unread_count(creds)
    except Exception as e:
        logger.debug("Unread email count fetch failed in briefing: %s", e)

    # Phase 10 extras
    habits_today = session.habit_manager.get_today_status()
    moods_today = session.mood_manager.get_today_moods()
    active_timer = session.time_tracker.get_active_timer()
    timer_summary = session.time_tracker.get_today_summary()

    return {
        "period": period,
        "date": today_str,
        "now": now.strftime("%H:%M"),
        "overdue_tasks": overdue,
        "due_today": due_today,
        "high_priority_tasks": high_pri,
        "total_pending": len(all_pending),
        "events_today": events_today + google_today,
        "events_upcoming": events_upcoming + google_upcoming,
        "weather": weather,
        "habits_today": habits_today,
        "moods_today": moods_today,
        "active_timer": active_timer,
        "timer_summary": timer_summary,
        "unread_email_count": unread_email_count,
    }


def _format_facts_for_llm(facts: dict, unit: str = "F") -> str:
    """Render facts as a compact text block the LLM can synthesize from.

    unit: "F" (default) or "C" — the temperature unit to display to Pike so
    the narrative matches the user's UI preference.
    """
    lines = [f"Time: {facts['now']} ({facts['period']}) on {facts['date']}"]

    if facts["weather"]:
        w = facts["weather"]
        temp = w.get("temperature")
        cond = w.get("condition") or "unknown"
        loc = w.get("location") or ""
        weather_line = f"Weather"
        if loc:
            weather_line += f" ({loc})"
        weather_line += f": {cond}"
        if temp is not None:
            if unit.upper() == "C":
                temp_c = round((float(temp) - 32) * 5 / 9)
                weather_line += f", {temp_c}°C"
            else:
                weather_line += f", {round(float(temp))}°F"
        lines.append(weather_line)

    if facts["overdue_tasks"]:
        lines.append(f"OVERDUE TASKS ({len(facts['overdue_tasks'])}):")
        for t in facts["overdue_tasks"][:5]:
            lines.append(f"  - {t['text']} (was due {t.get('due', '')[:10]})")

    if facts["due_today"]:
        lines.append(f"Due today ({len(facts['due_today'])}):")
        for t in facts["due_today"][:5]:
            lines.append(f"  - {t['text']}")

    if facts["high_priority_tasks"]:
        lines.append(f"High priority pending ({len(facts['high_priority_tasks'])}):")
        for t in facts["high_priority_tasks"][:5]:
            lines.append(f"  - {t['text']}")

    if facts["events_today"]:
        lines.append(f"Today's schedule ({len(facts['events_today'])}):")
        for e in facts["events_today"][:6]:
            t = (e.get("time_start") or "").strip()
            title = e.get("title") or e.get("summary") or "(untitled)"
            lines.append(f"  - {t} {title}".strip())

    if facts["events_upcoming"]:
        lines.append(f"Upcoming next 3 days ({len(facts['events_upcoming'])}):")
        for e in facts["events_upcoming"][:5]:
            d = e.get("date", "")
            title = e.get("title") or e.get("summary") or "(untitled)"
            lines.append(f"  - {d}: {title}")

    if facts["active_timer"]:
        at = facts["active_timer"]
        elapsed = at.get("elapsed_seconds", 0)
        mins = elapsed // 60
        label = at.get("label") or at.get("category") or "task"
        lines.append(f"Timer running on '{label}' for {mins}m")

    if facts["habits_today"]:
        done = sum(1 for h in facts["habits_today"] if h.get("done_today"))
        total = len(facts["habits_today"])
        if total:
            lines.append(f"Habits today: {done}/{total} complete")

    if facts.get("unread_email_count"):
        lines.append(f"Unread emails: {facts['unread_email_count']}")

    if not (
        facts["overdue_tasks"]
        or facts["due_today"]
        or facts["high_priority_tasks"]
        or facts["events_today"]
        or facts["high_priority_tasks"]
    ):
        lines.append("No urgent tasks, no scheduled events for today.")

    if facts["total_pending"]:
        lines.append(f"(Total pending tasks in backlog: {facts['total_pending']})")

    return "\n".join(lines)


def generate_narrative_briefing(session, period: str | None = None, unit: str = "F") -> dict:
    """Generate the personality's voiced briefing.

    unit: "F" or "C" — the temperature unit for the narrative. Passed from the
    UI so Pike's prose matches the user's C/F preference.

    Returns: {"narrative": str, "facts": dict, "period": str}
    """
    facts = collect_briefing_facts(session, period)
    period = facts["period"]
    facts_text = _format_facts_for_llm(facts, unit=unit)
    instruction = _PROMPTS.get(period, _PROMPTS["morning"])

    user_prompt = (
        f"{instruction}\n\n"
        f"FACTS (for synthesis — do not quote field names verbatim):\n"
        f"{facts_text}\n\n"
        f"Brief now."
    )

    narrative = ""
    try:
        briefing_content = router_chat(
            [
                {"role": "system", "content": session.system_prompt_base},
                {"role": "user", "content": user_prompt},
            ],
            sensitivity="private",
            task="summarize",
            model=CONFIG["model"]["chat"],
        )
        narrative = session.clean_reply(briefing_content).strip()
    except Exception as e:
        logger.exception("Narrative briefing generation failed")
        narrative = f"[Briefing unavailable — {e}]"

    return {"narrative": narrative, "facts": facts, "period": period}
