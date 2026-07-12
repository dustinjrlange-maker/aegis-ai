"""/calendar add must preview + confirm, not write immediately (2026-07-09
audit D3). Lower risk than model-driven writes (the user typed the data) but a
typo'd year still silently created a real event; now it previews and requires
/calendar confirm."""
import core.protocols.google_tools as gt
from core.protocols.google import GoogleProtocol


def _proto(monkeypatch, tmp_path):
    p = GoogleProtocol(data_dir=tmp_path)
    monkeypatch.setattr(p, "_get_creds", lambda account_id=None: "CREDS")
    return p


def _wire_create(monkeypatch, ok=True):
    calls = []
    monkeypatch.setattr(
        gt, "calendar_create",
        lambda creds, summary, start, end, description="":
        calls.append({"summary": summary, "start": start, "end": end})
        or {"success": ok})
    return calls


def test_calendar_add_previews_not_writes(monkeypatch, tmp_path):
    p = _proto(monkeypatch, tmp_path)
    calls = _wire_create(monkeypatch)
    out = p.cmd_calendar("add Dentist 2026-07-15 14:00")
    assert calls == [], "must not write on /calendar add"
    assert "Dentist" in out and "2026-07-15" in out
    assert "confirm" in out.lower()


def test_calendar_confirm_writes(monkeypatch, tmp_path):
    p = _proto(monkeypatch, tmp_path)
    calls = _wire_create(monkeypatch)
    p.cmd_calendar("add Dentist 2026-07-15 14:00")
    out = p.cmd_calendar("confirm")
    assert len(calls) == 1
    assert calls[0]["summary"] == "Dentist"
    assert "Dentist" in out


def test_calendar_cancel_discards(monkeypatch, tmp_path):
    p = _proto(monkeypatch, tmp_path)
    calls = _wire_create(monkeypatch)
    p.cmd_calendar("add Dentist 2026-07-15 14:00")
    p.cmd_calendar("cancel")
    p.cmd_calendar("confirm")            # nothing pending now
    assert calls == []


def test_calendar_confirm_with_nothing_pending(monkeypatch, tmp_path):
    p = _proto(monkeypatch, tmp_path)
    calls = _wire_create(monkeypatch)
    out = p.cmd_calendar("confirm")
    assert calls == []
    assert "nothing" in out.lower() or "no " in out.lower()


def test_calendar_add_bad_date_no_pending(monkeypatch, tmp_path):
    p = _proto(monkeypatch, tmp_path)
    calls = _wire_create(monkeypatch)
    out = p.cmd_calendar("add Dentist not-a-date 14:00")
    assert calls == []
    assert "invalid" in out.lower()
    # a following confirm must not resurrect anything
    p.cmd_calendar("confirm")
    assert calls == []
