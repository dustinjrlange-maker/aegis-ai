# tests/llm/test_call_sites.py
"""Assert each refactored site calls router.chat with the right sensitivity.

These patch the router so no Ollama call happens. They target the module-level
`_router_chat` / `router_chat` name the call sites import.
"""
import core.email_assistant as email_assistant
import core.memory.fact_extractor as fact_extractor
import core.memory.journal as journal
import core.briefing as briefing


def test_email_llm_tags_private(monkeypatch):
    captured = {}

    def fake_chat(messages, *, sensitivity, task=None, **kw):
        captured["sensitivity"] = sensitivity
        captured["task"] = task
        return "ok"

    monkeypatch.setattr(email_assistant, "_router_chat", fake_chat)
    out = email_assistant._llm([{"role": "user", "content": "hi"}], task="draft")
    assert out == "ok"
    assert captured["sensitivity"] == "private"
    assert captured["task"] == "draft"


def test_fact_extractor_tags_private(monkeypatch):
    """extract_keyed_facts must tag its LLM call sensitivity="private", task="extract"."""
    captured = {}

    def fake_chat(messages, *, sensitivity, task=None, **kw):
        captured["sensitivity"] = sensitivity
        captured["task"] = task
        return "NO NEW FACTS"

    monkeypatch.setattr(fact_extractor, "router_chat", fake_chat)
    result = fact_extractor.extract_keyed_facts([{"role": "user", "content": "hi"}])
    assert result == []
    assert captured["sensitivity"] == "private"
    assert captured["task"] == "extract"


def test_journal_generate_summary_tags_private(monkeypatch, tmp_path):
    """generate_summary must tag its LLM call sensitivity="private", task="summarize"."""
    captured = {}

    def fake_chat(messages, *, sensitivity, task=None, **kw):
        captured["sensitivity"] = sensitivity
        captured["task"] = task
        return "Summary text"

    monkeypatch.setattr(journal, "router_chat", fake_chat)
    filepath, summary_text = journal.generate_summary(
        [{"role": "user", "content": "hi"}],
        session_id="test-session",
        data_dir=tmp_path,
    )
    assert captured["sensitivity"] == "private"
    assert captured["task"] == "summarize"
    # Confirm write landed in tmp_path, not the repo
    assert str(tmp_path) in str(filepath)


def test_briefing_narrative_tags_private(monkeypatch):
    """generate_narrative_briefing must tag its LLM call sensitivity="private", task="summarize"."""
    captured = {}

    def fake_chat(messages, *, sensitivity, task=None, **kw):
        captured["sensitivity"] = sensitivity
        captured["task"] = task
        return "Narrative briefing text"

    # Minimal facts dict covering every key _format_facts_for_llm reads
    minimal_facts = {
        "period": "morning",
        "now": "09:00",
        "date": "2026-06-30",
        "weather": None,
        "overdue_tasks": [],
        "due_today": [],
        "high_priority_tasks": [],
        "events_today": [],
        "events_upcoming": [],
        "active_timer": None,
        "habits_today": [],
        "unread_email_count": 0,
        "total_pending": 0,
    }

    monkeypatch.setattr(
        briefing, "collect_briefing_facts",
        lambda session, period=None: minimal_facts,
    )
    monkeypatch.setattr(briefing, "router_chat", fake_chat)

    class FakeSession:
        system_prompt_base = "You are a helpful assistant."

        def clean_reply(self, text):
            return text

    result = briefing.generate_narrative_briefing(FakeSession(), period="morning")
    assert captured["sensitivity"] == "private"
    assert captured["task"] == "summarize"
    assert result["period"] == "morning"
