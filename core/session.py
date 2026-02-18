"""
Session Manager — Aegis AI
Manages per-user conversation sessions with isolated memory, protocols, and state.
"""

import logging
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
from core.protocols.bracket_commands import BracketCommandProtocol
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
        full_prompt = "\n\n".join([p for p in [system_prompt, capabilities_prompt, char_context, session_context] if p])

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
        self.protocol_registry.register(OperationsProtocol(data_dir=user_data_dir))
        self.protocol_registry.register(WebProtocol())
        self.protocol_registry.register(GoogleProtocol(data_dir=user_data_dir))
        self.protocol_registry.register(CommandProtocol())
        self.protocol_registry.register(CreativeProtocol())

        # Bracket command protocol — LLM emits [COMMAND: arg] tags
        bracket_proto = BracketCommandProtocol()
        bracket_proto.register_handler("REMEMBER", self._handle_remember)
        bracket_proto.register_handler("ADD_TASK", self._handle_add_task)
        bracket_proto.register_handler("COMPLETE_TASK", self._handle_complete_task)
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
        """Create a task via the operations protocol."""
        ops = self.protocol_registry.get("operations")
        if ops:
            task = ops.add_task(task_text)
            return f"Task #{task['id']} created"
        return "Operations protocol not available"

    def _handle_complete_task(self, task_ref: str) -> str:
        """Complete a task by ID (accepts '#3' or '3')."""
        ops = self.protocol_registry.get("operations")
        if not ops:
            return "Operations protocol not available"
        try:
            task_id = int(task_ref.strip().lstrip("#"))
        except ValueError:
            return f"Invalid task ID: {task_ref}"
        result = ops.complete_task(task_id)
        return f"Task #{task_id} completed" if result else f"Task #{task_id} not found"

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

    def get_or_create(self, user_id: str) -> UserSession:
        """Get an existing session or create a new one for the user.

        If the existing session has been inactive for longer than
        SESSION_TIMEOUT_MINUTES, it is ended (transcript saved) and a
        fresh session is created. This prevents stale context from
        bleeding across conversations.
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
