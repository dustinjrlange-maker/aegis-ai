"""
Session Manager — Aegis AI
Manages per-user conversation sessions with isolated memory, protocols, and state.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

from core.config import CONFIG, get_path, PROJECT_ROOT, load_capabilities
from core.memory.manager import MemoryManager
from core.memory.character_memory import CharacterMemory
from core.personality.pack_loader import (
    load_personality_pack,
    build_system_prompt,
    get_agent_display_name,
    load_voice_pack,
    load_theme_pack,
)
from core.protocols.registry import ProtocolRegistry
from core.protocols.communications import CommunicationsProtocol
from core.protocols.security import SecurityProtocol
from core.protocols.wellness import WellnessProtocol
from core.protocols.operations import OperationsProtocol
from core.protocols.web import WebProtocol
from core.protocols.google import GoogleProtocol
from core.protocols.command import CommandProtocol
from core.protocols.creative import CreativeProtocol
from core.protocols.email_ops import EmailOpsProtocol
from core.protocols.bracket_commands import BracketCommandProtocol
from core.memory.event_manager import EventManager
from core.memory.mood_manager import MoodManager
from core.memory.contact_manager import ContactManager
from core.memory.crew_files import CrewFilesManager
from core.memory.pinned_messages import PinnedMessageManager
from core.memory.habit_manager import HabitManager
from core.memory.behavior_tracker import BehaviorTracker
from core.memory.time_tracker import TimeTracker
from core.memory.weather_cache import WeatherService
from core.memory.alarm_manager import AlarmManager
from core.memory.file_manager import FileManager
from core.memory.social_manager import SocialMediaManager
from core.accounts.manager import AccountManager
from core.notifications import NotificationService
from core.agent import build_filler_cleaner
from core.auth import load_user_preferences

logger = logging.getLogger(__name__)


class UserSession:
    """Holds all per-user state for an active session."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.created = datetime.now()
        self.last_activity = datetime.now()

        # Load user preferences for pack selection
        prefs = load_user_preferences(user_id)
        personality_name = prefs.get("active_personality",
                                     CONFIG.get("packs", {}).get("active_personality", "default"))
        voice_name = prefs.get("active_voice",
                               CONFIG.get("packs", {}).get("active_voice", "default"))

        # Load packs
        self.personality_pack = load_personality_pack(personality_name)
        self.agent_name = get_agent_display_name(self.personality_pack)
        self.char_memory = CharacterMemory(self.personality_pack.get("memories", {}))

        # Initialize memory scoped to this user
        self.memory = MemoryManager(user_id=user_id)
        self.memory.set_names(self.agent_name)

        # Build system prompt
        core_directives_path = get_path(CONFIG, "personality_prompt")
        with open(core_directives_path, "r", encoding="utf-8") as f:
            core_directives = f.read()

        system_prompt = build_system_prompt(core_directives, self.personality_pack)
        capabilities_prompt = load_capabilities()
        char_context = self.char_memory.get_core_context()
        session_context = self.memory.build_session_context()
        _now = datetime.now()
        date_context = (
            f"CURRENT DATE: {_now.strftime('%A, %B %d, %Y')} ({_now.strftime('%Y-%m-%d')}). "
            "Use this to resolve relative days the user mentions (today, tomorrow, a weekday name)."
        )
        full_prompt = "\n\n".join([p for p in [system_prompt, capabilities_prompt, date_context, char_context, session_context] if p])

        # Conversation state
        self.messages = [{"role": "system", "content": full_prompt}]
        self.system_prompt_base = system_prompt

        # Build response cleaner
        self.clean_reply = build_filler_cleaner(self.personality_pack)

        # Protocol registry (per-user so task files etc. are isolated)
        user_data_dir = self.memory.user_data_dir
        self.protocol_registry = ProtocolRegistry()
        self.protocol_registry.register(SecurityProtocol())
        self.protocol_registry.register(WellnessProtocol())
        self.protocol_registry.register(CommunicationsProtocol())
        # Event manager (local calendar) — created before OperationsProtocol needs it
        self.event_manager = EventManager(user_data_dir)

        self.protocol_registry.register(OperationsProtocol(
            data_dir=user_data_dir, event_manager=self.event_manager))
        self.protocol_registry.register(WebProtocol())
        self.protocol_registry.register(GoogleProtocol(data_dir=user_data_dir))
        self.protocol_registry.register(CommandProtocol())
        self.protocol_registry.register(CreativeProtocol())
        from core.protocols.tooling import ToolingProtocol
        self.protocol_registry.register(ToolingProtocol(username=user_id))

        # Chat-driven email actions — needs a session back-ref for Gmail creds.
        email_ops = EmailOpsProtocol()
        email_ops.attach_session(self)
        self.protocol_registry.register(email_ops)

        # Operations' NLP event detection routes to Google Calendar when
        # connected — give it a session back-ref for the creds lookup.
        self.protocol_registry.get("operations").attach_session(self)

        # Phase 10 managers
        self.mood_manager = MoodManager(user_data_dir)
        self.contact_manager = ContactManager(user_data_dir)
        self.crew_files = CrewFilesManager(user_data_dir)
        self.pinned_messages = PinnedMessageManager(user_data_dir)
        self.habit_manager = HabitManager(user_data_dir)
        self.behavior_tracker = BehaviorTracker(user_data_dir)
        self.time_tracker = TimeTracker(user_data_dir)
        self.weather_service = WeatherService(user_data_dir)
        self.alarm_manager = AlarmManager(user_data_dir)
        self.file_manager = FileManager(user_data_dir)
        self.social_manager = SocialMediaManager(user_data_dir)
        self.accounts = AccountManager(user_data_dir)

        # The account the interactive Mail panel is currently acting on.
        # None = use the default account. Set via POST /api/email/active-account;
        # read by the email endpoints and the chat email handlers.
        self.current_mail_account = None

        # Heartbeat inbox_scan seam (Wave 3.5 Task-8): zero-arg callable.
        from core.accounts.inbox import fetch_unread_all_accounts
        self.fetch_unread_emails = lambda: fetch_unread_all_accounts(self)

        # File context pending injection (set by file analyze endpoint)
        self._pending_file_context = None

        # Trouble-escalation state (escalate-on-trouble mode)
        self._pending_escalation = None   # {"message": str, "ts": datetime} or None
        self._correction_streak = 0

        # Notification service (in-memory, session-scoped)
        self.notification_service = NotificationService()

        # Bracket command protocol — LLM emits [COMMAND: arg] tags
        bracket_proto = BracketCommandProtocol()
        bracket_proto.register_handler("REMEMBER", self._handle_remember)
        bracket_proto.register_handler("ADD_TASK", self._handle_add_task)
        bracket_proto.register_handler("COMPLETE_TASK", self._handle_complete_task)
        bracket_proto.register_handler("REMOVE_TASK", self._handle_remove_task)
        bracket_proto.register_handler("ADD_EVENT", self._handle_add_event)
        bracket_proto.register_handler("ADD_MOOD", self._handle_add_mood)
        bracket_proto.register_handler("ADD_CONTACT", self._handle_add_contact)
        self.protocol_registry.register(bracket_proto)

        # Tracking
        self.last_fact_extraction_index = 0
        self.session_ended = False

        logger.info("Session created for user '%s' (agent: %s)", user_id, self.agent_name)

    # --- Bracket command handlers ---

    def _handle_remember(self, fact_text: str) -> str:
        """Store a fact via the fact store."""
        if self.memory._fact_store:
            report = self.memory._fact_store.ingest(
                [{"key": "general.noted", "value": fact_text}],
                session_id=self.memory.session_id,
            )
            return f"Remembered ({report['added']} new, {report['updated']} updated)"
        return "No fact store available"

    def _handle_add_task(self, task_text: str) -> str:
        """Create a task via the operations protocol.

        Accepts either bare text or text with a trailing "| due: DATE" /
        "| deadline: DATE" / "| by: DATE" suffix. The pipe-suffixed form is
        parsed so the due date lands in its proper field instead of being
        baked into the title (which prevented dedup against NLP-created
        tasks).
        """
        ops = self.protocol_registry.get("operations")
        if not ops:
            return "Operations protocol not available"

        due = None
        due_time = None
        if "|" in task_text:
            parts = [p.strip() for p in task_text.split("|")]
            head = parts[0]
            for segment in parts[1:]:
                seg_lower = segment.lower()
                matched = False
                for prefix in ("due:", "deadline:", "by:"):
                    if seg_lower.startswith(prefix):
                        due_text = segment[len(prefix):].strip()
                        parser = getattr(ops, "_parse_natural_datetime", None)
                        if parser:
                            parsed_d, parsed_t = parser(due_text)
                            due = parsed_d or due_text
                            if parsed_t:
                                due_time = parsed_t
                        else:
                            due = due_text
                        matched = True
                        break
                if matched:
                    continue
                if seg_lower.startswith("time:"):
                    t_raw = segment[len("time:"):].strip()
                    m = re.match(r"^(\d{1,2}):(\d{2})$", t_raw)
                    if m:
                        hh, mm = int(m.group(1)), int(m.group(2))
                        if 0 <= hh <= 23 and 0 <= mm <= 59:
                            due_time = f"{hh:02d}:{mm:02d}"
            task_text = head

        # Clean any date/time language out of the title (Pike often dumps
        # verbose phrasing like "finish milo paws by thursday at 5pm" into
        # the bracket title instead of using the | due: / | time: suffixes).
        extractor = getattr(ops, "_extract_date_time", None)
        if extractor:
            cleaned, extracted_due, extracted_time = extractor(task_text)
            if cleaned:
                task_text = cleaned
            if extracted_due and not due:
                due = extracted_due
            if extracted_time and not due_time:
                due_time = extracted_time

        task = ops.add_task(task_text, due=due, due_time=due_time)
        if task is None:
            return "Task text was empty or duplicate"
        return f"Task #{task['id']} created"

    def _handle_complete_task(self, task_ref: str) -> str:
        """Complete a task by ID ('#3' / '3') or fuzzy title match."""
        ops = self.protocol_registry.get("operations")
        if not ops:
            return "Operations protocol not available"
        match = self._resolve_task_ref(ops, task_ref)
        if match is None:
            return f"No pending task matches '{task_ref}'"
        if ops.complete_task(match["id"]):
            return f"Task #{match['id']} '{match['text']}' completed"
        return f"Task #{match['id']} could not be completed"

    def _handle_remove_task(self, task_ref: str) -> str:
        """Remove (delete) a task by ID ('#3' / '3') or fuzzy title match."""
        ops = self.protocol_registry.get("operations")
        if not ops:
            return "Operations protocol not available"
        match = self._resolve_task_ref(ops, task_ref)
        if match is None:
            return f"No pending task matches '{task_ref}'"
        if ops.remove_task(match["id"]):
            return f"Task #{match['id']} '{match['text']}' removed"
        return f"Task #{match['id']} could not be removed"

    def _resolve_task_ref(self, ops, task_ref: str):
        """Resolve a task reference (numeric ID or fuzzy text) to an actual pending task.

        Returns the task dict on match, or None. Matches against pending tasks
        only — completed tasks are excluded from text-based matching.
        """
        ref = (task_ref or "").strip().lstrip("#").strip()
        if not ref:
            return None
        # Try ID first
        try:
            target_id = int(ref)
            for t in ops.get_pending_tasks():
                if t.get("id") == target_id:
                    return t
            return None
        except ValueError:
            pass
        # Typo-tolerant fuzzy text match — ref word counts as matched if any
        # task word is ≥0.8 SequenceMatcher ratio (handles "finish" vs "finsih").
        from difflib import SequenceMatcher
        ref_words = [w for w in ref.lower().split() if len(w) > 2]
        if not ref_words:
            return None
        best, best_score = None, 0.0
        for t in ops.get_pending_tasks():
            task_words = [w for w in (t.get("text") or "").lower().split() if len(w) > 2]
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
                best = t
        return best if best_score >= 0.5 else None

    _TIME_RANGE_RE = re.compile(
        r"^\s*(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*$"
    )

    def _parse_time_range(self, text):
        """Parse ``HH:MM-HH:MM`` (or ``H-H``) into 24h (start, end) strings.

        Returns ``(None, None)`` if the text is not a recognizable range.
        """
        m = self._TIME_RANGE_RE.match(text or "")
        if not m:
            return None, None
        sh, sm, eh, em = m.group(1), m.group(2) or "00", m.group(3), m.group(4) or "00"
        try:
            sh_i, eh_i = int(sh), int(eh)
        except ValueError:
            return None, None
        if not (0 <= sh_i <= 23 and 0 <= eh_i <= 23):
            return None, None
        return f"{sh_i:02d}:{sm}", f"{eh_i:02d}:{em}"

    def _handle_add_event(self, arg: str) -> str:
        """Create an event from a bracket command.

        Format: ``YYYY-MM-DD | title`` (all-day) or
        ``YYYY-MM-DD | HH:MM-HH:MM | title`` (timed). Events go to Google
        Calendar when the account is connected (they then sync back into the
        Aegis calendar view); otherwise they fall back to the local store.

        Dedupes against tasks created in the same turn: if Pike just emitted
        an [ADD_TASK:] for the same intent, the [ADD_EVENT:] is suppressed.
        The 8B model tends to interpret "have brunch today" as both
        task-worthy AND event-worthy and emit both brackets.
        """
        segments = [s.strip() for s in arg.split("|")]
        if len(segments) < 2:
            return "Invalid format. Use: YYYY-MM-DD | title"
        date_str = segments[0]
        time_start = time_end = None
        title_segments = segments[1:]
        if len(segments) >= 3:
            ts, te = self._parse_time_range(segments[1])
            if ts:
                time_start, time_end = ts, te
                title_segments = segments[2:]
        title = "|".join(title_segments).strip()
        if not title:
            return "Event title is empty"
        # Resolve the date deterministically. qwen3:8b is unreliable at date
        # arithmetic (it put "Wednesday" on the wrong day), so accept the
        # user's day words ("wednesday", "next friday", "tomorrow") and
        # compute the real date in code via the operations parser.
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            ops = self.protocol_registry.get("operations")
            resolved = ops._parse_natural_date(date_str.lower().strip()) if ops else None
            if not resolved:
                return ("Invalid date. Use YYYY-MM-DD or a day like "
                        "'wednesday' / 'next friday'.")
            date_str = resolved

        # Per-turn dedup vs. just-created task
        ops = self.protocol_registry.get("operations")
        if ops:
            from datetime import timedelta
            cutoff = datetime.now() - timedelta(seconds=5)
            title_words = {w.lower() for w in title.split() if len(w) > 2}
            for task in reversed(getattr(ops, "_tasks", [])[-5:]):
                try:
                    created_dt = datetime.fromisoformat(task.get("created", ""))
                except (ValueError, TypeError):
                    continue
                if created_dt < cutoff:
                    break
                task_words = {w.lower() for w in (task.get("text") or "").split() if len(w) > 2}
                if not title_words or not task_words:
                    continue
                overlap = len(title_words & task_words)
                smaller = min(len(title_words), len(task_words))
                if smaller and overlap / smaller >= 0.7:
                    logger.info(
                        "Event skipped — duplicate of recent task '%s'", task["text"]
                    )
                    return f"Event skipped — already created as task '{task['text']}'"

        google_proto = self.protocol_registry.get("google")
        creds = google_proto._get_creds() if google_proto else None
        from core.protocols.google_tools import create_event_or_local
        outcome = create_event_or_local(
            creds, self.event_manager, title, date_str,
            time_start=time_start, time_end=time_end,
        )

        when = date_str
        if time_start and time_end:
            when = f"{date_str} {time_start}-{time_end}"
        elif time_start:
            when = f"{date_str} {time_start}"
        if outcome["source"] == "google":
            acct = self.accounts.default() if getattr(self, "accounts", None) else None
            label = f" ({acct['label']})" if acct and acct.get("label") else ""
            return f"Event '{title}' added to your Google Calendar{label} on {when}"
        return f"Event '{title}' created on {when} (local — Google Calendar not connected)"

    def _handle_add_mood(self, arg: str) -> str:
        """Log mood from bracket command: happy, calm | feeling good."""
        parts = arg.split("|", 1)
        moods_str = parts[0].strip()
        note = parts[1].strip() if len(parts) > 1 else ""
        moods = [m.strip() for m in moods_str.split(",") if m.strip()]
        if not moods:
            return "No moods specified"
        entry = self.mood_manager.add_mood(moods=moods, note=note)
        return f"Mood logged: {', '.join(entry['moods'])}"

    def _handle_add_contact(self, arg: str) -> str:
        """Add contact from bracket command: Name | relationship."""
        parts = arg.split("|", 1)
        name = parts[0].strip()
        relationship = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            return "No contact name specified"
        contact = self.contact_manager.add_contact(name=name, relationship=relationship)
        return f"Contact '{contact['name']}' added"

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity = datetime.now()

    def end(self):
        """End this session — save memory."""
        if not self.session_ended:
            self.memory.end_session_quiet(self.messages)
            self.session_ended = True
            logger.info("Session ended for user '%s'", self.user_id)


class SessionManager:
    """Manages active user sessions."""

    # Sessions older than this (in minutes) are considered stale and auto-ended.
    SESSION_TIMEOUT_MINUTES = 30

    def __init__(self):
        self._sessions: dict[str, UserSession] = {}

    def get_or_create(self, user_id: str, touch: bool = True) -> UserSession:
        """Get an existing session or create a new one for the user.

        If the existing session has been inactive for longer than
        SESSION_TIMEOUT_MINUTES, it is ended (transcript saved) and a
        fresh session is created. This prevents stale context from
        bleeding across conversations.

        Args:
            touch: If True (default), reset last_activity on the session —
                correct for real chat messages. Pass touch=False for background
                accesses (e.g. the heartbeat) that must not reset the idle timer
                and thereby prevent the session from ever going stale.
        """
        existing = self._sessions.get(user_id)
        if existing is not None:
            idle_minutes = (datetime.now() - existing.last_activity).total_seconds() / 60
            if idle_minutes > self.SESSION_TIMEOUT_MINUTES:
                logger.info(
                    "Session for '%s' stale (%.0f min idle), auto-ending.",
                    user_id, idle_minutes,
                )
                self.end_session(user_id)
                existing = None

        if user_id not in self._sessions:
            self._sessions[user_id] = UserSession(user_id)
        session = self._sessions[user_id]
        if touch:
            session.touch()
        return session

    def get(self, user_id: str) -> UserSession | None:
        """Get an existing session without creating one."""
        return self._sessions.get(user_id)

    def end_session(self, user_id: str):
        """End and remove a specific user's session."""
        session = self._sessions.pop(user_id, None)
        if session:
            session.end()

    def end_all(self):
        """End all active sessions (server shutdown)."""
        for user_id in list(self._sessions.keys()):
            self.end_session(user_id)

    def active_users(self) -> list[str]:
        """List currently active user IDs."""
        return list(self._sessions.keys())
