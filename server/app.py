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

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional

import ollama
from core.config import CONFIG, get_path
from core.memory.manager import MemoryManager
from core.memory.character_memory import CharacterMemory
from core.personality.pack_loader import (
    load_personality_pack, build_system_prompt,
    get_agent_display_name, list_packs, load_voice_pack,
    load_theme_pack,
)
from core.protocols.registry import ProtocolRegistry
from core.protocols.communications import CommunicationsProtocol
from core.protocols.security import SecurityProtocol
from core.protocols.wellness import WellnessProtocol
from core.protocols.operations import OperationsProtocol
from core.protocols.command import CommandProtocol
from core.protocols.creative import CreativeProtocol
from core.voice import emotion


# --- Initialize Agent State ---

personality_pack = load_personality_pack()
agent_name = get_agent_display_name(personality_pack)
char_memory = CharacterMemory(personality_pack.get("memories", {}))
memory = MemoryManager()

with open(get_path(CONFIG, "personality_prompt"), "r", encoding="utf-8") as f:
    core_directives = f.read()

system_prompt = build_system_prompt(core_directives, personality_pack)
char_context = char_memory.get_core_context()
session_context = memory.build_session_context()
full_prompt = "\n\n".join([p for p in [system_prompt, char_context, session_context] if p])

# Build filler cleaner
from core.agent import build_filler_cleaner
clean_reply = build_filler_cleaner(personality_pack)

# Protocol registry
protocol_registry = ProtocolRegistry()
protocol_registry.register(SecurityProtocol())
protocol_registry.register(WellnessProtocol())
protocol_registry.register(CommunicationsProtocol())
protocol_registry.register(OperationsProtocol())
protocol_registry.register(CommandProtocol())
protocol_registry.register(CreativeProtocol())

# Conversation history (per-session, in-memory for now)
messages = [{"role": "system", "content": full_prompt}]

# Memory auto-save tracking
last_fact_extraction_index = 0
FACT_EXTRACTION_INTERVAL = 15  # messages between LLM-based extractions
session_ended = False  # guard against double end-session (beacon + shutdown)

logger = logging.getLogger("aegis.server")


# --- FastAPI App ---

@asynccontextmanager
async def lifespan(app):
    yield
    # Server shutting down — save everything (skip if already done via /end-session)
    global session_ended
    if not session_ended:
        logger.info("Server shutdown — saving session memory...")
        memory.end_session_quiet(messages)
        session_ended = True

app = FastAPI(title="Aegis AI", version="1.0.0", lifespan=lifespan)

# Serve static files (UI)
ui_dir = PROJECT_ROOT / "ui"
app.mount("/static", StaticFiles(directory=str(ui_dir / "static")), name="static")


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


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main UI."""
    index_path = ui_dir / "templates" / "index.html"
    return FileResponse(str(index_path))


@app.get("/api/status")
async def get_status():
    """Get full system status."""
    return {
        "agent_name": agent_name,
        "session_id": memory.session_id,
        "model": CONFIG["model"]["chat"],
        "active_personality": CONFIG.get("packs", {}).get("active_personality", "default"),
        "active_voice": CONFIG.get("packs", {}).get("active_voice", "default"),
        "protocols": protocol_registry.get_all_status(),
        "message_count": len(messages) - 1,  # exclude system
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Send a message and get a response."""
    user_input = req.message.strip()
    if not user_input:
        return ChatResponse(agent_name=agent_name, response="")

    # Check for slash commands
    if user_input.startswith("/"):
        cmd_parts = user_input[1:].split(None, 1)
        cmd_name = cmd_parts[0].lower() if cmd_parts else ""
        cmd_args = cmd_parts[1] if len(cmd_parts) > 1 else ""
        handled, cmd_response = protocol_registry.handle_command(cmd_name, cmd_args)
        if handled:
            return ChatResponse(agent_name=agent_name, response=cmd_response)

    # Run through protocols
    proto_context = {"messages": messages, "memory": memory, "char_memory": char_memory}
    proto_result = protocol_registry.process_input(user_input, proto_context)

    if proto_result.get("intercept"):
        return ChatResponse(
            agent_name=agent_name,
            response=proto_result["response"],
        )

    # Emotion detection
    emotion_result = emotion.detect_emotion(user_input)
    emotion_tag = emotion.format_emotion_tag(emotion_result)

    # Memory search
    relevant = memory.get_relevant_memories(user_input)
    char_relevant = char_memory.get_relevant_memories(user_input)

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
    messages.append({"role": "user", "content": user_input})
    messages_to_send = messages[:-1] + [{"role": "user", "content": augmented}]

    try:
        response = ollama.chat(model=CONFIG["model"]["chat"], messages=messages_to_send)
        reply = clean_reply(response["message"]["content"])

        # Run through output protocols
        output_result = protocol_registry.process_output(reply, proto_context)
        if not output_result.get("suppress"):
            reply = output_result["response"]

        messages.append({"role": "assistant", "content": reply})

        # Auto-save transcript (cheap, every response)
        global last_fact_extraction_index
        memory.periodic_save(messages)

        # Periodic fact extraction (expensive, every N messages)
        msg_count = len([m for m in messages if m["role"] != "system"])
        if msg_count - last_fact_extraction_index >= FACT_EXTRACTION_INTERVAL:
            memory.extract_recent_facts(messages, since_index=last_fact_extraction_index)
            last_fact_extraction_index = msg_count

        return ChatResponse(
            agent_name=agent_name,
            response=reply,
            emotion=emotion_result,
            wellness_flag=bool(proto_result.get("context_injections")),
        )

    except Exception as e:
        messages.pop()  # remove failed user message
        return ChatResponse(
            agent_name=agent_name,
            response=f"Communication error: {e}",
        )


@app.post("/api/end-session")
async def end_session():
    """End the current session — saves transcript, summary, facts, profile."""
    global session_ended
    if not session_ended:
        memory.end_session_quiet(messages)
        session_ended = True
    return {"success": True}


@app.get("/api/packs")
async def get_packs():
    """List installed packs."""
    return {
        "personalities": list_packs("personalities"),
        "voices": list_packs("voices"),
        "themes": list_packs("themes"),
        "active": {
            "personality": CONFIG.get("packs", {}).get("active_personality", "default"),
            "voice": CONFIG.get("packs", {}).get("active_voice", "default"),
            "theme": CONFIG.get("packs", {}).get("active_theme", "default"),
        },
    }


@app.post("/api/tasks")
async def manage_tasks(req: TaskRequest):
    """Task management endpoint."""
    ops = protocol_registry.get("operations")
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
async def get_theme():
    """Get active theme configuration."""
    theme_pack = load_theme_pack()
    return theme_pack.get("theme", {})


@app.get("/api/gpu")
async def get_gpu():
    """Get GPU status."""
    cmd = protocol_registry.get("command")
    if cmd:
        return cmd.get_gpu_info() or {"error": "GPU info unavailable"}
    return {"error": "Command protocol not available"}


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
        "description": "Your digital aegis — protective AI companion",
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
    print(f"  Agent: {agent_name}")
    print(f"  Access from this PC: http://localhost:8484")
    print(f"  Access from phone/tablet: http://<your-pc-ip>:8484")
    print()
    uvicorn.run(app, host="0.0.0.0", port=8484)
