"""
Tests for multi-user memory isolation — verify no cross-contamination
between users' profiles, transcripts, and ChromaDB knowledge bases.
"""

import sys
from pathlib import Path
import json
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.auth import create_user, active_sessions, USERS_FILE, USERS_DIR
from core.memory.manager import MemoryManager
from core.memory.profile import update_profile, get_profile_summary, get_profile_facts
from core.memory.transcript import save_transcript, list_transcripts
from core.memory.knowledge import KnowledgeStore


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Redirect all data to temp directory."""
    test_users_file = tmp_path / "users.json"
    test_users_dir = tmp_path / "users"
    monkeypatch.setattr("core.auth.USERS_FILE", test_users_file)
    monkeypatch.setattr("core.auth.USERS_DIR", test_users_dir)
    monkeypatch.setattr("core.memory.manager.PROJECT_ROOT", tmp_path)
    active_sessions.clear()
    yield
    active_sessions.clear()


@pytest.fixture
def two_users(tmp_path):
    """Create two test users with data directories."""
    for name in ["alice", "bob"]:
        create_user(name, name.title(), "pass1234")
        user_dir = tmp_path / "data" / "users" / name
        user_dir.mkdir(parents=True, exist_ok=True)
        for sub in ["conversation_logs", "session_journals", "knowledge_base", "security_protocols"]:
            (user_dir / sub).mkdir(exist_ok=True)
    return "alice", "bob"


class TestProfileIsolation:
    def test_profiles_are_separate(self, two_users, tmp_path):
        alice, bob = two_users
        alice_dir = tmp_path / "data" / "users" / alice
        bob_dir = tmp_path / "data" / "users" / bob

        # Add facts to Alice's profile
        update_profile(
            [{"category": "Personal", "fact": "Alice likes hiking"}],
            data_dir=alice_dir,
        )

        # Add different facts to Bob's profile
        update_profile(
            [{"category": "Personal", "fact": "Bob likes swimming"}],
            data_dir=bob_dir,
        )

        # Verify isolation
        alice_facts = get_profile_facts(data_dir=alice_dir)
        bob_facts = get_profile_facts(data_dir=bob_dir)

        alice_fact_texts = [f["fact"] for f in alice_facts]
        bob_fact_texts = [f["fact"] for f in bob_facts]

        assert "Alice likes hiking" in alice_fact_texts
        assert "Bob likes swimming" not in alice_fact_texts
        assert "Bob likes swimming" in bob_fact_texts
        assert "Alice likes hiking" not in bob_fact_texts

    def test_profile_summary_scoped(self, two_users, tmp_path):
        alice, bob = two_users
        alice_dir = tmp_path / "data" / "users" / alice

        update_profile(
            [{"category": "Work", "fact": "Software engineer"}],
            data_dir=alice_dir,
        )

        alice_summary = get_profile_summary(data_dir=alice_dir)
        bob_summary = get_profile_summary(data_dir=tmp_path / "data" / "users" / bob)

        assert "Software engineer" in alice_summary
        assert "Software engineer" not in bob_summary


class TestTranscriptIsolation:
    def test_transcripts_are_separate(self, two_users, tmp_path):
        alice, bob = two_users
        alice_dir = tmp_path / "data" / "users" / alice
        bob_dir = tmp_path / "data" / "users" / bob

        # Save Alice's transcript
        alice_msgs = [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "Hello from Alice"},
            {"role": "assistant", "content": "Hello Alice"},
        ]
        save_transcript(alice_msgs, "alice-session-1", data_dir=alice_dir)

        # Save Bob's transcript
        bob_msgs = [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "Hello from Bob"},
            {"role": "assistant", "content": "Hello Bob"},
        ]
        save_transcript(bob_msgs, "bob-session-1", data_dir=bob_dir)

        # Verify isolation
        alice_sessions = list_transcripts(data_dir=alice_dir)
        bob_sessions = list_transcripts(data_dir=bob_dir)

        assert "alice-session-1" in alice_sessions
        assert "bob-session-1" not in alice_sessions
        assert "bob-session-1" in bob_sessions
        assert "alice-session-1" not in bob_sessions


class TestKnowledgeIsolation:
    def test_chromadb_stores_are_separate(self, two_users, tmp_path):
        alice, bob = two_users
        alice_kb_dir = tmp_path / "data" / "users" / alice / "knowledge_base"
        bob_kb_dir = tmp_path / "data" / "users" / bob / "knowledge_base"

        alice_store = KnowledgeStore(alice_kb_dir)
        bob_store = KnowledgeStore(bob_kb_dir)

        # Store data in Alice's knowledge base
        alice_store.store_memory("fact_1", "Alice is a software engineer", {"type": "fact"})
        alice_store.store_memory("fact_2", "Alice lives in Seattle", {"type": "fact"})

        # Store data in Bob's knowledge base
        bob_store.store_memory("fact_1", "Bob is a teacher", {"type": "fact"})

        # Search in Alice's store
        alice_results = alice_store.search_memory("software engineer")
        assert len(alice_results) > 0
        assert any("software engineer" in r["text"].lower() for r in alice_results)

        # Search in Bob's store
        bob_results = bob_store.search_memory("teacher")
        assert len(bob_results) > 0
        assert any("teacher" in r["text"].lower() for r in bob_results)

        # Verify no cross-contamination
        alice_teacher = alice_store.search_memory("teacher")
        # Alice's store should not find Bob's data
        for r in alice_teacher:
            assert "bob" not in r["text"].lower()


class TestMemoryManagerIsolation:
    def test_memory_managers_use_different_dirs(self, two_users, tmp_path):
        alice, bob = two_users

        alice_mm = MemoryManager(user_id=alice)
        bob_mm = MemoryManager(user_id=bob)

        assert alice_mm.user_data_dir != bob_mm.user_data_dir
        assert alice in str(alice_mm.user_data_dir)
        assert bob in str(bob_mm.user_data_dir)

    def test_default_user_has_no_data_dir(self):
        mm = MemoryManager()
        assert mm.user_data_dir is None
