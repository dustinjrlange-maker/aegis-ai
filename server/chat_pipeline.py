"""
Aegis AI — Chat Pipeline
Reusable async chat function shared by the web UI and Telegram bot.
"""

import asyncio
import logging

import ollama
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

    # Run through input protocols
    proto_context = {
        "messages": session.messages,
        "memory": session.memory,
        "char_memory": session.char_memory,
    }
    proto_result = session.protocol_registry.process_input(user_input, proto_context)

    if proto_result.get("intercept"):
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
    for injection in budget_injections(proto_result.get("context_injections", [])):
        context_parts.append(injection)

    augmented = proto_result["input"]
    if context_parts:
        augmented = "[" + "\n".join(context_parts) + "]\n\n" + augmented

    # Add to history
    session.messages.append({"role": "user", "content": user_input})
    messages_to_send = session.messages[:-1] + [{"role": "user", "content": augmented}]

    try:
        # Run ollama.chat in a thread so we don't block the event loop
        response = await asyncio.to_thread(
            ollama.chat,
            model=CONFIG["model"]["chat"],
            messages=messages_to_send,
        )
        reply = session.clean_reply(response["message"]["content"])

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
