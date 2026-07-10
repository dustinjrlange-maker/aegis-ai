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

# A real send requires an explicit, STANDALONE send command ("send it",
# "yes, send it now") — never a send word buried inside a longer sentence.
# 2026-07-09 incident: "No I want you to send it from my personal email..."
# contained "send it" and transmitted a held draft. Confirmation is
# fail-closed: anything that isn't a short affirmative send command
# re-confirms instead.
_CONFIRM_SEND = re.compile(
    r"(?:(?:yes|yeah|yep|sure|ok(?:ay)?|please|alright|go ahead(?:\s+and)?)[\s,!.-]*)*"
    r"(?:send(?:\s+(?:it|that|this|the\s+(?:draft|email|message)))?"
    r"|ship\s+it|fire\s+it\s+off)"
    r"(?:[\s,!.]*(?:now|please|off))?[\s,!.]*$",
    re.IGNORECASE,
)

# ...and a send word alongside a question, deliberation, or negation is NOT a
# confirmation. Sending is irreversible: when in doubt, re-confirm.
_NOT_A_CONFIRM = re.compile(
    r"\?|\b(no|nope|wrong|not|stop|cancel|instead|should|shall|do you think|"
    r"not sure|maybe|later|wait|hold off|don't|do not|never ?mind)\b",
    re.IGNORECASE,
)


def _is_send_confirmation(text):
    """True only for a short, explicit, standalone send command."""
    t = (text or "").strip()
    return bool(_CONFIRM_SEND.match(t)) and not _NOT_A_CONFIRM.search(t)


# "from my personal email" — the user explicitly naming the From account.
_FROM_ACCOUNT = re.compile(
    r"\bfrom\s+(?:my|the|our)\s+([\w .&'-]{1,40}?)\s+"
    r"(?:e-?mail|g-?mail|account|address|inbox)\b",
    re.IGNORECASE,
)

# Dictated wording: "subject saying X", "the body of the email to say: Y".
# When these match, the user's text is used VERBATIM — no LLM composition
# (2026-07-09 incident: a dictated body was rewritten with its meaning flipped).
_SUBJECT_DICT = re.compile(
    r"\bsubject\b(?:\s+(?:line|body|field|header))?\s*"
    r"(?:to\s+say|saying|says|should\s+say|say|reads?|[:=])\s*[\"'“]?"
    r"(?P<subj>.+?)\s*[\"'”]?"
    r"(?=\s*(?:$|[,.;]|\band\b|\bthen\b|\bbody\b|\bmessage\b))",
    re.IGNORECASE,
)
_BODY_DICT = re.compile(
    r"\b(?:body|message)\b(?:\s+of\s+the\s+(?:e-?mail|message))?\s*"
    r"(?:to\s+say|saying|says|should\s+say|say|reads?|[:=])\s*:?\s*[\"'“]?"
    r"(?P<body>.+)$",
    re.IGNORECASE | re.DOTALL,
)


def _extract_dictation(text):
    """(subject|None, body|None) when the user dictated exact wording."""
    t = text or ""
    subj = None
    pos = 0
    m = _SUBJECT_DICT.search(t)
    if m:
        subj = m.group("subj").strip().strip("\"'“”").strip()
        pos = m.end()
    b = _BODY_DICT.search(t, pos)
    body = None
    if b:
        body = b.group("body").strip().strip("\"'“”").strip()
    return subj or None, body or None


_EMAIL_ADDR = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


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

        if ea._creds_from_session(self._session,
                                  ea.active_account_id(self._session)) is None:
            return self._intercept(
                result,
                "I can't reach your email yet — connect Google in the Mail panel first.")

        handler = {
            "reply": self._do_reply,
            "new": self._do_new,
            "forward": self._do_forward,
            "mark_read": self._do_mark_read,
            "archive": self._do_archive,
            "send": self._do_send,
            "edit": self._do_edit,
            "discard": self._do_discard,
        }.get(act)
        if handler is None:
            return result  # not wired in Phase 1 -> normal chat

        try:
            response = handler(action, text)
        except gt.RefreshError:
            logger.warning("Email action '%s': Google token expired/revoked", act)
            response = ("Google login expired for this account — reconnect it "
                        "in the Mail panel and try again.")
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
        acct_over, ask = self._explicit_from(text)
        if ask:
            return ask
        if acct_over is not None:
            action = {**action, "account": acct_over["id"]}
        intent = action.get("instruction") or text
        acct, acct_note = self._resolve_account(action)
        acct_id = acct["id"] if acct else None
        res = ea.draft_reply(self._session, message_id, intent=intent, account_id=acct_id)
        if not res.get("success"):
            return f"I couldn't draft that reply: {res.get('error', 'unknown error')}"
        self._pending = {
            "draft_id": res["draft_id"],
            "kind": "reply",
            "message_id": message_id,
            "to": res.get("to", ""),
            "subject": res.get("subject", ""),
            "intent": intent,
            "account_id": acct_id,
        }
        return (
            f"{acct_note}{self._from_line(acct)}"
            f"Here's your reply to {res.get('to', 'them')} —\n"
            f"Subject: {res.get('subject', '')}\n\n"
            f"{res.get('body', '')}\n\n"
            "Send it, tweak it, or discard?"
        )

    def _from_line(self, acct):
        """Preview 'From:' line for a resolved account, or '' when none."""
        if not acct:
            return ""
        label = acct.get("label", acct["id"])
        email = acct.get("email", "")
        if email:
            return f"From: {label} ({email})\n"
        return f"From: {label}\n"

    def _extract_recipient(self, action):
        """Recipient comes only from the classifier's structured TO= field.

        We deliberately do NOT scan the free text: an address in the message
        is usually content ("...john@x.com keeps emailing me"), not the intended
        recipient. If TO= is missing, the handler asks for an address.
        """
        cand = (action.get("to") or "").strip()
        m = _EMAIL_ADDR.search(cand)
        return m.group(0) if m else None

    def _do_new(self, action, text):
        acct_over, ask = self._explicit_from(text)
        if ask:
            return ask
        to = self._ground_recipient(self._extract_recipient(action), text)
        if not to:
            return "Who should I send it to? Give me an email address."
        intent = action.get("instruction") or text
        if acct_over is not None:
            action = {**action, "account": acct_over["id"]}
        acct, acct_note = self._resolve_account(action)
        acct_id = acct["id"] if acct else None
        subject_hint, body_verbatim = _extract_dictation(text)
        extra = {}
        if subject_hint:
            extra["subject_hint"] = subject_hint
        if body_verbatim:
            extra["body_verbatim"] = body_verbatim
        res = ea.draft_new(self._session, to, intent=intent, account_id=acct_id,
                           **extra)
        if not res.get("success"):
            return f"I couldn't draft that email: {res.get('error', 'unknown error')}"
        self._pending = {
            "draft_id": res["draft_id"], "kind": "new", "message_id": None,
            "to": res.get("to", to), "subject": res.get("subject", ""), "intent": intent,
            "account_id": acct_id,
            "subject_hint": subject_hint, "body_verbatim": body_verbatim,
        }
        return (
            f"{acct_note}{self._from_line(acct)}"
            f"Here's your email to {res.get('to', to)} —\n"
            f"Subject: {res.get('subject', '')}\n\n"
            f"{res.get('body', '')}\n\n"
            "Send it, tweak it, or discard?"
        )

    def _do_forward(self, action, text):
        message_id = self._resolve_ref(action)
        if not message_id:
            return "Which email should I forward?"
        acct_over, ask = self._explicit_from(text)
        if ask:
            return ask
        to = self._ground_recipient(self._extract_recipient(action), text)
        if not to:
            return "Who should I forward it to? Give me an email address."
        if acct_over is not None:
            action = {**action, "account": acct_over["id"]}
        acct, acct_note = self._resolve_account(action)
        acct_id = acct["id"] if acct else None
        res = ea.draft_forward(self._session, message_id, to, account_id=acct_id)
        if not res.get("success"):
            return f"I couldn't draft that forward: {res.get('error', 'unknown error')}"
        self._pending = {
            "draft_id": res["draft_id"], "kind": "forward", "message_id": message_id,
            "to": res.get("to", to), "subject": res.get("subject", ""), "intent": text,
            "account_id": acct_id,
        }
        return (
            f"{acct_note}{self._from_line(acct)}"
            f"Here's the forward to {res.get('to', to)} —\n"
            f"Subject: {res.get('subject', '')}\n\n"
            f"{res.get('body', '')}\n\n"
            "Send it, tweak it, or discard?"
        )

    def _do_mark_read(self, action, text):
        message_id = self._resolve_ref(action)
        if not message_id:
            return "Which email should I mark as read?"
        creds = ea._creds_from_session(self._session,
                                       ea.active_account_id(self._session))
        res = gt.gmail_mark_read(creds, message_id)
        if not res.get("ok"):
            return f"I couldn't mark it read: {res.get('error', 'unknown error')}"
        return "Marked it as read."

    def _do_archive(self, action, text):
        message_id = self._resolve_ref(action)
        if not message_id:
            return "Which email should I archive?"
        creds = ea._creds_from_session(self._session,
                                       ea.active_account_id(self._session))
        res = gt.gmail_archive(creds, message_id)
        if not res.get("ok"):
            return f"I couldn't archive it: {res.get('error', 'unknown error')}"
        return "Archived it — it's out of your inbox."

    def _do_send(self, action, text):
        t = (text or "").strip()
        acct_over, ask = self._explicit_from(t)
        if ask:
            return ask
        pending_acct = self._pending.get("account_id") or self._default_account_id()
        if acct_over is not None and acct_over["id"] != pending_acct:
            # The user is correcting the From account, not confirming the send.
            return self._switch_pending_account(acct_over)
        if not _is_send_confirmation(t):
            logger.info("email_ops: not a standalone confirmation, "
                        "re-confirming: %r", t[:120])
            return self._reconfirm()
        res = ea.send_draft(self._session, self._pending["draft_id"],
                            account_id=self._pending.get("account_id"))
        if not res.get("success"):
            return f"I couldn't send it: {res.get('error', 'unknown error')}"
        to = self._pending.get("to", "them")
        logger.info("email_ops: draft sent to %r (account %r)",
                    to, self._pending.get("account_id"))
        self._pending = None
        return f"Sent to {to}."

    def _reconfirm(self):
        """Re-ask before an irreversible send, restating From AND To so a
        wrong account or recipient is visible before transmission."""
        to = self._pending.get("to", "them")
        frm = ""
        accounts = getattr(self._session, "accounts", None)
        if accounts is not None:
            acct = None
            if self._pending.get("account_id"):
                acct = accounts.get(self._pending["account_id"])
            acct = acct or accounts.default()
            if acct:
                label = acct.get("label", acct["id"])
                email = acct.get("email", "")
                frm = f" from {label} ({email})" if email else f" from {label}"
        return (f"Just to confirm — send the draft{frm} to {to}? "
                'Say "send it" to confirm.')

    def _default_account_id(self):
        accounts = getattr(self._session, "accounts", None)
        if accounts is None:
            return None
        default = accounts.default()
        return default["id"] if default else None

    def _switch_pending_account(self, acct):
        """Redraft the held draft under *acct* — the user corrected the From
        account. Never sends."""
        p = self._pending
        old_acct_id = p.get("account_id")
        new_id = acct["id"]
        kind = p.get("kind", "reply")
        logger.info("email_ops: switching pending draft account %r -> %r",
                    old_acct_id, new_id)
        if kind == "new":
            extra = {}
            if p.get("subject_hint"):
                extra["subject_hint"] = p["subject_hint"]
            if p.get("body_verbatim"):
                extra["body_verbatim"] = p["body_verbatim"]
            res = ea.draft_new(self._session, p.get("to", ""),
                               intent=p.get("intent", ""), account_id=new_id,
                               **extra)
        elif kind == "forward":
            res = ea.draft_forward(self._session, p["message_id"],
                                   p.get("to", ""), account_id=new_id)
        else:
            res = ea.draft_reply(self._session, p["message_id"],
                                 intent=p.get("intent", ""), account_id=new_id)
        if not res.get("success"):
            return ("I couldn't redraft from that account: "
                    f"{res.get('error', 'unknown error')}")
        creds = ea._creds_from_session(self._session, old_acct_id)
        try:
            gt.gmail_delete_draft(creds, p["draft_id"])
        except Exception:
            logger.exception("Could not delete superseded draft")
        self._pending = {
            **p,
            "draft_id": res["draft_id"],
            "to": res.get("to", p.get("to", "")),
            "subject": res.get("subject", p.get("subject", "")),
            "account_id": new_id,
        }
        return (
            f"{self._from_line(acct)}"
            f"Redrafted to {self._pending['to']} —\n"
            f"Subject: {self._pending['subject']}\n\n"
            f"{res.get('body', '')}\n\n"
            "Send it, tweak it, or discard?"
        )

    def _known_addresses(self):
        """Email addresses this user demonstrably owns (linked accounts)."""
        accounts = getattr(self._session, "accounts", None)
        if accounts is None:
            return []
        return [a.get("email", "") for a in accounts.list() if a.get("email")]

    def _ground_recipient(self, to, text):
        """Ground a classifier-proposed recipient before any draft exists.

        Trusted only when the user literally typed it, or it matches a known
        address. A near-miss of a known address (the classifier 'normalizing'
        a spoken name — 2026-07-09: 'the switch stitch email' became an
        invented switchstitch@gmail.com belonging to a stranger) is repaired
        to the known address. Anything else -> None: the handler asks instead
        of emailing a stranger."""
        if not to:
            return None
        if to.lower() in (text or "").lower():
            return to
        known = self._known_addresses()
        for k in known:
            if k.lower() == to.lower():
                return k
        loc = to.split("@", 1)[0].lower().replace(".", "")
        for k in known:
            kloc = k.split("@", 1)[0].lower().replace(".", "")
            if loc and (loc in kloc or kloc in loc):
                logger.info("email_ops: repaired recipient %r -> known %r",
                            to, k)
                return k
        logger.info("email_ops: rejected ungrounded recipient %r", to)
        return None

    def _explicit_from(self, text):
        """(account|None, ask|None) for a 'from my X email' phrase in *text*.

        *ask* is a question to return verbatim when the user named an account
        that can't be matched — never silently fall back to the Mail panel's
        active account when the user said which account to use."""
        accounts = getattr(self._session, "accounts", None)
        if accounts is None:
            return None, None
        m = _FROM_ACCOUNT.search(text or "")
        if not m:
            return None, None
        hint = m.group(1).strip()
        acct = accounts.resolve(hint)
        if acct is not None:
            return acct, None
        labels = ", ".join(a.get("label", a["id"]) for a in accounts.list())
        return None, (f'Which account is "{hint}"? I have: {labels}. '
                      "Tell me which to send from.")

    def _do_discard(self, action, text):
        creds = ea._creds_from_session(self._session, self._pending.get("account_id"))
        try:
            gt.gmail_delete_draft(creds, self._pending["draft_id"])
        except Exception:
            logger.exception("Could not delete discarded draft")
        self._pending = None
        return "Discarded that draft."

    def _do_edit(self, action, text):
        p = self._pending
        if p.get("kind") == "forward":
            return ("I can't reword a forwarded message — discard it and forward "
                    "again if you need it different.")
        change = action.get("instruction") or text
        new_intent = f"{p.get('intent', '')} | revision: {change}".strip(" |")
        kind = p.get("kind", "reply")
        acct_over, ask = self._explicit_from(text)
        if ask:
            return ask
        acct_id = acct_over["id"] if acct_over is not None else p.get("account_id")
        if kind == "new":
            # Fresh dictation in the edit wins; otherwise compose from the
            # combined intent (a wording revision supersedes old verbatim text).
            subject_hint, body_verbatim = _extract_dictation(text)
            extra = {}
            if subject_hint:
                extra["subject_hint"] = subject_hint
            if body_verbatim:
                extra["body_verbatim"] = body_verbatim
            res = ea.draft_new(self._session, p.get("to", ""), intent=new_intent,
                               account_id=acct_id, **extra)
        elif kind == "forward":
            res = ea.draft_forward(self._session, p["message_id"], p.get("to", ""),
                                   note=new_intent, account_id=acct_id)
        else:
            res = ea.draft_reply(self._session, p["message_id"], intent=new_intent,
                                 account_id=acct_id)
        if not res.get("success"):
            return f"I couldn't revise it: {res.get('error', 'unknown error')}"
        # Delete the superseded draft with the creds it was CREATED under —
        # acct_id may now point at a different (corrected) account.
        creds = ea._creds_from_session(self._session, p.get("account_id"))
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
            "account_id": acct_id,
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

    _ALLOWED_ACTIONS = ("reply", "new", "forward", "mark_read", "archive",
                        "send", "edit", "discard")

    def _build_classifier_prompt(self, text, listing, pending):
        accounts = getattr(self._session, "accounts", None)
        listed = accounts.list() if accounts else []
        if listed:
            acct_field = "| ACCOUNT=<account id or -> "
            acct_lines = (
                "Linked accounts (choose ACCOUNT by context; - = default):\n"
                + "\n".join(
                    f"- {a['id']} — {a.get('label', '')}"
                    for a in listed)
                + "\n\n"
            )
            acct_rule = (
                "- ACCOUNT: which linked account to act as. Set it only if the "
                "user names or clearly implies one; otherwise leave it -.\n"
            )
        else:
            acct_field = ""
            acct_lines = ""
            acct_rule = ""
        return (
            "You classify a user's email request into ONE action.\n\n"
            "Recent inbox (most recent first):\n"
            f"{listing or '(inbox empty)'}\n\n"
            f"A draft is currently pending: {'yes' if pending else 'no'}\n\n"
            f'User said: "{text}"\n\n'
            "Reply with ONE line, exactly this format:\n"
            "ACTION=<reply|new|forward|mark_read|archive|send|edit|discard|none> | REF=<inbox number or -> "
            f"| TO=<email address or -> {acct_field}| INSTRUCTION=<what to say, or ->\n\n"
            f"{acct_lines}"
            "Rules:\n"
            "- reply: replying to an inbox email. REF = the inbox number. "
            "INSTRUCTION = what the reply should say.\n"
            "- new: a brand-new email. TO = the recipient's email address. "
            "INSTRUCTION = what it should say.\n"
            "- forward: forward an inbox email. REF = the inbox number. "
            "TO = the recipient's email address.\n"
            "- mark_read: mark an inbox email as read. REF = the inbox number.\n"
            "- archive: remove an inbox email from the inbox. REF = the inbox number.\n"
            "- send: send the pending draft. Only if a draft is pending.\n"
            "- edit: change the pending draft. INSTRUCTION = the change. "
            "Only if a draft is pending.\n"
            "- discard: cancel the pending draft. Only if a draft is pending.\n"
            "- none: anything that is not an email action.\n"
            f"{acct_rule}"
            "Output ONLY the one line. No explanation."
        )

    def _parse_classification(self, raw):
        text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
        m = re.search(r"ACTION\s*=\s*([a-zA-Z_]+)", text)
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
        acct_m = re.search(r"ACCOUNT\s*=\s*([^|]+)", text)
        if acct_m:
            acct_val = acct_m.group(1).strip()
            if acct_val and acct_val != "-":
                out["account"] = acct_val
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
        creds = ea._creds_from_session(self._session,
                                       ea.active_account_id(self._session))
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

    def _resolve_account(self, action):
        """Map the classifier's ACCOUNT= hint to an account record.

        Returns (account_or_None, note). *note* is a non-empty string ONLY when
        the user named an account that couldn't be matched and we fell back to
        the default — so the caller can tell them in the preview instead of
        silently composing from the wrong account. Absent/blank hint, or a hint
        that resolves, yields note = ""."""
        accounts = getattr(self._session, "accounts", None)
        if accounts is None:
            return None, ""
        hint = (action.get("account") or "").strip()
        if hint and hint != "-":
            acct = accounts.resolve(hint)
            if acct is not None:
                return acct, ""
            default = accounts.default()
            if default is not None:
                label = default.get("label", default["id"])
                return default, f'(Couldn\'t match account "{hint}" — using {label} instead.)\n'
            return None, ""
        # no explicit ACCOUNT= hint -> the account the Mail panel is viewing
        active_id = ea.active_account_id(self._session)
        acct = accounts.get(active_id) if active_id else accounts.default()
        return (acct, "") if acct else (None, "")

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
        out = self._parse_classification(raw)
        # Audit trail: the classifier drives irreversible actions, so its
        # decision must be reconstructible from logs (2026-07-09 incident
        # had no record of what the model actually returned).
        logger.info("email_ops classify: %r -> %s", text[:120], out)
        return out
