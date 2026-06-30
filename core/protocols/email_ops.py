"""Email Ops Protocol — Aegis AI

Phase 1: lets the conversational agent reply to inbox email, hold the draft,
and send it on explicit confirmation (draft-then-confirm). Flow per message:
gate -> classify (one local LLM call) -> resolve target -> act, intercepting
the chat with the result. Phases 2/3 (new/forward, mark-read/archive) extend
the action set later.

All LLM calls route through email_assistant._llm with sensitivity="private"
so the planned hybrid local/cloud router is a one-point swap; email stays
local-only by default.
"""
import logging
import re

from core.protocols.base import Protocol
from core import email_assistant as ea
from core.protocols import google_tools as gt

logger = logging.getLogger(__name__)

# Cheap gate: engage only when the message looks email-ish OR a draft is pending
# (so follow-ups like "send it" route here too).
_EMAIL_CUE = re.compile(
    r"\b(reply|respond|draft|compose|e-?mail|forward|inbox|archive|"
    r"mark\b.*\bread|send)\b",
    re.IGNORECASE,
)


class EmailOpsProtocol(Protocol):
    """Turns chat requests into email actions (Phase 1: reply/send/edit/discard)."""

    def __init__(self):
        super().__init__(
            name="email_ops",
            description="Chat-driven email actions (reply/send/edit/discard)",
            priority=Protocol.PRIORITY_NORMAL + 5,
        )
        self._session = None   # UserSession back-ref, set by session.py
        self._pending = None   # {draft_id, kind, message_id, to, subject, intent} or None
        self._id_map = {}      # inbox listing index -> message_id (set per classify)

    def attach_session(self, session):
        """Give the protocol access to its UserSession (creds + LLM)."""
        self._session = session

    def process_input(self, user_input, context):
        result = {"input": user_input, "context_injection": "",
                  "intercept": False, "response": ""}
        if not self._session:
            return result
        text = (user_input or "").strip()
        if not text:
            return result
        if not (self._pending or _EMAIL_CUE.search(text)):
            return result
        # Classification + dispatch arrive in later tasks.
        return result

    def process_output(self, response, context):
        return {"response": response, "suppress": False, "append": ""}

    def _intercept(self, result, response):
        result["intercept"] = True
        result["response"] = response
        return result
