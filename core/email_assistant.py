"""Pike-voiced email assistant.

Wraps the Gmail tools (read/list/draft/send) with personality-aware
summarization and drafting. Hard rule baked in at this layer:

    Drafts are SAVED to the user's Gmail drafts folder. They are NEVER sent
    automatically. Sending requires an explicit `send_draft(draft_id)` call —
    a separate operation the user (not Pike) initiates.

The reply drafter writes in the user's voice (first person, addressed to the
correspondent). The inbox digest writes in Pike's voice (third-person
narration of the inbox state).
"""
from __future__ import annotations

import logging
import re
import time as _time

from core.config import CONFIG
from core.llm import chat as _router_chat
from core.protocols import google_tools as gt

logger = logging.getLogger(__name__)


# Per-user narrative cache: {(user_id, categories_tuple): (timestamp_epoch_s, narrative_str)}
# NOTE: Single-process cache — fragments per-worker if uvicorn ever uses
# multiple workers. Fine for Aegis's single-user local deployment.
_narrative_cache: dict[str, tuple[float, str]] = {}
_NARRATIVE_TTL_S: float = 600.0  # 10 minutes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def active_account_id(session):
    """The account id the interactive Mail panel is currently acting on.

    Returns session.current_mail_account when it's set AND still exists, else
    the default account's id, else None (no accounts / not connected). A stale
    selection (account since deleted) falls back to the default.
    """
    accounts = getattr(session, "accounts", None)
    aid = getattr(session, "current_mail_account", None)
    if accounts is None:
        return None
    if aid and accounts.get(aid) is not None:
        return aid
    default = accounts.default()
    return default["id"] if default else None


def _creds_from_session(session, account_id=None):
    """Pull Google OAuth creds from the session, or None if not authorized."""
    google_proto = session.protocol_registry.get("google")
    if not google_proto:
        return None
    return google_proto._get_creds(account_id=account_id)


def _represent_as_block(session, account_id=None):
    """Prompt block describing how to present the user for this account.

    Empty string when there is no account layer or nothing configured, so
    prompts are unchanged for un-migrated users."""
    accounts = getattr(session, "accounts", None)
    if accounts is None:
        return ""
    acct = accounts.get(account_id) if account_id else accounts.default()
    if acct is None:
        return ""
    rep = acct.get("represent_as") or {}
    if not any(rep.get(k) for k in ("name", "signoff", "tone_hint")):
        return ""
    parts = [f"You are drafting from the user's '{acct.get('label', acct['id'])}' account."]
    if rep.get("name"):
        parts.append(f"Present the user as {rep['name']}.")
    if rep.get("signoff"):
        parts.append(f"Sign off as: {rep['signoff']}.")
    if rep.get("tone_hint"):
        parts.append(f"Tone: {rep['tone_hint']}.")
    return " ".join(parts) + "\n"


def _llm(messages: list[dict], *, sensitivity: str = "private",
         task: str | None = None) -> str:
    """Call the chat model via the central router and return the content.

    Email bodies are private content: tagged sensitivity="private" so they stay
    local by default even after the cloud backend is wired (see router spec).
    """
    return _router_chat(messages, sensitivity=sensitivity, task=task)


def _clean_email_text(raw: str) -> str:
    """Clean an LLM-drafted email while PRESERVING structure.

    Unlike session.clean_reply (a chat-persona filter that collapses newlines,
    caps at 3 sentences, and strips '!'), email bodies need their paragraph
    breaks and full length intact — and a draft's "Subject: ...\\n\\n<body>"
    layout depends on the newline survival. So we only strip qwen3 <think>
    reasoning blocks and any wrapping code fences, then trim.
    """
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    # Drop a leading/trailing ``` fence the model sometimes wraps output in.
    text = re.sub(r"^\s*```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _format_messages_for_llm(messages: list[dict]) -> str:
    """Render an inbox listing as a compact text block for the LLM."""
    if not messages:
        return "(inbox empty)"
    lines = []
    for i, m in enumerate(messages, 1):
        sender = (m.get("sender") or "Unknown").strip()
        subject = (m.get("subject") or "(no subject)").strip()
        snippet = (m.get("snippet") or "").strip()
        if len(snippet) > 200:
            snippet = snippet[:200] + "…"
        lines.append(f"[{i}] From: {sender}\n    Subject: {subject}\n    Preview: {snippet}")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_inbox_digest(session, max_messages: int = 10, fresh: bool = False,
                     categories: tuple = ("primary",), account_id=None) -> dict:
    """Pike-voiced summary of recent inbox.

    Returns: {narrative, unread_count, messages, cached_age_s, error?}
        - cached_age_s: int seconds since the cached narrative was generated.
          0 when the narrative was just regenerated (cache miss or fresh=True).
    """
    creds = _creds_from_session(session, account_id)
    if not creds:
        return {
            "narrative": "Email is not connected. Authorize Google access in settings first.",
            "unread_count": 0,
            "messages": [],
            "error": "not_authorized",
        }

    try:
        unread_count = gt.gmail_unread_count(creds, categories=categories)
        messages = gt.gmail_list_messages(creds, max_results=max_messages,
                                          categories=categories)
    except Exception as e:
        logger.exception("Inbox digest fetch failed")
        return {
            "narrative": f"[Inbox unavailable — {e}]",
            "unread_count": 0,
            "messages": [],
            "error": str(e),
        }

    if not messages:
        return {
            "narrative": "Inbox is clear. Nothing waiting.",
            "unread_count": unread_count,
            "messages": [],
        }

    # Narrative cache (per-user, TTL-gated). Always re-fetch the message list
    # since it's cheap; the LLM call is what we want to avoid.
    user_id = getattr(session, "user_id", "default")
    cache_key = (user_id, account_id, tuple(categories) if categories else ())
    cached = _narrative_cache.get(cache_key)
    if cached and not fresh:
        ts, narrative = cached
        if _time.time() - ts < _NARRATIVE_TTL_S:
            return {
                "narrative": narrative,
                "unread_count": unread_count,
                "messages": messages,
                "cached_age_s": int(_time.time() - ts),
            }

    facts_text = _format_messages_for_llm(messages)
    user_prompt = (
        f"Summarize the user's recent inbox in 3-5 sentences. "
        f"Lead with the most important or time-sensitive items. "
        f"Group obvious noise (newsletters, promos) into one mention. "
        f"Mention unread count ({unread_count}) only if non-zero. "
        f"Do not invent senders or subjects — only use what's listed below. "
        f"Stay in character.\n\n"
        f"INBOX (most recent first):\n{facts_text}\n\n"
        f"Brief now."
    )

    try:
        raw = _llm([
            {"role": "system", "content": session.system_prompt_base},
            {"role": "user", "content": user_prompt},
        ])
        narrative = session.clean_reply(raw).strip()
        # Cache the successful narrative.
        _narrative_cache[cache_key] = (_time.time(), narrative)
    except Exception as e:
        logger.exception("Inbox digest LLM call failed")
        narrative = f"[Briefing failed — {e}]"

    return {
        "narrative": narrative,
        "unread_count": unread_count,
        "messages": messages,
        "cached_age_s": 0,
    }


def draft_reply(session, message_id: str, intent: str | None = None,
                account_id: str | None = None) -> dict:
    """Draft a reply to a specific inbox message.

    Args:
        message_id: Gmail message id to reply to.
        intent: Optional natural-language hint about the reply
            ("polite decline", "accept the meeting", "ask for clarification on X").

    Returns: {success, draft_id, body, subject, to, original?, error?}

    The draft is SAVED to the user's Gmail drafts. NOT sent.
    """
    creds = _creds_from_session(session, account_id)
    if not creds:
        return {"success": False, "error": "Email not authorized"}

    original = gt.gmail_get_message(creds, message_id)
    if not original:
        return {"success": False, "error": f"Could not load message {message_id}"}

    intent_block = f"User's intent for the reply: {intent}\n" if intent else ""
    user_prompt = _represent_as_block(session, account_id) + (
        f"Draft a reply email IN THE USER'S VOICE (first person, addressed to the "
        f"sender). Keep it natural and matching the tone of the original. "
        f"3-8 sentences typical. Sign off appropriately. Do NOT include a "
        f"subject line in your output — only the body text. Do NOT add "
        f"'[draft]' markers, disclaimers, or meta-commentary. Output plain text "
        f"ready to send.\n\n"
        f"{intent_block}"
        f"ORIGINAL EMAIL:\n"
        f"From: {original.get('from', 'Unknown')}\n"
        f"Subject: {original.get('subject', '(no subject)')}\n"
        f"Date: {original.get('date', '')}\n\n"
        f"{original.get('body', '')}\n\n"
        f"--- end of original ---\n\n"
        f"Reply body:"
    )

    try:
        raw = _llm([
            {"role": "system", "content": session.system_prompt_base},
            {"role": "user", "content": user_prompt},
        ])
        body = _clean_email_text(raw)
    except Exception as e:
        logger.exception("Reply drafting LLM call failed")
        return {"success": False, "error": f"LLM failed: {e}"}

    # Reply subject: prepend "Re:" if not already there
    orig_subject = original.get("subject", "")
    subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"

    # Recipient: parse "Name <email>" from the From header — fallback to whole header
    sender = original.get("from", "")
    to = sender  # Gmail accepts "Name <email>" format directly

    result = gt.gmail_create_draft(
        creds, to=to, subject=subject, body=body, reply_to_id=message_id,
    )
    if not result.get("success"):
        return {
            "success": False,
            "error": result.get("error", "Draft creation failed"),
            "body": body,  # return body so user can copy/paste manually
        }

    return {
        "success": True,
        "draft_id": result["draft_id"],
        "body": body,
        "subject": subject,
        "to": to,
        "original": {
            "subject": orig_subject,
            "from": sender,
            "snippet": (original.get("body", "") or "")[:200],
        },
    }


def draft_new(session, to: str, intent: str, subject_hint: str | None = None,
              cc: str | None = None, bcc: str | None = None,
              account_id: str | None = None) -> dict:
    """Draft a new email (not a reply).

    Args:
        to: Recipient email address.
        intent: What the email should say (natural language).
        subject_hint: Optional subject line; if omitted, the LLM proposes one.
        cc: Optional CC recipient(s), comma-separated string. Falsy → omitted.
        bcc: Optional BCC recipient(s), comma-separated string. Falsy → omitted.

    Returns: {success, draft_id, body, subject, to, error?}
    """
    creds = _creds_from_session(session, account_id)
    if not creds:
        return {"success": False, "error": "Email not authorized"}

    if subject_hint:
        subject_instruction = f"Subject line: {subject_hint}"
        subject_block = ""
    else:
        subject_instruction = "Propose a brief, accurate subject line."
        subject_block = (
            "Output format: first line is `Subject: <line>`, blank line, then "
            "the body. Nothing else."
        )

    user_prompt = _represent_as_block(session, account_id) + (
        f"Draft an email IN THE USER'S VOICE to {to}. {subject_instruction} "
        f"Keep tone natural and concise. 3-8 sentences typical. "
        f"Do NOT add '[draft]' markers or meta-commentary.\n"
        f"{subject_block}\n\n"
        f"What the email should convey:\n{intent}\n\n"
        f"Draft:"
    )

    try:
        raw = _llm([
            {"role": "system", "content": session.system_prompt_base},
            {"role": "user", "content": user_prompt},
        ])
        text = _clean_email_text(raw)
    except Exception as e:
        logger.exception("New-draft LLM call failed")
        return {"success": False, "error": f"LLM failed: {e}"}

    # Parse subject if model included one
    subject = subject_hint or "(no subject)"
    body = text
    if not subject_hint and text.lower().startswith("subject:"):
        first_line, _, rest = text.partition("\n")
        subject = first_line[len("subject:"):].strip()
        body = rest.lstrip("\n").strip()

    result = gt.gmail_create_draft(creds, to=to, subject=subject, body=body, cc=cc, bcc=bcc)
    if not result.get("success"):
        return {
            "success": False,
            "error": result.get("error", "Draft creation failed"),
            "body": body,
            "subject": subject,
        }

    return {
        "success": True,
        "draft_id": result["draft_id"],
        "body": body,
        "subject": subject,
        "to": to,
    }


def draft_forward(session, message_id: str, to: str, note: str | None = None,
                  account_id: str | None = None) -> dict:
    """Forward an inbox message to a new recipient. Saved as a draft, NOT sent.

    Returns: {success, draft_id, subject, to, body, error?}

    No LLM voice generation here — the body is a template quoting the original,
    so there is nothing to inject represent-as into; account_id only selects
    the composing account's creds.
    """
    creds = _creds_from_session(session, account_id)
    if not creds:
        return {"success": False, "error": "Email not authorized"}
    original = gt.gmail_get_message(creds, message_id)
    if not original:
        return {"success": False, "error": f"Could not load message {message_id}"}

    orig_subject = original.get("subject", "") or "(no subject)"
    subject = orig_subject if orig_subject.lower().startswith("fwd:") else f"Fwd: {orig_subject}"
    parts = []
    if note:
        parts.append(note.strip())
        parts.append("")
    parts.append("---------- Forwarded message ----------")
    parts.append(f"From: {original.get('from', '')}")
    parts.append(f"Date: {original.get('date', '')}")
    parts.append(f"Subject: {orig_subject}")
    parts.append("")
    parts.append(original.get("body", "") or "")
    body = "\n".join(parts)

    result = gt.gmail_create_draft(creds, to=to, subject=subject, body=body)
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "Draft creation failed"), "body": body}
    return {"success": True, "draft_id": result["draft_id"], "subject": subject, "to": to, "body": body}


def list_drafts(session, max_results: int = 20, account_id=None) -> list[dict]:
    """List recent drafts. Pure passthrough."""
    creds = _creds_from_session(session, account_id)
    if not creds:
        return []
    return gt.gmail_list_drafts(creds, max_results=max_results)


def get_draft(session, draft_id: str, account_id=None) -> dict | None:
    """Get a draft's full contents. Pure passthrough."""
    creds = _creds_from_session(session, account_id)
    if not creds:
        return None
    return gt.gmail_get_draft(creds, draft_id)


def send_draft(session, draft_id: str, account_id: str | None = None) -> dict:
    """Send a previously-saved draft. EXPLICIT confirm step.

    Caller is responsible for collecting user intent before invoking this.
    account_id selects the composing account's creds so a draft made from
    account X is sent from X.
    """
    creds = _creds_from_session(session, account_id)
    if not creds:
        return {"success": False, "error": "Email not authorized"}
    return gt.gmail_send_draft(creds, draft_id)


def discard_draft(session, draft_id: str, account_id=None) -> dict:
    """Discard a draft. Irreversible."""
    creds = _creds_from_session(session, account_id)
    if not creds:
        return {"success": False, "error": "Email not authorized"}
    return gt.gmail_delete_draft(creds, draft_id)


def mark_read(session, message_id: str, account_id=None) -> dict:
    """Mark an inbox message as read.

    Always returns the {ok, error?} shape — the frontend can branch on
    `result.ok` and surface `result.error` when present.
    """
    creds = _creds_from_session(session, account_id)
    if not creds:
        return {"ok": False, "error": "not_authorized"}
    return gt.gmail_mark_read(creds, message_id)


def mark_unread(session, message_id: str, account_id=None) -> dict:
    """Mark an inbox message as unread. Returns {ok, error?}."""
    creds = _creds_from_session(session, account_id)
    if not creds:
        return {"ok": False, "error": "not_authorized"}
    return gt.gmail_mark_unread(creds, message_id)
