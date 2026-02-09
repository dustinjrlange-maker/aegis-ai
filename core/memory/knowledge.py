"""
Knowledge Base — Aegis AI
ChromaDB vector store for semantic memory search.
The agent can find relevant memories by meaning, not just keywords.
"""

from pathlib import Path
import chromadb
from core.config import CONFIG, get_path


# Persistent ChromaDB client
_client = None
_collection = None


def _get_collection():
    """Get or create the ChromaDB collection."""
    global _client, _collection

    if _collection is not None:
        return _collection

    db_path = get_path(CONFIG, "knowledge_base")
    db_path.mkdir(parents=True, exist_ok=True)

    _client = chromadb.PersistentClient(path=str(db_path))
    _collection = _client.get_or_create_collection(
        name="aegis_memory",
        metadata={"description": "Aegis AI companion memory bank"}
    )
    return _collection


def store_memory(memory_id, text, metadata=None):
    """Store a memory in the knowledge base for semantic search.

    Args:
        memory_id: Unique ID for this memory (e.g., "log_2026-02-07_123456")
        text: The text content to store and make searchable
        metadata: Optional dict with type, session_id, date, etc.
    """
    collection = _get_collection()

    if metadata is None:
        metadata = {}

    # ChromaDB requires string values in metadata
    clean_metadata = {}
    for k, v in metadata.items():
        if v is not None:
            clean_metadata[k] = str(v)

    collection.upsert(
        ids=[memory_id],
        documents=[text],
        metadatas=[clean_metadata]
    )


def search_memory(query, n_results=5, where=None):
    """Search the knowledge base by meaning.

    Args:
        query: Natural language search query
        n_results: Max number of results to return
        where: Optional ChromaDB filter dict (e.g., {"type": "summary"})

    Returns:
        List of dicts with id, text, metadata, and distance (lower = more relevant)
    """
    collection = _get_collection()

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


def store_summary(session_id, summary_text):
    """Store a session journal summary for semantic search."""
    store_memory(
        memory_id=f"log_{session_id}",
        text=summary_text,
        metadata={
            "type": "session_journal",
            "session_id": session_id
        }
    )


def store_facts(session_id, facts):
    """Store extracted facts for semantic search."""
    for i, fact_entry in enumerate(facts):
        store_memory(
            memory_id=f"fact_{session_id}_{i}",
            text=f"{fact_entry['category']}: {fact_entry['fact']}",
            metadata={
                "type": "companion_fact",
                "category": fact_entry["category"],
                "session_id": session_id
            }
        )


def get_relevant_context(user_message, n_results=3, max_distance=1.2):
    """Search for memories relevant to what the user just said.
    Returns formatted context string, or empty string if nothing is relevant.

    Args:
        user_message: The user's current message.
        n_results: Max results to return.
        max_distance: Only include results closer than this threshold.
            Lower = stricter matching. ChromaDB distances typically range 0.0-2.0.
    """
    memories = search_memory(user_message, n_results=n_results)

    if not memories:
        return ""

    # Filter out irrelevant results — ChromaDB always returns something,
    # even if it's a terrible match
    relevant = [m for m in memories if m.get("distance") is not None and m["distance"] < max_distance]

    if not relevant:
        return ""

    context_lines = []
    for mem in relevant:
        mem_type = mem["metadata"].get("type", "unknown")
        context_lines.append(f"[{mem_type}] {mem['text'][:500]}")

    return "\n".join(context_lines)
