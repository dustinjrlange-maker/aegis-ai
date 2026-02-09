"""
Memory Manager — Aegis AI
Orchestrates all memory systems: transcripts, summaries, facts, search, security.
"""

from datetime import datetime
from core.memory.transcript import save_transcript, list_transcripts
from core.memory.journal import generate_summary, load_recent_summaries
from core.memory.fact_extractor import extract_facts
from core.memory.profile import update_profile, get_profile_summary
from core.memory.knowledge import store_summary, store_facts, get_relevant_context
from core.security.privacy import get_security_context
from core.config import CONFIG


class MemoryManager:
    """Coordinates all of Aegis's memory systems."""

    def __init__(self):
        self.session_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.auto_extract = CONFIG["memory"]["auto_extract_facts"]
        self.auto_summarize = CONFIG["memory"]["auto_summarize"]
        self.agent_name = None
        self.companion_name = None

    def set_names(self, agent_name, companion_name=None):
        """Set the display names used in transcripts."""
        self.agent_name = agent_name
        if companion_name is None:
            companion_name = self._extract_companion_name()
        self.companion_name = companion_name

    def _extract_companion_name(self):
        """Extract companion's preferred name from profile."""
        profile = get_profile_summary()
        if not profile or profile == "No user profile on file.":
            return "Companion"

        # Check for preferred name first (e.g., "Prefers to go by the name")
        import re
        prefer_match = re.search(
            r'(?:prefers?\s+(?:to\s+)?(?:go\s+)?(?:by\s+)?(?:the\s+)?name\s+["\']?)(\w+)',
            profile, re.IGNORECASE
        )
        if prefer_match:
            return prefer_match.group(1)

        # Fall back to NAME field
        for line in profile.split("\n"):
            if "**NAME:**" in line.upper() or line.strip().startswith("- **NAME:"):
                name = line.split(":")[-1].strip().strip("*").strip()
                if name:
                    return name

        # Fall back to profile header
        for line in profile.split("\n"):
            if ("# User Profile" in line or "# Crew Dossier" in line) and "\u2014" in line:
                name = line.split("\u2014")[-1].strip()
                if name:
                    return name

        return "Companion"

    def build_session_context(self):
        """Build context for the agent at the start of a session.
        Returns a string to append to the system prompt with
        user profile info and security protocols."""
        context_parts = []

        # Current date and time — so the agent can reason about schedules
        now = datetime.now()
        day_name = now.strftime("%A")
        date_str = now.strftime("%B %d, %Y")
        time_str = now.strftime("%I:%M %p").lstrip("0")
        is_weekend = day_name in ("Saturday", "Sunday")
        context_parts.append(
            f"Current date: {day_name}, {date_str}. Current time: {time_str}."
            + (" It is the weekend — your companion's regular Monday-Friday job is off today." if is_weekend else "")
        )

        # Security protocols
        context_parts.append(get_security_context())

        # User profile — only include name, not full facts.
        # Full facts are available via memory search when relevant topics come up.
        profile = get_profile_summary()
        if profile and profile != "No user profile on file.":
            # Extract just the name from the profile
            companion_name = "Companion"
            for line in profile.split("\n"):
                if "**NAME:**" in line.upper() or line.strip().startswith("- **NAME:"):
                    companion_name = line.split(":")[-1].strip().strip("*")
                    break
                if "# User Profile" in line and "—" in line:
                    companion_name = line.split("—")[-1].strip()
                    break
            context_parts.append(
                f"\nYour companion's name is {companion_name}. "
                "You have a profile on them but do not reference it unless they bring up a relevant topic."
            )

        # Recent session journals — only include topics, not detailed facts
        recent_logs = load_recent_summaries(count=3)
        if recent_logs:
            context_parts.append(
                "\nYou have past session logs on file. Do not reference their contents unless your companion brings up a relevant topic."
            )

        return "\n\n".join(context_parts)

    def get_relevant_memories(self, user_message):
        """Search knowledge base for memories relevant to current message."""
        return get_relevant_context(
            user_message,
            n_results=CONFIG["memory"]["max_search_results"]
        )

    def end_session(self, messages):
        """Run all end-of-session memory processing.
        Called when the user exits the chat."""
        # Filter out system messages for processing
        chat_messages = [m for m in messages if m["role"] != "system"]

        if len(chat_messages) < 2:
            return

        print()
        print("Aegis: Processing session records...")

        # 1. Save full transcript
        save_transcript(messages, self.session_id,
                        agent_name=self.agent_name,
                        companion_name=self.companion_name)
        print("  — Conversation log archived.")

        # 2. Generate and save summary
        if self.auto_summarize:
            try:
                _, summary_text = generate_summary(messages, self.session_id)
                store_summary(self.session_id, summary_text)
                print("  — Session journal recorded.")
            except Exception as e:
                print(f"  — Warning: Could not generate summary: {e}")

        # 3. Extract and store facts
        if self.auto_extract:
            try:
                facts = extract_facts(messages)
                if facts:
                    update_profile(facts)
                    store_facts(self.session_id, facts)
                    print(f"  — User profile updated with {len(facts)} new entries.")
                else:
                    print("  — No new facts to record.")
            except Exception as e:
                print(f"  — Warning: Could not extract facts: {e}")

        print("  — All records secured. Session complete.")

    def periodic_save(self, messages):
        """Save transcript (always) — cheap, idempotent."""
        chat_messages = [m for m in messages if m["role"] != "system"]
        if len(chat_messages) < 2:
            return
        save_transcript(messages, self.session_id,
                        agent_name=self.agent_name,
                        companion_name=self.companion_name)

    def extract_recent_facts(self, messages, since_index=0):
        """Extract facts from only recent messages (since last extraction)."""
        recent = messages[since_index:]
        if len([m for m in recent if m["role"] != "system"]) < 4:
            return []
        try:
            facts = extract_facts(recent)
            if facts:
                update_profile(facts)
                store_facts(self.session_id, facts)
            return facts
        except Exception:
            return []

    def end_session_quiet(self, messages):
        """Like end_session but without print statements (for server use)."""
        chat_messages = [m for m in messages if m["role"] != "system"]
        if len(chat_messages) < 2:
            return
        save_transcript(messages, self.session_id,
                        agent_name=self.agent_name,
                        companion_name=self.companion_name)
        if self.auto_summarize:
            try:
                _, summary_text = generate_summary(messages, self.session_id)
                store_summary(self.session_id, summary_text)
            except Exception:
                pass
        if self.auto_extract:
            try:
                facts = extract_facts(messages)
                if facts:
                    update_profile(facts)
                    store_facts(self.session_id, facts)
            except Exception:
                pass
