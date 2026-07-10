"""RefreshError must never be swallowed (2026-07-09 audit sweep).

Google Testing-mode refresh tokens die ~weekly. A dead token that gets
swallowed looks like "not connected" / "empty calendar" / "transient send
failure" with no reconnect CTA — the July-8 silent-empty-inbox bug. The fix
pattern (re-raise from google_tools, catch at the seam, mark the account
status=error) existed only on two Gmail read paths; this pins it everywhere.
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.email_assistant as ea
from core.accounts.manager import AccountManager
from core.protocols import google_tools as gt
from core.protocols.google_tools import RefreshError


class _Boom:
    """Any attribute access raises RefreshError — simulates a dead token
    failing at API-call time."""

    def __getattr__(self, name):
        raise RefreshError("invalid_grant: Token has been expired or revoked")


GMAIL_CASES = [
    ("gmail_mark_read", ("CREDS", "m1")),
    ("gmail_mark_unread", ("CREDS", "m1")),
    ("gmail_archive", ("CREDS", "m1")),
    ("gmail_get_message", ("CREDS", "m1")),
    ("gmail_send", ("CREDS", "to@x.ca", "subj", "body")),
    ("gmail_create_draft", ("CREDS", "to@x.ca", "subj", "body")),
    ("gmail_list_drafts", ("CREDS",)),
    ("gmail_get_draft", ("CREDS", "d1")),
    ("gmail_send_draft", ("CREDS", "d1")),
    ("gmail_delete_draft", ("CREDS", "d1")),
    ("get_account_email", ("CREDS",)),
]

CALENDAR_CASES = [
    ("calendar_today", ("CREDS",)),
    ("calendar_upcoming", ("CREDS",)),
    ("calendar_next_event", ("CREDS",)),
    ("calendar_create", ("CREDS", "summary",
                         "2026-07-10T10:00:00", "2026-07-10T11:00:00")),
    ("calendar_update", ("CREDS", "e1")),
    ("calendar_delete", ("CREDS", "e1")),
]


@pytest.mark.parametrize("fn_name,args", GMAIL_CASES)
def test_gmail_funcs_reraise_refresh_error(monkeypatch, fn_name, args):
    monkeypatch.setattr(gt, "_get_gmail_service", lambda creds: _Boom())
    with pytest.raises(RefreshError):
        getattr(gt, fn_name)(*args)


@pytest.mark.parametrize("fn_name,args", CALENDAR_CASES)
def test_calendar_funcs_reraise_refresh_error(monkeypatch, fn_name, args):
    monkeypatch.setattr(gt, "_get_calendar_service", lambda creds: _Boom())
    with pytest.raises(RefreshError):
        getattr(gt, fn_name)(*args)


def test_load_credentials_refresh_error_marks_account_error(tmp_path, monkeypatch):
    """A token that dies during the pre-use refresh must flag the account
    (reconnect CTA), not silently look like 'never connected'."""
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": [
        {"id": "google-personal", "label": "Personal", "email": "a@b.c",
         "is_default": True, "status": "ok"}]}), encoding="utf-8")
    acct_dir = tmp_path / "accounts" / "google-personal"
    acct_dir.mkdir(parents=True)
    (acct_dir / "google_tokens.json").write_text(json.dumps(
        {"token": "t", "refresh_token": "rt", "token_uri": "u",
         "client_id": "c", "client_secret": "s", "scopes": ["x"]}),
        encoding="utf-8")

    class FakeCreds:
        expired = True
        refresh_token = "rt"
        valid = False

        def __init__(self, **kwargs):
            pass

        def refresh(self, request):
            raise RefreshError("invalid_grant")

    monkeypatch.setattr("google.oauth2.credentials.Credentials", FakeCreds)
    creds = gt.load_credentials(tmp_path, account_id="google-personal")
    assert creds is None
    assert AccountManager(tmp_path).get("google-personal")["status"] == "error"


# --- email_assistant seams: RefreshError -> reconnect failure + flagged ------

class _FakeAccounts:
    def __init__(self):
        self.statuses = {}

    def set_status(self, account_id, status):
        self.statuses[account_id] = status


class _Session:
    def __init__(self):
        self.accounts = _FakeAccounts()


def _boom(*a, **k):
    raise RefreshError("invalid_grant")


@pytest.mark.parametrize("call,gt_fn", [
    (lambda s: ea.send_draft(s, "d1", account_id="google-x"), "gmail_send_draft"),
    (lambda s: ea.discard_draft(s, "d1", account_id="google-x"), "gmail_delete_draft"),
    (lambda s: ea.draft_new(s, "to@x.ca", intent="hi", account_id="google-x",
                            body_verbatim="hi"), "gmail_create_draft"),
])
def test_email_assistant_surfaces_reconnect_on_refresh_error(monkeypatch, call, gt_fn):
    session = _Session()
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(ea.gt, gt_fn, _boom)
    res = call(session)
    assert res["success"] is False
    assert "reconnect" in res.get("error", "").lower()
    assert session.accounts.statuses.get("google-x") == "error"
