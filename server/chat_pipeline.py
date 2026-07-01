"""
Aegis AI — Chat Pipeline
Reusable async chat function shared by the web UI and Telegram bot.
"""

import asyncio
import logging

from core.llm import chat as router_chat
from core.config import CONFIG, load_capabilities
from core.voice import emotion
from core.protocols.context_budget import budget_injections

logger = logging.getLogger("aegis.chat_pipeline")


async def process_chat(session_manager, user_id: str, user_input: str) -> dict:
    """Process a chat message through the full Aegis pipeline.

    Returns dict with keys: agent_name, response, emotion, wellness_flag.
    """
    session = session_manager.get_or_create(user_id)

    if not user_input:
        return {
            "agent_name": session.agent_name,
            "response": "",
            "emotion": None,
            "wellness_flag": False,
        }

    # Check for slash commands
    if user_input.startswith("/"):
        cmd_parts = user_input[1:].split(None, 1)
        cmd_name = cmd_parts[0].lower() if cmd_parts else ""
        cmd_args = cmd_parts[1] if len(cmd_parts) > 1 else ""
        handled, cmd_response = session.protocol_registry.handle_command(cmd_name, cmd_args)
        if handled:
            return {
                "agent_name": session.agent_name,
                "response": cmd_response,
                "emotion": None,
                "wellness_flag": False,
            }

    # Refresh session context
    session_context = session.memory.build_session_context()
    msg_count = len([m for m in session.messages if m["role"] != "system"])
    char_context = session.char_memory.get_core_context(message_count=msg_count)
    capabilities_prompt = load_capabilities()
    refreshed_prompt = "\n\n".join(
        [p for p in [session.system_prompt_base, capabilities_prompt, char_context, session_context] if p]
    )
    session.messages[0] = {"role": "system", "content": refreshed_prompt}

    # Run through input protocols — include profile for location-aware protocols
    user_profile = ""
    try:
        fs = session.memory._fact_store
        if fs:
            user_profile = fs.render_profile(companion_name="user")
        if not user_profile:
            from core.memory.profile import get_profile_summary
            user_profile = get_profile_summary(data_dir=session.memory.user_data_dir) or ""
    except Exception:
        pass
    proto_context = {
        "messages": session.messages,
        "memory": session.memory,
        "char_memory": session.char_memory,
        "profile": user_profile,
    }
    proto_result = session.protocol_registry.process_input(user_input, proto_context)

    if proto_result.get("intercept"):
        # Save to message history so follow-up works
        session.messages.append({"role": "user", "content": user_input})
        session.messages.append({"role": "assistant", "content": proto_result["response"]})
        # Save transcript incrementally (same as LLM path)
        session.memory.periodic_save(session.messages)
        return {
            "agent_name": session.agent_name,
            "response": proto_result["response"],
            "emotion": None,
            "wellness_flag": False,
        }

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
    injections = proto_result.get("context_injections", [])
    if injections:
        logger.info("Protocol injections received: %d", len(injections))
        for i, inj in enumerate(injections):
            logger.info("  Injection %d (%d lines): %s", i, len(inj.splitlines()), inj[:150])
    for injection in budget_injections(injections):
        context_parts.append(injection)

    # Full-context injections bypass budget (article expansion, file analysis)
    for fc in proto_result.get("full_context_injections", []):
        context_parts.append(fc)

    # File context injection (from /api/files/{id}/analyze)
    if hasattr(session, "_pending_file_context") and session._pending_file_context:
        context_parts.append(session._pending_file_context)
        session._pending_file_context = None

    augmented = proto_result["input"]
    context_system_msg = None
    if context_parts:
        context_block = "\n".join(context_parts)
        # Inject context as a system message so the LLM treats it as
        # authoritative data (not user chatter wrapped in brackets).
        context_system_msg = {"role": "system", "content": context_block}
        logger.info("Context injection (%d chars): %s", len(context_block), context_block[:300])

    # Add to history
    session.messages.append({"role": "user", "content": user_input})
    messages_to_send = list(session.messages[:-1])
    if context_system_msg:
        messages_to_send.append(context_system_msg)
    messages_to_send.append({"role": "user", "content": augmented})

    try:
        # Run the (synchronous) router in a thread so we don't block the loop
        reply_content = await asyncio.to_thread(
            router_chat,
            messages_to_send,
            sensitivity="personal",
            task="chat",
            model=CONFIG["model"]["chat"],
        )
        reply = session.clean_reply(reply_content)

        # Run through output protocols
        output_result = session.protocol_registry.process_output(reply, proto_context)
        if not output_result.get("suppress"):
            reply = output_result["response"]

        # Extract bracket command actions (if any were executed during output processing)
        bracket_proto = session.protocol_registry.get("bracket_commands")
        bracket_actions = bracket_proto.get_pending_actions() if bracket_proto else []

        # Generate notifications from bracket action results
        if bracket_actions:
            for action in bracket_actions:
                label = action["command"].replace("_", " ").title()
                session.notification_service.add(
                    type="bracket_action_result",
                    title=f"{label}: {action['arg']}",
                    body=action.get("result", ""),
                )

        session.messages.append({"role": "assistant", "content": reply})

        # Auto-save transcript
        session.memory.periodic_save(session.messages)

        # Periodic fact extraction
        FACT_EXTRACTION_INTERVAL = 15
        msg_count = len([m for m in session.messages if m["role"] != "system"])
        if msg_count - session.last_fact_extraction_index >= FACT_EXTRACTION_INTERVAL:
            session.memory.extract_recent_facts(session.messages, since_index=session.last_fact_extraction_index)
            session.last_fact_extraction_index = msg_count

        return {
            "agent_name": session.agent_name,
            "response": reply,
            "emotion": emotion_result,
            "wellness_flag": bool(proto_result.get("context_injections")),
            "bracket_actions": bracket_actions,
        }

    except Exception as e:
        session.messages.pop()
        logger.error("Chat pipeline error: %s", e)
        return {
            "agent_name": session.agent_name,
            "response": f"Communication error: {e}",
            "emotion": None,
            "wellness_flag": False,
        }
