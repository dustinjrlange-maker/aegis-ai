"""Tests for chat-created calendar events routing to Google Calendar.

Covers the shared create_event_or_local helper (Google-first with local
fallback) and the bracket-handler time-range parser.
"""
import pytest

from core.protocols import google_tools
from core.session import UserSession


# --- Time-range parsing (bracket [ADD_EVENT] handler) ----------------------

class _TimeParser:
    """Minimal carrier for UserSession._parse_time_range without building a
    full session (which loads packs, memory, models)."""
    _TIME_RANGE_RE = UserSession._TIME_RANGE_RE
    _parse_time_range = UserSession._parse_time_range


@pytest.mark.parametrize("text,expected", [
    ("12:00-16:00", ("12:00", "16:00")),
    ("12-16", ("12:00", "16:00")),
    ("9:30-11:00", ("09:30", "11:00")),
    (" 8 - 9 ", ("08:00", "09:00")),
    ("dentist", (None, None)),      # not a time range
    ("", (None, None)),
    ("25:00-26:00", (None, None)),  # out of 24h range
])
def test_parse_time_range(text, expected):
    assert _TimeParser()._parse_time_range(text) == expected


# --- Timezone-aware datetime for the Google API ---------------------------
# Google Calendar rejects a bare dateTime with no offset/timeZone
# ("Missing time zone definition"). _rfc3339_local must attach the local
# offset for timed events and leave all-day dates untouched.

import re as _re


def test_rfc3339_local_attaches_offset():
    out = google_tools._rfc3339_local("2026-07-07T18:00:00")
    assert out.startswith("2026-07-07T18:00:00")
    # RFC3339 offset like -07:00 / +05:30, or Z for UTC machines
    assert _re.search(r"([+-]\d{2}:\d{2}|Z)$", out), out


def test_rfc3339_local_passes_through_date_only():
    assert google_tools._rfc3339_local("2026-07-07") == "2026-07-07"


class _FakeExec:
    def __init__(self, val):
        self._val = val

    def execute(self):
        return self._val


class _FakeEvents:
    def __init__(self, store):
        self.store = store

    def insert(self, calendarId, body):
        self.store["insert"] = body
        return _FakeExec({"id": "evt1", "htmlLink": "http://x"})

    def get(self, calendarId, eventId):
        return _FakeExec({"id": eventId, "start": {"date": "2026-07-04"},
                          "end": {"date": "2026-07-04"}})

    def update(self, calendarId, eventId, body):
        self.store["update"] = body
        return _FakeExec({"id": eventId})


class _FakeService:
    def __init__(self, store):
        self._events = _FakeEvents(store)

    def events(self):
        return self._events


def test_calendar_create_sends_timezone_for_timed_event(monkeypatch):
    store = {}
    monkeypatch.setattr(google_tools, "_get_calendar_service",
                        lambda creds: _FakeService(store))
    res = google_tools.calendar_create(
        object(), "Podcast", "2026-07-07T18:00:00", "2026-07-07T19:00:00")
    assert res["success"] is True
    start_dt = store["insert"]["start"]["dateTime"]
    assert _re.search(r"([+-]\d{2}:\d{2}|Z)$", start_dt), start_dt


def test_calendar_update_sends_timezone_for_timed_event(monkeypatch):
    store = {}
    monkeypatch.setattr(google_tools, "_get_calendar_service",
                        lambda creds: _FakeService(store))
    res = google_tools.calendar_update(
        object(), "evt1", summary="Send payment",
        start="2026-07-04T11:45:00", end="2026-07-04T12:45:00")
    assert res["success"] is True
    start_dt = store["update"]["start"]["dateTime"]
    assert _re.search(r"([+-]\d{2}:\d{2}|Z)$", start_dt), start_dt


# --- Google-or-local routing helper ---------------------------------------

# --- Deterministic weekday resolution (fixes 8B date arithmetic) ----------
# The bracket handler routes non-ISO dates through OperationsProtocol.
# _parse_natural_date so Pike can pass "wednesday" and code computes the date.

@pytest.mark.parametrize("dayname", [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
])
def test_parse_natural_date_lands_on_named_weekday(dayname):
    import datetime
    from core.protocols.operations import OperationsProtocol
    iso = OperationsProtocol._parse_natural_date(dayname)
    d = datetime.date.fromisoformat(iso)
    assert d.strftime("%A").lower() == dayname
    assert d >= datetime.date.today()  # never resolves to a past date


def test_parse_natural_date_today_and_tomorrow():
    import datetime
    from core.protocols.operations import OperationsProtocol
    assert OperationsProtocol._parse_natural_date("today") == datetime.date.today().isoformat()
    assert OperationsProtocol._parse_natural_date("tomorrow") == (
        datetime.date.today() + datetime.timedelta(days=1)).isoformat()


class _FakeEventManager:
    def __init__(self):
        self.created = None

    def add_event(self, title, date, time_start=None, time_end=None, description=""):
        self.created = {
            "title": title, "date": date, "time_start": time_start,
            "time_end": time_end, "description": description,
        }
        return dict(self.created)


def test_falls_back_to_local_when_no_creds():
    em = _FakeEventManager()
    out = google_tools.create_event_or_local(
        None, em, "Range day", "2026-07-04", time_start="12:00", time_end="16:00")
    assert out["source"] == "local"
    assert em.created["title"] == "Range day"
    assert em.created["time_start"] == "12:00"


def test_writes_to_google_when_connected(monkeypatch):
    calls = {}

    def fake_create(creds, summary, start, end, description=""):
        calls.update(summary=summary, start=start, end=end)
        return {"success": True, "event_id": "abc", "link": "http://x"}

    monkeypatch.setattr(google_tools, "calendar_create", fake_create)
    em = _FakeEventManager()
    out = google_tools.create_event_or_local(
        object(), em, "Range day", "2026-07-04", time_start="12:00", time_end="16:00")

    assert out["source"] == "google"
    assert calls["start"] == "2026-07-04T12:00:00"
    assert calls["end"] == "2026-07-04T16:00:00"
    # No local copy kept when Google write succeeds.
    assert em.created is None


def test_all_day_event_uses_date_strings(monkeypatch):
    calls = {}

    def fake_create(creds, summary, start, end, description=""):
        calls.update(start=start, end=end)
        return {"success": True, "event_id": "abc", "link": ""}

    monkeypatch.setattr(google_tools, "calendar_create", fake_create)
    google_tools.create_event_or_local(object(), _FakeEventManager(), "Dentist", "2026-07-04")
    assert calls["start"] == "2026-07-04"
    assert calls["end"] == "2026-07-04"


def test_falls_back_to_local_when_google_write_fails(monkeypatch):
    def fake_create(creds, summary, start, end, description=""):
        return {"success": False, "error": "boom"}

    monkeypatch.setattr(google_tools, "calendar_create", fake_create)
    em = _FakeEventManager()
    out = google_tools.create_event_or_local(
        object(), em, "Range day", "2026-07-04", time_start="12:00")
    assert out["source"] == "local"
    assert em.created["title"] == "Range day"


def test_single_time_defaults_one_hour_end(monkeypatch):
    calls = {}

    def fake_create(creds, summary, start, end, description=""):
        calls.update(start=start, end=end)
        return {"success": True, "event_id": "abc", "link": ""}

    monkeypatch.setattr(google_tools, "calendar_create", fake_create)
    google_tools.create_event_or_local(
        object(), _FakeEventManager(), "Call", "2026-07-04", time_start="14:00")
    assert calls["start"] == "2026-07-04T14:00:00"
    assert calls["end"] == "2026-07-04T15:00:00"
