"""
Aegis AI — FastAPI Server
Exposes the Aegis agent as an HTTP API for multi-device access.
Run with: uvicorn server.app:app --host 0.0.0.0 --port 8484
"""

import sys
import re
import logging
from pathlib import Path
from contextlib import asynccontextmanager

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, Request, UploadFile, File, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional
import json
import tempfile

import ollama
from core.config import CONFIG, get_path, load_capabilities
from core.auth import (
    load_users, create_user, verify_user, change_passcode,
    create_session, validate_token, invalidate_token, get_current_user,
    load_user_preferences, save_user_preferences, user_exists,
)
from core.session import SessionManager, UserSession
from core.personality.pack_loader import (
    load_personality_pack, build_system_prompt,
    get_agent_display_name, list_packs, load_voice_pack,
    load_theme_pack,
)
from core.voice import emotion
from core.memory.transcript import list_transcripts, load_transcript
from core.memory.profile import (
    get_profile_summary, get_profile_facts, update_profile, remove_profile_fact,
)
from core.agent import build_filler_cleaner


# --- Session Manager (replaces global state) ---
session_manager = SessionManager()

logger = logging.getLogger("aegis.server")

# Load core directives once (shared, immutable)
with open(get_path(CONFIG, "personality_prompt"), "r", encoding="utf-8") as f:
    _core_directives = f.read()


# --- Auth Dependency ---

async def require_user(request: Request) -> str:
    """FastAPI dependency — require a valid authenticated user."""
    user_id = get_current_user(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id


# --- FastAPI App ---

@asynccontextmanager
async def lifespan(app):
    yield
    # Server shutting down — save all sessions
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


class TaskRequest(BaseModel):
    action: str  # add, done, remove
    text: Optional[str] = None
    task_id: Optional[int] = None
    priority: Optional[str] = "normal"


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
    """Invalidate the current session token."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        user_id = validate_token(token)
        if user_id:
            session_manager.end_session(user_id)
        invalidate_token(token)
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
    session = session_manager.get_or_create(user_id)
    user_input = req.message.strip()
    if not user_input:
        return ChatResponse(agent_name=session.agent_name, response="")

    # Check for slash commands
    if user_input.startswith("/"):
        cmd_parts = user_input[1:].split(None, 1)
        cmd_name = cmd_parts[0].lower() if cmd_parts else ""
        cmd_args = cmd_parts[1] if len(cmd_parts) > 1 else ""
        handled, cmd_response = session.protocol_registry.handle_command(cmd_name, cmd_args)
        if handled:
            return ChatResponse(agent_name=session.agent_name, response=cmd_response)

    # Refresh session context
    session_context = session.memory.build_session_context()
    msg_count = len([m for m in session.messages if m["role"] != "system"])
    char_context = session.char_memory.get_core_context(message_count=msg_count)
    capabilities_prompt = load_capabilities()
    refreshed_prompt = "\n\n".join([p for p in [session.system_prompt_base, capabilities_prompt, char_context, session_context] if p])
    session.messages[0] = {"role": "system", "content": refreshed_prompt}

    # Run through protocols
    proto_context = {"messages": session.messages, "memory": session.memory, "char_memory": session.char_memory}
    proto_result = session.protocol_registry.process_input(user_input, proto_context)

    if proto_result.get("intercept"):
        return ChatResponse(
            agent_name=session.agent_name,
            response=proto_result["response"],
        )

    # Emotion detection
    emotion_result = emotion.detect_emotion(user_input)
    emotion_tag = emotion.format_emotion_tag(emotion_result)

    # Memory search
    relevant = session.memory.get_relevant_memories(user_input)
    char_relevant = session.char_memory.get_relevant_memories(user_input)

    # Build augmented input
    context_parts = []
    if relevant:
        context_parts.append("Relevant memory:\n" + relevant)
    if char_relevant:
        context_parts.append(char_relevant)
    if emotion_tag:
        context_parts.append(emotion_tag)
    for injection in proto_result.get("context_injections", []):
        context_parts.append(injection)

    augmented = proto_result["input"]
    if context_parts:
        augmented = "[" + "\n".join(context_parts) + "]\n\n" + augmented

    # Add to history
    session.messages.append({"role": "user", "content": user_input})
    messages_to_send = session.messages[:-1] + [{"role": "user", "content": augmented}]

    try:
        response = ollama.chat(model=CONFIG["model"]["chat"], messages=messages_to_send)
        reply = session.clean_reply(response["message"]["content"])

        # Run through output protocols
        output_result = session.protocol_registry.process_output(reply, proto_context)
        if not output_result.get("suppress"):
            reply = output_result["response"]

        session.messages.append({"role": "assistant", "content": reply})

        # Auto-save transcript
        session.memory.periodic_save(session.messages)

        # Periodic fact extraction
        FACT_EXTRACTION_INTERVAL = 15
        msg_count = len([m for m in session.messages if m["role"] != "system"])
        if msg_count - session.last_fact_extraction_index >= FACT_EXTRACTION_INTERVAL:
            session.memory.extract_recent_facts(session.messages, since_index=session.last_fact_extraction_index)
            session.last_fact_extraction_index = msg_count

        return ChatResponse(
            agent_name=session.agent_name,
            response=reply,
            emotion=emotion_result,
            wellness_flag=bool(proto_result.get("context_injections")),
        )

    except Exception as e:
        session.messages.pop()
        return ChatResponse(
            agent_name=session.agent_name,
            response=f"Communication error: {e}",
        )


@app.post("/api/end-session")
async def end_session(user_id: str = Depends(require_user)):
    """End the current session — saves transcript, summary, facts, profile."""
    session_manager.end_session(user_id)
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
        task = ops.add_task(req.text, priority=req.priority or "normal")
        return {"success": True, "task": task}
    elif req.action == "done" and req.task_id:
        task = ops.complete_task(req.task_id)
        return {"success": bool(task), "task": task}
    elif req.action == "remove" and req.task_id:
        removed = ops.remove_task(req.task_id)
        return {"success": removed}
    elif req.action == "list":
        return {"tasks": ops.get_pending_tasks()}
    else:
        return {"error": "Invalid action. Use: add, done, remove, list"}


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
        audio_seg = audio_seg.set_frame_rate(16000).set_channels(1)
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
async def get_transcripts(user_id: str = Depends(require_user)):
    """List available session transcripts."""
    session = session_manager.get_or_create(user_id)
    session_ids = list_transcripts(data_dir=session.memory.user_data_dir)[:50]
    sessions = []
    for sid in session_ids:
        sessions.append({"session_id": sid})
    return {"sessions": sessions}


@app.get("/api/transcripts/{session_id}")
async def get_transcript(session_id: str, user_id: str = Depends(require_user)):
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
