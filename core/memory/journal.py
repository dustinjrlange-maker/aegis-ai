"""
Session Journals — Aegis AI
Generates summaries of conversations for quick reference.
"""

import re
from datetime import datetime
from pathlib import Path
import ollama
from core.config import CONFIG, get_path


SUMMARY_PROMPT = """You are an AI companion's memory system. Analyze this conversation and produce a concise session journal entry.

IMPORTANT: Focus ONLY on what the COMPANION said, did, and felt. Do NOT record what the AI agent said, suggested, or advised -- that is not useful for memory and can reinforce bad patterns. The purpose of this journal is to remember the human, not the AI.

Format your response EXACTLY like this:

SESSION: {session_id}
TOPICS: [comma-separated list of main topics the COMPANION brought up]
KEY POINTS:
- [bullet points about what the COMPANION shared, experienced, or expressed]
COMPANION MOOD: [brief assessment of companion's emotional state]
ACTION ITEMS: [any tasks, promises, or follow-ups the COMPANION mentioned, or "None"]
NOTABLE FACTS: [any new personal facts learned about the COMPANION]

Keep it concise. Only record facts about the human companion. Do NOT include what the AI said or suggested.

CONVERSATION:
{conversation}"""


def get_journal_dir(data_dir=None):
    """Get the journal directory, optionally scoped to a user."""
    if data_dir is not None:
        d = Path(data_dir) / "session_journals"
        d.mkdir(parents=True, exist_ok=True)
        return d
    return get_path(CONFIG, "session_journals")


def generate_summary(messages, session_id=None, data_dir=None):
    """Generate a session journal summary of a conversation and save it."""
    logs_dir = get_journal_dir(data_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    if session_id is None:
        session_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    agent_name = CONFIG.get("agent_name", "Aegis")

    # Build conversation text for the summarizer
    conversation_lines = []
    for msg in messages:
        if msg["role"] == "system":
            continue
        elif msg["role"] == "user":
            conversation_lines.append(f"Companion: {msg['content']}")
        elif msg["role"] == "assistant":
            conversation_lines.append(f"{agent_name}: {msg['content']}")

    conversation_text = "\n".join(conversation_lines)

    prompt = SUMMARY_PROMPT.format(
        session_id=session_id,
        conversation=conversation_text
    )

    # Use Ollama to generate the summary
    response = ollama.chat(
        model=CONFIG["model"]["summary"],
        messages=[{"role": "user", "content": prompt}]
    )

    summary_text = re.sub(r'<think>.*?</think>', '', response["message"]["content"], flags=re.DOTALL).strip()

    # Save as markdown
    filepath = logs_dir / f"session_{session_id}.md"
    lines = []
    lines.append(f"# Session Journal — {session_id}")
    lines.append(f"**Recorded:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append(summary_text)

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return filepath, summary_text


def load_recent_summaries(count=5, data_dir=None):
    """Load the most recent session journal summaries."""
    logs_dir = get_journal_dir(data_dir)
    if not logs_dir.exists():
        return []

    files = sorted(logs_dir.glob("session_*.md"), reverse=True)[:count]
    summaries = []
    for f in files:
        summaries.append({
            "session_id": f.stem.replace("session_", ""),
            "content": f.read_text(encoding="utf-8")
        })
    return summaries
