"""
Google Tools -- Aegis AI
Low-level Google API operations for OAuth, Gmail, and Calendar.
Separated from the protocol to keep API calls isolated and testable.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Tolerate Google returning a superset of the requested scopes during the OAuth
# token exchange. This happens with incremental consent (include_granted_scopes):
# once an account has granted a scope (e.g. an older gmail.send), Google keeps
# returning it even after we stop requesting it, and oauthlib would otherwise
# raise "Scope has changed" and abort the exchange.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

logger = logging.getLogger(__name__)

# Token file name (stored per-user in data/users/<username>/)
TOKEN_FILE = "google_tokens.json"
REGISTRY_FILE = "accounts.json"


def _resolve_token_dir(user_data_dir, account_id=None):
    """Map a user data dir (+ optional account id) to the dir holding TOKEN_FILE.

    With an accounts.json registry present, tokens live per-account under
    accounts/<id>/ (account_id=None selects the default account). Without a
    registry — or when the id is unknown — the legacy layout (TOKEN_FILE
    directly in user_data_dir) applies.
    """
    base = Path(user_data_dir)
    registry = base / REGISTRY_FILE
    if not registry.exists():
        return base
    try:
        accounts = json.loads(
            registry.read_text(encoding="utf-8")).get("accounts", [])
    except (json.JSONDecodeError, IOError):
        return base
    acct = None
    if account_id is not None:
        acct = next((a for a in accounts if a.get("id") == account_id), None)
    else:
        acct = next((a for a in accounts if a.get("is_default")), None)
        if acct is None and accounts:
            acct = accounts[0]
    if acct is None:
        return base
    return base / "accounts" / acct["id"]


# ---------------------------------------------------------------------------
# OAuth Token Management
# ---------------------------------------------------------------------------

def load_credentials(user_data_dir, account_id=None):
    """Load OAuth credentials from the user's token file.

    Auto-refreshes expired tokens and saves refreshed tokens back.
    Returns google.oauth2.credentials.Credentials or None.
    """
    try:
        from google.oauth2.credentials import Credentials
    except ImportError:
        logger.debug("google-auth not installed")
        return None

    token_dir = _resolve_token_dir(user_data_dir, account_id)
    token_path = token_dir / TOKEN_FILE
    if not token_path.exists():
        return None

    try:
        token_data = json.loads(token_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Could not load Google tokens: %s", e)
        return None

    try:
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes"),
        )
    except Exception as e:
        logger.warning("Could not create credentials object: %s", e)
        return None

    # Auto-refresh if expired
    if creds.expired and creds.refresh_token:
        try:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            save_credentials(user_data_dir, creds, account_id=account_id)
            logger.debug("Refreshed Google OAuth tokens")
        except Exception as e:
            logger.warning("Could not refresh Google tokens: %s", e)
            return None

    if not creds.valid:
        return None

    return creds


def save_credentials(user_data_dir, credentials, account_id=None):
    """Persist OAuth credentials to the user's token file."""
    token_path = _resolve_token_dir(user_data_dir, account_id) / TOKEN_FILE
    token_path.parent.mkdir(parents=True, exist_ok=True)

    token_data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes) if credentials.scopes else [],
    }

    token_path.write_text(
        json.dumps(token_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def revoke_credentials(user_data_dir, account_id=None):
    """Revoke the user's Google tokens and delete the token file."""
    token_dir = _resolve_token_dir(user_data_dir, account_id)
    token_path = token_dir / TOKEN_FILE
    creds = load_credentials(user_data_dir, account_id)

    if creds and creds.token:
        try:
            import requests
            requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": creds.token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
            logger.info("Revoked Google OAuth token")
        except Exception as e:
            logger.warning("Could not revoke Google token: %s", e)

    if token_path.exists():
        token_path.unlink()
        logger.info("Deleted Google token file")


def build_auth_url(redirect_uri, state=None, prompt="consent"):
    """Generate an OAuth2 consent URL for Google sign-in.

    Returns the authorization URL string, or None on failure.
    """
    try:
        from google_auth_oauthlib.flow import Flow
        from integrations.google_config import SCOPES, get_client_config
    except ImportError:
        logger.warning("google-auth-oauthlib not installed")
        return None

    client_cfg = get_client_config()
    if not client_cfg["client_id"]:
        return None

    client_config = {
        "web": {
            "client_id": client_cfg["client_id"],
            "client_secret": client_cfg["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }

    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = redirect_uri

    kwargs = {
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": prompt,
    }
    if state:
        kwargs["state"] = state

    auth_url, _ = flow.authorization_url(**kwargs)
    return auth_url


def exchange_code(code, redirect_uri):
    """Exchange an authorization code for OAuth tokens.

    Returns google.oauth2.credentials.Credentials or None.
    """
    try:
        from google_auth_oauthlib.flow import Flow
        from integrations.google_config import SCOPES, get_client_config
    except ImportError:
        return None

    client_cfg = get_client_config()
    if not client_cfg["client_id"]:
        return None

    client_config = {
        "web": {
            "client_id": client_cfg["client_id"],
            "client_secret": client_cfg["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }

    try:
        flow = Flow.from_client_config(client_config, scopes=SCOPES)
        flow.redirect_uri = redirect_uri
        flow.fetch_token(code=code)
        return flow.credentials
    except Exception as e:
        logger.warning("Could not exchange Google auth code: %s", e)
        return None


# ---------------------------------------------------------------------------
# Gmail Functions
# ---------------------------------------------------------------------------

def _get_gmail_service(creds):
    """Build a Gmail API service object."""
    try:
        from googleapiclient.discovery import build
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        logger.warning("Could not build Gmail service: %s", e)
        return None


def get_account_email(creds):
    """Return the connected Google account's email via Gmail getProfile.

    Covered by the existing gmail.modify scope (no extra scope needed). Returns
    "" on any failure (logged) so account linking is never blocked by it.
    """
    service = _get_gmail_service(creds)
    if not service:
        return ""
    try:
        return service.users().getProfile(userId="me").execute().get("emailAddress", "")
    except Exception as e:
        logger.warning("Could not fetch account email: %s", e)
        return ""


def gmail_unread_count(creds, categories=("primary",)):
    """Get the number of unread emails in the inbox (Primary tab by default)."""
    service = _get_gmail_service(creds)
    if not service:
        return 0

    try:
        results = service.users().messages().list(
            userId="me",
            q="is:unread " + _inbox_query(categories),
            maxResults=1,
        ).execute()
        return results.get("resultSizeEstimate", 0)
    except Exception as e:
        logger.warning("Could not get unread count: %s", e)
        return 0


def gmail_mark_read(creds, message_id):
    """Mark an inbox message as read (removes the UNREAD label).

    Returns {ok: True} on success, {ok: False, error: ...} on failure.
    """
    service = _get_gmail_service(creds)
    if not service:
        return {"ok": False, "error": "Gmail service unavailable"}
    try:
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        return {"ok": True}
    except Exception as e:
        logger.warning("Could not mark message read: %s", e)
        return {"ok": False, "error": str(e)}


def gmail_archive(creds, message_id):
    """Archive an inbox message (removes the INBOX label).

    Returns {ok: True} on success, {ok: False, error: ...} on failure.
    """
    service = _get_gmail_service(creds)
    if not service:
        return {"ok": False, "error": "Gmail service unavailable"}
    try:
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["INBOX"]},
        ).execute()
        return {"ok": True}
    except Exception as e:
        logger.warning("Could not archive message: %s", e)
        return {"ok": False, "error": str(e)}


def _inbox_query(categories=("primary",)):
    """Build a Gmail search query for the inbox, scoped to tab categories.

    Gmail's tabbed inbox maps to search categories: primary, social,
    promotions, updates, forums. Passing categories=("primary",) yields the
    Primary tab only (the user's default view) and hides promo/social noise.
    Pass None/empty to disable filtering (all inbox mail).
    """
    if not categories:
        return "in:inbox"
    cats = " OR ".join("category:%s" % c for c in categories)
    return "in:inbox (%s)" % cats


def gmail_list_messages(creds, max_results=10, categories=("primary",),
                        extra_query=None):
    """List recent inbox messages.

    categories: Gmail tab categories to include (default Primary only).
    extra_query: appended to the Gmail search query (e.g. "is:unread").
    Returns list of {id, subject, sender, date, snippet}.
    """
    service = _get_gmail_service(creds)
    if not service:
        return []

    try:
        q = _inbox_query(categories)
        if extra_query:
            q = f"{q} {extra_query}"
        results = service.users().messages().list(
            userId="me",
            q=q,
            maxResults=max_results,
        ).execute()

        messages = []
        for msg_stub in results.get("messages", []):
            msg = service.users().messages().get(
                userId="me",
                id=msg_stub["id"],
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            messages.append({
                "id": msg["id"],
                "subject": headers.get("Subject", "(no subject)"),
                "sender": headers.get("From", "Unknown"),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
            })

        return messages
    except Exception as e:
        logger.warning("Could not list Gmail messages: %s", e)
        return []


def gmail_get_message(creds, message_id):
    """Get a full email message by ID.

    Returns {subject, from, to, date, body} or None.
    """
    service = _get_gmail_service(creds)
    if not service:
        return None

    try:
        msg = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full",
        ).execute()

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        # Extract body text
        body = _extract_body(msg.get("payload", {}))

        return {
            "subject": headers.get("Subject", "(no subject)"),
            "from": headers.get("From", "Unknown"),
            "to": headers.get("To", ""),
            "date": headers.get("Date", ""),
            "body": body,
        }
    except Exception as e:
        logger.warning("Could not get Gmail message %s: %s", message_id, e)
        return None


def _extract_body(payload):
    """Extract a best-effort text body from a Gmail payload.

    Prefers text/plain; falls back to text/html (tags stripped). Recurses
    through nested multipart/* so HTML-only mail (often wrapped in
    multipart/related → multipart/alternative) still yields text instead of
    a blank body. Script/style blocks are dropped before tag-stripping so
    CSS/JS never leaks into the rendered text.
    """
    import base64
    import re

    def _decode(part):
        data = part.get("body", {}).get("data")
        if not data:
            return ""
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    def _walk(part):
        """Return (plain_text, html_text) found anywhere in this subtree."""
        mime = part.get("mimeType", "")
        plain = _decode(part) if mime == "text/plain" else ""
        html = _decode(part) if mime == "text/html" else ""
        for sub in part.get("parts", []) or []:
            sub_plain, sub_html = _walk(sub)
            plain = plain or sub_plain
            html = html or sub_html
        return plain, html

    plain, html = _walk(payload)
    if plain.strip():
        return plain
    if html.strip():
        import html as _htmlmod
        text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html,
                      flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = _htmlmod.unescape(text)          # &nbsp; &amp; &#39; -> real chars
        text = text.replace("‌", "")        # drop zero-width non-joiners
        text = text.replace("\xa0", " ")         # nbsp -> normal space
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text

    return "(could not extract message body)"


def gmail_send(creds, to, subject, body, reply_to_id=None):
    """Send an email via Gmail.

    Returns {success, message_id} or {success: False, error: ...}.
    """
    service = _get_gmail_service(creds)
    if not service:
        return {"success": False, "error": "Gmail service unavailable"}

    try:
        import base64
        from email.mime.text import MIMEText

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject

        if reply_to_id:
            # Get the original message to extract Message-ID and thread
            original = service.users().messages().get(
                userId="me", id=reply_to_id, format="metadata",
                metadataHeaders=["Message-ID"],
            ).execute()
            orig_headers = {h["name"]: h["value"] for h in original.get("payload", {}).get("headers", [])}
            if "Message-ID" in orig_headers:
                message["In-Reply-To"] = orig_headers["Message-ID"]
                message["References"] = orig_headers["Message-ID"]

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        send_body = {"raw": raw}
        if reply_to_id:
            original = service.users().messages().get(userId="me", id=reply_to_id, format="minimal").execute()
            send_body["threadId"] = original.get("threadId")

        result = service.users().messages().send(userId="me", body=send_body).execute()
        return {"success": True, "message_id": result.get("id", "")}
    except Exception as e:
        logger.warning("Could not send Gmail message: %s", e)
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Gmail Drafts
# ---------------------------------------------------------------------------


def _build_mime_message(to, subject, body, reply_to_id=None, service=None, cc=None, bcc=None):
    """Build a base64-encoded MIME message and (optional) thread id for a reply."""
    import base64
    from email.mime.text import MIMEText

    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    if cc:
        message["Cc"] = cc
    if bcc:
        message["Bcc"] = bcc

    thread_id = None
    if reply_to_id and service:
        try:
            original = service.users().messages().get(
                userId="me", id=reply_to_id, format="metadata",
                metadataHeaders=["Message-ID"],
            ).execute()
            orig_headers = {h["name"]: h["value"] for h in original.get("payload", {}).get("headers", [])}
            if "Message-ID" in orig_headers:
                message["In-Reply-To"] = orig_headers["Message-ID"]
                message["References"] = orig_headers["Message-ID"]
            thread_id = original.get("threadId")
        except Exception as e:
            logger.warning("Could not fetch original for reply context: %s", e)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    return raw, thread_id


def gmail_create_draft(creds, to, subject, body, reply_to_id=None, cc=None, bcc=None):
    """Create a draft email (saved to user's Gmail drafts, NOT sent).

    Returns {success, draft_id, message_id} or {success: False, error: ...}.
    """
    service = _get_gmail_service(creds)
    if not service:
        return {"success": False, "error": "Gmail service unavailable"}

    try:
        raw, thread_id = _build_mime_message(to, subject, body, reply_to_id, service, cc=cc, bcc=bcc)
        draft_body = {"message": {"raw": raw}}
        if thread_id:
            draft_body["message"]["threadId"] = thread_id

        result = service.users().drafts().create(userId="me", body=draft_body).execute()
        return {
            "success": True,
            "draft_id": result.get("id", ""),
            "message_id": result.get("message", {}).get("id", ""),
        }
    except Exception as e:
        logger.warning("Could not create Gmail draft: %s", e)
        return {"success": False, "error": str(e)}


def gmail_list_drafts(creds, max_results=20):
    """List recent drafts.

    Returns list of {draft_id, message_id, subject, to, snippet, updated}.
    """
    service = _get_gmail_service(creds)
    if not service:
        return []

    try:
        results = service.users().drafts().list(
            userId="me",
            maxResults=max_results,
        ).execute()

        drafts = []
        for stub in results.get("drafts", []):
            draft = service.users().drafts().get(
                userId="me",
                id=stub["id"],
                format="metadata",
            ).execute()
            msg = draft.get("message", {})
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            drafts.append({
                "draft_id": draft.get("id", ""),
                "message_id": msg.get("id", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "to": headers.get("To", ""),
                "snippet": msg.get("snippet", ""),
                "updated": headers.get("Date", ""),
            })

        return drafts
    except Exception as e:
        logger.warning("Could not list Gmail drafts: %s", e)
        return []


def gmail_get_draft(creds, draft_id):
    """Get full draft contents.

    Returns {draft_id, message_id, subject, to, body, thread_id} or None.
    """
    service = _get_gmail_service(creds)
    if not service:
        return None

    try:
        draft = service.users().drafts().get(
            userId="me", id=draft_id, format="full",
        ).execute()
        msg = draft.get("message", {})
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        body = _extract_body(msg.get("payload", {}))
        return {
            "draft_id": draft.get("id", ""),
            "message_id": msg.get("id", ""),
            "subject": headers.get("Subject", "(no subject)"),
            "to": headers.get("To", ""),
            "body": body,
            "thread_id": msg.get("threadId", ""),
        }
    except Exception as e:
        logger.warning("Could not get Gmail draft %s: %s", draft_id, e)
        return None


def gmail_send_draft(creds, draft_id):
    """Send a previously-saved draft. EXPLICIT user-confirmed sends only.

    Returns {success, message_id} or {success: False, error: ...}.
    """
    service = _get_gmail_service(creds)
    if not service:
        return {"success": False, "error": "Gmail service unavailable"}

    try:
        result = service.users().drafts().send(
            userId="me", body={"id": draft_id},
        ).execute()
        return {"success": True, "message_id": result.get("id", "")}
    except Exception as e:
        logger.warning("Could not send Gmail draft %s: %s", draft_id, e)
        return {"success": False, "error": str(e)}


def gmail_delete_draft(creds, draft_id):
    """Discard a draft. Irreversible.

    Returns {success} or {success: False, error: ...}.
    """
    service = _get_gmail_service(creds)
    if not service:
        return {"success": False, "error": "Gmail service unavailable"}

    try:
        service.users().drafts().delete(userId="me", id=draft_id).execute()
        return {"success": True}
    except Exception as e:
        logger.warning("Could not delete Gmail draft %s: %s", draft_id, e)
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Calendar Functions
# ---------------------------------------------------------------------------

def _get_calendar_service(creds):
    """Build a Calendar API service object."""
    try:
        from googleapiclient.discovery import build
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        logger.warning("Could not build Calendar service: %s", e)
        return None


def _format_event(event):
    """Format a calendar event into a simple dict."""
    start = event.get("start", {})
    end = event.get("end", {})
    return {
        "google_id": event.get("id", ""),
        "summary": event.get("summary", "(no title)"),
        "start": start.get("dateTime", start.get("date", "")),
        "end": end.get("dateTime", end.get("date", "")),
        "location": event.get("location", ""),
    }


def calendar_today(creds):
    """Get today's calendar events.

    Returns list of {summary, start, end, location}.
    """
    service = _get_calendar_service(creds)
    if not service:
        return []

    try:
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)

        result = service.events().list(
            calendarId="primary",
            timeMin=start_of_day.isoformat(),
            timeMax=end_of_day.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute()

        return [_format_event(e) for e in result.get("items", [])]
    except Exception as e:
        logger.warning("Could not get today's calendar events: %s", e)
        return []


def calendar_upcoming(creds, days=7):
    """Get upcoming calendar events for the next N days.

    Returns list of {summary, start, end, location}.
    """
    service = _get_calendar_service(creds)
    if not service:
        return []

    try:
        now = datetime.now(timezone.utc)
        end_date = now + timedelta(days=days)

        result = service.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=end_date.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()

        return [_format_event(e) for e in result.get("items", [])]
    except Exception as e:
        logger.warning("Could not get upcoming calendar events: %s", e)
        return []


def calendar_next_event(creds):
    """Get the next upcoming calendar event.

    Returns a single event dict or None.
    """
    service = _get_calendar_service(creds)
    if not service:
        return None

    try:
        now = datetime.now(timezone.utc)

        result = service.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=1,
        ).execute()

        items = result.get("items", [])
        if items:
            return _format_event(items[0])
        return None
    except Exception as e:
        logger.warning("Could not get next calendar event: %s", e)
        return None


def _rfc3339_local(dt_str):
    """Attach the machine's local UTC offset to a naive ``YYYY-MM-DDTHH:MM:SS``
    string, yielding an RFC3339 timestamp Google Calendar accepts (e.g.
    ``2026-07-07T18:00:00-07:00``).

    The Google Calendar API rejects a ``dateTime`` that carries neither an
    offset nor a ``timeZone`` field ("Missing time zone definition"). Aegis
    runs on the user's own machine, so the local timezone is the user's
    intended timezone. Returns the input unchanged if it is not a parseable
    naive datetime (e.g. an all-day date, or a value that already has an
    offset).
    """
    from datetime import datetime
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return dt_str
    return dt.astimezone().isoformat()


def calendar_create(creds, summary, start, end, description=""):
    """Create a new calendar event.

    Args:
        summary: Event title
        start: ISO datetime string or date string
        end: ISO datetime string or date string
        description: Optional event description

    Returns {success, event_id, link} or {success: False, error: ...}.
    """
    service = _get_calendar_service(creds)
    if not service:
        return {"success": False, "error": "Calendar service unavailable"}

    try:
        # Determine if this is a date or datetime
        is_date = len(start) <= 10  # "2026-02-16" vs "2026-02-16T10:00:00"

        event_body = {
            "summary": summary,
            "description": description,
        }

        if is_date:
            event_body["start"] = {"date": start}
            event_body["end"] = {"date": end}
        else:
            event_body["start"] = {"dateTime": _rfc3339_local(start)}
            event_body["end"] = {"dateTime": _rfc3339_local(end)}

        result = service.events().insert(
            calendarId="primary",
            body=event_body,
        ).execute()

        return {
            "success": True,
            "event_id": result.get("id", ""),
            "link": result.get("htmlLink", ""),
        }
    except Exception as e:
        logger.warning("Could not create calendar event: %s", e)
        return {"success": False, "error": str(e)}


def create_event_or_local(creds, event_manager, title, date,
                          time_start=None, time_end=None, description=""):
    """Create an event on Google Calendar when connected, else fall back to
    the local event store.

    Centralizes the "chat-created events belong on Google Calendar" rule so
    every chat path (the [ADD_EVENT] bracket handler and the NLP event
    detector) behaves identically. Google-written events sync back into the
    Aegis calendar view, so no local copy is kept when the write succeeds.

    Returns ``{source, success, message, link}`` where ``source`` is
    ``"google"`` or ``"local"``.
    """
    if creds:
        if time_start:
            start = f"{date}T{time_start}:00"
            if time_end:
                end = f"{date}T{time_end}:00"
            else:
                from datetime import datetime as _dt, timedelta as _td
                try:
                    end = (_dt.strptime(start, "%Y-%m-%dT%H:%M:%S")
                           + _td(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    end = start
        else:
            start = date
            end = date
        result = calendar_create(creds, title, start, end, description=description)
        if result.get("success"):
            return {
                "source": "google",
                "success": True,
                "message": f"'{title}' added to your Google Calendar",
                "link": result.get("link", ""),
            }
        logger.warning(
            "Google Calendar create failed (%s); falling back to local store",
            result.get("error"),
        )

    event = event_manager.add_event(
        title=title, date=date, time_start=time_start,
        time_end=time_end, description=description,
    )
    return {
        "source": "local",
        "success": True,
        "message": f"'{event['title']}' saved to the local calendar",
        "link": "",
        "event": event,
    }


def calendar_update(creds, event_id, **kwargs):
    """Update an existing Google Calendar event.

    Accepted kwargs: summary, description, start, end (ISO strings).
    Returns {success, event_id} or {success: False, error: ...}.
    """
    service = _get_calendar_service(creds)
    if not service:
        return {"success": False, "error": "Calendar service unavailable"}

    try:
        # Fetch existing event first
        existing = service.events().get(calendarId="primary", eventId=event_id).execute()

        if "summary" in kwargs:
            existing["summary"] = kwargs["summary"]
        if "description" in kwargs:
            existing["description"] = kwargs["description"]
        if "start" in kwargs:
            start_val = kwargs["start"]
            is_date = len(start_val) <= 10
            existing["start"] = {"date": start_val} if is_date else {"dateTime": _rfc3339_local(start_val)}
        if "end" in kwargs:
            end_val = kwargs["end"]
            is_date = len(end_val) <= 10
            existing["end"] = {"date": end_val} if is_date else {"dateTime": _rfc3339_local(end_val)}

        result = service.events().update(
            calendarId="primary",
            eventId=event_id,
            body=existing,
        ).execute()

        return {
            "success": True,
            "event_id": result.get("id", ""),
        }
    except Exception as e:
        logger.warning("Could not update calendar event %s: %s", event_id, e)
        return {"success": False, "error": str(e)}


def calendar_delete(creds, event_id):
    """Delete a Google Calendar event.

    Returns {success: True} or {success: False, error: ...}.
    """
    service = _get_calendar_service(creds)
    if not service:
        return {"success": False, "error": "Calendar service unavailable"}

    try:
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return {"success": True}
    except Exception as e:
        logger.warning("Could not delete calendar event %s: %s", event_id, e)
        return {"success": False, "error": str(e)}
