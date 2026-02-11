"""
Memory Manager — Aegis AI
Orchestrates all memory systems: transcripts, summaries, facts, search, security.
"""

import json
from datetime import datetime
from pathlib import Path
from core.memory.transcript import save_transcript, list_transcripts
from core.memory.journal import generate_summary, load_recent_summaries
from core.memory.fact_extractor import extract_facts, extract_keyed_facts
from core.memory.profile import update_profile, get_profile_summary
from core.memory.fact_store import FactStore
from core.memory.knowledge import KnowledgeStore, store_summary, store_facts, get_relevant_context
from core.security.privacy import get_security_context
from core.config import CONFIG, PROJECT_ROOT


class MemoryManager:
    """Coordinates all of Aegis's memory systems."""

    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.session_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.auto_extract = CONFIG["memory"]["auto_extract_facts"]
        self.auto_summarize = CONFIG["memory"]["auto_summarize"]
        self.agent_name = None
        self.companion_name = None

        # Per-user data directory
        if user_id == "default":
            self.user_data_dir = None
            self._knowledge = None  # Use global default
            self._fact_store = None
        else:
            self.user_data_dir = PROJECT_ROOT / "data" / "users" / user_id
            self.user_data_dir.mkdir(parents=True, exist_ok=True)
            self._knowledge = KnowledgeStore(self.user_data_dir / "knowledge_base")
            self._fact_store = FactStore(self.user_data_dir)
            # Auto-migrate from legacy profile.md if fact store is empty
            if not self._fact_store.get_all_facts():
                profile_path = self.user_data_dir / "profile.md"
                if profile_path.exists():
                    self._fact_store.migrate_from_profile_md(profile_path)

    def set_names(self, agent_name, companion_name=None):
        """Set the display names used in transcripts."""
        self.agent_name = agent_name
        if companion_name is None:
            companion_name = self._extract_companion_name()
        self.companion_name = companion_name

    def _extract_companion_name(self):
        """Extract companion's preferred name from profile or fact store."""
        # Check structured fact store first
        if self._fact_store:
            name_fact = self._fact_store.get_fact("identity.name")
            if name_fact:
                return name_fact["value"].split()[0]  # First name only

        profile = get_profile_summary(data_dir=self.user_data_dir)
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
        """Build context for the agent at the start of a session."""
        context_parts = []

        # Current date and time with contextual awareness
        now = datetime.now()
        day_name = now.strftime("%A")
        date_str = now.strftime("%B %d, %Y")
        time_str = now.strftime("%I:%M %p").lstrip("0")
        hour = now.hour
        is_weekend = day_name in ("Saturday", "Sunday")

        # Time-of-day label for contextual responses
        if hour < 6:
            time_label = "late night"
        elif hour < 12:
            time_label = "morning"
        elif hour < 13:
            time_label = "around lunchtime"
        elif hour < 17:
            time_label = "afternoon"
        elif hour < 21:
            time_label = "evening"
        else:
            time_label = "late evening"

        time_context = (
            f"Current date: {day_name}, {date_str}. Current time: {time_str} ({time_label})."
        )
        if is_weekend:
            time_context += " Weekend -- companion's regular job is off."
        context_parts.append(time_context)

        # Security protocols
        context_parts.append(get_security_context())

        # User profile — prefer structured fact store over legacy profile.md
        companion_name = self.companion_name or "Companion"
        profile_text = None
        if self._fact_store and self._fact_store.get_all_facts():
            profile_text = self._fact_store.render_profile(companion_name=companion_name)
        else:
            legacy = get_profile_summary(data_dir=self.user_data_dir)
            if legacy and legacy != "No user profile on file.":
                profile_text = legacy

        if profile_text:
            context_parts.append(
                f"=== COMPANION BACKGROUND (not about you) ===\n"
                f"These are {companion_name}'s facts, not yours. This is background knowledge only.\n"
                f"Do NOT proactively reference these details. Only use them if {companion_name} brings up the topic.\n"
                f"{profile_text}"
            )
        else:
            context_parts.append(
                "This is a new companion — you don't have a profile on them yet. "
                "Get to know them through conversation. Key facts will be recorded automatically."
            )

        # Recent session journals (limited to 2 to reduce context pressure)
        recent_logs = load_recent_summaries(count=2, data_dir=self.user_data_dir)
        if recent_logs:
            journal_text = "\n\n---\n\n".join(
                entry["content"] if isinstance(entry, dict) else str(entry)
                for entry in recent_logs
            )
            context_parts.append(
                "Recent sessions (your memory of past conversations):\n"
                "Use for continuity only. Do NOT repeat topics, phrases, or details from these logs.\n\n"
                f"{journal_text}"
            )

        return "\n\n".join(context_parts)

    def get_relevant_memories(self, user_message):
        """Search knowledge base for memories relevant to current message."""
        if self._knowledge:
            return self._knowledge.get_relevant_context(
                user_message,
                n_results=CONFIG["memory"]["max_search_results"]
            )
        return get_relevant_context(
            user_message,
            n_results=CONFIG["memory"]["max_search_results"]
        )

    def end_session(self, messages):
        """Run all end-of-session memory processing."""
        chat_messages = [m for m in messages if m["role"] != "system"]

        if len(chat_messages) < 2:
            return

        print()
        print("Aegis: Processing session records...")

        # 1. Save full transcript
        save_transcript(messages, self.session_id,
                        agent_name=self.agent_name,
                        companion_name=self.companion_name,
                        data_dir=self.user_data_dir)
        print("  -- Conversation log archived.")

        # 2. Generate and save summary
        if self.auto_summarize:
            try:
                _, summary_text = generate_summary(messages, self.session_id,
                                                   data_dir=self.user_data_dir)
                if self._knowledge:
                    self._knowledge.store_summary(self.session_id, summary_text)
                else:
                    store_summary(self.session_id, summary_text)
                print("  -- Session journal recorded.")
            except Exception as e:
                print(f"  -- Warning: Could not generate summary: {e}")

        # 3. Extract and store facts
        if self.auto_extract:
            try:
                if self._fact_store:
                    # Use structured fact store
                    keyed = extract_keyed_facts(messages)
                    if keyed:
                        keyed_dicts = [{"key": k, "value": v} for k, v in keyed]
                        report = self._fact_store.ingest(keyed_dicts, session_id=self.session_id)
                        total = report["added"] + report["updated"]
                        print(f"  -- Facts: {report['added']} new, {report['updated']} updated, "
                              f"{report['duplicates']} confirmed, {report['conflicts']} conflicts.")
                    else:
                        print("  -- No new facts to record.")
                    # Also store in knowledge base for semantic search
                    legacy_facts = extract_facts(messages)
                    if legacy_facts and self._knowledge:
                        self._knowledge.store_facts(self.session_id, legacy_facts)
                else:
                    # Legacy path
                    facts = extract_facts(messages)
                    if facts:
                        update_profile(facts, data_dir=self.user_data_dir)
                        if self._knowledge:
                            self._knowledge.store_facts(self.session_id, facts)
                        else:
                            store_facts(self.session_id, facts)
                        print(f"  -- User profile updated with {len(facts)} new entries.")
                    else:
                        print("  -- No new facts to record.")
            except Exception as e:
                print(f"  -- Warning: Could not extract facts: {e}")

        print("  -- All records secured. Session complete.")

    def periodic_save(self, messages):
        """Save transcript (always) -- cheap, idempotent."""
        chat_messages = [m for m in messages if m["role"] != "system"]
        if len(chat_messages) < 2:
            return
        save_transcript(messages, self.session_id,
                        agent_name=self.agent_name,
                        companion_name=self.companion_name,
                        data_dir=self.user_data_dir)

    def extract_recent_facts(self, messages, since_index=0):
        """Extract facts from only recent messages (since last extraction)."""
        recent = messages[since_index:]
        if len([m for m in recent if m["role"] != "system"]) < 4:
            return []
        try:
            if self._fact_store:
                keyed = extract_keyed_facts(recent)
                if keyed:
                    keyed_dicts = [{"key": k, "value": v} for k, v in keyed]
                    self._fact_store.ingest(keyed_dicts, session_id=self.session_id)
                # Also store in knowledge base
                facts = extract_facts(recent)
                if facts and self._knowledge:
                    self._knowledge.store_facts(self.session_id, facts)
                return facts
            else:
                facts = extract_facts(recent)
                if facts:
                    update_profile(facts, data_dir=self.user_data_dir)
                    if self._knowledge:
                        self._knowledge.store_facts(self.session_id, facts)
                    else:
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
                        companion_name=self.companion_name,
                        data_dir=self.user_data_dir)
        if self.auto_summarize:
            try:
                _, summary_text = generate_summary(messages, self.session_id,
                                                   data_dir=self.user_data_dir)
                if self._knowledge:
                    self._knowledge.store_summary(self.session_id, summary_text)
                else:
                    store_summary(self.session_id, summary_text)
            except Exception:
                pass
        if self.auto_extract:
            try:
                if self._fact_store:
                    keyed = extract_keyed_facts(messages)
                    if keyed:
                        keyed_dicts = [{"key": k, "value": v} for k, v in keyed]
                        self._fact_store.ingest(keyed_dicts, session_id=self.session_id)
                    facts = extract_facts(messages)
                    if facts and self._knowledge:
                        self._knowledge.store_facts(self.session_id, facts)
                else:
                    facts = extract_facts(messages)
                    if facts:
                        update_profile(facts, data_dir=self.user_data_dir)
                        if self._knowledge:
                            self._knowledge.store_facts(self.session_id, facts)
                        else:
                            store_facts(self.session_id, facts)
            except Exception:
                pass

    # --- Summary Methods ---

    def generate_summary(self):
        """Generate a quick-access memory summary JSON for this user."""
        if not self.user_data_dir:
            return {}

        from core.memory.profile import get_profile_facts

        facts = get_profile_facts(data_dir=self.user_data_dir)
        transcripts = list_transcripts(data_dir=self.user_data_dir)

        # Get recent topics from journals
        recent_journals = load_recent_summaries(count=5, data_dir=self.user_data_dir)
        recent_topics = []
        for journal in recent_journals:
            content = journal.get("content", "")
            for line in content.split("\n"):
                if line.startswith("TOPICS:"):
                    topics = line.replace("TOPICS:", "").strip().strip("[]")
                    recent_topics.extend([t.strip() for t in topics.split(",") if t.strip()])

        summary = {
            "user": self.user_id,
            "generated": datetime.now().isoformat(),
            "key_facts": [f["fact"] for f in facts[:20]],
            "recent_topics": recent_topics[:10],
            "relationship_stage": "established" if facts else "new",
            "stats": {
                "total_sessions": len(transcripts),
                "total_facts": len(facts),
            },
        }

        summary_path = self.user_data_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return summary

    def load_summary(self):
        """Load the quick-access memory summary for this user."""
        if not self.user_data_dir:
            return {}

        summary_path = self.user_data_dir / "summary.json"
        if summary_path.exists():
            try:
                return json.loads(summary_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                pass
        return {}
