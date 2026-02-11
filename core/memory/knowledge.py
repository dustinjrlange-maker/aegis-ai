"""
Knowledge Base — Aegis AI
ChromaDB vector store for semantic memory search.
The agent can find relevant memories by meaning, not just keywords.
"""

import logging
from pathlib import Path
import chromadb
from core.config import CONFIG, get_path

logger = logging.getLogger(__name__)


class KnowledgeStore:
    """Per-user ChromaDB vector store for semantic memory search."""

    def __init__(self, db_path=None):
        if db_path is None:
            db_path = get_path(CONFIG, "knowledge_base")
        self._db_path = Path(db_path)
        self._client = None
        self._collection = None

    def _get_collection(self):
        """Get or create the ChromaDB collection."""
        if self._collection is not None:
            return self._collection

        self._db_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._db_path))
        self._collection = self._client.get_or_create_collection(
            name="aegis_memory",
            metadata={"description": "Aegis AI companion memory bank"}
        )
        return self._collection

    def store_memory(self, memory_id, text, metadata=None):
        """Store a memory in the knowledge base for semantic search."""
        collection = self._get_collection()

        if metadata is None:
            metadata = {}

        clean_metadata = {}
        for k, v in metadata.items():
            if v is not None:
                clean_metadata[k] = str(v)

        collection.upsert(
            ids=[memory_id],
            documents=[text],
            metadatas=[clean_metadata]
        )

    def search_memory(self, query, n_results=5, where=None):
        """Search the knowledge base by meaning."""
        collection = self._get_collection()

        if collection.count() == 0:
            return []

        kwargs = {
            "query_texts": [query],
            "n_results": min(n_results, collection.count())
        }
        if where:
            kwargs["where"] = where

        results = collection.query(**kwargs)

        memories = []
        if results and results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                memories.append({
                    "id": results["ids"][0][i],
                    "text": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "distance": results["distances"][0][i] if results["distances"] else None
                })

        return memories

    def store_summary(self, session_id, summary_text):
        """Store a session journal summary for semantic search."""
        self.store_memory(
            memory_id=f"log_{session_id}",
            text=summary_text,
            metadata={
                "type": "session_journal",
                "session_id": session_id
            }
        )

    def store_facts(self, session_id, facts):
        """Store extracted facts for semantic search."""
        for i, fact_entry in enumerate(facts):
            self.store_memory(
                memory_id=f"fact_{session_id}_{i}",
                text=f"{fact_entry['category']}: {fact_entry['fact']}",
                metadata={
                    "type": "companion_fact",
                    "category": fact_entry["category"],
                    "session_id": session_id
                }
            )

    def get_relevant_context(self, user_message, n_results=3, max_distance=1.5):
        """Search for memories relevant to what the user just said."""
        try:
            memories = self.search_memory(user_message, n_results=n_results)
        except Exception as e:
            logger.warning("Memory search failed: %s", e)
            return ""

        if not memories:
            logger.debug("No memories found for: %s", user_message[:80])
            return ""

        relevant = [m for m in memories if m.get("distance") is not None and m["distance"] < max_distance]

        if not relevant:
            distances = [f"{m.get('distance', '?'):.2f}" for m in memories[:3]]
            logger.debug("Memories found but filtered out (distances: %s, threshold: %s): %s",
                          ", ".join(distances), max_distance, user_message[:80])
            return ""

        logger.debug("Found %d relevant memories for: %s", len(relevant), user_message[:80])

        context_lines = []
        for mem in relevant:
            mem_type = mem["metadata"].get("type", "unknown")
            context_lines.append(f"[{mem_type}] {mem['text'][:500]}")

        return "\n".join(context_lines)


# --- Backward-compatible module-level functions ---
# These use a default global instance for terminal/legacy usage.

_default_store = None


def _get_default():
    global _default_store
    if _default_store is None:
        _default_store = KnowledgeStore()
    return _default_store


def store_memory(memory_id, text, metadata=None):
    """Store a memory (backward-compat wrapper)."""
    _get_default().store_memory(memory_id, text, metadata)


def search_memory(query, n_results=5, where=None):
    """Search memories (backward-compat wrapper)."""
    return _get_default().search_memory(query, n_results, where)


def store_summary(session_id, summary_text):
    """Store a session summary (backward-compat wrapper)."""
    _get_default().store_summary(session_id, summary_text)


def store_facts(session_id, facts):
    """Store extracted facts (backward-compat wrapper)."""
    _get_default().store_facts(session_id, facts)


def get_relevant_context(user_message, n_results=3, max_distance=1.5):
    """Get relevant context (backward-compat wrapper)."""
    return _get_default().get_relevant_context(user_message, n_results, max_distance)
