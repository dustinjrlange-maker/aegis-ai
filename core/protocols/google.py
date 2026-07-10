"""
Google Protocol -- Aegis AI
Integrates Gmail and Google Calendar via OAuth2.

Provides context injection (unread count, next event) and slash commands
for inbox browsing, message reading, and calendar management.
When Google is not connected, the protocol does nothing gracefully.
"""

import time
import logging
from datetime import datetime, timedelta

from core.protocols.base import Protocol
from core.config import CONFIG

try:
    from google.auth.exceptions import RefreshError
except ImportError:  # pragma: no cover
    class RefreshError(Exception):  # type: ignore[no-redef]
        pass

logger = logging.getLogger(__name__)


class GoogleProtocol(Protocol):
    """Gmail and Google Calendar integration."""

    def __init__(self, data_dir=None):
        google_cfg = CONFIG.get("google", {})
        super().__init__(
            name="google",
            description="Gmail and Google Calendar integration",
            priority=Protocol.PRIORITY_NORMAL - 3,  # 47
        )
        self._data_dir = data_dir
        self._cache_ttl = google_cfg.get("cache_ttl_seconds", 300)
        self._inject_calendar = google_cfg.get("calendar_injection", True)
        self._inject_email = google_cfg.get("email_injection", True)

        # Cache
        self._cached_unread = 0
        self._cached_next_event = None
        self._cache_time = 0.0

        # Check if Google integration is available
        self._available = False
        try:
            from integrations.google_config import is_enabled
            self._available = is_enabled()
        except ImportError:
            pass

        if not google_cfg.get("enabled", True):
            self.disable()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_creds(self, account_id=None):
        """Load credentials for the current user (optionally a specific
        linked account). Returns Credentials or None."""
        if not self._data_dir or not self._available:
            return None
        try:
            from core.protocols.google_tools import load_credentials
            return load_credentials(self._data_dir, account_id=account_id)
        except Exception as e:
            logger.debug("Could not load Google credentials: %s", e)
            return None

    def _refresh_cache(self):
        """Refresh cached unread count and next event if stale."""
        now = time.time()
        if now - self._cache_time < self._cache_ttl:
            return

        creds = self._get_creds()
        if not creds:
            return

        try:
            from core.protocols.google_tools import gmail_unread_count, calendar_next_event
            if self._inject_email:
                self._cached_unread = gmail_unread_count(creds)
            if self._inject_calendar:
                self._cached_next_event = calendar_next_event(creds)
            self._cache_time = now
        except RefreshError as e:
            # Dead token at use time: flag the account (reconnect CTA) and
            # advance the cache window so we don't hammer the dead token on
            # every message for the next 5 minutes.
            logger.warning("Google cache refresh: token expired/revoked: %s", e)
            self._cache_time = now
            from core.protocols.google_tools import _mark_account_error
            _mark_account_error(self._data_dir, None)
        except Exception as e:
            logger.warning("Google cache refresh failed: %s", e)

    # ------------------------------------------------------------------
    # Protocol interface
    # ------------------------------------------------------------------

    def process_input(self, user_input, context):
        result = {
            "input": user_input,
            "context_injection": "",
            "intercept": False,
            "response": "",
        }

        if not self._enabled or not self._available:
            return result

        creds = self._get_creds()
        if not creds:
            return result

        self._refresh_cache()

        # Build minimal context injection (2-3 lines max)
        parts = []
        if self._inject_email and self._cached_unread > 0:
            parts.append(f"{self._cached_unread} unread emails")
        if self._inject_calendar and self._cached_next_event:
            evt = self._cached_next_event
            start = evt.get("start", "")
            # Try to format the time nicely
            time_str = _format_time_short(start)
            parts.append(f'Next event: "{evt["summary"]}" at {time_str}')

        if parts:
            injection = "[Google: " + ". ".join(parts) + ". Do NOT mention unless asked.]"
            result["context_injection"] = injection

        # Inject upcoming Google Calendar event within 30 minutes
        if self._inject_calendar and self._cached_next_event:
            evt = self._cached_next_event
            start = evt.get("start", "")
            if "T" in start:
                try:
                    event_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    now = datetime.now(event_dt.tzinfo) if event_dt.tzinfo else datetime.now()
                    diff_min = (event_dt - now).total_seconds() / 60
                    if 0 < diff_min <= 30:
                        time_str = _format_time_short(start)
                        line = f"[Event in ~{int(diff_min)} min: '{evt['summary']}' at {time_str}]"
                        if result["context_injection"]:
                            result["context_injection"] += "\n" + line
                        else:
                            result["context_injection"] = line
                except (ValueError, TypeError):
                    pass

        return result

    def process_output(self, response, context):
        return {"response": response, "suppress": False, "append": ""}

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    def get_commands(self):
        return [
            {"command": "google", "description": "Google account status & help", "handler": "cmd_google"},
            {"command": "gmail", "description": "View Gmail inbox", "handler": "cmd_gmail"},
            {"command": "calendar", "description": "View/manage Google Calendar", "handler": "cmd_calendar"},
        ]

    def cmd_google(self, args=""):
        """Handle /google [disconnect]."""
        args = args.strip().lower()

        if args == "disconnect":
            creds = self._get_creds()
            if not creds:
                return "  Google account is not connected."
            try:
                from core.protocols.google_tools import revoke_credentials
                revoke_credentials(self._data_dir)
                self._cache_time = 0.0
                self._cached_unread = 0
                self._cached_next_event = None
                return "  Google account disconnected. Tokens revoked."
            except Exception as e:
                return f"  Error disconnecting: {e}"

        # Status display
        if not self._available:
            return (
                "\n  GOOGLE INTEGRATION"
                "\n  =================="
                "\n  Status: NOT CONFIGURED"
                "\n"
                "\n  To enable Google integration:"
                "\n  1. Create a Google Cloud project at console.cloud.google.com"
                "\n  2. Enable Gmail API and Google Calendar API"
                "\n  3. Create OAuth2 credentials (Web application type)"
                "\n  4. Add redirect URI: http://localhost:8484/api/google/callback"
                "\n  5. Place client_id and client_secret in data/google_client.json"
                "\n  6. Set \"enabled\": true in that file"
                "\n"
            )

        creds = self._get_creds()
        if not creds:
            return (
                "\n  GOOGLE INTEGRATION"
                "\n  =================="
                "\n  Status: NOT CONNECTED"
                "\n  Integration is configured but your Google account is not linked."
                "\n  Connect at Settings > Google or visit /api/google/auth"
                "\n"
            )

        return (
            "\n  GOOGLE INTEGRATION"
            "\n  =================="
            "\n  Status: CONNECTED"
            "\n  Commands: /gmail, /calendar"
            "\n  Disconnect: /google disconnect"
            "\n"
        )

    def cmd_gmail(self, args=""):
        """Handle /gmail [inbox|read <id>]."""
        creds = self._get_creds()
        if not creds:
            return "  Google account not connected. Connect at Settings > Google or visit /api/google/auth"

        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0].lower() if parts else "inbox"

        if subcmd == "read" and len(parts) > 1:
            return self._gmail_read(creds, parts[1].strip())

        return self._gmail_inbox(creds)

    def _gmail_inbox(self, creds):
        """Show inbox summary."""
        try:
            from core.protocols.google_tools import gmail_unread_count, gmail_list_messages
        except ImportError:
            return "  Google API libraries not installed."

        unread = gmail_unread_count(creds)
        messages = gmail_list_messages(creds, max_results=10)

        lines = [
            "",
            f"  GMAIL INBOX ({unread} unread)",
            "  ============================",
            "",
        ]

        if not messages:
            lines.append("  No messages found.")
        else:
            for msg in messages:
                sender = msg["sender"]
                # Truncate long sender names
                if len(sender) > 40:
                    sender = sender[:37] + "..."
                subject = msg["subject"]
                if len(subject) > 50:
                    subject = subject[:47] + "..."
                lines.append(f"  [{msg['id'][:8]}] {sender}")
                lines.append(f"           {subject}")
                lines.append("")

        lines.append("  Use /gmail read <id> to read a message")
        lines.append("")
        return "\n".join(lines)

    def _gmail_read(self, creds, message_id):
        """Read a full email message."""
        try:
            from core.protocols.google_tools import gmail_list_messages, gmail_get_message
        except ImportError:
            return "  Google API libraries not installed."

        # Allow partial ID matching
        if len(message_id) < 16:
            messages = gmail_list_messages(creds, max_results=20)
            match = None
            for msg in messages:
                if msg["id"].startswith(message_id):
                    match = msg["id"]
                    break
            if not match:
                return f"  Message not found with ID starting '{message_id}'"
            message_id = match

        msg = gmail_get_message(creds, message_id)
        if not msg:
            return f"  Could not load message {message_id}"

        body = msg["body"]
        if len(body) > 2000:
            body = body[:2000] + "\n  ... (truncated)"

        lines = [
            "",
            f"  From: {msg['from']}",
            f"  To: {msg['to']}",
            f"  Date: {msg['date']}",
            f"  Subject: {msg['subject']}",
            "  " + "-" * 50,
            "",
            body,
            "",
        ]
        return "\n".join(lines)

    def cmd_calendar(self, args=""):
        """Handle /calendar [today|week|add <summary> <date> <time>]."""
        creds = self._get_creds()
        if not creds:
            return "  Google account not connected. Connect at Settings > Google or visit /api/google/auth"

        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0].lower() if parts else "today"

        if subcmd == "week":
            return self._calendar_week(creds)
        elif subcmd == "add" and len(parts) > 1:
            return self._calendar_add(creds, parts[1].strip())

        return self._calendar_today(creds)

    def _calendar_today(self, creds):
        """Show today's events."""
        try:
            from core.protocols.google_tools import calendar_today
        except ImportError:
            return "  Google API libraries not installed."

        events = calendar_today(creds)
        today_str = datetime.now().strftime("%A, %B %d")

        lines = [
            "",
            f"  CALENDAR - {today_str}",
            "  " + "=" * 30,
            "",
        ]

        if not events:
            lines.append("  No events today.")
        else:
            for evt in events:
                time_str = _format_time_short(evt["start"])
                end_str = _format_time_short(evt["end"])
                lines.append(f"  {time_str} - {end_str}: {evt['summary']}")
                if evt.get("location"):
                    lines.append(f"    Location: {evt['location']}")

        lines.append("")
        return "\n".join(lines)

    def _calendar_week(self, creds):
        """Show this week's events."""
        try:
            from core.protocols.google_tools import calendar_upcoming
        except ImportError:
            return "  Google API libraries not installed."

        events = calendar_upcoming(creds, days=7)

        lines = [
            "",
            "  CALENDAR - Next 7 Days",
            "  " + "=" * 30,
            "",
        ]

        if not events:
            lines.append("  No upcoming events.")
        else:
            current_date = ""
            for evt in events:
                start = evt["start"]
                # Group by date
                date_str = _format_date_short(start)
                if date_str != current_date:
                    current_date = date_str
                    lines.append(f"  {date_str}")
                    lines.append("  " + "-" * 20)

                time_str = _format_time_short(start)
                lines.append(f"    {time_str}: {evt['summary']}")

        lines.append("")
        return "\n".join(lines)

    def _calendar_add(self, creds, args_str):
        """Create a new calendar event from /calendar add <summary> <date> <time>."""
        try:
            from core.protocols.google_tools import calendar_create
        except ImportError:
            return "  Google API libraries not installed."

        # Parse: "Meeting with Bob 2026-02-20 14:00"
        parts = args_str.rsplit(maxsplit=2)
        if len(parts) < 3:
            return "  Usage: /calendar add <summary> <date YYYY-MM-DD> <time HH:MM>"

        summary = parts[0]
        date_str = parts[1]
        time_str = parts[2]

        try:
            start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(hours=1)  # Default 1-hour event
            start_iso = start_dt.isoformat()
            end_iso = end_dt.isoformat()
        except ValueError:
            return "  Invalid date/time format. Use: YYYY-MM-DD HH:MM"

        result = calendar_create(creds, summary, start_iso, end_iso)
        if result["success"]:
            return f"  Event created: {summary} on {date_str} at {time_str}"
        else:
            return f"  Failed to create event: {result.get('error', 'unknown error')}"

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self):
        status = super().get_status()
        status["available"] = self._available
        status["connected"] = self._get_creds() is not None
        status["cached_unread"] = self._cached_unread
        status["cache_age_seconds"] = int(time.time() - self._cache_time) if self._cache_time else -1
        return status


# ---------------------------------------------------------------------------
# Time formatting helpers
# ---------------------------------------------------------------------------

def _format_time_short(iso_str):
    """Format an ISO datetime string to a short time like '2:00 PM'."""
    if not iso_str:
        return ""
    try:
        # Handle full datetime
        if "T" in iso_str:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.strftime("%I:%M %p").lstrip("0")
        # Date-only (all-day event)
        return "All day"
    except (ValueError, TypeError):
        return iso_str


def _format_date_short(iso_str):
    """Format an ISO datetime string to a short date like 'Mon Feb 16'."""
    if not iso_str:
        return ""
    try:
        if "T" in iso_str:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(iso_str, "%Y-%m-%d")
        return dt.strftime("%a %b %d")
    except (ValueError, TypeError):
        return iso_str
