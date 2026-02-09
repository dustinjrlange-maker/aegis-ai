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

    def get_core_context(self):
        """Get all core memories as a context string for the system prompt.
        These are always available to the agent."""
        if not self.core_memories:
            return ""

        lines = ["=== CHARACTER MEMORIES (core) ===",
                 "These are things you remember from your own life. Reference them "
                 "naturally when relevant — don't recite them, let them color your responses."]

        for mem in self.core_memories:
            lines.append(f"- {mem['content']}")

        return "\n".join(lines)

    def get_relevant_memories(self, text, max_results=3):
        """Find character memories relevant to the given text.
        Uses simple keyword/tag matching. Returns formatted context string.

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
            # Filter out very common words
            common = {"the", "a", "an", "is", "are", "was", "were", "and", "or",
                      "to", "in", "on", "at", "for", "of", "it", "that", "this",
                      "you", "i", "my", "your", "his", "her", "we", "they"}
            meaningful_overlap = overlap - common
            score += len(meaningful_overlap)

            if score > 0:
                scored.append((score, mem))

        if not scored:
            return ""

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:max_results]

        lines = []
        for _, mem in top:
            lines.append(f"[character_memory] {mem['content']}")

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
