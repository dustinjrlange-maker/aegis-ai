# core/accounts/inbox.py
"""Multi-account unread fetch — the real implementation behind the heartbeat
inbox_scan seam (session.fetch_unread_emails, Wave 3 Task-13)."""

import logging

from core.protocols.google_tools import gmail_list_messages

logger = logging.getLogger(__name__)


def fetch_unread_all_accounts(session):
    """Unread emails across all inbox_scan-enabled accounts, or None.

    Shape: [{"from": str, "subject": str, "account": str}, ...].
    None = email not configured (no account layer / no eligible account),
    which makes the heartbeat inbox_scan job self-disable as designed.
    Per-account failures are logged and skipped, never raised.
    """
    accounts = getattr(session, "accounts", None)
    if accounts is None:
        return None
    eligible = accounts.list(feature="inbox_scan")
    if not eligible:
        return None
    out = []
    for acct in eligible:
        creds = accounts.creds_for(acct["id"])
        if creds is None:
            continue
        label = acct.get("label") or acct["id"]
        try:
            msgs = gmail_list_messages(creds, max_results=25,
                                       extra_query="is:unread")
        except Exception:
            logger.exception("unread fetch failed for account %s", acct["id"])
            continue
        for m in msgs:
            out.append({"from": m.get("sender", ""),
                        "subject": m.get("subject", ""),
                        "account": label})
    return out
