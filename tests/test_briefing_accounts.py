"""Tests for multi-account calendar aggregation in collect_briefing_facts.

FakeAccounts is kept importable at module level — later tasks reuse it.
"""
from datetime import datetime
from unittest.mock import patch, MagicMock
import types

from core.briefing import collect_briefing_facts


class FakeAccounts:
    def __init__(self, accounts, creds_by_id):
        self._accounts = accounts
        self._creds = creds_by_id

    def list(self, feature=None):
        if feature is None:
            return self._accounts
        return [a for a in self._accounts if a.get("features", {}).get(feature)]

    def creds_for(self, account_id=None):
        return self._creds.get(account_id)


def _make_session(accounts=None):
    """Minimal stub session for collect_briefing_facts."""
    session = types.SimpleNamespace()
    session.accounts = accounts

    # protocol_registry: return None for all get() calls
    registry = MagicMock()
    registry.get.return_value = None
    session.protocol_registry = registry

    # event_manager: no local events
    em = MagicMock()
    em.list_events.return_value = []
    session.event_manager = em

    # weather
    ws = MagicMock()
    ws.get_weather.return_value = None
    session.weather_service = ws

    # habit / mood / time
    session.habit_manager = MagicMock()
    session.habit_manager.get_today_status.return_value = []
    session.mood_manager = MagicMock()
    session.mood_manager.get_today_moods.return_value = []
    session.time_tracker = MagicMock()
    session.time_tracker.get_active_timer.return_value = None
    session.time_tracker.get_today_summary.return_value = {}

    return session


def _today():
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Test 1: two accounts → events merged, titles prefixed [Label]
# ---------------------------------------------------------------------------

def test_labeled_merge_two_accounts():
    """Two calendar-enabled accounts → events merged with [Label] prefix."""
    creds_a = object()
    creds_b = object()

    acct_a = {"id": "personal", "label": "Personal",
               "features": {"briefing_calendar": True}}
    acct_b = {"id": "hbo",      "label": "HBO",
               "features": {"briefing_calendar": True}}

    today = _today()
    event_map = {
        id(creds_a): [{"summary": "Dentist",   "start": today}],
        id(creds_b): [{"summary": "Shoot Day", "start": today}],
    }

    accounts = FakeAccounts(
        accounts=[acct_a, acct_b],
        creds_by_id={"personal": creds_a, "hbo": creds_b},
    )
    session = _make_session(accounts=accounts)

    with patch("core.protocols.google_tools.calendar_upcoming",
               side_effect=lambda creds, days=4: event_map[id(creds)]), \
         patch("core.protocols.google_tools.gmail_unread_count", return_value=0):
        facts = collect_briefing_facts(session, period="morning")

    titles   = [e["title"]   for e in facts["events_today"]]
    acct_fld = [e["account"] for e in facts["events_today"]]

    assert "[Personal] Dentist"   in titles
    assert "[HBO] Shoot Day"      in titles
    assert "Personal"             in acct_fld
    assert "HBO"                  in acct_fld


# ---------------------------------------------------------------------------
# Test 2: single account → NO [Label] prefix
# ---------------------------------------------------------------------------

def test_single_account_no_prefix():
    """Single calendar-enabled account → event title has no [Label] prefix."""
    creds_a = object()
    acct_a = {"id": "personal", "label": "Personal",
               "features": {"briefing_calendar": True}}

    today = _today()
    accounts = FakeAccounts(accounts=[acct_a], creds_by_id={"personal": creds_a})
    session  = _make_session(accounts=accounts)

    with patch("core.protocols.google_tools.calendar_upcoming",
               return_value=[{"summary": "Dentist", "start": today}]), \
         patch("core.protocols.google_tools.gmail_unread_count", return_value=0):
        facts = collect_briefing_facts(session, period="morning")

    titles = [e["title"] for e in facts["events_today"]]
    assert "Dentist" in titles
    assert not any("[" in t for t in titles), f"Unexpected prefix in: {titles}"


# ---------------------------------------------------------------------------
# Test 3: creds_for returns None → account skipped, no exception
# ---------------------------------------------------------------------------

def test_error_skip_none_creds():
    """Account whose creds_for returns None is skipped; briefing does not raise."""
    acct_bad = {"id": "bad-account", "label": "Bad",
                "features": {"briefing_calendar": True}}

    accounts = FakeAccounts(accounts=[acct_bad], creds_by_id={})
    session  = _make_session(accounts=accounts)

    with patch("core.protocols.google_tools.gmail_unread_count", return_value=0):
        facts = collect_briefing_facts(session, period="morning")

    assert facts["events_today"] == []
