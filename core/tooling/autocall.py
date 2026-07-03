"""
Tool Autocall Loop (Phase 4B) — runs Pike's [TOOL:] calls and feeds results back.

Kept dependency-injected (router/call_tool/process_output are passed in) so the
control flow is unit-testable without a real LLM, session, or subprocess. The
chat pipeline wires in the real implementations.
"""

import asyncio
import logging

logger = logging.getLogger("aegis.tooling.autocall")

MAX_TOOL_ROUNDS = 3


def _format_tool_result(result):
    """Render a tool result (list of text lines, or anything) for Pike's context."""
    if isinstance(result, list):
        return "\n".join(str(r) for r in result) or "(empty)"
    return str(result)


async def run_tool_loop(*, username, tooling, convo, reply, raw_reply, route_meta,
                        router, call_tool, process_output, clean_reply,
                        sensitivity, task_tag, model, max_rounds=MAX_TOOL_ROUNDS):
    """Execute pending [TOOL:] calls, thread results back, and re-prompt.

    Args (all injected for testability):
      tooling: object with get_pending_tool_calls() / get_rejections().
      convo: message list that produced `raw_reply` (the first LLM reply).
      reply: the cleaned first reply (fallback if the loop does nothing/raises).
      raw_reply: the uncleaned first reply (carries the [TOOL:] tag into context).
      route_meta: RouteMeta of the first call (updated as re-prompts route).
      router(convo, sensitivity, task_tag, model) -> (raw_reply, route_meta).
      call_tool(username, tool_id, method, args) -> service result dict.
      process_output(reply) -> registry process_output dict (re-parses [TOOL:]).
      clean_reply(raw) -> cleaned string.

    Returns (final_reply, route_meta, pin_notes).
    """
    pin_notes = []
    rounds = 0
    try:
        while tooling.get_pending_tool_calls() and rounds < max_rounds:
            rounds += 1
            result_msgs = []
            for call in tooling.get_pending_tool_calls():
                res = await asyncio.to_thread(
                    call_tool, username, call["tool_id"], call["method"], call["args"])
                status = res.get("status")
                if status == "ok":
                    result_msgs.append(
                        f"Tool result for {call['tool_id']}.{call['method']}: "
                        f"{_format_tool_result(res.get('result'))}")
                elif status == "needs_pin":
                    pin_notes.append(
                        f"🔒 {res.get('method', call['method'])} on "
                        f"{res.get('tool_id', call['tool_id'])} needs your PIN — "
                        f"reply /tools pin <your vault PIN> to run it.")
                else:
                    result_msgs.append(
                        f"Tool {call['tool_id']}.{call['method']} failed: "
                        f"{res.get('message', 'error')}")
            for rej in tooling.get_rejections():
                result_msgs.append(f"(You tried {rej}, which isn't an available tool.)")
            if not result_msgs:            # only needs_pin this round → nothing to synthesize
                break
            convo = convo + [
                {"role": "assistant", "content": raw_reply},
                {"role": "system", "content": "\n".join(result_msgs)},
            ]
            raw_reply, route_meta = await asyncio.to_thread(
                router, convo, sensitivity, task_tag, model)
            reply = clean_reply(raw_reply)
            out = process_output(reply)
            if not out.get("suppress"):
                reply = out["response"]
    except Exception as e:
        logger.error("Tool autocall loop error: %s", e)
        # fall through — `reply` holds the last good (or pre-loop) answer
    if pin_notes:
        reply = (reply + "\n\n" + "\n".join(pin_notes)).strip()
    return reply, route_meta, pin_notes
