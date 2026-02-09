"""
Session Journals — Aegis AI
Generates summaries of conversations for quick reference.
"""

from datetime import datetime
from pathlib import Path
import ollama
from core.config import CONFIG, get_path


SUMMARY_PROMPT = """You are an AI companion's memory system. Analyze this conversation and produce a concise session journal entry.

Format your response EXACTLY like this:

SESSION: {session_id}
TOPICS: [comma-separated list of main topics discussed]
KEY POINTS:
- [bullet point summaries of important information exchanged]
COMPANION MOOD: [brief assessment of companion's emotional state]
ACTION ITEMS: [any tasks, promises, or follow-ups mentioned, or "None"]
NOTABLE FACTS: [any new personal facts learned about the companion]

Keep it concise. This journal is for quick reference, not a full record.

CONVERSATION:
{conversation}"""


def generate_summary(messages, session_id=None):
    """Generate a session journal summary of a conversation and save it."""
    logs_dir = get_path(CONFIG, "session_journals")
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

    summary_text = response["message"]["content"]

    # Save as markdown
    filepath = logs_dir / f"session_{session_id}.md"
    lines = []
    lines.append(f"# Session Journal — {session_id}")
    lines.append(f"**Recorded:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append(summary_text)

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return filepath, summary_text


def load_recent_summaries(count=5):
    """Load the most recent session journal summaries."""
    logs_dir = get_path(CONFIG, "session_journals")
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
