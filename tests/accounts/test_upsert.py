# tests/accounts/test_upsert.py
import json
from core.accounts.manager import AccountManager, _slugify_account_id


def _seed(tmp_path, accounts):
    (tmp_path / "accounts.json").write_text(
        json.dumps({"accounts": accounts}), encoding="utf-8")


def test_slugify():
    assert _slugify_account_id("SwitchStitch") == "google-switchstitch"
    assert _slugify_account_id("HBO Max!!") == "google-hbo-max"
    assert _slugify_account_id("") == "google-account"


def test_upsert_creates_new_account(tmp_path):
    am = AccountManager(tmp_path)
    acct_id = am.upsert_account("SwitchStitch", "TheSwitchStitch@gmail.com",
                                {"name": "Switch"})
    assert acct_id == "google-switchstitch"
    a = am.get("google-switchstitch")
    assert a["email"] == "TheSwitchStitch@gmail.com"
    assert a["label"] == "SwitchStitch"
    assert a["is_default"] is False
    assert a["provider"] == "google"
    assert a["features"] == {"briefing_calendar": True, "inbox_scan": True}
    assert a["status"] == "ok"
    assert a["represent_as"] == {"name": "Switch", "signoff": "Switch", "tone_hint": ""}


def test_upsert_dedupes_by_email_case_insensitive(tmp_path):
    _seed(tmp_path, [{
        "id": "google-personal", "provider": "google",
        "email": "dustin.jr.lange@gmail.com", "label": "Personal",
        "is_default": True, "represent_as": {"name": "Dustin", "signoff": "Dustin", "tone_hint": ""},
        "features": {"briefing_calendar": True, "inbox_scan": True}, "status": "error",
    }])
    am = AccountManager(tmp_path)
    acct_id = am.upsert_account("Dustin Personal", "DUSTIN.JR.LANGE@gmail.com",
                                {"name": "Dustin"})
    assert acct_id == "google-personal"
    assert len(am.list()) == 1
    # Established (non-empty) label is PRESERVED on re-link — never renamed.
    assert am.get("google-personal")["label"] == "Personal"
    assert am.get("google-personal")["status"] == "ok"


def test_upsert_fills_empty_label_on_relink(tmp_path):
    _seed(tmp_path, [{
        "id": "google-personal", "provider": "google",
        "email": "dustin.jr.lange@gmail.com", "label": "",
        "is_default": True, "represent_as": {"name": "", "signoff": "", "tone_hint": ""},
        "features": {"briefing_calendar": True, "inbox_scan": True}, "status": "ok",
    }])
    am = AccountManager(tmp_path)
    acct_id = am.upsert_account("Dustin Personal", "dustin.jr.lange@gmail.com",
                                {"name": "Dustin"})
    assert acct_id == "google-personal"
    assert len(am.list()) == 1
    # Empty fields get FILLED on re-link (fill-empty still works).
    assert am.get("google-personal")["label"] == "Dustin Personal"
    assert am.get("google-personal")["represent_as"]["name"] == "Dustin"


def test_upsert_slug_collision_appends_number(tmp_path):
    _seed(tmp_path, [{"id": "google-work", "provider": "google", "email": "a@x.com",
                      "label": "Work", "is_default": False,
                      "represent_as": {"name": "", "signoff": "", "tone_hint": ""},
                      "features": {"briefing_calendar": True, "inbox_scan": True},
                      "status": "ok"}])
    am = AccountManager(tmp_path)
    acct_id = am.upsert_account("Work", "b@y.com", {"name": "Me"})
    assert acct_id == "google-work-2"
    assert len(am.list()) == 2


def test_upsert_blank_email_still_creates(tmp_path):
    am = AccountManager(tmp_path)
    acct_id = am.upsert_account("SwitchStitch", "", {"name": "Switch"})
    assert acct_id == "google-switchstitch"
    assert am.get("google-switchstitch")["email"] == ""


def test_upsert_leaves_no_tmp_file(tmp_path):
    am = AccountManager(tmp_path)
    am.upsert_account("SwitchStitch", "s@x.com", {"name": "Switch"})
    assert not (tmp_path / "accounts.json.tmp").exists()
