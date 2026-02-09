"""
Conversation Logs — Aegis AI
Saves and loads full conversation records.
"""

from datetime import datetime
from pathlib import Path
from core.config import CONFIG, get_path


def get_transcript_path(session_id=None):
    """Get the file path for a transcript."""
    transcript_dir = get_path(CONFIG, "conversation_logs")
    if session_id is None:
        session_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return transcript_dir / f"session_{session_id}.md"


def save_transcript(messages, session_id=None):
    """Save a full conversation transcript as a markdown file."""
    transcript_dir = get_path(CONFIG, "conversation_logs")
    transcript_dir.mkdir(parents=True, exist_ok=True)

    if session_id is None:
        session_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    filepath = transcript_dir / f"session_{session_id}.md"
    now = datetime.now()

    agent_name = CONFIG.get("agent_name", "Aegis")

    lines = []
    lines.append(f"# Conversation Log — Session {session_id}")
    lines.append(f"**Date:** {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Agent:** {agent_name}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if role == "system":
            continue
        elif role == "user":
            lines.append(f"**Companion:** {content}")
            lines.append("")
        elif role == "assistant":
            lines.append(f"**{agent_name}:** {content}")
            lines.append("")

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return filepath


def load_transcript(session_id):
    """Load a transcript by session ID."""
    filepath = get_path(CONFIG, "conversation_logs") / f"session_{session_id}.md"
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return None


def list_transcripts():
    """List all available transcript session IDs, newest first."""
    transcript_dir = get_path(CONFIG, "conversation_logs")
    if not transcript_dir.exists():
        return []

    files = sorted(transcript_dir.glob("session_*.md"), reverse=True)
    return [f.stem.replace("session_", "") for f in files]
