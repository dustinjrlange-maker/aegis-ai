"""
Operations Protocol — Aegis AI
Digital assistant capabilities: tasks, calendar, email, file organization.
Handles the companion's daily digital life.
"""

import json
import logging
import re
from datetime import datetime, date, timedelta
from pathlib import Path
from core.protocols.base import Protocol
from core.config import PROJECT_ROOT, CONFIG

logger = logging.getLogger(__name__)


_LEADING_POLITENESS = [
    r"^pike\s*[,:]?\s*",
    r"^hey\s+pike\s*[,:]?\s*",
    r"^hey\s*[,:]?\s*",
    r"^please\s+",
    r"^can\s+you\s+(?:please\s+)?",
    r"^could\s+you\s+(?:please\s+)?",
    r"^would\s+you\s+(?:please\s+)?",
    r"^will\s+you\s+(?:please\s+)?",
]
_LEADING_FILLER = [
    r"^to\s+",
    r"^that\s+i\s+",
    r"^that\s+",
    r"^me\s+to\s+",
    r"^i\s+want\s+to\s+",
    r"^i\s+need\s+to\s+",
    r"^i\s+have\s+to\s+",
    r"^i\s+gotta\s+",
]


def clean_task_title(text):
    """Strip politeness, filler, and stray punctuation from a task title."""
    if not text:
        return text
    t = text.strip()
    t = re.sub(r"^[\s,;:!?.\-]+", "", t)
    changed = True
    while changed:
        changed = False
        for p in _LEADING_POLITENESS:
            new_t = re.sub(p, "", t, flags=re.IGNORECASE)
            if new_t != t:
                t = new_t.strip()
                changed = True
    for p in _LEADING_FILLER:
        new_t = re.sub(p, "", t, flags=re.IGNORECASE)
        if new_t != t:
            t = new_t.strip()
            break
    t = re.sub(r"\s+(please|thanks?|thank\s+you)\s*[.!?]*$", "", t, flags=re.IGNORECASE)
    t = t.rstrip(".,!?;:")
    if t:
        t = t[0].upper() + t[1:]
    return t.strip()


class OperationsProtocol(Protocol):
    """Digital assistant — tasks, scheduling, email, files."""

    # Patterns that suggest task-related intent. Order matters: more specific
    # patterns first so "make a new task to X" wins over a generic verb match.
    TASK_PATTERNS = [
        r"remind\s+me\s+to\s+(.+)",
        r"(?:make|create|add)\s+(?:a\s+)?(?:new\s+)?task\s*[:\-,]?\s*(?:to\s+)?(.+)",
        r"new\s+task\s*[:\-]\s*(.+)",
        r"new\s+task\s+to\s+(.+)",
        r"i\s+need\s+to\s+(.+?)(?:\s+by\s+|\s+before\s+|$)",
        r"don'?t\s+let\s+me\s+forget\s+(?:to\s+)?(.+)",
    ]

    # Patterns for "I finished this" / "mark X done" — fires NLP completion path
    # so the 8B model can't fumble COMPLETE_TASK vs REMOVE_TASK bracket selection.
    TASK_COMPLETE_PATTERNS = [
        r"mark\s+(?:the\s+)?(?:task\s+)?(.+?)\s+(?:as\s+)?(?:done|finished|complete|completed)\b",
        r"^(?:task\s+)?(.+?)\s+is\s+(?:done|finished|complete|completed)\.?\s*$",
        r"i\s*(?:'m|m| am)?\s*(?:just\s+)?(?:finished|completed|done\s+with)\s+(?:the\s+)?(?:task\s+)?(.+)",
        r"^completed\s+(?:the\s+)?(?:task\s+)?(.+)",
        r"^(?:complete|finish|knock\s+out)\s+(?:the\s+)?(?:task\s+)?(.+)",
        r"^check\s+off\s+(?:the\s+)?(?:task\s+)?(.+)",
    ]

    # Patterns for "delete/remove/cancel X" — fires NLP removal path.
    TASK_REMOVE_PATTERNS = [
        r"(?:delete|remove|cancel|scrap|trash|kill)\s+(?:the\s+)?(?:task\s+)?(.+?)(?:\s+task)?\.?\s*$",
        r"get\s+rid\s+of\s+(?:the\s+)?(?:task\s+)?(.+)",
        r"throw\s+out\s+(?:the\s+)?(?:task\s+)?(.+)",
    ]

    # Patterns that suggest calendar event creation
    EVENT_PATTERNS = [
        # "schedule dentist on March 20 at 2pm"
        r"schedule\s+(.+?)\s+(?:on|for)\s+(.+?)(?:\s+at\s+(.+))?$",
        # "add/create an event: X on DATE at TIME"
        r"(?:add|create)\s+(?:an?\s+)?event\s*[:\-]?\s*(.+?)\s+(?:on|for)\s+(.+?)(?:\s+at\s+(.+))?$",
        # "meeting/appointment/call with X on DATE at TIME"
        r"(?:meeting|appointment|call)\s+with\s+(.+?)\s+on\s+(.+?)(?:\s+at\s+(.+))?$",
        # "i have X on DATE at TIME"
        r"i\s+have\s+(?:a\s+|an\s+)?(.+?)\s+on\s+(.+?)(?:\s+at\s+(.+))?$",
    ]

    # Day name lookup
    _DAY_NAMES = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }

    _MONTH_NAMES = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "jun": 6, "jul": 7, "aug": 8, "sep": 9,
        "oct": 10, "nov": 11, "dec": 12,
    }

    def __init__(self, data_dir=None, event_manager=None):
        super().__init__(
            name="operations",
            description="Digital assistant — tasks, calendar, email, file organization",
            priority=Protocol.PRIORITY_NORMAL - 5,  # Just below communications
        )
        if data_dir is not None:
            self.TASK_FILE = Path(data_dir) / "tasks.json"
            self.RECURRING_FILE = Path(data_dir) / "recurring.json"
        else:
            self.TASK_FILE = PROJECT_ROOT / "data" / "tasks.json"
            self.RECURRING_FILE = PROJECT_ROOT / "data" / "recurring.json"
        self._event_manager = event_manager
        self._tasks = []
        self._recurring = []
        self._load_tasks()
        self._load_recurring()

    def _load_tasks(self):
        """Load tasks from disk, backfilling new schema defaults."""
        if self.TASK_FILE.exists():
            try:
                with open(self.TASK_FILE, "r", encoding="utf-8") as f:
                    self._tasks = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._tasks = []
        for task in self._tasks:
            task.setdefault("subtasks", [])
            task.setdefault("starred", False)
            task.setdefault("activity_type", "general")
            task.setdefault("notes", "")
            task.setdefault("attachments", [])
            task.setdefault("due_time", None)

    def _save_tasks(self):
        """Persist tasks to disk."""
        self.TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.TASK_FILE, "w", encoding="utf-8") as f:
            json.dump(self._tasks, f, indent=2, ensure_ascii=False)

    def _load_recurring(self):
        """Load recurring tasks from disk."""
        if self.RECURRING_FILE.exists():
            try:
                with open(self.RECURRING_FILE, "r", encoding="utf-8") as f:
                    self._recurring = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._recurring = []

    def _save_recurring(self):
        """Persist recurring tasks to disk."""
        self.RECURRING_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.RECURRING_FILE, "w", encoding="utf-8") as f:
            json.dump(self._recurring, f, indent=2, ensure_ascii=False)

    # --- Recurring Task Management ---

    def add_recurring(self, text, frequency, time=None, category="general"):
        """Add a recurring task/habit."""
        recurring = {
            "id": max((r["id"] for r in self._recurring), default=0) + 1,
            "text": text,
            "frequency": frequency,  # daily, weekly, weekday, weekend
            "time": time,            # optional preferred time e.g. "18:00"
            "category": category,
            "enabled": True,
            "last_generated": None,
            "created": datetime.now().isoformat(),
        }
        self._recurring.append(recurring)
        self._save_recurring()
        return recurring

    def remove_recurring(self, recurring_id):
        """Remove a recurring task by ID."""
        before = len(self._recurring)
        self._recurring = [r for r in self._recurring if r["id"] != recurring_id]
        if len(self._recurring) < before:
            self._save_recurring()
            return True
        return False

    def list_recurring(self):
        """List all recurring tasks."""
        return [r for r in self._recurring if r.get("enabled", True)]

    def check_recurring(self):
        """Check if any recurring tasks need to generate today's task.

        For each enabled recurring entry, determine if a task should be
        auto-created based on frequency and last_generated date.
        """
        today = date.today()
        today_str = today.isoformat()
        weekday = today.weekday()  # 0=Mon, 6=Sun
        generated = []

        for rec in self._recurring:
            if not rec.get("enabled", True):
                continue

            last = rec.get("last_generated")
            should_generate = False

            if rec["frequency"] == "daily":
                should_generate = (last != today_str)

            elif rec["frequency"] == "weekday":
                should_generate = (weekday < 5 and last != today_str)

            elif rec["frequency"] == "weekend":
                should_generate = (weekday >= 5 and last != today_str)

            elif rec["frequency"] == "weekly":
                # Generate on the same weekday as the created date
                try:
                    created_date = datetime.fromisoformat(rec["created"]).date()
                    created_weekday = created_date.weekday()
                except (ValueError, KeyError):
                    created_weekday = 0  # default to Monday

                if weekday == created_weekday:
                    # Check last_generated is not already this week
                    if last is None:
                        should_generate = True
                    else:
                        try:
                            last_date = date.fromisoformat(last)
                            week_start = today - timedelta(days=weekday)
                            should_generate = (last_date < week_start)
                        except ValueError:
                            should_generate = True

            if should_generate:
                category = rec.get("category", "general")
                task = self.add_task(rec["text"], category=category)
                rec["last_generated"] = today_str
                generated.append(task)

        if generated:
            self._save_recurring()

        return generated

    # --- NLP date/time parsing ---

    @classmethod
    def _parse_natural_date(cls, text):
        """Parse natural date text into YYYY-MM-DD string. Returns None on failure."""
        text = text.strip().lower().rstrip(".,!?")
        today = date.today()

        if text == "today":
            return today.isoformat()
        if text == "tomorrow":
            return (today + timedelta(days=1)).isoformat()
        if text == "yesterday":
            return (today - timedelta(days=1)).isoformat()

        # "next monday", "next friday", etc.
        next_match = re.match(r"next\s+(\w+)", text)
        if next_match:
            day_name = next_match.group(1).lower()
            if day_name in cls._DAY_NAMES:
                target_wd = cls._DAY_NAMES[day_name]
                days_ahead = target_wd - today.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                return (today + timedelta(days=days_ahead)).isoformat()

        # Bare day name: "thursday", "monday"
        if text in cls._DAY_NAMES:
            target_wd = cls._DAY_NAMES[text]
            days_ahead = target_wd - today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            return (today + timedelta(days=days_ahead)).isoformat()

        # "March 20", "march 20th", "Mar 20"
        month_day = re.match(r"(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?", text)
        if month_day:
            month_name = month_day.group(1).lower()
            day_num = int(month_day.group(2))
            if month_name in cls._MONTH_NAMES:
                m = cls._MONTH_NAMES[month_name]
                y = today.year
                try:
                    d = date(y, m, day_num)
                    if d < today:
                        d = date(y + 1, m, day_num)
                    return d.isoformat()
                except ValueError:
                    pass

        # "3/20", "03/20"
        slash_match = re.match(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text)
        if slash_match:
            m = int(slash_match.group(1))
            d_num = int(slash_match.group(2))
            y = int(slash_match.group(3)) if slash_match.group(3) else today.year
            if y < 100:
                y += 2000
            try:
                return date(y, m, d_num).isoformat()
            except ValueError:
                pass

        # YYYY-MM-DD (already formatted)
        iso_match = re.match(r"\d{4}-\d{2}-\d{2}", text)
        if iso_match:
            try:
                date.fromisoformat(iso_match.group())
                return iso_match.group()
            except ValueError:
                pass

        return None

    @classmethod
    def _parse_natural_datetime(cls, text):
        """Parse "<date> at <time>" or "<date> by <time>" into (date_str, time_str).
        Either component can be None. If the text has no recognizable date but
        only a time, returns (None, None) since bare times are ambiguous.
        Time is always returned as 24h "HH:MM"."""
        if not text:
            return (None, None)
        # Split off trailing "at TIME" or "by TIME"
        m = re.search(
            r"\s+(?:at|by)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$",
            text.strip(),
            re.IGNORECASE,
        )
        time_str = None
        date_text = text
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            ampm = (m.group(3) or "").lower()
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            # no am/pm: hour already in 24h form, validated below
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                time_str = f"{hour:02d}:{minute:02d}"
                date_text = text[: m.start()].strip()
            else:
                time_str = None  # invalid time, ignore
                date_text = text[: m.start()].strip()  # still drop the bad tail
        date_str = cls._parse_natural_date(date_text) if date_text else None
        return (date_str, time_str)

    @classmethod
    def _extract_date_time(cls, text):
        """Pull date/time language out of a task description, return cleaned title.

        Designed to be called from the NLP task-creation path BEFORE
        ``clean_task_title``. Recognizes verbose phrasings the conversational
        flow tends to produce, in addition to the trailing forms that
        ``_parse_natural_datetime`` already handles:

        - "(and the )?(set (the )?)?time (for|to|at) HH(:MM)?(am|pm)"
        - "(and the )?(set (the )?)?date (for|to|on) <date>"
        - Trailing "at TIME" / "by TIME"
        - Trailing lone "today" / "tomorrow" / weekday (also after "and"/"with"/"on")

        Returns (cleaned_text, date_str_or_None, time_str_or_None).
        Each component is stripped from the returned text when extracted.
        """
        if not text:
            return (text, None, None)

        work = text.strip()
        due = None
        due_time = None

        # 1. Verbose time phrasing: "set the time for 12:30 pm"
        time_verbose = re.compile(
            r"(?:\s*(?:and|;|,)\s*)?"
            r"(?:set\s+)?(?:the\s+)?"
            r"time\s+(?:for|to|at)\s+"
            r"(\d{1,2})(?::(\d{2}))?\s*"
            r"(am|pm|a\.m\.|p\.m\.)?",
            re.IGNORECASE,
        )
        tm = time_verbose.search(work)
        if tm:
            hour = int(tm.group(1))
            minute = int(tm.group(2) or 0)
            ampm = (tm.group(3) or "").lower().replace(".", "")
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                due_time = f"{hour:02d}:{minute:02d}"
                work = (work[:tm.start()] + " " + work[tm.end():]).strip()

        # 2. Verbose date phrasing: "set the date for june 30th" / "the date for today"
        # Match only KNOWN date forms so we don't overshoot.
        date_verbose = re.compile(
            r"(?:\s*(?:and|;|,)\s*)?"
            r"(?:set\s+)?(?:the\s+)?"
            r"date\s+(?:for|to|on)\s+"
            r"("
            r"today|tomorrow|yesterday|"
            r"(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
            r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
            r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
            r"\s+\d{1,2}(?:st|nd|rd|th)?|"
            r"\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
            r"\d{4}-\d{2}-\d{2}"
            r")",
            re.IGNORECASE,
        )
        dm = date_verbose.search(work)
        if dm:
            parsed_d = cls._parse_natural_date(dm.group(1).strip())
            if parsed_d:
                due = parsed_d
                work = (work[:dm.start()] + " " + work[dm.end():]).strip()

        # 3+4. Loop trailing date + trailing time, peeling off the tail until
        # nothing more matches. Handles chains like "by thursday at 5pm" — first
        # iteration strips "at 5pm", second iteration strips "by thursday".
        trail_date = re.compile(
            r"\s+(?:and\s+|with\s+|on\s+|for\s+|by\s+)?"
            r"(today|tomorrow|yesterday|"
            r"(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))"
            r"\s*\.?\s*$",
            re.IGNORECASE,
        )
        trail_time = re.compile(
            r"\s+(?:at|by)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\s*\.?\s*$",
            re.IGNORECASE,
        )
        for _ in range(4):  # bounded iteration; at most date+time+date+time
            progressed = False
            td = trail_date.search(work)
            if td:
                if not due:
                    parsed_d = cls._parse_natural_date(td.group(1))
                    if parsed_d:
                        due = parsed_d
                work = work[:td.start()].rstrip()
                progressed = True
            if not due_time:
                tm2 = trail_time.search(work)
                if tm2:
                    hour = int(tm2.group(1))
                    minute = int(tm2.group(2) or 0)
                    ampm = (tm2.group(3) or "").lower().replace(".", "")
                    if ampm == "pm" and hour != 12:
                        hour += 12
                    elif ampm == "am" and hour == 12:
                        hour = 0
                    if 0 <= hour <= 23 and 0 <= minute <= 59:
                        due_time = f"{hour:02d}:{minute:02d}"
                        work = work[:tm2.start()].rstrip()
                        progressed = True
            if not progressed:
                break

        # Final cleanup — collapse spaces, strip stray "and" at end, drop stray punct
        work = re.sub(r"\s+", " ", work).strip()
        work = re.sub(r"\s+and\s*$", "", work, flags=re.IGNORECASE).strip()
        work = work.rstrip(".,;:").strip()
        return work, due, due_time

    @staticmethod
    def _parse_natural_time(text):
        """Parse natural time text into HH:MM string. Returns None on failure."""
        if not text:
            return None
        text = text.strip().lower().rstrip(".,!?")

        # Named times
        named = {"noon": "12:00", "midnight": "00:00",
                 "morning": "09:00", "afternoon": "14:00", "evening": "18:00"}
        if text in named:
            return named[text]

        # "3pm", "3:00 PM", "15:00", "3:30pm"
        time_match = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.IGNORECASE)
        if time_match:
            h = int(time_match.group(1))
            m = int(time_match.group(2)) if time_match.group(2) else 0
            ampm = time_match.group(3)
            if ampm:
                ampm = ampm.lower()
                if ampm == "pm" and h < 12:
                    h += 12
                elif ampm == "am" and h == 12:
                    h = 0
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"

        return None

    def process_input(self, user_input, context):
        """Detect task/event intent from natural language and inject context."""
        result = {
            "input": user_input,
            "context_injection": "",
            "intercept": False,
            "response": "",
        }

        # Check recurring tasks — generates any due today (cheap date comparisons)
        self.check_recurring()

        injection_parts = []

        # NLP event detection — auto-create events from conversation
        lower = user_input.lower().strip()
        event_created = False

        if self._event_manager:
            for pattern in self.EVENT_PATTERNS:
                match = re.search(pattern, lower, re.IGNORECASE)
                if match:
                    title = match.group(1).strip().rstrip(".!,")
                    date_text = match.group(2).strip().rstrip(".!,") if match.group(2) else None
                    time_text = match.group(3).strip().rstrip(".!,") if match.lastindex >= 3 and match.group(3) else None

                    parsed_date = self._parse_natural_date(date_text) if date_text else None
                    parsed_time = self._parse_natural_time(time_text) if time_text else None

                    if title and parsed_date and len(title) > 2:
                        event = self._event_manager.add_event(
                            title=title, date=parsed_date,
                            time_start=parsed_time,
                        )
                        time_info = f" at {parsed_time}" if parsed_time else ""
                        injection_parts.append(
                            f"[System: Event created: '{event['title']}' on {parsed_date}{time_info}. "
                            f"Briefly acknowledge in your response. "
                            f"Do NOT emit [SCHEDULE_EVENT:] OR [ADD_TASK:] — "
                            f"the event is already saved. Do not duplicate it as a task.]"
                        )
                        event_created = True
                    break

        # Upcoming event context injection (within 30 minutes)
        if self._event_manager:
            try:
                now = datetime.now()
                today_str = date.today().isoformat()
                day_events = self._event_manager.get_events_for_date(today_str)
                for ev in day_events:
                    ts = ev.get("time_start")
                    if not ts:
                        continue
                    event_dt = datetime.strptime(f"{today_str} {ts}", "%Y-%m-%d %H:%M")
                    diff_min = (event_dt - now).total_seconds() / 60
                    if 0 < diff_min <= 30:
                        injection_parts.append(
                            f"[Event in ~{int(diff_min)} min: '{ev['title']}' at {ts}]"
                        )
                        break  # Only inject one upcoming event
            except Exception:
                pass

        # NLP task detection — auto-create tasks from conversation
        task_created = False
        if not event_created:
            for pattern in self.TASK_PATTERNS:
                match = re.search(pattern, lower, re.IGNORECASE)
                if match:
                    captured = match.group(1)
                    cleaned, due, due_time = self._extract_date_time(captured)
                    task_text = clean_task_title(cleaned)
                    if len(task_text) > 3:
                        task = self.add_task(task_text, due=due, due_time=due_time)
                        if task is not None:
                            task_created = True
                            injection_parts.append(
                                f"[System: A task was auto-detected and saved: "
                                f"'#{task['id']}: {task['text']}'. "
                                f"Briefly acknowledge in your response. "
                                f"Do NOT emit [ADD_TASK:] OR [SCHEDULE_EVENT:] — "
                                f"the task is already saved and the user said 'task', "
                                f"not 'event'. Do not duplicate it as a calendar event.]"
                            )
                    break

        # NLP completion detection — bypasses Pike's bracket selection (which
        # the 8B model often confuses with REMOVE_TASK). If the user clearly
        # says "mark X done" or "X is finished", just do it server-side.
        if not event_created and not task_created:
            for pattern in self.TASK_COMPLETE_PATTERNS:
                match = re.search(pattern, lower, re.IGNORECASE)
                if match:
                    ref = (match.group(1) or "").strip()
                    resolved = self._resolve_pending_task_by_text(ref)
                    if resolved:
                        if self.complete_task(resolved["id"]):
                            injection_parts.append(
                                f"[System: Task #{resolved['id']} '{resolved['text']}' "
                                f"was MARKED COMPLETE (struck through, kept in history). "
                                f"Briefly acknowledge in your response. Do NOT emit any "
                                f"task bracket command — the action is already done.]"
                            )
                    break

            # NLP removal detection
            for pattern in self.TASK_REMOVE_PATTERNS:
                match = re.search(pattern, lower, re.IGNORECASE)
                if match:
                    ref = (match.group(1) or "").strip()
                    resolved = self._resolve_pending_task_by_text(ref)
                    if resolved:
                        if self.remove_task(resolved["id"]):
                            injection_parts.append(
                                f"[System: Task #{resolved['id']} '{resolved['text']}' "
                                f"was DELETED (gone entirely from the list). "
                                f"Briefly acknowledge in your response. Do NOT emit any "
                                f"task bracket command — the action is already done.]"
                            )
                    break

        # Always inject pending tasks — even when a new task was just created
        pending = self.get_pending_tasks()
        overdue = self.get_overdue_tasks()
        if pending:
            task_summary = []
            now = datetime.now()

            if overdue:
                task_summary.append(
                    f"OVERDUE tasks ({len(overdue)}): " +
                    ", ".join(f"#{t['id']}: {t['text']}" for t in overdue[:3])
                )

            high = [t for t in pending if t.get("priority") == "high"]
            if high:
                task_summary.append(
                    f"High priority ({len(high)}): " +
                    ", ".join(f"#{t['id']}: {t['text']}" for t in high[:3])
                )

            # Show age for tasks pending > 3 days
            stale = []
            for t in pending:
                try:
                    created = datetime.fromisoformat(t["created"])
                    age_days = (now - created).days
                    if age_days >= 3:
                        stale.append(f"#{t['id']}: {t['text']} (pending {age_days} days)")
                except (ValueError, KeyError):
                    pass
            if stale:
                task_summary.append("Aging tasks: " + ", ".join(stale[:3]))

            task_summary.append(f"Total pending: {len(pending)}")
            injection_parts.append(
                "[Companion's pending tasks: " +
                "; ".join(task_summary) +
                ". Do NOT mention tasks unless they specifically ask about tasks, plans, or to-do items.]"
            )

        if injection_parts:
            result["context_injection"] = "\n".join(injection_parts)

        return result

    def process_output(self, response, context):
        """Pass through."""
        return {"response": response, "suppress": False, "append": ""}

    # --- Task Management ---

    def _get_speller(self):
        """Lazy-init pyspellchecker with the configured proper-noun whitelist.

        The whitelist combines:
        - operations.spellcheck_whitelist from core_config.json
        - All name tokens from crew_files (if the manager is available)
        Words on the whitelist are taught to the SpellChecker as "correct" so
        they never get flagged as misspellings.
        """
        if getattr(self, "_speller", None) is not None:
            return self._speller, self._spellcheck_whitelist
        try:
            from spellchecker import SpellChecker
        except ImportError:
            self._speller = False  # mark "tried and failed" so we don't retry
            self._spellcheck_whitelist = set()
            return None, None
        spell = SpellChecker(language="en", distance=2)
        whitelist = set()
        ops_cfg = CONFIG.get("operations", {}) if isinstance(CONFIG, dict) else {}
        for w in ops_cfg.get("spellcheck_whitelist", []):
            if w:
                whitelist.add(w.lower())
        try:
            from core.memory.crew_files import CrewFilesManager
            mgr = CrewFilesManager(self.TASK_FILE.parent)
            for crew in mgr.list_profiles():
                for part in (crew.get("name") or "").split():
                    if len(part) > 2:
                        whitelist.add(part.lower())
        except Exception:
            pass
        if whitelist:
            spell.word_frequency.load_words(whitelist)
        self._speller = spell
        self._spellcheck_whitelist = whitelist
        return spell, whitelist

    def _spellcheck_title(self, text: str) -> str:
        """Per-word dictionary spellcheck. Preserves proper nouns via whitelist,
        preserves original capitalization, never adds or removes words.

        Gated by config flag `operations.spellcheck_titles`. Skipped for very
        short titles since there's not much room for typos.
        """
        ops_cfg = CONFIG.get("operations", {}) if isinstance(CONFIG, dict) else {}
        if not ops_cfg.get("spellcheck_titles", True):
            return text
        if not text or len(text.strip()) < 4:
            return text

        spell, whitelist = self._get_speller()
        if spell is None or spell is False:
            return text

        out = []
        changed = False
        for raw_word in text.split():
            # Peel trailing punctuation; preserve to reattach.
            m = re.match(r"^(.*?)([.,!?:;)\]]*)$", raw_word)
            core, trail = m.group(1), m.group(2)
            # Peel possessive "'s" (case-insensitive) for the check.
            pm = re.match(r"^(.+?)('s)$", core, re.IGNORECASE)
            check_part, suffix = (pm.group(1), pm.group(2)) if pm else (core, "")
            lower = check_part.lower()

            # Skip rules: too short, has digit, all-caps acronym, whitelisted,
            # or already a known word.
            if (len(lower) <= 2
                    or any(c.isdigit() for c in lower)
                    or (check_part.isupper() and len(check_part) <= 4)
                    or lower in whitelist
                    or not check_part.isalpha()
                    or lower in spell):
                out.append(raw_word)
                continue

            correction = spell.correction(lower)
            if not correction or correction == lower:
                out.append(raw_word)
                continue
            # Insertion-typos (missing letter) are far more common than
            # extra-letter typos. If the top correction is SHORTER than the
            # original, prefer a same-length-or-longer candidate. Catches
            # "Mke → Make" instead of "Mke → Me" (both edit-distance 1; "Me"
            # wins on frequency but is the wrong direction).
            if len(correction) < len(lower):
                candidates = spell.candidates(lower) or set()
                longer = [c for c in candidates if len(c) >= len(lower)]
                if longer:
                    correction = max(longer, key=lambda c: spell[c])

            # Preserve original capitalization style.
            if check_part.isupper():
                correction = correction.upper()
            elif check_part[0].isupper():
                correction = correction[0].upper() + correction[1:]
            out.append(correction + suffix + trail)
            changed = True

        if not changed:
            return text
        result = " ".join(out)
        logger.info("Spellcheck: %r → %r", text, result)
        return result

    def add_task(self, text, priority="normal", due=None, due_time=None,
                 category="general", activity_type="general"):
        """Add a task to the list. Silently dedupes against a recent similar task.

        Two-tier dedup:
        - Exact match (normalized) within 60s → return existing
        - Fuzzy match (≥70% word overlap) within 30s → return existing
        The fuzzy tier exists because Pike often emits an [ADD_TASK:] bracket
        with a cleaned title for the same user request the NLP path already
        captured with a typo-preserved title.
        """
        text = clean_task_title(text)
        if not text or len(text) < 2:
            return None
        text = self._spellcheck_title(text)
        now_ts = datetime.now()
        norm = text.strip().lower()
        norm_words = [w for w in norm.split() if len(w) > 2]  # ignore tiny words like "to", "a"
        from difflib import SequenceMatcher
        for existing in reversed(self._tasks[-10:]):
            if existing.get("completed"):
                continue
            existing_text = (existing.get("text") or "").strip().lower()
            try:
                created_dt = datetime.fromisoformat(existing.get("created", ""))
                age_sec = (now_ts - created_dt).total_seconds()
            except (ValueError, TypeError):
                continue
            if age_sec >= 60:
                continue
            # Tier 1 — exact match within 60s
            if existing_text == norm:
                return existing
            # Tier 2 — typo-tolerant fuzzy match within 60s. A word counts as
            # matched if any existing word == or has SequenceMatcher ratio ≥ 0.8.
            # Catches "finsih" vs "finish" — the spellcheck LLM is non-deterministic
            # at 8B even at temp 0, so two creations within 60s can diverge.
            existing_words = [w for w in existing_text.split() if len(w) > 2]
            if norm_words and existing_words:
                def _fuzzy_match_count(a, b):
                    n = 0
                    for aw in a:
                        for bw in b:
                            if aw == bw or SequenceMatcher(None, aw, bw).ratio() >= 0.8:
                                n += 1
                                break
                    return n
                matched = _fuzzy_match_count(norm_words, existing_words)
                larger = max(len(norm_words), len(existing_words))
                smaller = min(len(norm_words), len(existing_words))
                # Standard fuzzy: ≥70% of the larger set matched
                fuzzy_hit = larger and matched / larger >= 0.7
                # Containment: the smaller side is fully matched in the larger,
                # AND has at least 2 significant words. Catches Pike emitting
                # "Milo paws" as a nominalized bracket title for the NLP-captured
                # "Finsih the milo paws" — same intent, shorter phrasing.
                contain_hit = smaller >= 2 and matched >= smaller
                if fuzzy_hit or contain_hit:
                    # Backfill due date if the new attempt has one and the existing one doesn't
                    if due and not existing.get("due"):
                        existing["due"] = due
                        self._save_tasks()
                    return existing
        task = {
            "id": len(self._tasks) + 1,
            "text": text,
            "priority": priority,
            "category": category,
            "due": due,
            "due_time": due_time,
            "created": now_ts.isoformat(),
            "completed": False,
            "completed_at": None,
            "subtasks": [],
            "notes": "",
            "starred": False,
            "activity_type": activity_type,
        }
        self._tasks.append(task)
        self._save_tasks()
        return task

    def complete_task(self, task_id):
        """Mark a task as completed."""
        for task in self._tasks:
            if task["id"] == task_id and not task["completed"]:
                task["completed"] = True
                task["completed_at"] = datetime.now().isoformat()
                self._save_tasks()
                return task
        return None

    def uncomplete_task(self, task_id):
        """Revert a completed task back to pending (clears completed_at)."""
        for task in self._tasks:
            if task["id"] == task_id and task.get("completed"):
                task["completed"] = False
                task["completed_at"] = None
                self._save_tasks()
                return task
        return None

    def _resolve_pending_task_by_text(self, text: str):
        """Typo-tolerant fuzzy match of text against pending task titles.

        Counts a ref word as "matched" if any task word is ≥0.8 SequenceMatcher
        ratio (catches typos like 'finish' vs 'finsih'). Final score is matched
        count divided by max(len(ref_words), len(task_words)); threshold 0.5.
        Returns the best task or None.
        """
        if not text:
            return None
        from difflib import SequenceMatcher
        ref_words = [w for w in text.lower().split() if len(w) > 2]
        if not ref_words:
            return None
        best, best_score = None, 0.0
        for task in self.get_pending_tasks():
            task_words = [w for w in (task.get("text") or "").lower().split() if len(w) > 2]
            if not task_words:
                continue
            matched = 0
            for rw in ref_words:
                for tw in task_words:
                    if rw == tw or SequenceMatcher(None, rw, tw).ratio() >= 0.8:
                        matched += 1
                        break
            score = matched / max(len(ref_words), len(task_words))
            if score > best_score:
                best_score = score
                best = task
        return best if best_score >= 0.5 else None

    def remove_task(self, task_id):
        """Remove a task from the list."""
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t["id"] != task_id]
        if len(self._tasks) < before:
            self._save_tasks()
            return True
        return False

    def update_task(self, task_id, **updates):
        """Update allowed fields on a task."""
        allowed = {"text", "priority", "due", "due_time", "activity_type", "starred", "notes"}
        for task in self._tasks:
            if task["id"] == task_id:
                for k, v in updates.items():
                    if k in allowed:
                        task[k] = v
                self._save_tasks()
                return task
        return None

    def add_subtask(self, task_id, text):
        """Add a subtask to a task."""
        for task in self._tasks:
            if task["id"] == task_id:
                task.setdefault("subtasks", [])
                task["subtasks"].append({"text": text, "completed": False})
                self._save_tasks()
                return task
        return None

    def complete_subtask(self, task_id, subtask_idx):
        """Mark a subtask as completed."""
        for task in self._tasks:
            if task["id"] == task_id:
                subs = task.get("subtasks", [])
                if 0 <= subtask_idx < len(subs):
                    subs[subtask_idx]["completed"] = True
                    self._save_tasks()
                    return task
        return None

    def remove_subtask(self, task_id, subtask_idx):
        """Remove a subtask by index."""
        for task in self._tasks:
            if task["id"] == task_id:
                subs = task.get("subtasks", [])
                if 0 <= subtask_idx < len(subs):
                    subs.pop(subtask_idx)
                    self._save_tasks()
                    return task
        return None

    def toggle_star(self, task_id):
        """Toggle the starred flag on a task."""
        for task in self._tasks:
            if task["id"] == task_id:
                task["starred"] = not task.get("starred", False)
                self._save_tasks()
                return task
        return None

    def add_attachment(self, task_id, filename: str):
        """Record an attachment filename on a task. The actual file lives on disk
        at {user_data_dir}/task_attachments/{task_id}/{filename} — this method
        only tracks the filename in the task record."""
        for task in self._tasks:
            if task["id"] == task_id:
                atts = task.setdefault("attachments", [])
                if filename not in atts:
                    atts.append(filename)
                    self._save_tasks()
                return task
        return None

    def remove_attachment(self, task_id, filename: str):
        """Remove an attachment filename from a task record."""
        for task in self._tasks:
            if task["id"] == task_id:
                atts = task.get("attachments", [])
                if filename in atts:
                    atts.remove(filename)
                    self._save_tasks()
                    return task
        return None

    def list_attachments(self, task_id):
        """Return the list of attachment filenames for a task."""
        for task in self._tasks:
            if task["id"] == task_id:
                return list(task.get("attachments", []))
        return []

    def get_pending_tasks(self):
        """Get all incomplete tasks."""
        return [t for t in self._tasks if not t["completed"]]

    @staticmethod
    def task_due_datetime(task):
        """Return a task's full deadline as a local datetime, or None.

        Combines ``due`` (YYYY-MM-DD) and ``due_time`` (HH:MM). Tasks without
        ``due_time`` default to 23:59 so day-only deadlines don't get flagged
        overdue at 12:01 AM. Returns None when ``due`` is missing or the
        stored value can't be parsed.

        This is the canonical helper for any "is this task past its deadline"
        check anywhere in the codebase — UI bucketing, notifications,
        briefing facts, all call this. Don't reimplement.
        """
        if not task:
            return None
        due = task.get("due")
        if not due:
            return None
        try:
            due_date_str = (due or "")[:10]
            due_time_str = task.get("due_time") or "23:59"
            return datetime.strptime(f"{due_date_str} {due_time_str}", "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return None

    @classmethod
    def is_overdue(cls, task, now=None):
        """True if the task has a parseable deadline that's already passed.

        Completed tasks are never considered overdue (even if the deadline
        is past). Pass an explicit ``now`` to test against a specific time,
        otherwise defaults to ``datetime.now()``.
        """
        if not task or task.get("completed"):
            return False
        dt = cls.task_due_datetime(task)
        if dt is None:
            return False
        return dt < (now or datetime.now())

    def get_overdue_tasks(self):
        """Get pending tasks whose deadline is past."""
        now = datetime.now()
        return [t for t in self._tasks if self.is_overdue(t, now=now)]

    def format_task_list(self, tasks=None):
        """Format tasks as a readable list."""
        if tasks is None:
            tasks = self.get_pending_tasks()

        if not tasks:
            return "  No pending tasks."

        lines = []
        for t in tasks:
            status = "x" if t["completed"] else " "
            pri = {"high": "!!", "normal": " ", "low": ".."}[t.get("priority", "normal")]
            due_str = f" (due: {t['due'][:10]})" if t.get("due") else ""
            lines.append(f"  [{status}] {pri} #{t['id']}: {t['text']}{due_str}")
        return "\n".join(lines)

    def get_daily_briefing(self):
        """Generate a daily briefing of tasks and schedule."""
        pending = self.get_pending_tasks()
        overdue = self.get_overdue_tasks()

        lines = ["  DAILY BRIEFING", "  =============="]

        if overdue:
            lines.append(f"  OVERDUE ({len(overdue)}):")
            for t in overdue:
                lines.append(f"    !! #{t['id']}: {t['text']} (was due {t['due'][:10]})")

        high = [t for t in pending if t.get("priority") == "high"]
        if high:
            lines.append(f"  HIGH PRIORITY ({len(high)}):")
            for t in high:
                lines.append(f"    ! #{t['id']}: {t['text']}")

        normal = [t for t in pending if t.get("priority", "normal") == "normal"]
        if normal:
            lines.append(f"  TASKS ({len(normal)}):")
            for t in normal:
                lines.append(f"    #{t['id']}: {t['text']}")

        if not pending:
            lines.append("  All clear. No pending tasks.")

        return "\n".join(lines)

    # --- Commands ---

    def get_commands(self):
        return [
            {"command": "task", "description": "Task management", "handler": "cmd_task"},
            {"command": "tasks", "description": "List pending tasks", "handler": "cmd_list_tasks"},
            {"command": "briefing", "description": "Daily briefing", "handler": "cmd_briefing"},
            {"command": "habit", "description": "Recurring tasks/habits", "handler": "cmd_habit"},
        ]

    def cmd_task(self, args=""):
        """Handle /task commands."""
        parts = args.strip().split(None, 1) if args.strip() else []
        if not parts:
            return self._task_help()

        subcmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if subcmd == "add":
            if not rest:
                return "  Usage: /task add <description>"
            # Parse optional priority: /task add !! call mom
            priority = "normal"
            if rest.startswith("!"):
                priority = "high"
                rest = rest.lstrip("!").strip()
            elif rest.startswith(".."):
                priority = "low"
                rest = rest[2:].strip()
            task = self.add_task(rest, priority=priority)
            return f"  Added task #{task['id']}: {task['text']} [{task['priority']}]"

        elif subcmd == "done":
            try:
                tid = int(rest.strip().lstrip("#"))
            except ValueError:
                return "  Usage: /task done <id>"
            task = self.complete_task(tid)
            if task:
                return f"  Completed: #{task['id']} {task['text']}"
            return f"  Task #{tid} not found or already completed."

        elif subcmd == "remove":
            try:
                tid = int(rest.strip().lstrip("#"))
            except ValueError:
                return "  Usage: /task remove <id>"
            if self.remove_task(tid):
                return f"  Removed task #{tid}."
            return f"  Task #{tid} not found."

        elif subcmd == "list":
            return "\n  Pending Tasks:\n" + self.format_task_list()

        elif subcmd == "all":
            return "\n  All Tasks:\n" + self.format_task_list(self._tasks)

        else:
            return self._task_help()

    def cmd_list_tasks(self, args=""):
        """List pending tasks."""
        return "\n  Pending Tasks:\n" + self.format_task_list()

    def cmd_briefing(self, args=""):
        """Show daily briefing."""
        return "\n" + self.get_daily_briefing()

    def cmd_habit(self, args=""):
        """Handle /habit commands."""
        parts = args.strip().split(None, 1) if args.strip() else []
        if not parts:
            return self._habit_help()

        subcmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if subcmd == "add":
            # Parse: /habit add <frequency> <text>
            habit_parts = rest.strip().split(None, 1) if rest.strip() else []
            if len(habit_parts) < 2:
                return "  Usage: /habit add <frequency> <text>\n  Frequencies: daily, weekly, weekday, weekend"
            frequency = habit_parts[0].lower()
            if frequency not in ("daily", "weekly", "weekday", "weekend"):
                return f"  Unknown frequency '{frequency}'. Use: daily, weekly, weekday, weekend"
            text = habit_parts[1]
            rec = self.add_recurring(text, frequency)
            return f"  Added recurring #{rec['id']}: {rec['text']} [{rec['frequency']}]"

        elif subcmd == "list":
            recurring = self.list_recurring()
            if not recurring:
                return "  No recurring tasks."
            lines = ["", "  Recurring Tasks / Habits:"]
            for r in recurring:
                time_str = f" at {r['time']}" if r.get("time") else ""
                last_str = f" (last: {r['last_generated']})" if r.get("last_generated") else " (never run)"
                lines.append(f"    #{r['id']}: {r['text']} [{r['frequency']}{time_str}]{last_str}")
            return "\n".join(lines)

        elif subcmd == "remove":
            try:
                rid = int(rest.strip().lstrip("#"))
            except ValueError:
                return "  Usage: /habit remove <id>"
            if self.remove_recurring(rid):
                return f"  Removed recurring task #{rid}."
            return f"  Recurring task #{rid} not found."

        else:
            return self._habit_help()

    def _habit_help(self):
        return (
            "\n  Habit Commands:\n"
            "    /habit add <freq> <text>  — Add a recurring task\n"
            "    /habit list               — Show all recurring tasks\n"
            "    /habit remove <id>        — Remove a recurring task\n"
            "  Frequencies: daily, weekly, weekday, weekend"
        )

    def _task_help(self):
        return (
            "\n  Task Commands:\n"
            "    /task add <text>       — Add a task (prefix !! for high priority)\n"
            "    /task done <id>        — Mark task completed\n"
            "    /task remove <id>      — Remove a task\n"
            "    /task list             — Show pending tasks\n"
            "    /task all              — Show all tasks including completed\n"
            "    /tasks                 — Quick list\n"
            "    /briefing              — Daily briefing\n"
            "    /habit                 — Recurring tasks (see /habit for details)"
        )

    def get_status(self):
        status = super().get_status()
        pending = self.get_pending_tasks()
        status["pending_tasks"] = len(pending)
        status["total_tasks"] = len(self._tasks)
        status["overdue"] = len(self.get_overdue_tasks())
        status["recurring_tasks"] = len(self.list_recurring())
        return status
