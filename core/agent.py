"""
Aegis AI — Core Agent
The main conversation loop for the Aegis AI companion.

Loads core directives, active personality/voice/theme packs, and
character memories to create a complete agent experience.
"""

import re
import sys
from pathlib import Path

# Ensure project root is on the path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import ollama
from core.config import CONFIG, get_path, PROJECT_ROOT as PROJ_ROOT, load_capabilities
from core.memory.manager import MemoryManager
from core.memory.character_memory import CharacterMemory
from core.voice import tts_engine, stt_engine, input_router, emotion
from core.personality.pack_loader import (
    load_personality_pack,
    load_voice_pack,
    build_system_prompt,
    get_agent_display_name,
    get_banner,
    list_packs,
)
from core.protocols.registry import ProtocolRegistry
from core.protocols.communications import CommunicationsProtocol
from core.protocols.security import SecurityProtocol
from core.protocols.wellness import WellnessProtocol
from core.protocols.operations import OperationsProtocol
from core.protocols.web import WebProtocol
from core.protocols.command import CommandProtocol
from core.protocols.creative import CreativeProtocol


def load_core_directives():
    """Load the core Aegis directives from file."""
    directives_path = get_path(CONFIG, "personality_prompt")
    with open(directives_path, "r", encoding="utf-8") as f:
        return f.read()


def build_filler_cleaner(personality_pack):
    """Build a response cleaner from the personality pack's filler phrases."""
    filler_data = personality_pack.get("filler_phrases", [])
    word_replacements = {}

    if isinstance(filler_data, list):
        phrases = filler_data
    elif isinstance(filler_data, dict):
        phrases = filler_data
    else:
        phrases = []

    # If pack has structured filler data with word replacements
    if isinstance(personality_pack.get("filler_phrases"), list):
        phrases = personality_pack["filler_phrases"]
    else:
        # Load from pack — filler_phrases.json has {"phrases": [...], "word_replacements": {...}}
        pack_data = personality_pack.get("filler_phrases", [])
        if isinstance(pack_data, dict):
            phrases = pack_data.get("phrases", [])
            word_replacements = pack_data.get("word_replacements", {})
        else:
            phrases = pack_data if isinstance(pack_data, list) else []

    def clean_reply(text):
        """Post-process agent response using pack-specific filters."""
        # Strip qwen3 thinking blocks (chain-of-thought reasoning)
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

        # Strip emoji (qwen3 likes to add them, cp1252 console can't handle them)
        text = re.sub(r'[\U00010000-\U0010ffff]', '', text)

        # Normalize curly quotes
        text = text.replace("\u2018", "'").replace("\u2019", "'")
        text = text.replace("\u201c", '"').replace("\u201d", '"')

        # Strip third-person narration
        text = re.sub(r'\*[^*]+\*\s*', '', text)
        text = re.sub(r'^[a-z].*?[,\.]\s*"', '"', text)
        text = text.strip('"')

        # Replace exclamation marks with periods
        text = text.replace("!", ".")

        # Strip filler phrases
        for phrase in phrases:
            base = phrase.rstrip(".,")
            pattern = re.escape(base) + r'\b[.,]?\s*'
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)

        # Replace words that leak chatbot tone
        for word, replacement in word_replacements.items():
            text = re.sub(rf'\b{word}\b', replacement, text, flags=re.IGNORECASE)

        # Clean up whitespace
        text = re.sub(r'  +', ' ', text)
        text = re.sub(r'\n ', '\n', text)
        text = text.strip()
        text = re.sub(r'^\.\s*', '', text)
        text = re.sub(r'\.\s*\.', '.', text)
        text = text.strip()

        # Hard sentence cap — models ignore "keep it short" instructions.
        # Collapse to single line first, then cap at 3 sentences.
        # Only allow multi-line for content with actual list markers (1. or -).
        has_list = bool(re.search(r'(?m)^[\s]*(?:\d+\.|[-*])\s', text))
        if not has_list:
            # Collapse newlines to spaces for non-list content
            text = re.sub(r'\s*\n\s*', ' ', text)
            # Split on sentence boundaries and cap at 3
            sentences = re.split(r'(?<=[.?])\s+', text)
            sentences = [s for s in sentences if s.strip()]
            if len(sentences) > 3:
                sentences = sentences[:3]
            # Drop trailing fragment if it's too short or doesn't end properly
            # (e.g., "Or.." left over from a cut-off thought)
            while sentences and (
                len(sentences[-1].split()) <= 2
                and not sentences[-1].rstrip('.').endswith(('?', '.'))
            ):
                sentences.pop()
            if sentences:
                text = ' '.join(sentences)
                if not text.endswith(('.', '?')):
                    text += '.'

        return text.strip()

    return clean_reply


def _authenticate():
    """Authenticate the user at the terminal. Returns username."""
    from getpass import getpass
    from core.auth import user_exists, load_users, verify_user, create_user, load_user_preferences

    print()
    if not user_exists():
        print("  No user accounts found. Let's create one.")
        print()
        while True:
            username = input("  Username: ").strip().lower()
            if not username or not username.isalnum():
                print("  Username must be alphanumeric. Try again.")
                continue
            display_name = input(f"  Display name [{username.title()}]: ").strip()
            if not display_name:
                display_name = username.title()
            passcode = getpass("  Passcode (min 4 chars): ")
            if len(passcode) < 4:
                print("  Passcode must be at least 4 characters. Try again.")
                continue
            passcode_confirm = getpass("  Confirm passcode: ")
            if passcode != passcode_confirm:
                print("  Passcodes don't match. Try again.")
                continue
            try:
                create_user(username, display_name, passcode)
                print(f"  Account '{username}' created.")
                return username
            except ValueError as e:
                print(f"  Error: {e}")
    else:
        while True:
            username = input("  Username: ").strip().lower()
            passcode = getpass("  Passcode: ")
            if verify_user(username, passcode):
                users = load_users()
                display = users.get(username, {}).get("display_name", username.title())
                print(f"  Welcome back, {display}.")
                return username
            print("  Invalid username or passcode. Try again.")


def run():
    """Main chat loop with the Aegis agent."""
    print()
    print("=" * 60)
    print("  AEGIS AI — SECURE LOGIN")
    print("=" * 60)

    # Authenticate
    user_id = _authenticate()

    # Load user preferences for pack selection
    from core.auth import load_user_preferences
    prefs = load_user_preferences(user_id)
    personality_name = prefs.get("active_personality",
                                 CONFIG.get("packs", {}).get("active_personality", "default"))

    # Load packs
    personality_pack = load_personality_pack(personality_name)
    voice_pack = load_voice_pack()
    agent_name = get_agent_display_name(personality_pack)
    banner = get_banner(personality_pack)

    print()
    print("=" * 60)
    print(banner)
    print("=" * 60)
    print()
    print("  Initializing systems...")

    # Initialize memory scoped to this user
    memory = MemoryManager(user_id=user_id)
    memory.set_names(agent_name)

    # Load character memories from personality pack
    char_memory = CharacterMemory(personality_pack.get("memories", {}))

    # Build system prompt: core directives + pack overlay + capabilities + character memories + session
    core_directives = load_core_directives()
    personality_prompt = build_system_prompt(core_directives, personality_pack)
    capabilities_prompt = load_capabilities()
    session_context = memory.build_session_context()
    char_context = char_memory.get_core_context()

    system_prompt_parts = [personality_prompt]
    if capabilities_prompt:
        system_prompt_parts.append(capabilities_prompt)
    if char_context:
        system_prompt_parts.append(char_context)
    system_prompt_parts.append(session_context)
    system_prompt = "\n\n".join(system_prompt_parts)

    # Build response cleaner from pack
    clean_reply = build_filler_cleaner(personality_pack)

    # Initialize protocol registry
    user_data_dir = memory.user_data_dir
    protocol_registry = ProtocolRegistry()
    protocol_registry.register(SecurityProtocol())
    protocol_registry.register(WellnessProtocol())
    protocol_registry.register(CommunicationsProtocol())
    protocol_registry.register(OperationsProtocol(data_dir=user_data_dir))
    protocol_registry.register(WebProtocol())
    protocol_registry.register(CommandProtocol())
    protocol_registry.register(CreativeProtocol())
    print("  Protocols online: " + ", ".join(protocol_registry.list_protocols()))

    # Conversation history
    messages = [{"role": "system", "content": system_prompt}]

    print("  Memory systems online.")

    if tts_engine.is_enabled():
        print("  TTS: Enabled (will load on first response)")
    if stt_engine.is_enabled():
        print("  STT: Enabled (will load on first /voice command)")
    if emotion.is_enabled():
        print("  Emotion detection: Enabled (will load on first message)")

    print()
    print("  Type 'quit' or 'exit' to end the session.")
    print("  Type 'status' for systems status.")
    print("  Type '/tts' to toggle voice output on/off.")
    if stt_engine.is_enabled():
        print("  Type '/voice' or '/v' for a single voice message.")
        print("  Type '/voice on' for hands-free voice mode.")
    print("  Type '/pack list' to see installed packs.")
    print()

    # Initial greeting
    greeting = "Welcome. What can I help you with today?"
    print(f"{agent_name}: {greeting}")
    print()

    if tts_engine.is_enabled():
        tts_engine.speak(greeting)

    while True:
        user_input, input_source = input_router.get_input()

        if user_input is None:
            print()
            _end_session(memory, messages, agent_name)
            break

        if not user_input:
            continue

        if user_input.lower() in ["quit", "exit"]:
            _end_session(memory, messages, agent_name)
            break

        if user_input.lower() == "status":
            _show_status(memory, agent_name, personality_pack, protocol_registry)
            continue

        if user_input.lower() in ["/tts", "/tts on", "/tts off"]:
            _toggle_tts(user_input.lower())
            continue

        # Pack commands
        if user_input.lower().startswith("/pack"):
            _handle_pack_command(user_input)
            continue

        # Protocol commands (e.g., /security, /wellness)
        if user_input.startswith("/"):
            cmd_parts = user_input[1:].split(None, 1)
            cmd_name = cmd_parts[0].lower() if cmd_parts else ""
            cmd_args = cmd_parts[1] if len(cmd_parts) > 1 else ""
            handled, cmd_response = protocol_registry.handle_command(cmd_name, cmd_args)
            if handled:
                if cmd_response:
                    print(cmd_response)
                continue

        # Refresh session context (picks up new profile facts, updated time)
        refreshed_session = memory.build_session_context()
        msg_count = len([m for m in messages if m["role"] != "system"])
        refreshed_char = char_memory.get_core_context(message_count=msg_count)
        refreshed_parts = [personality_prompt]
        if capabilities_prompt:
            refreshed_parts.append(capabilities_prompt)
        if refreshed_char:
            refreshed_parts.append(refreshed_char)
        refreshed_parts.append(refreshed_session)
        messages[0] = {"role": "system", "content": "\n\n".join(refreshed_parts)}

        # Run input through protocol pipeline
        proto_context = {
            "messages": messages,
            "memory": memory,
            "char_memory": char_memory,
            "agent_name": agent_name,
        }
        proto_result = protocol_registry.process_input(user_input, proto_context)

        # If a protocol intercepted the response, display it and continue
        if proto_result.get("intercept"):
            print()
            print(f"{agent_name}: {proto_result['response']}")
            print()
            if tts_engine.is_enabled():
                tts_engine.speak(proto_result["response"])
            continue

        # Detect emotion
        emotion_result = emotion.detect_emotion(user_input)
        emotion_tag = emotion.format_emotion_tag(emotion_result)

        # Search for relevant memories (both personal and character)
        relevant = memory.get_relevant_memories(user_input)
        char_relevant = char_memory.get_relevant_memories(user_input)

        # Build augmented input
        context_parts = []
        if relevant:
            context_parts.append(
                "Relevant memory (use only if directly related to what they just said):\n"
                + relevant
            )
        if char_relevant:
            context_parts.append(char_relevant)
        if emotion_tag:
            context_parts.append(emotion_tag)

        # Add protocol context injections
        for injection in proto_result.get("context_injections", []):
            context_parts.append(injection)

        if context_parts:
            context_block = "[" + "\n".join(context_parts) + "]"
            augmented_input = f"{context_block}\n\n{proto_result['input']}"
        else:
            augmented_input = proto_result["input"]

        # Add user message to history
        messages.append({"role": "user", "content": user_input})

        # Build messages to send — swap in augmented version for the latest
        messages_to_send = messages[:-1] + [{"role": "user", "content": augmented_input}]

        try:
            response = ollama.chat(
                model=CONFIG["model"]["chat"],
                messages=messages_to_send
            )

            reply = clean_reply(response["message"]["content"])

            # Run output through protocol pipeline
            output_result = protocol_registry.process_output(reply, proto_context)
            if output_result.get("suppress"):
                messages.pop()
                continue

            reply = output_result["response"]

            print()
            print(f"{agent_name}: {reply}")
            print()

            if tts_engine.is_enabled():
                tts_engine.speak(reply)

            messages.append({"role": "assistant", "content": reply})

        except Exception as e:
            print(f"\n[Communication error: {e}]")
            print(f"{agent_name}: Experiencing some interference. Let's try that again.")
            print()
            messages.pop()


def _end_session(memory, messages, agent_name):
    """Handle end of session."""
    print()
    farewell = "Take care. Until next time."
    print(f"{agent_name}: {farewell}")
    if tts_engine.is_enabled():
        tts_engine.speak(farewell, blocking=True)
    memory.end_session(messages)


def _toggle_tts(cmd):
    """Toggle TTS on or off at runtime."""
    tts_cfg = CONFIG.get("voice", {}).get("tts", {})
    if cmd == "/tts off":
        tts_cfg["enabled"] = False
        tts_engine.stop()
        print("  [TTS off]")
    elif cmd == "/tts on":
        tts_cfg["enabled"] = True
        print("  [TTS on]")
    else:
        current = tts_cfg.get("enabled", False)
        tts_cfg["enabled"] = not current
        if current:
            tts_engine.stop()
        print(f"  [TTS {'off' if current else 'on'}]")


def _handle_pack_command(user_input):
    """Handle /pack commands."""
    parts = user_input.strip().split()
    if len(parts) < 2:
        print("  Usage: /pack list | /pack info <name>")
        return

    subcmd = parts[1].lower()

    if subcmd == "list":
        print()
        print("  Installed Personality Packs:")
        for name in list_packs("personalities"):
            active = " (active)" if name == CONFIG.get("packs", {}).get("active_personality") else ""
            print(f"    - {name}{active}")
        print()
        print("  Installed Voice Packs:")
        for name in list_packs("voices"):
            active = " (active)" if name == CONFIG.get("packs", {}).get("active_voice") else ""
            print(f"    - {name}{active}")
        print()
        print("  Installed Themes:")
        for name in list_packs("themes"):
            active = " (active)" if name == CONFIG.get("packs", {}).get("active_theme") else ""
            print(f"    - {name}{active}")
        print()
    elif subcmd == "info" and len(parts) >= 3:
        pack_name = parts[2]
        pack = load_personality_pack(pack_name)
        manifest = pack.get("manifest", {})
        if manifest:
            print(f"\n  Pack: {manifest.get('name', pack_name)}")
            print(f"  Author: {manifest.get('author', 'Unknown')}")
            print(f"  Version: {manifest.get('version', '?')}")
            print(f"  Description: {manifest.get('description', 'No description')}")
            if manifest.get("disclaimer"):
                print(f"  Disclaimer: {manifest['disclaimer']}")
            memories = pack.get("memories", {})
            total_memories = sum(len(m.get("memories", [])) for m in memories.values())
            print(f"  Character memories: {total_memories}")
            print()
        else:
            print(f"  Pack '{pack_name}' not found or has no manifest.")
    else:
        print("  Usage: /pack list | /pack info <name>")


def _show_status(memory, agent_name, personality_pack, protocol_registry=None):
    """Display systems status."""
    from core.memory.transcript import list_transcripts
    from core.memory.profile import load_profile

    transcripts = list_transcripts()
    profile = load_profile()

    overlay = personality_pack.get("config_overlay", {})
    terminology = overlay.get("terminology", {})
    status_header = terminology.get("status_header", "AEGIS SYSTEMS STATUS")

    print()
    print("=" * 40)
    print(f"  {status_header}")
    print("=" * 40)
    print(f"  Agent: {agent_name}")
    print(f"  Session ID: {memory.session_id}")
    print(f"  Model: {CONFIG['model']['chat']}")
    print(f"  Active personality: {CONFIG.get('packs', {}).get('active_personality', 'default')}")
    print(f"  Active voice: {CONFIG.get('packs', {}).get('active_voice', 'default')}")
    print(f"  Archived logs: {len(transcripts)}")
    print(f"  User profile: {'Active' if profile else 'Not yet created'}")
    print(f"  Auto-summarize: {'Online' if memory.auto_summarize else 'Offline'}")
    print(f"  Auto fact extraction: {'Online' if memory.auto_extract else 'Offline'}")
    print(f"  TTS: {'Online' if tts_engine.is_enabled() else 'Offline'}")
    print(f"  STT: {'Online' if stt_engine.is_enabled() else 'Offline'}")
    print(f"  Emotion detection: {'Online' if emotion.is_enabled() else 'Offline'}")
    print(f"  Input mode: {input_router.get_mode()}")

    # Protocol status
    if protocol_registry:
        print("  ---")
        print("  Protocols:")
        for status in protocol_registry.get_all_status():
            state = "ACTIVE" if status["enabled"] else "DISABLED"
            print(f"    {status['name']}: {state} (priority: {status['priority']})")

    print("=" * 40)
    print()


if __name__ == "__main__":
    run()
