"""[ADD_EVENT:] bracket path must PROPOSE, not auto-write (2026-07-09 audit,
follow-up A2b).

A2 gated the NLP regex path but the [ADD_EVENT:] bracket handler — the path
the 8B is actually instructed to use — still wrote to Google Calendar
immediately, on the default account, no confirmation. Same incident class
(irreversible action from raw model output + wrong-account routing). It now
proposes through the shared ops confirm mechanism and names the target
account so the write identity is visible before the user says yes.
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.protocols.google_tools as gt
from core.auth import create_user, active_sessions
from core.session import SessionManager
from core.protocols.operations import OperationsProtocol


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr("core.auth.USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr("core.auth.USERS_DIR", tmp_path / "users")
    monkeypatch.setattr("core.memory.manager.PROJECT_ROOT", tmp_path)
    active_sessions.clear()
    yield
    active_sessions.clear()


@pytest.fixture
def session(tmp_path):
    create_user("testuser", "Test User", "pass1234")
    user_dir = tmp_path / "data" / "users" / "testuser"
    user_dir.mkdir(parents=True, exist_ok=True)
    for sub in ["conversation_logs", "session_journals", "knowledge_base",
                "security_protocols"]:
        (user_dir / sub).mkdir(exist_ok=True)
    return SessionManager().get_or_create("testuser")


@pytest.fixture
def writes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        gt, "create_event_or_local",
        lambda creds, em, title, date, time_start=None, time_end=None, description="":
        calls.append({"title": title, "date": date, "time_start": time_start,
                      "time_end": time_end}) or {"source": "local"})
    return calls


def test_bracket_add_event_proposes_not_writes(session, writes):
    result = session._handle_add_event("2026-07-15 | 14:00-15:00 | dinner")
    assert writes == [], "bracket must not write to the calendar directly"
    ops = session.protocol_registry.get("operations")
    assert ops._pending_event is not None
    assert ops._pending_event["title"] == "dinner"
    assert ops._pending_event["date"] == "2026-07-15"
    assert ops._pending_event["time_start"] == "14:00"
    assert ops._pending_event["time_end"] == "15:00"
    # The reply must read as a question/offer, not a false "added" confirmation.
    assert result.rstrip().endswith("?")
    assert "added" not in result.lower()


def test_bracket_then_confirm_writes_with_time_range(session, writes):
    session._handle_add_event("2026-07-15 | 14:00-15:00 | dinner")
    ops = session.protocol_registry.get("operations")
    ops.process_input("yes", {})
    assert len(writes) == 1
    assert writes[0]["title"] == "dinner"
    assert writes[0]["time_start"] == "14:00"
    assert writes[0]["time_end"] == "15:00"      # time_end threaded through confirm
    assert ops._pending_event is None


def test_bracket_then_nonconfirm_discards(session, writes):
    session._handle_add_event("2026-07-15 | dinner")
    ops = session.protocol_registry.get("operations")
    ops.process_input("nah, not that day", {})
    assert writes == []
    assert ops._pending_event is None


# --- ops.propose_event names the target account (write identity visible) ----

class _EM:
    def get_events_for_date(self, d): return []
    def list_events(self): return []


class _Accounts:
    def __init__(self, label): self._label = label
    def default(self): return {"id": "google-personal", "label": self._label}


class _Sess:
    def __init__(self, label): self.accounts = _Accounts(label)


def test_propose_event_names_google_account(tmp_path, monkeypatch):
    ops = OperationsProtocol(event_manager=_EM(), data_dir=tmp_path)
    ops._session = _Sess("Personal")
    monkeypatch.setattr(ops, "_google_creds", lambda: "CREDS")
    preview = ops.propose_event("dentist", "2026-07-15", time_start="09:00")
    assert "Personal" in preview
    assert "dentist" in preview
    assert ops._pending_event["title"] == "dentist"


def test_propose_event_local_when_no_creds(tmp_path, monkeypatch):
    ops = OperationsProtocol(event_manager=_EM(), data_dir=tmp_path)
    ops._session = _Sess("Personal")
    monkeypatch.setattr(ops, "_google_creds", lambda: None)
    preview = ops.propose_event("dentist", "2026-07-15")
    assert "local" in preview.lower()


def test_confirm_names_account_in_result(tmp_path, monkeypatch):
    """After a confirmed write to Google, the authoritative confirmation names
    the account so the user can see where it landed."""
    ops = OperationsProtocol(event_manager=_EM(), data_dir=tmp_path)
    ops._session = _Sess("SwitchStitch")
    monkeypatch.setattr(ops, "_google_creds", lambda: "CREDS")
    monkeypatch.setattr(gt, "create_event_or_local",
                        lambda *a, **k: {"source": "google"})
    ops._pending_event = {"title": "shoot", "date": "2026-07-20",
                          "time_start": "10:00", "time_end": None}
    result = ops.process_input("yes", {})
    assert result["intercept"] is True
    assert "SwitchStitch" in result["response"]
