"""
Personal Log Storage — Aegis AI
Freeform journal entries with optional audio/video recordings.
Each entry is a JSON file in data/users/{username}/personal_logs/.
Audio saved to personal_logs/audio/. Video saved to personal_logs/video/.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _logs_dir(data_dir: Path) -> Path:
    """Get the personal_logs directory for a user."""
    d = data_dir / "personal_logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _audio_dir(data_dir: Path) -> Path:
    """Get the audio subdirectory."""
    d = data_dir / "personal_logs" / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _video_dir(data_dir: Path) -> Path:
    """Get the video subdirectory."""
    d = data_dir / "personal_logs" / "video"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_log_entry(
    text: str,
    data_dir: Path,
    audio_bytes: bytes | None = None,
    transcription: str | None = None,
    video_bytes: bytes | None = None,
) -> dict:
    """Save a personal log entry. Returns the entry dict."""
    now = datetime.now()
    log_id = now.strftime("%Y-%m-%d_%H%M%S")
    has_audio = audio_bytes is not None and len(audio_bytes) > 0
    has_video = video_bytes is not None and len(video_bytes) > 0

    entry = {
        "id": log_id,
        "created": now.isoformat(),
        "text": text or "",
        "has_audio": has_audio,
        "audio_file": "",
        "transcription": transcription or "",
        "has_video": has_video,
        "video_file": "",
    }

    # Save audio file
    if has_audio:
        audio_filename = f"log_{log_id}.webm"
        audio_path = _audio_dir(data_dir) / audio_filename
        audio_path.write_bytes(audio_bytes)
        entry["audio_file"] = f"audio/{audio_filename}"

    # Save video file
    if has_video:
        video_filename = f"log_{log_id}.webm"
        video_path = _video_dir(data_dir) / video_filename
        video_path.write_bytes(video_bytes)
        entry["video_file"] = f"video/{video_filename}"

    # Save entry JSON
    entry_path = _logs_dir(data_dir) / f"{log_id}.json"
    entry_path.write_text(
        json.dumps(entry, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Personal log created: %s", log_id)
    return entry


def list_personal_logs(data_dir: Path) -> list[dict]:
    """List all personal log entries, newest first, with preview text."""
    logs_path = _logs_dir(data_dir)
    entries = []
    for f in sorted(logs_path.glob("*.json"), reverse=True):
        try:
            entry = json.loads(f.read_text(encoding="utf-8"))
            entries.append({
                "id": entry["id"],
                "created": entry["created"],
                "preview": (entry.get("text") or entry.get("transcription") or "")[:100],
                "has_audio": entry.get("has_audio", False),
                "has_video": entry.get("has_video", False),
            })
        except (json.JSONDecodeError, KeyError, IOError) as e:
            logger.warning("Skipping bad log file %s: %s", f.name, e)
    return entries


def load_personal_log(log_id: str, data_dir: Path) -> dict | None:
    """Load a full personal log entry by ID."""
    entry_path = _logs_dir(data_dir) / f"{log_id}.json"
    if not entry_path.exists():
        return None
    try:
        return json.loads(entry_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Could not load log %s: %s", log_id, e)
        return None


def delete_personal_log(log_id: str, data_dir: Path) -> bool:
    """Delete a personal log entry and its audio file."""
    entry_path = _logs_dir(data_dir) / f"{log_id}.json"
    if not entry_path.exists():
        return False

    try:
        entry = json.loads(entry_path.read_text(encoding="utf-8"))
        # Delete audio file if present
        if entry.get("audio_file"):
            audio_path = _logs_dir(data_dir) / entry["audio_file"]
            audio_path.unlink(missing_ok=True)
        # Delete video file if present
        if entry.get("video_file"):
            video_path = _logs_dir(data_dir) / entry["video_file"]
            video_path.unlink(missing_ok=True)
        # Delete entry
        entry_path.unlink()
        logger.info("Personal log deleted: %s", log_id)
        return True
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Error deleting log %s: %s", log_id, e)
        return False


def get_audio_path(log_id: str, data_dir: Path) -> Path | None:
    """Get the filesystem path to a log's audio file."""
    entry = load_personal_log(log_id, data_dir)
    if not entry or not entry.get("audio_file"):
        return None
    audio_path = _logs_dir(data_dir) / entry["audio_file"]
    if audio_path.exists():
        return audio_path
    return None


def get_video_path(log_id: str, data_dir: Path) -> Path | None:
    """Get the filesystem path to a log's video file."""
    entry = load_personal_log(log_id, data_dir)
    if not entry or not entry.get("video_file"):
        return None
    video_path = _logs_dir(data_dir) / entry["video_file"]
    if video_path.exists():
        return video_path
    return None


def load_recent_log_text(count: int, data_dir: Path) -> list[str]:
    """Load recent personal log text snippets for MemoryManager context injection."""
    logs_path = _logs_dir(data_dir)
    entries = []
    for f in sorted(logs_path.glob("*.json"), reverse=True):
        if len(entries) >= count:
            break
        try:
            entry = json.loads(f.read_text(encoding="utf-8"))
            text = entry.get("text") or entry.get("transcription") or ""
            if text.strip():
                entries.append(text.strip()[:200])
        except (json.JSONDecodeError, KeyError, IOError):
            continue
    return entries
