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

# A real send requires an explicit send word, not just an affirmative — the
# classifier alone must not be able to transmit a held draft on a vague "yeah ok".
_SEND_PHRASE = re.compile(r"\b(send|ship it|fire it off)\b", re.IGNORECASE)


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

        action = self._classify(text)
        act = action.get("action", "none")
        if act == "none":
            return result  # fall through to normal chat

        # Actions that operate on a pending draft are no-ops without one.
        if act in ("send", "edit", "discard") and not self._pending:
            return result

        if ea._creds_from_session(self._session) is None:
            return self._intercept(
                result,
                "I can't reach your email yet — connect Google in the Mail panel first.")

        handler = {
            "reply": self._do_reply,
            "send": self._do_send,
            "edit": self._do_edit,
            "discard": self._do_discard,
        }.get(act)
        if handler is None:
            return result  # not wired in Phase 1 -> normal chat

        try:
            response = handler(action, text)
        except Exception as e:
            logger.exception("Email action '%s' failed", act)
            response = f"Something went wrong with that email action: {e}"
        if response is None:
            return result  # handler declined -> normal chat
        return self._intercept(result, response)

    # ---- action handlers ----

    def _do_reply(self, action, text):
        message_id = self._resolve_ref(action)
        if not message_id:
            return "I couldn't tell which email you mean — which one should I reply to?"
        intent = action.get("instruction") or text
        res = ea.draft_reply(self._session, message_id, intent=intent)
        if not res.get("success"):
            return f"I couldn't draft that reply: {res.get('error', 'unknown error')}"
        self._pending = {
            "draft_id": res["draft_id"],
            "kind": "reply",
            "message_id": message_id,
            "to": res.get("to", ""),
            "subject": res.get("subject", ""),
            "intent": intent,
        }
        return (
            f"Here's your reply to {res.get('to', 'them')} —\n"
            f"Subject: {res.get('subject', '')}\n\n"
            f"{res.get('body', '')}\n\n"
            "Send it, tweak it, or discard?"
        )

    def _do_send(self, action, text):
        if not _SEND_PHRASE.search(text or ""):
            return ("Just to confirm — send the draft to "
                    f"{self._pending.get('to', 'them')}? Say \"send it\" to confirm.")
        res = ea.send_draft(self._session, self._pending["draft_id"])
        if not res.get("success"):
            return f"I couldn't send it: {res.get('error', 'unknown error')}"
        to = self._pending.get("to", "them")
        self._pending = None
        return f"Sent to {to}."

    def _do_discard(self, action, text):
        creds = ea._creds_from_session(self._session)
        try:
            gt.gmail_delete_draft(creds, self._pending["draft_id"])
        except Exception:
            logger.exception("Could not delete discarded draft")
        self._pending = None
        return "Discarded that draft."

    def _do_edit(self, action, text):
        p = self._pending
        change = action.get("instruction") or text
        new_intent = f"{p.get('intent', '')} | revision: {change}".strip(" |")
        res = ea.draft_reply(self._session, p["message_id"], intent=new_intent)
        if not res.get("success"):
            return f"I couldn't revise it: {res.get('error', 'unknown error')}"
        creds = ea._creds_from_session(self._session)
        try:
            gt.gmail_delete_draft(creds, p["draft_id"])
        except Exception:
            logger.exception("Could not delete superseded draft")
        self._pending = {
            **p,
            "draft_id": res["draft_id"],
            "to": res.get("to", p.get("to", "")),
            "subject": res.get("subject", p.get("subject", "")),
            "intent": new_intent,
        }
        return (
            f"Updated draft to {res.get('to', 'them')} —\n\n"
            f"{res.get('body', '')}\n\n"
            "Send it, tweak it, or discard?"
        )

    def process_output(self, response, context):
        return {"response": response, "suppress": False, "append": ""}

    def _intercept(self, result, response):
        result["intercept"] = True
        result["response"] = response
        return result

    # ---- classification ----

    _ALLOWED_ACTIONS = ("reply", "new", "forward", "send", "edit", "discard")

    def _build_classifier_prompt(self, text, listing, pending):
        return (
            "You classify a user's email request into ONE action.\n\n"
            "Recent inbox (most recent first):\n"
            f"{listing or '(inbox empty)'}\n\n"
            f"A draft is currently pending: {'yes' if pending else 'no'}\n\n"
            f'User said: "{text}"\n\n'
            "Reply with ONE line, exactly this format:\n"
            "ACTION=<reply|new|forward|send|edit|discard|none> | REF=<inbox number or -> "
            "| TO=<email address or -> | INSTRUCTION=<what to say, or ->\n\n"
            "Rules:\n"
            "- reply: replying to an inbox email. REF = the inbox number. "
            "INSTRUCTION = what the reply should say.\n"
            "- new: a brand-new email. TO = the recipient's email address. "
            "INSTRUCTION = what it should say.\n"
            "- forward: forward an inbox email. REF = the inbox number. "
            "TO = the recipient's email address.\n"
            "- send: send the pending draft. Only if a draft is pending.\n"
            "- edit: change the pending draft. INSTRUCTION = the change. "
            "Only if a draft is pending.\n"
            "- discard: cancel the pending draft. Only if a draft is pending.\n"
            "- none: anything that is not an email action.\n"
            "Output ONLY the one line. No explanation."
        )

    def _parse_classification(self, raw):
        text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
        m = re.search(r"ACTION\s*=\s*([a-zA-Z]+)", text)
        if not m:
            return {"action": "none"}
        action = m.group(1).strip().lower()
        if action not in self._ALLOWED_ACTIONS:
            return {"action": "none"}
        out = {"action": action}
        ref_m = re.search(r"REF\s*=\s*#?(\d+)", text)
        if ref_m:
            out["ref"] = ref_m.group(1)
        to_m = re.search(r"TO\s*=\s*([^|]+)", text)
        if to_m:
            to_val = to_m.group(1).strip()
            if to_val and to_val != "-":
                out["to"] = to_val
        ins_m = re.search(r"INSTRUCTION\s*=\s*(.+)", text)
        if ins_m:
            ins = ins_m.group(1).strip()
            # strip a trailing " | KEY=..." if the model crammed extra fields after
            ins = re.split(r"\s*\|\s*[A-Z]+\s*=", ins)[0].strip()
            if ins and ins != "-":
                out["instruction"] = ins
        return out

    # ---- target resolution ----

    def _recent_inbox(self):
        """Return (listing_text, {index: message_id}) for the last ~15 inbox msgs."""
        creds = ea._creds_from_session(self._session)
        if not creds:
            return "", {}
        try:
            msgs = gt.gmail_list_messages(creds, max_results=15, categories=None)
        except Exception:
            logger.exception("Could not list inbox for classification")
            return "", {}
        lines, id_map = [], {}
        for i, m in enumerate(msgs, 1):
            id_map[i] = m.get("id")
            sender = (m.get("sender") or "?").strip()
            subject = (m.get("subject") or "(no subject)").strip()
            lines.append(f"#{i} · {sender} · {subject}")
        return "\n".join(lines), id_map

    def _resolve_ref(self, action):
        ref = action.get("ref")
        if ref and str(ref).isdigit():
            return self._id_map.get(int(ref))
        return None

    def _classify(self, text):
        if self._pending is None:
            listing, self._id_map = self._recent_inbox()
        else:
            listing, self._id_map = "", {}
        prompt = self._build_classifier_prompt(text, listing, self._pending is not None)
        try:
            raw = ea._llm(
                [{"role": "system",
                  "content": "You are an email-intent classifier. Output ONE line only."},
                 {"role": "user", "content": prompt}],
                sensitivity="private", task="email_classify",
            )
        except Exception:
            logger.exception("Email classify LLM call failed")
            return {"action": "none"}
        return self._parse_classification(raw)
