"""
Aegis AI — FastAPI Server
Exposes the Aegis agent as an HTTP API for multi-device access.
Run with: uvicorn server.app:app --host 0.0.0.0 --port 8484
"""

import sys
import logging
from pathlib import Path
from contextlib import asynccontextmanager

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, Request, UploadFile, File, Form, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional
import json
import hmac
import hashlib
import secrets
import tempfile

from core.config import CONFIG
from core.auth import (
    load_users, create_user, verify_user, change_passcode,
    create_session, validate_token, invalidate_token, get_current_user,
    load_user_preferences, save_user_preferences, user_exists,
)
from core.session import SessionManager
from core.personality.pack_loader import (
    list_packs, load_voice_pack, load_theme_pack,
)
from core.memory.transcript import list_transcripts, load_transcript
from core.memory.profile import (
    get_profile_summary, get_profile_facts, update_profile, remove_profile_fact,
)
from core.vault_pin import (
    has_vault_pin, set_vault_pin, verify_vault_pin, remove_vault_pin,
    create_vault_unlock, validate_vault_unlock, invalidate_vault_unlock,
)
from core.memory.personal_log import (
    create_log_entry, list_personal_logs, load_personal_log,
    delete_personal_log, get_audio_path, get_video_path,
    update_personal_log_title,
)
from core.feature_toggles import load_feature_toggles, save_feature_toggles
from core.memory.news_service import NewsService
from server.chat_pipeline import process_chat


# --- Session Manager (replaces global state) ---
session_manager = SessionManager()

# --- News Service (shared singleton) ---
_news_service = NewsService()

# OAuth state store: maps state_token -> user_id (short-lived, in-memory)
_oauth_states: dict[str, str] = {}

logger = logging.getLogger("aegis.server")


# --- Auth Dependency ---

async def require_user(request: Request) -> str:
    """FastAPI dependency — require a valid authenticated user."""
    user_id = get_current_user(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id


async def require_vault_access(request: Request) -> str:
    """FastAPI dependency — require auth + vault PIN if set."""
    user_id = get_current_user(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not has_vault_pin(user_id):
        return user_id
    vault_token = request.headers.get("X-Vault-Token", "")
    if not validate_vault_unlock(user_id, vault_token):
        raise HTTPException(status_code=403, detail="Vault PIN required")
    return user_id


# --- FastAPI App ---

@asynccontextmanager
async def lifespan(app):
    # Prime psutil CPU counter (first call always returns 0)
    try:
        import psutil
        psutil.cpu_percent(interval=None)
    except ImportError:
        pass

    # Startup — try to start Telegram bot
    telegram_app = None
    try:
        from integrations.telegram_bot import start_telegram_bot, stop_telegram_bot
        telegram_app = await start_telegram_bot(session_manager, process_chat)
    except ImportError:
        logger.info("python-telegram-bot not installed — Telegram integration skipped")
    except Exception as e:
        logger.warning("Could not start Telegram bot: %s", e)

    yield

    # Shutdown — stop Telegram bot
    if telegram_app:
        try:
            from integrations.telegram_bot import stop_telegram_bot
            await stop_telegram_bot(telegram_app)
        except Exception as e:
            logger.warning("Error stopping Telegram bot: %s", e)

    # Save all sessions
    logger.info("Server shutdown — saving all active sessions...")
    session_manager.end_all()

app = FastAPI(title="Aegis AI", version="2.0.0", lifespan=lifespan)

# Serve static files (UI)
ui_dir = PROJECT_ROOT / "ui"
app.mount("/static", StaticFiles(directory=str(ui_dir / "static")), name="static")


# --- Request/Response Models ---

class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    agent_name: str
    response: str
    emotion: Optional[dict] = None
    wellness_flag: bool = False
    bracket_actions: Optional[list] = None


class TaskRequest(BaseModel):
    action: str  # add, done, remove
    text: Optional[str] = None
    task_id: Optional[int] = None
    priority: Optional[str] = "normal"
    due: Optional[str] = None
    due_time: Optional[str] = None


class ThemeSwitchRequest(BaseModel):
    theme_name: str


class ProtocolToggleRequest(BaseModel):
    protocol_name: str
    enabled: bool


class PackSwitchRequest(BaseModel):
    pack_name: str


class SettingsUpdateRequest(BaseModel):
    section: str
    key: str
    value: object


class RegisterRequest(BaseModel):
    username: str
    display_name: Optional[str] = ""
    passcode: str


class LoginRequest(BaseModel):
    username: str
    passcode: str


class PasscodeChangeRequest(BaseModel):
    current_passcode: str
    new_passcode: str


class ProfileFactRequest(BaseModel):
    category: str
    fact: str


class ProfileFactDeleteRequest(BaseModel):
    fact_text: str


class AccountUpdateRequest(BaseModel):
    display_name: str


class VaultPinSetRequest(BaseModel):
    pin: str


class VaultPinVerifyRequest(BaseModel):
    pin: str


class VaultPinRemoveRequest(BaseModel):
    current_pin: str


class PersonalLogRequest(BaseModel):
    text: str


class PersonalLogTitleUpdate(BaseModel):
    title: str


class FeatureToggleRequest(BaseModel):
    feature: str
    enabled: bool


class TaskUpdateRequest(BaseModel):
    task_id: int
    text: Optional[str] = None
    priority: Optional[str] = None
    due: Optional[str] = None
    due_time: Optional[str] = None
    activity_type: Optional[str] = None
    starred: Optional[bool] = None
    notes: Optional[str] = None


class SubtaskRequest(BaseModel):
    task_id: int
    action: str  # "add", "complete", "remove"
    text: Optional[str] = None
    index: Optional[int] = None


class TaskStarRequest(BaseModel):
    task_id: int


class NotificationActionRequest(BaseModel):
    notification_id: Optional[str] = None  # None = apply to all


class EventRequest(BaseModel):
    action: str  # "add", "update", "delete", "list"
    event_id: Optional[str] = None
    title: Optional[str] = None
    date: Optional[str] = None
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    description: Optional[str] = ""
    all_day: Optional[bool] = False
    category: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    repeat_type: Optional[str] = "none"
    repeat_until: Optional[str] = None
    reminder_minutes: Optional[int] = 0
    save_to_google: Optional[bool] = False


class MoodRequest(BaseModel):
    action: str  # "add", "delete", "list"
    moods: Optional[list[str]] = None
    note: Optional[str] = ""
    energy: Optional[int] = None
    mood_id: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None


class ContactRequest(BaseModel):
    action: str  # "add", "update", "delete", "list", "search"
    contact_id: Optional[str] = None
    name: Optional[str] = None
    relationship: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    birthday: Optional[str] = None
    likes: Optional[str] = None
    dislikes: Optional[str] = None
    notes: Optional[str] = None
    query: Optional[str] = None


class CrewFileRequest(BaseModel):
    action: str  # "add", "update", "delete", "list", "search", "get"
    profile_id: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    relationship: Optional[str] = None
    department: Optional[str] = None
    bio: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    birthday: Optional[str] = None
    likes: Optional[str] = None
    dislikes: Optional[str] = None
    notes: Optional[str] = None
    history: Optional[str] = None
    query: Optional[str] = None


class HabitRequest(BaseModel):
    action: str  # "add", "check", "uncheck", "delete"
    habit_id: Optional[str] = None
    name: Optional[str] = None
    frequency: Optional[str] = "daily"
    date: Optional[str] = None


class BehaviorRequest(BaseModel):
    action: str  # "add", "relapse", "delete"
    behavior_id: Optional[str] = None
    name: Optional[str] = None
    note: Optional[str] = ""


class PinRequest(BaseModel):
    role: Optional[str] = None
    text: Optional[str] = None
    sender: Optional[str] = None
    note: Optional[str] = ""


class UnpinRequest(BaseModel):
    pin_id: str


class TimerRequest(BaseModel):
    action: str  # "start", "stop", "delete"
    activity: Optional[str] = None
    category: Optional[str] = "general"
    entry_id: Optional[str] = None


class WeatherLocationRequest(BaseModel):
    lat: float
    lon: float
    name: Optional[str] = ""


class AlarmRequest(BaseModel):
    action: str  # "add", "toggle", "delete", "dismiss"
    alarm_id: Optional[str] = None
    label: Optional[str] = None
    time: Optional[str] = None
    days: Optional[list[str]] = None


class SocialProjectRequest(BaseModel):
    name: str


class SocialPostRequest(BaseModel):
    project_id: str
    content: str
    platform: Optional[str] = ""
    status: Optional[str] = "draft"


# --- Auth Routes (unauthenticated) ---

@app.post("/api/auth/register")
async def auth_register(req: RegisterRequest):
    """Create a new user account."""
    try:
        user = create_user(req.username, req.display_name, req.passcode)
        token = create_session(req.username.lower().strip())
        return {
            "success": True,
            "token": token,
            "username": req.username.lower().strip(),
            "display_name": user["display_name"],
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest):
    """Login and receive a session token.

    Always ends any existing session so the user starts fresh.
    Conversation history from the old session is saved to transcript.
    """
    username = req.username.lower().strip()
    if verify_user(username, req.passcode):
        # End any stale session so the next get_or_create() starts fresh
        session_manager.end_session(username)
        token = create_session(username)
        users = load_users()
        display_name = users.get(username, {}).get("display_name", username.title())
        return {
            "success": True,
            "token": token,
            "username": username,
            "display_name": display_name,
        }
    return {"success": False, "error": "Invalid username or passcode"}


@app.get("/api/auth/check")
async def auth_check(request: Request):
    """Validate a session token and return user info."""
    user_id = get_current_user(request)
    if user_id is None:
        return {"authenticated": False}
    users = load_users()
    user_data = users.get(user_id, {})
    return {
        "authenticated": True,
        "username": user_id,
        "display_name": user_data.get("display_name", user_id.title()),
    }


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    """Invalidate the current session token and vault unlock."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        user_id = validate_token(token)
        if user_id:
            session_manager.end_session(user_id)
        invalidate_token(token)
    # Also invalidate vault token if present
    vault_token = request.headers.get("X-Vault-Token", "")
    if vault_token:
        invalidate_vault_unlock(vault_token)
    return {"success": True}


@app.get("/api/auth/has-users")
async def auth_has_users():
    """Check if any user accounts exist (for first-run detection)."""
    return {"has_users": user_exists()}


# --- Main UI Route ---

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main UI (no-cache so code updates are always fresh)."""
    index_path = ui_dir / "templates" / "index.html"
    return FileResponse(
        str(index_path),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# --- Protected API Routes ---

@app.get("/api/status")
async def get_status(user_id: str = Depends(require_user)):
    """Get full system status."""
    session = session_manager.get_or_create(user_id)
    prefs = load_user_preferences(user_id)
    return {
        "agent_name": session.agent_name,
        "session_id": session.memory.session_id,
        "model": CONFIG["model"]["chat"],
        "active_personality": prefs.get("active_personality",
                                        CONFIG.get("packs", {}).get("active_personality", "default")),
        "active_voice": prefs.get("active_voice",
                                  CONFIG.get("packs", {}).get("active_voice", "default")),
        "protocols": session.protocol_registry.get_all_status(),
        "message_count": len(session.messages) - 1,
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user_id: str = Depends(require_user)):
    """Send a message and get a response."""
    result = await process_chat(session_manager, user_id, req.message.strip())
    return ChatResponse(**result)


@app.post("/api/end-session")
async def end_session(user_id: str = Depends(require_user)):
    """End the current session — saves transcript, summary, facts, profile."""
    session_manager.end_session(user_id)
    return {"success": True}


@app.post("/api/shutdown")
async def shutdown():
    """Save all sessions and shut down gracefully (called by Electron on quit).

    No auth required — only accessible from localhost during app teardown.
    """
    logger.info("Shutdown endpoint called — saving all sessions...")
    session_manager.end_all()
    return {"success": True}


@app.get("/api/packs")
async def get_packs(user_id: str = Depends(require_user)):
    """List installed packs."""
    prefs = load_user_preferences(user_id)
    return {
        "personalities": list_packs("personalities"),
        "voices": list_packs("voices"),
        "themes": list_packs("themes"),
        "active": {
            "personality": prefs.get("active_personality",
                                     CONFIG.get("packs", {}).get("active_personality", "default")),
            "voice": prefs.get("active_voice",
                               CONFIG.get("packs", {}).get("active_voice", "default")),
            "theme": prefs.get("active_theme",
                               CONFIG.get("packs", {}).get("active_theme", "default")),
        },
    }


@app.post("/api/tasks")
async def manage_tasks(req: TaskRequest, user_id: str = Depends(require_user)):
    """Task management endpoint."""
    session = session_manager.get_or_create(user_id)
    ops = session.protocol_registry.get("operations")
    if not ops:
        return {"error": "Operations protocol not available"}

    if req.action == "add" and req.text:
        task = ops.add_task(
            req.text,
            priority=req.priority or "normal",
            due=req.due,
            due_time=req.due_time,
        )
        return {"success": True, "task": task}
    elif req.action == "done" and req.task_id:
        task = ops.complete_task(req.task_id)
        return {"success": bool(task), "task": task}
    elif req.action == "uncomplete" and req.task_id:
        task = ops.uncomplete_task(req.task_id)
        return {"success": bool(task), "task": task}
    elif req.action == "remove" and req.task_id:
        removed = ops.remove_task(req.task_id)
        return {"success": removed}
    elif req.action == "list":
        # Return ALL tasks (pending + completed) so the UI can show
        # strike-through for recently-completed ones. Clients that only want
        # pending tasks should filter on `t.completed`.
        return {"tasks": list(ops._tasks)}
    else:
        return {"error": "Invalid action. Use: add, done, remove, list"}


@app.get("/api/tasks/count")
async def get_task_count(user_id: str = Depends(require_user)):
    """Lightweight task count for sidebar badge polling."""
    session = session_manager.get_or_create(user_id)
    ops = session.protocol_registry.get("operations")
    count = len(ops.get_pending_tasks()) if ops else 0
    return {"count": count}


@app.post("/api/tasks/update")
async def update_task(req: TaskUpdateRequest, user_id: str = Depends(require_user)):
    """Update task fields (text, priority, due, activity_type, starred)."""
    session = session_manager.get_or_create(user_id)
    ops = session.protocol_registry.get("operations")
    if not ops:
        return {"error": "Operations protocol not available"}
    updates = {}
    if req.text is not None:
        updates["text"] = req.text
    if req.priority is not None:
        updates["priority"] = req.priority
    if req.due is not None:
        updates["due"] = req.due
    if req.due_time is not None:
        updates["due_time"] = req.due_time
    if req.activity_type is not None:
        updates["activity_type"] = req.activity_type
    if req.starred is not None:
        updates["starred"] = req.starred
    if req.notes is not None:
        updates["notes"] = req.notes
    task = ops.update_task(req.task_id, **updates)
    return {"success": bool(task), "task": task}


@app.post("/api/tasks/subtask")
async def manage_subtask(req: SubtaskRequest, user_id: str = Depends(require_user)):
    """Add, complete, or remove subtasks."""
    session = session_manager.get_or_create(user_id)
    ops = session.protocol_registry.get("operations")
    if not ops:
        return {"error": "Operations protocol not available"}
    if req.action == "add" and req.text:
        task = ops.add_subtask(req.task_id, req.text)
        return {"success": bool(task), "task": task}
    elif req.action == "complete" and req.index is not None:
        task = ops.complete_subtask(req.task_id, req.index)
        return {"success": bool(task), "task": task}
    elif req.action == "remove" and req.index is not None:
        task = ops.remove_subtask(req.task_id, req.index)
        return {"success": bool(task), "task": task}
    return {"error": "Invalid subtask action. Use: add, complete, remove"}


@app.post("/api/tasks/star")
async def toggle_task_star(req: TaskStarRequest, user_id: str = Depends(require_user)):
    """Toggle star on a task."""
    session = session_manager.get_or_create(user_id)
    ops = session.protocol_registry.get("operations")
    if not ops:
        return {"error": "Operations protocol not available"}
    task = ops.toggle_star(req.task_id)
    return {"success": bool(task), "task": task}


# ── Task attachments (image uploads) ────────────────────────────────────────

_ATTACHMENT_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp"}
_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_ATTACHMENT_MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".heic": "image/heic",
    ".heif": "image/heif", ".bmp": "image/bmp",
}


def _safe_attachment_name(raw: str) -> str:
    """Reduce an uploaded filename to a safe stem + allowed extension.
    Strips directory separators, keeps `a-zA-Z0-9._-`, and lower-cases the ext."""
    import re as _re
    from pathlib import Path as _P
    name = _P(raw or "").name  # strips any path
    stem, _, ext = name.rpartition(".")
    if not stem:
        stem, ext = name, ""
    stem = _re.sub(r"[^A-Za-z0-9._-]", "_", stem)[:80] or "file"
    ext = "." + _re.sub(r"[^A-Za-z0-9]", "", ext).lower() if ext else ""
    return stem + ext


def _task_attachments_dir(session, task_id: int):
    """Resolve the on-disk directory for a task's attachments. Created on demand."""
    from pathlib import Path as _P
    base = _P(session.memory.user_data_dir) / "task_attachments" / str(task_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


@app.post("/api/tasks/{task_id}/attachments")
async def upload_task_attachment(
    task_id: int,
    file: UploadFile = File(...),
    user_id: str = Depends(require_user),
):
    """Upload an image attachment for a task. Multipart 'file' field."""
    session = session_manager.get_or_create(user_id)
    ops = session.protocol_registry.get("operations")
    if not ops:
        raise HTTPException(status_code=503, detail="Operations protocol not available")

    # Verify task exists
    task = next((t for t in ops._tasks if t["id"] == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task #{task_id} not found")

    safe = _safe_attachment_name(file.filename or "upload")
    from pathlib import Path as _P
    ext = _P(safe).suffix.lower()
    if ext not in _ATTACHMENT_ALLOWED_EXTS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type {ext!r}. Allowed: {sorted(_ATTACHMENT_ALLOWED_EXTS)}",
        )

    data = await file.read()
    if len(data) > _ATTACHMENT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 10 MB limit")
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # Resolve collisions by appending -1, -2, ... before the extension
    target_dir = _task_attachments_dir(session, task_id)
    target_path = target_dir / safe
    if target_path.exists():
        stem, suffix = _P(safe).stem, _P(safe).suffix
        n = 1
        while True:
            candidate = target_dir / f"{stem}-{n}{suffix}"
            if not candidate.exists():
                target_path = candidate
                break
            n += 1

    target_path.write_bytes(data)
    ops.add_attachment(task_id, target_path.name)
    return {"success": True, "filename": target_path.name, "size": len(data)}


@app.get("/api/tasks/{task_id}/attachments/{filename}")
async def get_task_attachment(
    task_id: int,
    filename: str,
    user_id: str = Depends(require_user),
):
    """Serve an image attachment for a task."""
    session = session_manager.get_or_create(user_id)
    safe = _safe_attachment_name(filename)
    target_dir = _task_attachments_dir(session, task_id)
    path = target_dir / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    from pathlib import Path as _P
    media_type = _ATTACHMENT_MEDIA_TYPES.get(_P(safe).suffix.lower(), "application/octet-stream")
    return FileResponse(str(path), media_type=media_type, filename=safe)


@app.delete("/api/tasks/{task_id}/attachments/{filename}")
async def delete_task_attachment(
    task_id: int,
    filename: str,
    user_id: str = Depends(require_user),
):
    """Delete an attachment from a task — removes the file AND the task record entry."""
    session = session_manager.get_or_create(user_id)
    ops = session.protocol_registry.get("operations")
    if not ops:
        raise HTTPException(status_code=503, detail="Operations protocol not available")
    safe = _safe_attachment_name(filename)
    target_dir = _task_attachments_dir(session, task_id)
    path = target_dir / safe
    file_removed = False
    if path.exists() and path.is_file():
        try:
            path.unlink()
            file_removed = True
        except OSError:
            pass
    ops.remove_attachment(task_id, safe)
    return {"success": True, "file_removed": file_removed, "filename": safe}


@app.post("/api/events")
async def manage_events(req: EventRequest, user_id: str = Depends(require_user)):
    """Local event CRUD with recurring events, conflicts, and optional Google write."""
    session = session_manager.get_or_create(user_id)
    em = session.event_manager
    if req.action == "add" and req.title and req.date:
        # Check conflicts (advisory)
        conflicts = []
        if req.time_start and req.time_end:
            conflicts = em.check_conflicts(req.date, req.time_start, req.time_end)

        # Optional: save to Google Calendar instead of local
        if req.save_to_google:
            try:
                google_proto = session.protocol_registry.get("google")
                if google_proto:
                    creds = google_proto._get_creds()
                    if creds:
                        from core.protocols.google_tools import calendar_create
                        start_str = req.date
                        end_str = req.date
                        if req.time_start:
                            start_str = f"{req.date}T{req.time_start}:00"
                            if req.time_end:
                                end_str = f"{req.date}T{req.time_end}:00"
                            else:
                                from datetime import datetime as dt, timedelta
                                end_dt = dt.strptime(start_str, "%Y-%m-%dT%H:%M:%S") + timedelta(hours=1)
                                end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
                        result = calendar_create(creds, req.title, start_str, end_str,
                                                 description=req.description or "")
                        return {"success": result.get("success", False),
                                "google_event": result, "conflicts": conflicts,
                                "source": "google"}
            except Exception:
                pass
            return {"error": "Google Calendar not connected or write failed"}

        event = em.add_event(
            title=req.title, date=req.date,
            time_start=req.time_start, time_end=req.time_end,
            description=req.description or "",
            all_day=req.all_day or False,
            category=req.category or "general",
            repeat_type=req.repeat_type or "none",
            repeat_until=req.repeat_until,
            reminder_minutes=req.reminder_minutes or 0,
        )
        return {"success": True, "event": event, "conflicts": conflicts}
    elif req.action == "update" and req.event_id:
        updates = {}
        for field in ("title", "date", "time_start", "time_end",
                       "description", "all_day", "category",
                       "repeat_type", "repeat_until", "reminder_minutes"):
            val = getattr(req, field, None)
            if val is not None:
                updates[field] = val
        event = em.update_event(req.event_id, **updates)
        return {"success": bool(event), "event": event}
    elif req.action == "delete" and req.event_id:
        # If it's a recurring instance (contains _r), delete just that occurrence
        if "_r" in req.event_id:
            # Extract date from synthetic ID: "abc123_r20260320" -> "2026-03-20"
            r_part = req.event_id.split("_r")[1]
            if len(r_part) == 8:
                occ_date = f"{r_part[:4]}-{r_part[4:6]}-{r_part[6:8]}"
                return {"success": em.delete_occurrence(req.event_id, occ_date)}
        return {"success": em.delete_event(req.event_id)}
    elif req.action == "list":
        events = em.list_events(req.start_date, req.end_date)
        return {"events": events}
    return {"error": "Invalid action. Use: add, update, delete, list"}


@app.get("/api/events/conflicts")
async def check_event_conflicts(
    date: str, time_start: str, time_end: str,
    exclude_id: str = None,
    user_id: str = Depends(require_user),
):
    """Real-time conflict checking for calendar UI."""
    session = session_manager.get_or_create(user_id)
    conflicts = session.event_manager.check_conflicts(date, time_start, time_end, exclude_id)
    return {"conflicts": conflicts}


@app.get("/api/events/reminders/check")
async def check_event_reminders(user_id: str = Depends(require_user)):
    """Check for event reminders that are due now."""
    session = session_manager.get_or_create(user_id)
    due = session.event_manager.check_due_reminders()
    return {"due": due}


@app.get("/api/calendar/month/{year}/{month}")
async def calendar_month(year: int, month: int, user_id: str = Depends(require_user)):
    """Get local + Google events for a calendar month view."""
    import calendar as cal_mod
    first_day = 1
    last_day = cal_mod.monthrange(year, month)[1]
    start_date = f"{year:04d}-{month:02d}-{first_day:02d}"
    end_date = f"{year:04d}-{month:02d}-{last_day:02d}"

    session = session_manager.get_or_create(user_id)
    local_events = session.event_manager.list_events(start_date, end_date)

    google_events = []
    try:
        google_proto = session.protocol_registry.get("google")
        if google_proto:
            creds = google_proto._get_creds()
            if creds:
                from core.protocols.google_tools import calendar_upcoming
                from datetime import datetime as dt, timedelta
                month_start = dt(year, month, 1)
                month_end = dt(year, month, last_day, 23, 59, 59)
                now = dt.now()
                if month_end > now:
                    days_ahead = (month_end - now).days + 1
                    raw = calendar_upcoming(creds, days=days_ahead)
                    for ev in raw:
                        ev_date = ev.get("start", "")[:10]
                        if start_date <= ev_date <= end_date:
                            google_events.append({
                                "id": "google_" + ev.get("google_id", ev.get("summary", "")[:8]),
                                "title": ev.get("summary", "(no title)"),
                                "date": ev_date,
                                "time_start": ev.get("start", "")[11:16] or None,
                                "time_end": ev.get("end", "")[11:16] or None,
                                "description": ev.get("location", ""),
                                "source": "google",
                                "read_only": False,
                            })
    except Exception:
        pass

    return {
        "year": year,
        "month": month,
        "local_events": local_events,
        "google_events": google_events,
    }


@app.post("/api/calendar/google")
async def google_calendar_write(req: EventRequest, user_id: str = Depends(require_user)):
    """Google Calendar write operations: create, update, delete."""
    session = session_manager.get_or_create(user_id)
    google_proto = session.protocol_registry.get("google")
    if not google_proto:
        return {"error": "Google integration not available"}

    creds = google_proto._get_creds()
    if not creds:
        return {"error": "Google account not connected"}

    if req.action == "create" and req.title:
        from core.protocols.google_tools import calendar_create
        date_str = req.date or ""
        start_str = date_str
        end_str = date_str
        if req.time_start:
            start_str = f"{date_str}T{req.time_start}:00"
            if req.time_end:
                end_str = f"{date_str}T{req.time_end}:00"
            else:
                from datetime import datetime as dt, timedelta
                end_dt = dt.strptime(start_str, "%Y-%m-%dT%H:%M:%S") + timedelta(hours=1)
                end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
        result = calendar_create(creds, req.title, start_str, end_str,
                                 description=req.description or "")
        return result

    elif req.action == "update" and req.event_id:
        from core.protocols.google_tools import calendar_update
        real_id = req.event_id.replace("google_", "", 1) if req.event_id.startswith("google_") else req.event_id
        updates = {}
        if req.title:
            updates["summary"] = req.title
        if req.description:
            updates["description"] = req.description
        if req.date and req.time_start:
            updates["start"] = f"{req.date}T{req.time_start}:00"
            if req.time_end:
                updates["end"] = f"{req.date}T{req.time_end}:00"
        result = calendar_update(creds, real_id, **updates)
        return result

    elif req.action == "delete" and req.event_id:
        from core.protocols.google_tools import calendar_delete
        real_id = req.event_id.replace("google_", "", 1) if req.event_id.startswith("google_") else req.event_id
        result = calendar_delete(creds, real_id)
        return result

    return {"error": "Invalid action. Use: create, update, delete"}


@app.get("/api/briefing")
async def get_briefing(user_id: str = Depends(require_user)):
    """Get structured daily briefing data."""
    from datetime import datetime as dt, timedelta

    session = session_manager.get_or_create(user_id)
    ops = session.protocol_registry.get("operations")
    now = dt.now()
    today_str = now.strftime("%Y-%m-%d")
    tomorrow = now + timedelta(hours=24)

    overdue_tasks = []
    due_today = []
    high_priority_tasks = []
    all_pending = []
    total_pending = 0

    if ops:
        for task in ops.get_pending_tasks():
            total_pending += 1
            all_pending.append(task)
            if task.get("priority") == "high":
                high_priority_tasks.append(task)
            due_dt = ops.task_due_datetime(task)
            if due_dt is not None:
                if due_dt < now:
                    overdue_tasks.append(task)
                elif due_dt.strftime("%Y-%m-%d") == today_str:
                    due_today.append(task)

    # "Other pending" — pending tasks not surfaced in overdue/due-today/high-priority,
    # so the Pending stat is never silently un-clickable.
    surfaced_ids = {id(t) for t in overdue_tasks + due_today + high_priority_tasks}
    other_pending = [t for t in all_pending if id(t) not in surfaced_ids]

    # Local events
    events_today = session.event_manager.list_events(today_str, today_str)
    end_3d = (now + timedelta(days=3)).strftime("%Y-%m-%d")
    tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    events_upcoming = session.event_manager.list_events(tomorrow_str, end_3d)

    # Google events (best-effort)
    google_today = []
    google_upcoming = []
    try:
        google_proto = session.protocol_registry.get("google")
        if google_proto:
            creds = google_proto._get_creds()
            if creds:
                from core.protocols.google_tools import calendar_upcoming
                raw = calendar_upcoming(creds, days=4)
                for ev in raw:
                    ev_date = ev.get("start", "")[:10]
                    item = {
                        "title": ev.get("summary", "(no title)"),
                        "date": ev_date,
                        "time_start": ev.get("start", "")[11:16] or None,
                        "source": "google",
                    }
                    if ev_date == today_str:
                        google_today.append(item)
                    elif tomorrow_str <= ev_date <= end_3d:
                        google_upcoming.append(item)
    except Exception:
        pass

    # Side-effect: generate notifications
    ns = session.notification_service
    ns.generate_from_tasks(ops)
    ns.generate_from_events(session.event_manager)

    # Phase 10: extra briefing data
    today_moods = session.mood_manager.get_today_moods()
    habits_today = session.habit_manager.get_today_status()
    active_timer = session.time_tracker.get_active_timer()
    timer_summary = session.time_tracker.get_today_summary()
    weather = session.weather_service.get_weather()
    if "error" in weather:
        weather = None

    return {
        "date": today_str,
        "overdue_tasks": overdue_tasks,
        "due_today": due_today,
        "high_priority_tasks": high_priority_tasks,
        "other_pending": other_pending,
        "events_today": events_today + google_today,
        "events_upcoming": events_upcoming + google_upcoming,
        "total_pending": total_pending,
        "moods_today": today_moods,
        "habits_today": habits_today,
        "active_timer": active_timer,
        "timer_summary": timer_summary,
        "weather": weather,
    }


@app.get("/api/briefing/narrative")
async def get_briefing_narrative(
    period: str | None = None,
    unit: str | None = None,
    user_id: str = Depends(require_user),
):
    """Get the personality-voiced briefing narrative.

    period: morning | afternoon | evening | late (optional — auto-detected from time of day)
    unit:   F | C (optional, default F) — temperature unit for the narrative prose
    """
    from core.briefing import generate_narrative_briefing
    session = session_manager.get_or_create(user_id)
    return generate_narrative_briefing(session, period=period, unit=(unit or "F"))


# ── Email assistant ─────────────────────────────────────────────────────────
# All email writing is gated through Gmail drafts. There is NO endpoint that
# both composes and sends in one call. Sending requires:
#   POST /api/email/send-draft/{draft_id}  body: {"confirm": true}


@app.get("/api/email/inbox-digest")
async def email_inbox_digest(fresh: int = 0, max_messages: int = 10,
                              user_id: str = Depends(require_user)):
    """Pike-voiced summary of the user's recent inbox."""
    from core.email_assistant import get_inbox_digest
    session = session_manager.get_or_create(user_id)
    return get_inbox_digest(session, max_messages=max_messages, fresh=bool(fresh))


@app.get("/api/email/drafts")
async def email_list_drafts(max_results: int = 20, user_id: str = Depends(require_user)):
    """List the user's recent Gmail drafts."""
    from core.email_assistant import list_drafts
    session = session_manager.get_or_create(user_id)
    return {"drafts": list_drafts(session, max_results=max_results)}


@app.get("/api/email/drafts/{draft_id}")
async def email_get_draft(draft_id: str, user_id: str = Depends(require_user)):
    """Get one draft's full contents."""
    from core.email_assistant import get_draft
    session = session_manager.get_or_create(user_id)
    draft = get_draft(session, draft_id)
    if not draft:
        return {"error": "Draft not found"}
    return draft


@app.post("/api/email/draft-reply")
async def email_draft_reply(body: dict, user_id: str = Depends(require_user)):
    """Draft a reply to an inbox message. Saves to Gmail drafts. Does NOT send.

    Body: {message_id: str, intent?: str}
    """
    from core.email_assistant import draft_reply
    session = session_manager.get_or_create(user_id)
    message_id = body.get("message_id", "").strip()
    if not message_id:
        return {"success": False, "error": "message_id required"}
    intent = body.get("intent")
    return draft_reply(session, message_id, intent=intent)


@app.post("/api/email/draft")
async def email_draft_new(body: dict, user_id: str = Depends(require_user)):
    """Draft a fresh email (not a reply). Saves to Gmail drafts. Does NOT send.

    Body: {to: str, intent: str, subject?: str, cc?: str, bcc?: str}
    """
    from core.email_assistant import draft_new
    session = session_manager.get_or_create(user_id)
    to = body.get("to", "").strip()
    intent = body.get("intent", "").strip()
    if not to or not intent:
        return {"success": False, "error": "to and intent required"}
    subject_hint = body.get("subject")
    cc = (body.get("cc") or "").strip() or None
    bcc = (body.get("bcc") or "").strip() or None
    return draft_new(session, to=to, intent=intent, subject_hint=subject_hint,
                     cc=cc, bcc=bcc)


@app.post("/api/email/send-draft/{draft_id}")
async def email_send_draft(draft_id: str, body: dict, user_id: str = Depends(require_user)):
    """Send a previously-saved draft. EXPLICIT user confirmation required.

    Body: {confirm: true}  — must be true. Belt-and-suspenders against accidental sends.
    """
    if body.get("confirm") is not True:
        return {"success": False, "error": "Send requires {\"confirm\": true} in body"}
    from core.email_assistant import send_draft
    session = session_manager.get_or_create(user_id)
    return send_draft(session, draft_id)


@app.delete("/api/email/drafts/{draft_id}")
async def email_discard_draft(draft_id: str, user_id: str = Depends(require_user)):
    """Discard a draft. Irreversible."""
    from core.email_assistant import discard_draft
    session = session_manager.get_or_create(user_id)
    return discard_draft(session, draft_id)


@app.patch("/api/email/drafts/{draft_id}")
async def email_update_draft(draft_id: str, body: dict,
                              user_id: str = Depends(require_user)):
    """Update a draft's subject/body in-place.

    Gmail's draft API doesn't support partial updates — the canonical move is
    to re-create the draft with the new MIME content. We honor the incoming
    draft_id by passing it as the existing draft to overwrite.
    """
    from core.email_assistant import _creds_from_session
    from core.protocols import google_tools as gt
    session = session_manager.get_or_create(user_id)
    creds = _creds_from_session(session)
    if not creds:
        return {"ok": False, "error": "not_authorized"}
    subject = body.get("subject", "")
    body_text = body.get("body", "")
    # Pull the existing draft to recover To/CC/BCC headers
    service = gt._get_gmail_service(creds) if hasattr(gt, "_get_gmail_service") else None
    if not service:
        return {"ok": False, "error": "Gmail service unavailable"}
    try:
        existing = service.users().drafts().get(
            userId="me", id=draft_id, format="metadata",
            metadataHeaders=["To", "Cc", "Bcc"],
        ).execute()
        headers = {h["name"]: h["value"] for h in
                   existing.get("message", {}).get("payload", {}).get("headers", [])}
        to = headers.get("To", "")
        cc = headers.get("Cc", "")
        bcc = headers.get("Bcc", "")
    except Exception as e:
        logger.warning("PATCH /api/email/drafts/%s — header fetch failed: %s",
                       draft_id, e)
        return {
            "ok": False,
            "error": "Couldn't read existing draft recipients. "
                     "Refresh the drafts list and try again.",
        }
    # Build new MIME and overwrite the draft in place
    try:
        raw, thread_id = gt._build_mime_message(
            to=to, subject=subject, body=body_text,
            reply_to_id=None, service=service,
            cc=cc or None, bcc=bcc or None,
        )
        draft_body = {"message": {"raw": raw}}
        if thread_id:
            draft_body["message"]["threadId"] = thread_id
        result = service.users().drafts().update(
            userId="me", id=draft_id, body=draft_body
        ).execute()
        return {
            "ok": True,
            "draft_id": result.get("id", draft_id),
        }
    except Exception as e:
        logger.warning("PATCH /api/email/drafts/%s — update failed: %s", draft_id, e)
        return {"ok": False, "error": "Draft save failed."}


@app.post("/api/email/mark-read/{message_id}")
async def email_mark_read(message_id: str, user_id: str = Depends(require_user)):
    """Mark an inbox message as read."""
    from core.email_assistant import mark_read
    session = session_manager.get_or_create(user_id)
    return mark_read(session, message_id)


@app.get("/api/email/messages/{message_id}")
async def email_get_message(message_id: str, user_id: str = Depends(require_user)):
    """Get a single inbox message's full body."""
    from core.email_assistant import _creds_from_session
    from core.protocols import google_tools as gt
    session = session_manager.get_or_create(user_id)
    creds = _creds_from_session(session)
    if not creds:
        return {"error": "not_authorized"}
    msg = gt.gmail_get_message(creds, message_id)
    if msg is None:
        return {"error": "not_found"}
    return msg


@app.get("/api/notifications")
async def get_notifications(user_id: str = Depends(require_user)):
    """Get all notifications, lazily generating from tasks/events."""
    session = session_manager.get_or_create(user_id)
    ns = session.notification_service
    ops = session.protocol_registry.get("operations")
    ns.generate_from_tasks(ops)
    ns.generate_from_events(session.event_manager)
    return {
        "notifications": ns.get_all(),
        "unread_count": ns.get_unread_count(),
    }


@app.get("/api/notifications/count")
async def get_notification_count(user_id: str = Depends(require_user)):
    """Lightweight unread count for polling."""
    session = session_manager.get_or_create(user_id)
    return {"unread_count": session.notification_service.get_unread_count()}


@app.post("/api/notifications/read")
async def mark_notifications_read(req: NotificationActionRequest, user_id: str = Depends(require_user)):
    """Mark one or all notifications as read."""
    session = session_manager.get_or_create(user_id)
    ns = session.notification_service
    if req.notification_id:
        ns.mark_read(req.notification_id)
    else:
        ns.mark_all_read()
    return {"success": True, "unread_count": ns.get_unread_count()}


@app.post("/api/notifications/dismiss")
async def dismiss_notifications(req: NotificationActionRequest, user_id: str = Depends(require_user)):
    """Dismiss one or all notifications."""
    session = session_manager.get_or_create(user_id)
    ns = session.notification_service
    if req.notification_id:
        ns.dismiss(req.notification_id)
    else:
        ns.dismiss_all()
    return {"success": True, "unread_count": ns.get_unread_count()}


@app.get("/api/theme")
async def get_theme(user_id: str = Depends(require_user)):
    """Get active theme configuration."""
    prefs = load_user_preferences(user_id)
    theme_name = prefs.get("active_theme", CONFIG.get("packs", {}).get("active_theme", "default"))
    theme_pack = load_theme_pack(theme_name)
    return theme_pack.get("theme", {})


@app.get("/api/gpu")
async def get_gpu(user_id: str = Depends(require_user)):
    """Get GPU status."""
    session = session_manager.get_or_create(user_id)
    cmd = session.protocol_registry.get("command")
    if cmd:
        return cmd.get_gpu_info() or {"error": "GPU info unavailable"}
    return {"error": "Command protocol not available"}


@app.post("/api/stt")
async def speech_to_text(audio: UploadFile = File(...), user_id: str = Depends(require_user)):
    """Transcribe uploaded audio via STT engine."""
    try:
        from pydub import AudioSegment
        from core.voice.stt_engine import transcribe

        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            content = await audio.read()
            tmp.write(content)
            tmp_path = tmp.name

        audio_seg = AudioSegment.from_file(tmp_path)
        audio_seg = audio_seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        wav_path = tmp_path.replace(".webm", ".wav")
        audio_seg.export(wav_path, format="wav")

        import numpy as np
        import wave
        with wave.open(wav_path, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            audio_np = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

        text = transcribe(audio_np)

        Path(tmp_path).unlink(missing_ok=True)
        Path(wav_path).unlink(missing_ok=True)

        return {"text": text or ""}
    except ImportError as e:
        logger.warning(f"STT dependencies not available: {e}")
        return {"text": "", "error": f"STT not available: {e}"}
    except Exception as e:
        logger.error(f"STT error: {e}")
        return {"text": "", "error": str(e)}


@app.get("/api/commands")
async def get_commands(user_id: str = Depends(require_user)):
    """List all available slash commands from protocols."""
    session = session_manager.get_or_create(user_id)
    all_commands = []
    for proto_name in session.protocol_registry.list_protocols():
        proto = session.protocol_registry.get(proto_name)
        if not proto:
            continue
        try:
            cmds = proto.get_commands()
            for cmd in cmds:
                all_commands.append({
                    "command": cmd.get("command", ""),
                    "description": cmd.get("description", ""),
                    "protocol": proto.name,
                })
        except Exception:
            pass
    return all_commands


@app.get("/api/transcripts")
async def get_transcripts(user_id: str = Depends(require_vault_access)):
    """List available session transcripts."""
    session = session_manager.get_or_create(user_id)
    session_ids = list_transcripts(data_dir=session.memory.user_data_dir)[:50]
    sessions = []
    for sid in session_ids:
        sessions.append({"session_id": sid})
    return {"sessions": sessions}


@app.get("/api/transcripts/{session_id}")
async def get_transcript(session_id: str, user_id: str = Depends(require_vault_access)):
    """Load a specific session transcript."""
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        return {"error": "Invalid session ID"}

    session = session_manager.get_or_create(user_id)
    content = load_transcript(session_id, data_dir=session.memory.user_data_dir)
    if content is not None:
        companion = session.memory.companion_name or "Companion"
        content = content.replace("**Agent:** Aegis", f"**Agent:** {session.agent_name}")
        parts = content.split("---\n", 1)
        if len(parts) == 2:
            parts[1] = parts[1].replace("**Companion:**", f"**{companion}:**")
            parts[1] = parts[1].replace("**Aegis:**", f"**{session.agent_name}:**")
            content = parts[0] + "---\n" + parts[1]
        return {"session_id": session_id, "content": content}
    return {"error": "Transcript not found"}


@app.post("/api/theme/switch")
async def switch_theme(req: ThemeSwitchRequest, user_id: str = Depends(require_user)):
    """Switch active theme at runtime (per-user)."""
    available_themes = list_packs("themes")
    if req.theme_name not in available_themes:
        return {"error": f"Theme '{req.theme_name}' not found"}

    prefs = load_user_preferences(user_id)
    prefs["active_theme"] = req.theme_name
    save_user_preferences(user_id, prefs)

    theme_pack = load_theme_pack(req.theme_name)
    return {"success": True, "theme": theme_pack.get("theme", {})}


@app.post("/api/personality/switch")
async def switch_personality(req: PackSwitchRequest, user_id: str = Depends(require_user)):
    """Switch active personality pack at runtime (per-user)."""
    available = list_packs("personalities")
    if req.pack_name not in available:
        return {"error": f"Personality '{req.pack_name}' not found"}

    # Save to user preferences
    prefs = load_user_preferences(user_id)
    prefs["active_personality"] = req.pack_name
    save_user_preferences(user_id, prefs)

    # End current session so the next request creates a new one with the new pack
    session_manager.end_session(user_id)

    return {
        "success": True,
        "pack": req.pack_name,
    }


@app.post("/api/voice/switch")
async def switch_voice(req: PackSwitchRequest, user_id: str = Depends(require_user)):
    """Switch active voice pack at runtime (per-user)."""
    available = list_packs("voices")
    if req.pack_name not in available:
        return {"error": f"Voice '{req.pack_name}' not found"}

    prefs = load_user_preferences(user_id)
    prefs["active_voice"] = req.pack_name
    save_user_preferences(user_id, prefs)

    voice_pack = load_voice_pack(req.pack_name)

    return {
        "success": True,
        "pack": req.pack_name,
        "reference_path": voice_pack.get("reference_path"),
    }


@app.post("/api/protocol/toggle")
async def toggle_protocol(req: ProtocolToggleRequest, user_id: str = Depends(require_user)):
    """Enable or disable a protocol at runtime."""
    if req.protocol_name == "security" and not req.enabled:
        return {"error": "Security protocol cannot be disabled"}

    session = session_manager.get_or_create(user_id)
    if req.enabled:
        success = session.protocol_registry.enable(req.protocol_name)
    else:
        success = session.protocol_registry.disable(req.protocol_name)

    if success:
        proto = session.protocol_registry.get(req.protocol_name)
        return {"success": True, "status": proto.get_status() if proto else {}}
    return {"error": f"Protocol '{req.protocol_name}' not found"}


@app.get("/api/protocol/{name}")
async def get_protocol_detail(name: str, user_id: str = Depends(require_user)):
    """Get detailed status for a single protocol."""
    session = session_manager.get_or_create(user_id)
    proto = session.protocol_registry.get(name)
    if proto:
        status = proto.get_status()
        status["commands"] = []
        try:
            for cmd in proto.get_commands():
                status["commands"].append({
                    "command": cmd.get("command", ""),
                    "description": cmd.get("description", ""),
                })
        except Exception:
            pass
        return status
    return {"error": f"Protocol '{name}' not found"}


@app.get("/api/packs/{pack_type}/{pack_name}")
async def get_pack_detail(pack_type: str, pack_name: str, user_id: str = Depends(require_user)):
    """Get detailed info about a specific pack."""
    if pack_type not in ("personalities", "voices", "themes"):
        return {"error": "Invalid pack type"}

    pack_dir = PROJECT_ROOT / "packs" / pack_type / pack_name
    if not pack_dir.exists():
        return {"error": f"Pack '{pack_name}' not found"}

    manifest_path = pack_dir / "manifest.json"
    result = {"name": pack_name, "type": pack_type, "manifest": {}, "files": []}

    if manifest_path.exists():
        result["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))

    for f in pack_dir.iterdir():
        if f.is_file():
            result["files"].append(f.name)

    return result


@app.get("/api/system/info")
async def get_system_info(user_id: str = Depends(require_user)):
    """Get detailed system information."""
    import platform

    session = session_manager.get_or_create(user_id)
    prefs = load_user_preferences(user_id)

    info = {
        "agent_name": session.agent_name,
        "companion_name": session.memory.companion_name or "Companion",
        "session_id": session.memory.session_id,
        "model": CONFIG["model"]["chat"],
        "summary_model": CONFIG["model"].get("summary", "--"),
        "platform": platform.system(),
        "python_version": platform.python_version(),
        "message_count": len(session.messages) - 1,
        "memory": {
            "auto_extract_facts": CONFIG["memory"]["auto_extract_facts"],
            "auto_summarize": CONFIG["memory"]["auto_summarize"],
            "max_context": CONFIG["memory"]["max_context_messages"],
            "max_search_results": CONFIG["memory"]["max_search_results"],
        },
        "voice": {
            "tts_enabled": CONFIG.get("voice", {}).get("tts", {}).get("enabled", False),
            "stt_enabled": CONFIG.get("voice", {}).get("stt", {}).get("enabled", False),
            "input_mode": CONFIG.get("voice", {}).get("stt", {}).get("input_mode", "text"),
        },
        "packs": {
            "personality": prefs.get("active_personality",
                                     CONFIG.get("packs", {}).get("active_personality", "default")),
            "voice": prefs.get("active_voice",
                               CONFIG.get("packs", {}).get("active_voice", "default")),
            "theme": prefs.get("active_theme",
                               CONFIG.get("packs", {}).get("active_theme", "default")),
        },
    }
    return info


@app.get("/api/settings")
async def get_settings(user_id: str = Depends(require_user)):
    """Get current configurable settings."""
    return {
        "memory": {
            "max_context_messages": CONFIG["memory"]["max_context_messages"],
            "summary_after_messages": CONFIG["memory"].get("summary_after_messages", 30),
            "max_search_results": CONFIG["memory"]["max_search_results"],
            "auto_extract_facts": CONFIG["memory"]["auto_extract_facts"],
            "auto_summarize": CONFIG["memory"]["auto_summarize"],
        },
        "voice": {
            "tts_enabled": CONFIG.get("voice", {}).get("tts", {}).get("enabled", False),
            "stt_enabled": CONFIG.get("voice", {}).get("stt", {}).get("enabled", False),
            "input_mode": CONFIG.get("voice", {}).get("stt", {}).get("input_mode", "text"),
        },
        "emotion": {
            "enabled": CONFIG.get("emotion", {}).get("enabled", False),
            "threshold": CONFIG.get("emotion", {}).get("threshold", 0.75),
        },
    }


@app.post("/api/settings")
async def update_settings(req: SettingsUpdateRequest, user_id: str = Depends(require_user)):
    """Update a configuration setting at runtime."""
    allowed_settings = {
        "memory": {
            "max_context_messages": int,
            "summary_after_messages": int,
            "max_search_results": int,
            "auto_extract_facts": bool,
            "auto_summarize": bool,
        },
        "voice.tts": {"enabled": bool},
        "voice.stt": {"enabled": bool, "input_mode": str},
        "emotion": {"enabled": bool, "threshold": float},
    }

    section_map = allowed_settings.get(req.section)
    if not section_map or req.key not in section_map:
        return {"error": f"Setting '{req.section}.{req.key}' is not configurable"}

    expected_type = section_map[req.key]
    try:
        typed_value = expected_type(req.value)
    except (ValueError, TypeError):
        return {"error": f"Invalid value type for {req.key}, expected {expected_type.__name__}"}

    # Apply to in-memory config
    if req.section == "memory":
        CONFIG["memory"][req.key] = typed_value
        session = session_manager.get(user_id)
        if session:
            if req.key == "auto_extract_facts":
                session.memory.auto_extract = typed_value
            elif req.key == "auto_summarize":
                session.memory.auto_summarize = typed_value
    elif req.section == "voice.tts":
        CONFIG.setdefault("voice", {}).setdefault("tts", {})[req.key] = typed_value
    elif req.section == "voice.stt":
        CONFIG.setdefault("voice", {}).setdefault("stt", {})[req.key] = typed_value
    elif req.section == "emotion":
        CONFIG.setdefault("emotion", {})[req.key] = typed_value

    # Persist to disk
    try:
        config_path = PROJECT_ROOT / "core" / "config" / "core_config.json"
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        if req.section == "memory":
            config_data.setdefault("memory", {})[req.key] = typed_value
        elif req.section == "voice.tts":
            config_data.setdefault("voice", {}).setdefault("tts", {})[req.key] = typed_value
        elif req.section == "voice.stt":
            config_data.setdefault("voice", {}).setdefault("stt", {})[req.key] = typed_value
        elif req.section == "emotion":
            config_data.setdefault("emotion", {})[req.key] = typed_value
        config_path.write_text(json.dumps(config_data, indent=4), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not persist setting to config: {e}")

    return {"success": True, "section": req.section, "key": req.key, "value": typed_value}


# --- Feature Toggle Routes ---

@app.get("/api/features")
async def get_features(user_id: str = Depends(require_user)):
    """Get the user's feature toggles."""
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    return load_feature_toggles(data_dir)


@app.post("/api/features")
async def update_feature(req: FeatureToggleRequest, user_id: str = Depends(require_user)):
    """Enable or disable a single feature."""
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    toggles = load_feature_toggles(data_dir)
    if req.feature not in toggles:
        return {"error": f"Unknown feature: {req.feature}"}
    toggles[req.feature] = req.enabled
    save_feature_toggles(data_dir, toggles)
    return {"success": True, "feature": req.feature, "enabled": req.enabled}


# --- Vault Routes ---

@app.get("/api/vault/profile")
async def vault_get_profile(user_id: str = Depends(require_user)):
    """Get the user's profile — from fact store if available, else legacy."""
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    fact_store = session.memory._fact_store

    if fact_store and fact_store.get_all_facts():
        text = fact_store.render_profile_markdown()
        facts = [
            {"category": f["key"].split(".")[0].upper(), "fact": f["value"]}
            for f in fact_store.get_all_facts()
        ]
        pending = fact_store.get_pending_review()
        return {"text": text, "facts": facts, "pending_review": pending}
    else:
        text = get_profile_summary(data_dir=data_dir)
        facts = get_profile_facts(data_dir=data_dir)
        return {"text": text, "facts": facts, "pending_review": []}


@app.put("/api/vault/profile")
async def vault_update_profile(req: ProfileFactRequest, user_id: str = Depends(require_user)):
    """Add a fact to the user's profile."""
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    update_profile([{"category": req.category, "fact": req.fact}], data_dir=data_dir)
    return {"success": True}


@app.delete("/api/vault/profile/fact")
async def vault_delete_fact(req: ProfileFactDeleteRequest, user_id: str = Depends(require_user)):
    """Remove a fact from the user's profile."""
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    removed = remove_profile_fact(req.fact_text, data_dir=data_dir)
    return {"success": removed}


class ConflictResolveRequest(BaseModel):
    index: int
    keep: str  # "new", "existing", or "both"


@app.post("/api/vault/profile/resolve")
async def vault_resolve_conflict(req: ConflictResolveRequest, user_id: str = Depends(require_user)):
    """Resolve a fact conflict in the structured fact store."""
    session = session_manager.get_or_create(user_id)
    fact_store = session.memory._fact_store
    if not fact_store:
        return {"error": "Fact store not available"}
    if req.keep not in ("new", "existing", "both"):
        return {"error": "keep must be 'new', 'existing', or 'both'"}
    success = fact_store.resolve_conflict(req.index, keep=req.keep)
    return {"success": success}


@app.get("/api/vault/summary")
async def vault_get_summary(user_id: str = Depends(require_user)):
    """Get the quick-access memory summary."""
    session = session_manager.get_or_create(user_id)
    summary = session.memory.load_summary()
    return summary or {"message": "No summary generated yet"}


@app.post("/api/vault/summary/regenerate")
async def vault_regenerate_summary(user_id: str = Depends(require_user)):
    """Force regenerate the memory summary."""
    session = session_manager.get_or_create(user_id)
    summary = session.memory.generate_summary()
    return summary


@app.get("/api/vault/account")
async def vault_get_account(user_id: str = Depends(require_user)):
    """Get account info and stats."""
    users = load_users()
    user_data = users.get(user_id, {})
    session = session_manager.get_or_create(user_id)

    # Count stats
    data_dir = session.memory.user_data_dir
    transcript_count = len(list_transcripts(data_dir=data_dir)) if data_dir else 0
    fact_count = len(get_profile_facts(data_dir=data_dir))

    return {
        "username": user_id,
        "display_name": user_data.get("display_name", user_id.title()),
        "created": user_data.get("created", ""),
        "stats": {
            "total_sessions": transcript_count,
            "total_facts": fact_count,
        },
    }


@app.put("/api/vault/account")
async def vault_update_account(req: AccountUpdateRequest, user_id: str = Depends(require_user)):
    """Update account display name."""
    users = load_users()
    if user_id in users:
        users[user_id]["display_name"] = req.display_name
        from core.auth import save_users
        save_users(users)
    return {"success": True}


@app.put("/api/vault/account/passcode")
async def vault_change_passcode(req: PasscodeChangeRequest, user_id: str = Depends(require_user)):
    """Change the user's passcode."""
    try:
        success = change_passcode(user_id, req.current_passcode, req.new_passcode)
        if success:
            return {"success": True}
        return {"success": False, "error": "Current passcode is incorrect"}
    except ValueError as e:
        return {"success": False, "error": str(e)}


# --- Vault PIN Routes ---

@app.get("/api/vault/pin/status")
async def vault_pin_status(user_id: str = Depends(require_user)):
    """Check if the user has a vault PIN set."""
    return {"has_pin": has_vault_pin(user_id)}


@app.post("/api/vault/pin/set")
async def vault_pin_set(req: VaultPinSetRequest, user_id: str = Depends(require_user)):
    """Set or change the vault PIN."""
    try:
        set_vault_pin(user_id, req.pin)
        return {"success": True}
    except ValueError as e:
        return {"success": False, "error": str(e)}


@app.post("/api/vault/pin/verify")
async def vault_pin_verify(req: VaultPinVerifyRequest, user_id: str = Depends(require_user)):
    """Verify vault PIN and get an unlock token."""
    if verify_vault_pin(user_id, req.pin):
        token = create_vault_unlock(user_id)
        return {"success": True, "vault_token": token}
    return {"success": False, "error": "Incorrect PIN"}


@app.post("/api/vault/pin/remove")
async def vault_pin_remove(req: VaultPinRemoveRequest, user_id: str = Depends(require_user)):
    """Remove the vault PIN (requires current PIN)."""
    if remove_vault_pin(user_id, req.current_pin):
        return {"success": True}
    return {"success": False, "error": "Incorrect PIN"}


# --- Personal Log Routes ---

@app.get("/api/personal-logs")
async def get_personal_logs(user_id: str = Depends(require_vault_access)):
    """List personal log entries."""
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    if not data_dir:
        return {"logs": []}
    return {"logs": list_personal_logs(data_dir)}


@app.post("/api/personal-logs")
async def create_personal_log(req: PersonalLogRequest, user_id: str = Depends(require_vault_access)):
    """Create a text-only personal log entry."""
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    if not data_dir:
        return {"error": "No user data directory"}
    entry = create_log_entry(text=req.text, data_dir=data_dir)
    return {"success": True, "entry": entry}


@app.post("/api/personal-logs/audio")
async def create_personal_log_audio(
    audio: UploadFile = File(...),
    text: str = Form(""),
    user_id: str = Depends(require_vault_access),
):
    """Create a personal log with audio upload. Transcription is best-effort."""
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    if not data_dir:
        return {"error": "No user data directory"}

    audio_bytes = await audio.read()

    # Best-effort transcription
    transcription = ""
    try:
        from pydub import AudioSegment
        from core.voice.stt_engine import transcribe

        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        audio_seg = AudioSegment.from_file(tmp_path)
        audio_seg = audio_seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        wav_path = tmp_path.replace(".webm", ".wav")
        audio_seg.export(wav_path, format="wav")

        import numpy as np
        import wave
        with wave.open(wav_path, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            audio_np = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

        transcription = transcribe(audio_np) or ""

        Path(tmp_path).unlink(missing_ok=True)
        Path(wav_path).unlink(missing_ok=True)
    except Exception as e:
        logger.warning("Personal log transcription failed (audio still saved): %s", e)

    entry = create_log_entry(
        text=text,
        data_dir=data_dir,
        audio_bytes=audio_bytes,
        transcription=transcription,
    )
    return {"success": True, "entry": entry}


@app.get("/api/personal-logs/{log_id}")
async def get_personal_log(log_id: str, user_id: str = Depends(require_vault_access)):
    """Load a specific personal log entry."""
    if ".." in log_id or "/" in log_id or "\\" in log_id:
        return {"error": "Invalid log ID"}
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    if not data_dir:
        return {"error": "No user data directory"}
    entry = load_personal_log(log_id, data_dir)
    if entry:
        return entry
    return {"error": "Log not found"}


@app.delete("/api/personal-logs/{log_id}")
async def delete_personal_log_endpoint(log_id: str, user_id: str = Depends(require_vault_access)):
    """Delete a personal log entry."""
    if ".." in log_id or "/" in log_id or "\\" in log_id:
        return {"error": "Invalid log ID"}
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    if not data_dir:
        return {"error": "No user data directory"}
    if delete_personal_log(log_id, data_dir):
        return {"success": True}
    return {"success": False, "error": "Log not found"}


@app.patch("/api/personal-logs/{log_id}")
async def update_personal_log_endpoint(
    log_id: str,
    req: PersonalLogTitleUpdate,
    user_id: str = Depends(require_vault_access),
):
    """Update the title of a personal log entry. Stamps title_edited_at."""
    if ".." in log_id or "/" in log_id or "\\" in log_id:
        return {"error": "Invalid log ID"}
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    if not data_dir:
        return {"error": "No user data directory"}
    entry = update_personal_log_title(log_id, req.title, data_dir)
    if entry:
        return {"success": True, "entry": entry}
    return {"success": False, "error": "Log not found or invalid title"}


@app.get("/api/personal-logs/{log_id}/audio")
async def get_personal_log_audio(log_id: str, user_id: str = Depends(require_vault_access)):
    """Stream a personal log's audio file."""
    if ".." in log_id or "/" in log_id or "\\" in log_id:
        raise HTTPException(status_code=400, detail="Invalid log ID")
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    if not data_dir:
        raise HTTPException(status_code=404, detail="No user data directory")
    audio_path = get_audio_path(log_id, data_dir)
    if audio_path:
        return FileResponse(str(audio_path), media_type="audio/webm")
    raise HTTPException(status_code=404, detail="Audio not found")


# --- Google OAuth Routes ---

@app.get("/api/google/auth")
async def google_auth_start(request: Request, user_id: str = Depends(require_user)):
    """Start Google OAuth flow -- returns auth URL for the user to visit."""
    try:
        from integrations.google_config import is_enabled
        from core.protocols.google_tools import build_auth_url
    except ImportError:
        return {"error": "Google integration libraries not installed"}

    if not is_enabled():
        return {"error": "Google integration not configured. See data/google_client.json"}

    # Build redirect URI from the incoming request
    host = request.headers.get("host", "localhost:8484")
    # Google treats 127.0.0.1 and localhost as distinct redirect URIs; the
    # Electron shell loads the app over 127.0.0.1, so normalize to the
    # localhost form that's registered in the Google Cloud Console.
    host = host.replace("127.0.0.1", "localhost")
    scheme = request.headers.get("x-forwarded-proto", "http")
    redirect_uri = f"{scheme}://{host}/api/google/callback"

    # Generate state token to pass user identity through OAuth redirect
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = user_id

    auth_url = build_auth_url(redirect_uri, state=state)
    if not auth_url:
        return {"error": "Could not generate Google auth URL"}

    return {"auth_url": auth_url}


@app.get("/api/google/callback")
async def google_auth_callback(request: Request):
    """Handle OAuth callback from Google (browser redirect, not API call)."""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        return HTMLResponse(f"<h2>Google authorization failed</h2><p>{error}</p>")

    if not code or not state:
        return HTMLResponse("<h2>Invalid callback</h2><p>Missing code or state parameter.</p>")

    # Validate state and recover user_id
    user_id = _oauth_states.pop(state, None)
    if not user_id:
        return HTMLResponse("<h2>Invalid or expired state</h2><p>Please try connecting again.</p>")

    try:
        from core.protocols.google_tools import exchange_code, save_credentials
    except ImportError:
        return HTMLResponse("<h2>Error</h2><p>Google integration libraries not installed.</p>")

    # Build the same redirect URI used in the auth request
    host = request.headers.get("host", "localhost:8484")
    # Must match the normalization done in the auth request above.
    host = host.replace("127.0.0.1", "localhost")
    scheme = request.headers.get("x-forwarded-proto", "http")
    redirect_uri = f"{scheme}://{host}/api/google/callback"

    credentials = exchange_code(code, redirect_uri)
    if not credentials:
        return HTMLResponse("<h2>Error</h2><p>Could not exchange authorization code.</p>")

    # Save tokens to the user's data directory
    from core.config import PROJECT_ROOT
    user_data_dir = PROJECT_ROOT / "data" / "users" / user_id
    user_data_dir.mkdir(parents=True, exist_ok=True)
    save_credentials(user_data_dir, credentials)

    logger.info("Google account connected for user '%s'", user_id)

    return HTMLResponse(
        "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
        "<h2>Google connected!</h2>"
        "<p>You can close this tab and return to Aegis.</p>"
        "</body></html>"
    )


@app.get("/api/google/status")
async def google_status(user_id: str = Depends(require_user)):
    """Check if the user's Google account is connected."""
    try:
        from integrations.google_config import is_enabled
        from core.protocols.google_tools import load_credentials
    except ImportError:
        return {"configured": False, "connected": False}

    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir

    configured = is_enabled()
    creds = load_credentials(data_dir) if data_dir and configured else None

    result = {
        "configured": configured,
        "connected": creds is not None,
    }

    if creds:
        result["scopes"] = list(creds.scopes) if creds.scopes else []

    return result


@app.post("/api/google/disconnect")
async def google_disconnect(user_id: str = Depends(require_user)):
    """Revoke and delete Google OAuth tokens."""
    try:
        from core.protocols.google_tools import revoke_credentials
    except ImportError:
        return {"success": False, "error": "Google integration libraries not installed"}

    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    if not data_dir:
        return {"success": False, "error": "No user data directory"}

    revoke_credentials(data_dir)

    # Clear protocol cache if active
    google_proto = session.protocol_registry.get("google")
    if google_proto:
        google_proto._cache_time = 0.0
        google_proto._cached_unread = 0
        google_proto._cached_next_event = None

    return {"success": True}


# --- Phase 10: Mood Tracking ---

@app.post("/api/moods")
async def manage_moods(req: MoodRequest, user_id: str = Depends(require_user)):
    """Mood CRUD endpoint."""
    session = session_manager.get_or_create(user_id)
    mm = session.mood_manager
    if req.action == "add" and req.moods:
        entry = mm.add_mood(moods=req.moods, note=req.note or "", energy=req.energy)
        return {"success": True, "mood": entry}
    elif req.action == "delete" and req.mood_id:
        return {"success": mm.delete_mood(req.mood_id)}
    elif req.action == "list":
        return {"moods": mm.list_moods(req.start, req.end)}
    return {"error": "Invalid action. Use: add, delete, list"}


@app.get("/api/moods/today")
async def get_today_moods(user_id: str = Depends(require_user)):
    """Get moods logged today."""
    session = session_manager.get_or_create(user_id)
    return {"moods": session.mood_manager.get_today_moods()}


# --- Phase 10: Contacts ---

@app.post("/api/contacts")
async def manage_contacts(req: ContactRequest, user_id: str = Depends(require_user)):
    """Contact CRUD endpoint."""
    session = session_manager.get_or_create(user_id)
    cm = session.contact_manager
    if req.action == "add" and req.name:
        kwargs = {}
        for f in ("relationship", "phone", "email", "birthday", "likes", "dislikes", "notes"):
            val = getattr(req, f, None)
            if val is not None:
                kwargs[f] = val
        contact = cm.add_contact(name=req.name, **kwargs)
        return {"success": True, "contact": contact}
    elif req.action == "update" and req.contact_id:
        kwargs = {}
        for f in ("name", "relationship", "phone", "email", "birthday", "likes", "dislikes", "notes"):
            val = getattr(req, f, None)
            if val is not None:
                kwargs[f] = val
        contact = cm.update_contact(req.contact_id, **kwargs)
        return {"success": bool(contact), "contact": contact}
    elif req.action == "delete" and req.contact_id:
        return {"success": cm.delete_contact(req.contact_id)}
    elif req.action == "list":
        return {"contacts": cm.list_contacts()}
    elif req.action == "search" and req.query:
        return {"contacts": cm.search_contacts(req.query)}
    return {"error": "Invalid action. Use: add, update, delete, list, search"}


@app.get("/api/contacts/search")
async def search_contacts(q: str = "", user_id: str = Depends(require_user)):
    """Search contacts by query string."""
    session = session_manager.get_or_create(user_id)
    if q:
        return {"contacts": session.contact_manager.search_contacts(q)}
    return {"contacts": session.contact_manager.list_contacts()}


# --- Crew Files ---

@app.post("/api/crew")
async def manage_crew(req: CrewFileRequest, user_id: str = Depends(require_user)):
    """Crew files CRUD endpoint."""
    session = session_manager.get_or_create(user_id)
    cf = session.crew_files
    fields = ("role", "relationship", "department", "bio", "phone",
              "email", "birthday", "likes", "dislikes", "notes", "history")
    if req.action == "add" and req.name:
        kwargs = {}
        for f in fields:
            val = getattr(req, f, None)
            if val is not None:
                kwargs[f] = val
        profile = cf.add_profile(name=req.name, **kwargs)
        return {"success": True, "profile": profile}
    elif req.action == "update" and req.profile_id:
        kwargs = {}
        for f in ("name",) + fields:
            val = getattr(req, f, None)
            if val is not None:
                kwargs[f] = val
        profile = cf.update_profile(req.profile_id, **kwargs)
        return {"success": bool(profile), "profile": profile}
    elif req.action == "delete" and req.profile_id:
        return {"success": cf.delete_profile(req.profile_id)}
    elif req.action == "get" and req.profile_id:
        profile = cf.get_profile(req.profile_id)
        return {"profile": profile}
    elif req.action == "list":
        return {"profiles": cf.list_profiles()}
    elif req.action == "search" and req.query:
        return {"profiles": cf.search_profiles(req.query)}
    return {"error": "Invalid action. Use: add, update, delete, get, list, search"}


# --- Phase 10: Habits ---

@app.post("/api/habits")
async def manage_habits(req: HabitRequest, user_id: str = Depends(require_user)):
    """Habit CRUD endpoint."""
    session = session_manager.get_or_create(user_id)
    hm = session.habit_manager
    if req.action == "add" and req.name:
        habit = hm.add_habit(name=req.name, frequency=req.frequency or "daily")
        return {"success": True, "habit": habit}
    elif req.action == "check" and req.habit_id:
        habit = hm.check_in(req.habit_id, req.date)
        return {"success": bool(habit), "habit": habit}
    elif req.action == "uncheck" and req.habit_id:
        habit = hm.uncheck(req.habit_id, req.date)
        return {"success": bool(habit), "habit": habit}
    elif req.action == "delete" and req.habit_id:
        return {"success": hm.delete_habit(req.habit_id)}
    return {"error": "Invalid action. Use: add, check, uncheck, delete"}


@app.get("/api/habits/today")
async def get_habits_today(user_id: str = Depends(require_user)):
    """Get all habits with today's completion status."""
    session = session_manager.get_or_create(user_id)
    return {"habits": session.habit_manager.get_today_status()}


# --- Phase 10: Behavior Tracking ---

@app.post("/api/behaviors")
async def manage_behaviors(req: BehaviorRequest, user_id: str = Depends(require_user)):
    """Behavior tracking endpoint."""
    session = session_manager.get_or_create(user_id)
    bt = session.behavior_tracker
    if req.action == "add" and req.name:
        behavior = bt.add_behavior(name=req.name)
        return {"success": True, "behavior": behavior}
    elif req.action == "relapse" and req.behavior_id:
        behavior = bt.log_relapse(req.behavior_id, note=req.note or "")
        return {"success": bool(behavior), "behavior": behavior}
    elif req.action == "delete" and req.behavior_id:
        return {"success": bt.delete_behavior(req.behavior_id)}
    elif req.action == "list":
        return {"behaviors": bt.list_behaviors()}
    return {"error": "Invalid action. Use: add, relapse, delete, list"}


# --- Phase 10: Pinned Messages ---

@app.post("/api/pinned-messages")
async def pin_message(req: PinRequest, user_id: str = Depends(require_user)):
    """Pin a chat message."""
    session = session_manager.get_or_create(user_id)
    if not req.text:
        return {"error": "Message text is required"}
    entry = session.pinned_messages.pin_message(
        role=req.role or "user",
        text=req.text,
        sender=req.sender or "",
        note=req.note or "",
    )
    return {"success": True, "pinned": entry}


@app.get("/api/pinned-messages")
async def get_pinned_messages(user_id: str = Depends(require_user)):
    """List all pinned messages."""
    session = session_manager.get_or_create(user_id)
    return {"pinned": session.pinned_messages.list_pinned()}


@app.post("/api/pinned-messages/unpin")
async def unpin_message(req: UnpinRequest, user_id: str = Depends(require_user)):
    """Unpin a message."""
    session = session_manager.get_or_create(user_id)
    return {"success": session.pinned_messages.unpin(req.pin_id)}


# --- Phase 10: Time Tracker ---

@app.post("/api/timer")
async def manage_timer(req: TimerRequest, user_id: str = Depends(require_user)):
    """Timer start/stop/delete endpoint."""
    session = session_manager.get_or_create(user_id)
    tt = session.time_tracker
    if req.action == "start" and req.activity:
        entry = tt.start_timer(activity=req.activity, category=req.category or "general")
        return {"success": True, "entry": entry}
    elif req.action == "stop":
        entry = tt.stop_timer()
        return {"success": bool(entry), "entry": entry}
    elif req.action == "delete" and req.entry_id:
        return {"success": tt.delete_entry(req.entry_id)}
    elif req.action == "list":
        return {"entries": tt.list_entries()}
    return {"error": "Invalid action. Use: start, stop, delete, list"}


@app.get("/api/timer/active")
async def get_active_timer(user_id: str = Depends(require_user)):
    """Get the currently running timer."""
    session = session_manager.get_or_create(user_id)
    active = session.time_tracker.get_active_timer()
    return {"active": active}


@app.get("/api/timer/today")
async def get_timer_today(user_id: str = Depends(require_user)):
    """Get today's time tracking summary."""
    session = session_manager.get_or_create(user_id)
    return session.time_tracker.get_today_summary()


# --- Phase 10: Weather ---

@app.get("/api/weather")
async def get_weather(detail: str = "current", user_id: str = Depends(require_user)):
    """Get weather for saved location. Use detail=full for forecast."""
    session = session_manager.get_or_create(user_id)
    return session.weather_service.get_weather(detail=detail)


@app.post("/api/weather/location")
async def set_weather_location(req: WeatherLocationRequest, user_id: str = Depends(require_user)):
    """Set the user's weather location."""
    session = session_manager.get_or_create(user_id)
    loc = session.weather_service.set_location(lat=req.lat, lon=req.lon, name=req.name or "")
    return {"success": True, "location": loc}


@app.get("/api/weather/location")
async def get_weather_location(user_id: str = Depends(require_user)):
    """Get the saved weather location."""
    session = session_manager.get_or_create(user_id)
    loc = session.weather_service.get_location()
    return {"location": loc}


# --- System Performance ---

@app.get("/api/system/perf")
async def get_system_perf(user_id: str = Depends(require_user)):
    """Get system performance metrics (CPU, RAM, disk, network)."""
    try:
        import psutil
    except ImportError:
        return {"error": "psutil not installed"}

    cpu_pct = psutil.cpu_percent(interval=None)
    cpu_cores = psutil.cpu_percent(percpu=True)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()

    # Top 10 processes by CPU
    top_procs = []
    try:
        for proc in sorted(
            psutil.process_iter(["name", "cpu_percent"]),
            key=lambda p: p.info.get("cpu_percent") or 0,
            reverse=True,
        )[:10]:
            info = proc.info
            if info.get("cpu_percent", 0) > 0:
                top_procs.append({
                    "name": info.get("name", "?"),
                    "cpu": info.get("cpu_percent", 0),
                })
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    return {
        "cpu_percent": cpu_pct,
        "cpu_cores": cpu_cores,
        "ram_percent": mem.percent,
        "ram_used_gb": round(mem.used / (1024 ** 3), 2),
        "ram_total_gb": round(mem.total / (1024 ** 3), 2),
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / (1024 ** 3), 2),
        "disk_total_gb": round(disk.total / (1024 ** 3), 2),
        "net_sent_mb": round(net.bytes_sent / (1024 ** 2), 2),
        "net_recv_mb": round(net.bytes_recv / (1024 ** 2), 2),
        "top_processes": top_procs,
    }


# --- News Feed ---

@app.get("/api/news")
async def get_news(
    source: str = "ddgs",
    category: str = "local",
    location: str = "",
    user_id: str = Depends(require_user),
):
    """Get news headlines from the specified source."""
    articles = _news_service.get_news(
        source=source, category=category, location=location
    )
    return {"articles": articles}


@app.get("/api/news/sources")
async def get_news_sources(user_id: str = Depends(require_user)):
    """Get available news sources."""
    return {"sources": _news_service.get_sources()}


# --- Web Fetch (article reader) ---

@app.post("/api/web/fetch")
async def web_fetch_page(
    request: Request,
    user_id: str = Depends(require_user),
):
    """Fetch and extract rich article content from a URL for in-app reading."""
    import logging
    _log = logging.getLogger(__name__)
    from core.protocols.web_tools import fetch_page_rich
    body = await request.json()
    url = body.get("url", "")
    if not url:
        return {"error": "No URL provided"}
    try:
        _log.info("[ARTICLE] Fetching: %s", url)
        result = fetch_page_rich(url, max_chars=50000, timeout=15)
        title = result.get("title", "")
        html = result.get("html", "")
        text = result.get("text", "")
        success = result.get("success", False)
        _log.info("[ARTICLE] success=%s title=%s html_len=%d text_len=%d",
                  success, title[:60], len(html), len(text))
        if not success:
            return {"title": title, "error": text or "No content extracted", "success": False}
        if html and len(html) > 100:
            return {"title": title, "content": html, "success": True}
        if text and len(text) > 50:
            # Convert plain text paragraphs to HTML
            content = ""
            for p in text.split("\n\n"):
                p = p.strip()
                if p:
                    content += "<p style='margin-bottom:10px'>" + p.replace("\n", "<br>") + "</p>"
            return {"title": title, "content": content, "success": True}
        return {"title": title, "error": "No content extracted", "success": False}
    except Exception as e:
        return {"error": str(e), "success": False}


# --- Video Personal Logs ---

@app.post("/api/personal-logs/video")
async def create_video_log(
    video: UploadFile = File(...),
    text: str = Form(""),
    user_id: str = Depends(require_user),
):
    """Create a personal log with video recording."""
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    if not data_dir:
        return {"error": "No user data directory"}

    video_bytes = await video.read()

    # Best-effort audio extraction + STT from video
    transcription = ""
    try:
        from pydub import AudioSegment
        from core.voice.stt_engine import transcribe
        import numpy as np
        import wave

        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name

        audio_seg = AudioSegment.from_file(tmp_path)
        audio_seg = audio_seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        wav_path = tmp_path.replace(".webm", ".wav")
        audio_seg.export(wav_path, format="wav")

        with wave.open(wav_path, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            audio_np = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

        transcription = transcribe(audio_np) or ""

        Path(tmp_path).unlink(missing_ok=True)
        Path(wav_path).unlink(missing_ok=True)
    except Exception as e:
        logger.warning("Video audio extraction/STT failed (video still saved): %s", e)

    entry = create_log_entry(
        text=text or transcription,
        data_dir=data_dir,
        video_bytes=video_bytes,
        transcription=transcription,
    )
    return {"success": True, "entry": entry}


@app.get("/api/personal-logs/{log_id}/video")
async def get_log_video(log_id: str, user_id: str = Depends(require_vault_access)):
    """Serve video file for a personal log."""
    if ".." in log_id or "/" in log_id or "\\" in log_id:
        raise HTTPException(status_code=400, detail="Invalid log ID")
    session = session_manager.get_or_create(user_id)
    data_dir = session.memory.user_data_dir
    if not data_dir:
        raise HTTPException(status_code=404, detail="No user data directory")
    video_path = get_video_path(log_id, data_dir)
    if not video_path:
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(video_path, media_type="video/webm")


# --- Phase 10: Alarms ---

@app.post("/api/alarms")
async def manage_alarms(req: AlarmRequest, user_id: str = Depends(require_user)):
    """Alarm CRUD endpoint."""
    session = session_manager.get_or_create(user_id)
    am = session.alarm_manager
    if req.action == "add" and req.label and req.time:
        alarm = am.add_alarm(label=req.label, time=req.time, days=req.days)
        return {"success": True, "alarm": alarm}
    elif req.action == "toggle" and req.alarm_id:
        alarm = am.toggle_alarm(req.alarm_id)
        return {"success": bool(alarm), "alarm": alarm}
    elif req.action == "delete" and req.alarm_id:
        return {"success": am.delete_alarm(req.alarm_id)}
    elif req.action == "dismiss" and req.alarm_id:
        alarm = am.dismiss(req.alarm_id)
        return {"success": bool(alarm), "alarm": alarm}
    elif req.action == "list":
        return {"alarms": am.list_alarms()}
    return {"error": "Invalid action. Use: add, toggle, delete, dismiss, list"}


@app.get("/api/alarms/check")
async def check_alarms(user_id: str = Depends(require_user)):
    """Check for due alarms (polled by frontend)."""
    session = session_manager.get_or_create(user_id)
    due = session.alarm_manager.check_due_alarms()
    return {"due": due}


# --- Phase 10: File Upload ---

@app.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: str = Depends(require_user),
):
    """Upload a file for analysis."""
    session = session_manager.get_or_create(user_id)
    content = await file.read()
    result = session.file_manager.upload_file(
        original_name=file.filename or "upload",
        file_bytes=content,
        mime_type=file.content_type or "",
    )
    if "error" in result:
        return {"success": False, "error": result["error"]}
    return {"success": True, "file": {k: v for k, v in result.items() if k != "text_content"}}


@app.get("/api/files")
async def list_files(user_id: str = Depends(require_user)):
    """List uploaded files."""
    session = session_manager.get_or_create(user_id)
    return {"files": session.file_manager.list_files()}


@app.delete("/api/files/{file_id}")
async def delete_file(file_id: str, user_id: str = Depends(require_user)):
    """Delete an uploaded file."""
    if ".." in file_id or "/" in file_id or "\\" in file_id:
        return {"error": "Invalid file ID"}
    session = session_manager.get_or_create(user_id)
    return {"success": session.file_manager.delete_file(file_id)}


@app.post("/api/files/{file_id}/analyze")
async def analyze_file(file_id: str, user_id: str = Depends(require_user)):
    """Inject a file's text content into the next chat message as context."""
    if ".." in file_id or "/" in file_id or "\\" in file_id:
        return {"error": "Invalid file ID"}
    session = session_manager.get_or_create(user_id)
    text = session.file_manager.get_text(file_id)
    if not text:
        return {"success": False, "error": "No text content available for this file"}
    file_info = session.file_manager.get_file(file_id)
    name = file_info.get("original_name", "file") if file_info else "file"
    # Truncate to avoid blowing context
    max_chars = 8000
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... [truncated, {len(text)} chars total]"
    session._pending_file_context = f"[Uploaded file: {name}]\n{text}"
    return {"success": True, "preview": text[:500]}


# --- Phase 10: Social Media ---

@app.post("/api/social/projects")
async def create_social_project(req: SocialProjectRequest, user_id: str = Depends(require_user)):
    """Create a social media project."""
    session = session_manager.get_or_create(user_id)
    project = session.social_manager.add_project(name=req.name)
    return {"success": True, "project": project}


@app.get("/api/social/projects")
async def list_social_projects(user_id: str = Depends(require_user)):
    """List social media projects."""
    session = session_manager.get_or_create(user_id)
    return {"projects": session.social_manager.list_projects()}


@app.delete("/api/social/projects/{project_id}")
async def delete_social_project(project_id: str, user_id: str = Depends(require_user)):
    """Delete a social media project."""
    session = session_manager.get_or_create(user_id)
    return {"success": session.social_manager.delete_project(project_id)}


@app.post("/api/social/projects/{project_id}/accounts")
async def add_social_account(
    project_id: str,
    platform: str = Form(...),
    handle: str = Form(...),
    user_id: str = Depends(require_user),
):
    """Add a social media account to a project."""
    session = session_manager.get_or_create(user_id)
    project = session.social_manager.add_account(project_id, platform, handle)
    return {"success": bool(project), "project": project}


@app.post("/api/social/posts")
async def create_social_post(req: SocialPostRequest, user_id: str = Depends(require_user)):
    """Create a post in a project."""
    session = session_manager.get_or_create(user_id)
    post = session.social_manager.add_post(
        project_id=req.project_id,
        content=req.content,
        platform=req.platform or "",
        status=req.status or "draft",
    )
    if post:
        return {"success": True, "post": post}
    return {"success": False, "error": "Project not found"}


# --- Static/PWA Routes ---

@app.get("/sw.js")
async def service_worker():
    """Serve the service worker from root path."""
    sw_path = ui_dir / "static" / "sw.js"
    from fastapi.responses import Response
    if sw_path.exists():
        return Response(
            content=sw_path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"},
        )
    return Response(status_code=404)


@app.get("/manifest.json")
async def manifest():
    """PWA manifest for phone/tablet install."""
    theme_pack = load_theme_pack()
    colors = theme_pack.get("theme", {}).get("colors", {})
    bg_color = colors.get("background", "#0f172a")
    theme_color = colors.get("blue_1", "#2563eb")
    return {
        "name": "Aegis AI",
        "short_name": "Aegis",
        "description": "Your digital aegis -- protective AI companion",
        "start_url": "/",
        "display": "standalone",
        "background_color": bg_color,
        "theme_color": theme_color,
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }


if __name__ == "__main__":
    import uvicorn
    print(f"\n  Aegis AI Server starting...")
    print(f"  Multi-user mode enabled")
    print(f"  Access from this PC: http://localhost:8484")
    print(f"  Access from phone/tablet: http://<your-pc-ip>:8484")
    print()
    uvicorn.run(app, host="0.0.0.0", port=8484)
