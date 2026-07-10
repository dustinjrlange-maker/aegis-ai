"""NLP event detection must PROPOSE, never auto-write (2026-07-09 audit).

Before this fix, a casual "i have a meeting with bob on friday at 2pm" in
conversation wrote a real Google Calendar event during process_input — before
Pike even responded, with no confirmation. Now the mention becomes a pending
proposal; only a standalone affirmative on the NEXT turn creates the event,
and anything else discards it (fail-closed, single-turn).
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.protocols.google_tools as gt
from core.protocols.operations import OperationsProtocol


class _FakeEventManager:
    def get_events_for_date(self, d):
        return []

    def list_events(self):
        return []


@pytest.fixture
def proto(tmp_path):
    return OperationsProtocol(event_manager=_FakeEventManager(),
                              data_dir=tmp_path)


@pytest.fixture
def created(monkeypatch):
    calls = []

    def fake_create(creds, event_manager, title, date, time_start=None,
                    time_end=None, **kw):
        calls.append({"title": title, "date": date, "time_start": time_start})
        return {"source": "local", "success": True}

    monkeypatch.setattr(gt, "create_event_or_local", fake_create)
    return calls


MENTION = "i have a meeting with bob on friday at 2pm"


def test_casual_event_mention_does_not_autocreate(proto, created):
    result = proto.process_input(MENTION, {})
    assert created == [], "mention alone must NEVER write to the calendar"
    inj = result["context_injection"]
    assert "NOT been saved" in inj
    assert "bob" in inj.lower()


def test_confirm_creates_proposed_event(proto, created):
    proto.process_input(MENTION, {})
    result = proto.process_input("yes please", {})
    assert len(created) == 1
    assert created[0]["title"] == "bob"
    assert created[0]["time_start"] == "14:00"
    assert "Event created" in result["context_injection"]


def test_nonconfirm_reply_discards_proposal(proto, created):
    proto.process_input(MENTION, {})
    proto.process_input("anyway, how's the weather looking", {})
    assert created == []
    # a later bare "yes" must not resurrect the dropped proposal
    proto.process_input("yes", {})
    assert created == []


def test_decline_discards_proposal(proto, created):
    proto.process_input(MENTION, {})
    proto.process_input("no, don't add that", {})
    assert created == []


def test_confirm_with_no_pending_is_inert(proto, created):
    proto.process_input("yes", {})
    assert created == []
