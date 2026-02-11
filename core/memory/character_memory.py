"""
Character Memory — Aegis AI
Loads and serves character-specific memories from personality packs.
These give the agent a lived-in backstory that it can draw on naturally.
"""


class CharacterMemory:
    """Manages character memories from the active personality pack."""

    def __init__(self, pack_memories=None):
        """Initialize with memories dict from a personality pack.

        Args:
            pack_memories: dict of {filename_stem: {memories: [...]}} from pack_loader
        """
        self.all_memories = []
        self.core_memories = []
        self.secondary_memories = []
        self.by_tag = {}

        if pack_memories:
            self._load(pack_memories)

    def _load(self, pack_memories):
        """Parse and index all memories from pack data."""
        for source_name, data in pack_memories.items():
            memories = data.get("memories", [])
            for mem in memories:
                entry = {
                    "source": source_name,
                    "type": mem.get("type", "unknown"),
                    "content": mem.get("content", ""),
                    "weight": mem.get("weight", "secondary"),
                    "tags": mem.get("tags", []),
                }
                self.all_memories.append(entry)

                if entry["weight"] == "core":
                    self.core_memories.append(entry)
                else:
                    self.secondary_memories.append(entry)

                for tag in entry["tags"]:
                    if tag not in self.by_tag:
                        self.by_tag[tag] = []
                    self.by_tag[tag].append(entry)

    def get_core_context(self, message_count=0):
        """Get core memories as a context string for the system prompt.

        For the first few exchanges (message_count < 10), include all core
        memories so the agent has identity grounding. After that, omit them
        to prevent the model from treating backstory as conversation topics.
        The relevant memories (secondary, topic-matched) still inject via
        get_relevant_memories() on every turn.

        Args:
            message_count: Number of non-system messages in the conversation.
                           Pass 0 to always include (e.g., initial prompt build).
        """
        if not self.core_memories:
            return ""

        # After 10 messages the model has enough context — stop injecting
        # core memories to prevent them from becoming conversation topics.
        if message_count > 10:
            return ""

        lines = [
            "=== YOUR BACKGROUND ===",
            "These are your memories. Only reference if the topic comes up naturally.",
        ]

        for mem in self.core_memories:
            lines.append(f"- {mem['content']}")

        return "\n".join(lines)

    # Common words to exclude from content matching — prevents noise matches
    COMMON_WORDS = {
        # Articles, prepositions, conjunctions
        "the", "a", "an", "is", "are", "was", "were", "and", "or", "but",
        "to", "in", "on", "at", "for", "of", "it", "that", "this", "with",
        "from", "by", "as", "if", "not", "no", "so", "up", "out", "about",
        # Pronouns
        "you", "i", "my", "your", "his", "her", "we", "they", "me", "him",
        "them", "our", "its", "who", "what", "which", "their",
        # Common verbs
        "do", "did", "does", "done", "have", "has", "had", "been", "be",
        "get", "got", "go", "went", "going", "come", "came", "make", "made",
        "take", "took", "know", "knew", "think", "thought", "say", "said",
        "see", "saw", "want", "need", "can", "could", "would", "should",
        "will", "just", "like", "really", "very", "much",
        # Time words
        "today", "yesterday", "tomorrow", "now", "then", "when", "time",
        "day", "week", "month", "year", "morning", "night", "hour",
        # Everyday words
        "thing", "things", "way", "good", "bad", "new", "old", "long",
        "right", "well", "back", "still", "here", "there", "how", "all",
        "some", "any", "more", "also", "than", "too", "only", "even",
    }

    def get_relevant_memories(self, text, max_results=2):
        """Find character memories relevant to the given text.
        Uses keyword/tag matching with a minimum threshold to prevent noise.

        Args:
            text: The user's message to match against.
            max_results: Max number of secondary memories to include.
        """
        if not self.secondary_memories:
            return ""

        text_lower = text.lower()
        scored = []

        for mem in self.secondary_memories:
            score = 0
            # Check tag matches
            for tag in mem["tags"]:
                if tag.lower() in text_lower:
                    score += 2
            # Check content word overlap
            mem_words = set(mem["content"].lower().split())
            text_words = set(text_lower.split())
            overlap = mem_words & text_words
            meaningful_overlap = overlap - self.COMMON_WORDS
            score += len(meaningful_overlap)

            if score >= 4:
                scored.append((score, mem))

        if not scored:
            return ""

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:max_results]

        lines = [
            "[Character backstory -- reference ONLY if the companion's message "
            "directly relates. Do NOT shoehorn into unrelated conversation.]"
        ]
        for _, mem in top:
            lines.append(f"[character_memory -- use ONLY if directly relevant] {mem['content']}")

        return "\n".join(lines)

    def get_all_as_context(self):
        """Get all memories (core + secondary) formatted for context injection."""
        parts = []

        core = self.get_core_context()
        if core:
            parts.append(core)

        if self.secondary_memories:
            parts.append("\n=== CHARACTER MEMORIES (secondary — use when relevant) ===")
            for mem in self.secondary_memories:
                parts.append(f"- [{mem['type']}] {mem['content']}")

        return "\n".join(parts) if parts else ""
