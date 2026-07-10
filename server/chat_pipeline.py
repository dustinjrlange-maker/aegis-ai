"""
Aegis AI — Chat Pipeline
Reusable async chat function shared by the web UI and Telegram bot.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from core.llm import chat_with_meta as router_chat_with_meta
from core.llm.turn_classifier import classify, route_task_tag, inject_fact_memories
from core.llm.trouble import (detect_trouble, detect_private_content,
                              detect_private_categories)
from core.llm.config import load_config as _load_router_config, resolve_api_key as _resolve_key
from core.config import CONFIG, load_capabilities
from core.tooling import service as tool_service
from core.tooling.autocall import run_tool_loop
from core.voice import emotion
from core.protocols.context_budget import budget_injections

logger = logging.getLogger("aegis.chat_pipeline")


@dataclass(frozen=True)
class EscalationPlan:
    action: str      # "local" | "escalate"
    new_streak: int
    reason: str


def evaluate_escalation(user_message, *, streak, cfg, key_present) -> EscalationPlan:
    """Trouble-only routing decision under escalate-on-trouble mode. Pure — no I/O.

    Decides ONLY whether the local model appears to be failing this turn (the
    user is correcting Pike NOW) and escalation is enabled. It does NOT decide
    the private-content gate: that check runs later, over the ACTUAL assembled
    outgoing payload (retrieved memories + uploaded file text + context
    injections + history + current message) via `payload_has_private_content`,
    because those are assembled after this call and would otherwise reach the
    cloud unscanned.

    Returns action "escalate" (trouble + feature on + key present) or "local".
    """
    t = detect_trouble(user_message, streak)
    if not (cfg.cloud_trouble_escalation and key_present and t.is_trouble):
        return EscalationPlan("local", t.new_streak, t.reason)
    return EscalationPlan("escalate", t.new_streak, t.reason)


def payload_has_private_content(messages) -> tuple[bool, str]:
    """Scan every string-content message in an outgoing payload for private
    content. Returns (True, reason) on the first hit, else (False, "")."""
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            is_priv, reason = detect_private_content(content)
            if is_priv:
                return True, reason
    return False, ""


def payload_private_categories(messages) -> set:
    """ALL private categories across an outgoing payload — consent decisions
    must cover the full set, not just the first match."""
    cats = set()
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            cats |= detect_private_categories(content)
    return cats

_MODE_HINTS = {
    "emotional": "[Response mode: emotional support — you may take up to 5-6 sentences. Stay specific to their words, no advice, no cheerleading, no roleplay.]",
    "task": "[Response mode: task — give the complete, structured answer; take the length it needs.]",
}


async def process_chat(session_manager, user_id: str, user_input: str) -> dict:
    """Process a chat message through the full Aegis pipeline.

    Returns dict with keys: agent_name, response, emotion, wellness_flag.
    """
    session = session_manager.get_or_create(user_id)

    # Resolve a pending trouble-escalation consent prompt. An affirmative reply
    # re-runs the ORIGINAL turn with cloud forced on; anything else clears the
    # pending state and proceeds normally. The consent covers the private
    # CATEGORIES named in the prompt — the re-assembled payload is re-scanned
    # against them below (audit: the re-run used to skip the scan entirely).
    _pending = getattr(session, "_pending_escalation", None)
    _force_trouble_cloud = False
    _consented_categories = set()
    if _pending:
        fresh = (datetime.now() - _pending["ts"]) < timedelta(minutes=5)
        affirmatives = ("yes", "yes use cloud", "use cloud", "go ahead", "ok",
                        "okay", "allow", "allowed", "do it", "sure")
        # Normalize punctuation/whitespace so "yes, use cloud", "yes!", "Yes."
        # all match the comma-free affirmatives above.
        normalized = re.sub(r"[^\w\s]", "", user_input.strip().lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if fresh and normalized in affirmatives:
            user_input = _pending["message"]     # re-run the ORIGINAL turn
            _force_trouble_cloud = True
            _consented_categories = set(_pending.get("categories", []))
        session._pending_escalation = None

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
        if cmd_name == "cloud" and not cmd_args:
            payload = getattr(session, "last_cloud_payload", None)
            if payload:
                preview = (
                    f"Last cloud call — {payload['model']} at {payload['at']}, "
                    f"{payload['message_count']} messages sent.\n\n"
                    f"Final message sent:\n{payload['last_user_message']}"
                )
            else:
                preview = "No cloud calls this session."
            return {
                "agent_name": session.agent_name,
                "response": preview,
                "emotion": None,
                "wellness_flag": False,
            }
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

    # Per-turn classification: drives routing (task tag) + reply shaping (mode)
    turn = classify(
        user_input,
        emotion_label=(emotion_result or {}).get("label"),
        emotion_score=(emotion_result or {}).get("score", 0.0),
    )
    task_tag = route_task_tag(turn)

    # Escalate-on-trouble: decide (trouble-only) whether this turn wants cloud.
    # The private-content gate is deferred until the payload is assembled below.
    _rcfg = _load_router_config()
    _plan = evaluate_escalation(
        user_input, streak=getattr(session, "_correction_streak", 0),
        cfg=_rcfg, key_present=_resolve_key() is not None)
    session._correction_streak = _plan.new_streak
    _wants_cloud = _force_trouble_cloud or (_plan.action == "escalate")

    # Memory search. Emotional turns skip the fact/task injection — the 8B
    # fixates on injected details (task titles surfaced mid-grief in live
    # testing). Character memories stay: Pike's own past informs presence.
    relevant = ""
    if inject_fact_memories(turn):
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
    mode_hint = _MODE_HINTS.get(turn.mode)
    if mode_hint:
        context_parts.append(mode_hint)
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

    # Private-content gate on the ACTUAL outgoing payload. Retrieved memories,
    # uploaded file text, and context injections are all assembled above — so we
    # scan the exact preview of what would be sent (NOT just the bare user turn)
    # before escalating. If it's private, bail to a consent prompt. This runs
    # BEFORE appending the current turn so a consent-bail never persists/dupes it.
    if _wants_cloud and _rcfg.trouble_private_consent:
        _preview = list(session.messages)            # prior turns (incl system[0])
        if context_system_msg:
            _preview = _preview + [context_system_msg]
        _preview = _preview + [{"role": "user", "content": augmented}]
        _cats = payload_private_categories(_preview)
        # A consented re-run proceeds ONLY if the consent still covers every
        # private category in the re-assembled payload — new private content
        # (interleaved turns, fresh memory retrieval) re-prompts instead.
        if _cats and (not _force_trouble_cloud
                      or not _cats <= _consented_categories):
            _reason = ", ".join(sorted(_cats))
            session._pending_escalation = {"message": user_input,
                                           "ts": datetime.now(),
                                           "categories": sorted(_cats)}
            return {
                "agent_name": session.agent_name,
                "response": (f"⚠ I'm struggling with this, and it looks like it involves "
                             f"private info ({_reason}). I can get better help from the cloud, "
                             f"but that sends it to Anthropic. Reply “yes, use cloud” to allow "
                             f"it just this once — otherwise I'll keep trying locally."),
                "emotion": emotion_result, "wellness_flag": False, "bracket_actions": [],
            }

    # Add to history
    session.messages.append({"role": "user", "content": user_input})
    messages_to_send = list(session.messages[:-1])
    if context_system_msg:
        messages_to_send.append(context_system_msg)
    messages_to_send.append({"role": "user", "content": augmented})

    try:
        # Run the (synchronous) router in a thread so we don't block the loop
        reply_content, route_meta = await asyncio.to_thread(
            router_chat_with_meta,
            messages_to_send,
            sensitivity="personal",
            task=task_tag,
            model=CONFIG["model"]["chat"],
            trouble=_wants_cloud,
        )
        reply = session.clean_reply(reply_content, mode=turn.mode)

        # Run through output protocols
        output_result = session.protocol_registry.process_output(reply, proto_context)
        if not output_result.get("suppress"):
            reply = output_result["response"]

        # --- Phase 4B: tool auto-call loop (feed tool results back to Pike) ---
        tooling_proto = session.protocol_registry.get("tooling")
        if tooling_proto is not None and tooling_proto.get_pending_tool_calls():
            def _tool_router(convo, sensitivity, task, model):
                return router_chat_with_meta(convo, sensitivity=sensitivity,
                                             task=task, model=model)

            def _tool_process_output(r):
                return session.protocol_registry.process_output(r, proto_context)

            # Slim synthesis context: the 8B fixates on injected details (facts,
            # session context — same failure documented for emotional turns), so
            # the re-prompt gets ONLY a minimal persona line + the user's question.
            # The tool result is appended by the loop itself.
            slim_convo = [
                {"role": "system", "content": (
                    f"You are {session.agent_name}. Answer the user's question "
                    "using the tool results provided. Be direct and concise.")},
                {"role": "user", "content": user_input},
            ]
            reply, route_meta, _pin_notes = await run_tool_loop(
                username=user_id, tooling=tooling_proto,
                convo=slim_convo, reply=reply, raw_reply=reply_content,
                route_meta=route_meta,
                router=_tool_router, call_tool=tool_service.call_tool,
                process_output=_tool_process_output,
                clean_reply=lambda rc: session.clean_reply(rc, mode=turn.mode),
                # Tool-synthesis rounds carry tool RESULTS — i.e. file contents.
                # Pin them to "private" so those never leave the machine, even
                # with cloud on. (Revisit under the "best of both" build if
                # cloud-eligible tool synthesis is ever wanted.)
                sensitivity="private", task_tag=task_tag,
                model=CONFIG["model"]["chat"])

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

        display_reply = reply
        if route_meta.backend_used == "cloud":
            display_reply = f"{reply}\n\n☁ cloud brain"
            # RAM-only; overwritten each cloud call; never persisted.
            session.last_cloud_payload = {
                "model": route_meta.cloud_model,
                "at": datetime.now().isoformat(timespec="seconds"),
                "message_count": len(messages_to_send),
                "last_user_message": messages_to_send[-1]["content"],
            }

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
            "response": display_reply,
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
