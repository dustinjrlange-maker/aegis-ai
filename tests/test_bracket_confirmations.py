"""Bracket handler results must be surfaced to the user as an authoritative
confirmation line, so the model's (possibly wrong) prose can't hide what
actually happened.
"""
import json

from core.protocols.bracket_commands import BracketCommandProtocol


def test_handler_result_surfaced_as_confirmation():
    p = BracketCommandProtocol()
    p.register_handler(
        "ADD_EVENT",
        lambda arg: "Event 'Podcast' added to your Google Calendar on 2026-07-08 18:00-20:00",
    )
    out = p.process_output("Sure, done for you. [ADD_EVENT: 2026-07-08 | 18:00-20:00 | Podcast]", {})
    assert "added to your Google Calendar" in out["append"]
    assert out["append"].startswith("✓")           # ✓ marker
    assert "[ADD_EVENT" not in out["response"]            # tag stripped from prose


def test_all_bracket_reply_not_left_as_bare_punctuation():
    p = BracketCommandProtocol()
    p.register_handler("ADD_EVENT", lambda arg: "House movie night saved to the local calendar")
    out = p.process_output(".[ADD_EVENT: 2026-07-09 | movie]", {})
    # The lone '.' left by stripping is removed; the confirmation carries info.
    assert out["response"] == ""
    assert "House movie night" in out["append"]


def test_handler_error_uses_warning_marker():
    p = BracketCommandProtocol()

    def boom(arg):
        raise ValueError("calendar unavailable")

    p.register_handler("ADD_EVENT", boom)
    out = p.process_output("Adding it. [ADD_EVENT: 2026-07-08 | x]", {})
    assert out["append"].startswith("⚠")            # ⚠ marker
    assert "calendar unavailable" in out["append"]


def test_no_brackets_means_no_confirmation():
    p = BracketCommandProtocol()
    p.register_handler("ADD_EVENT", lambda arg: "should not run")
    out = p.process_output("Just chatting, nothing to do here.", {})
    assert out["append"] == ""
    assert out["response"] == "Just chatting, nothing to do here."


def test_capabilities_forbids_editing_events_from_chat():
    """Guardrail for #2 — Pike must not claim it can edit/delete events."""
    from core.config import PROJECT_ROOT
    caps = json.loads((PROJECT_ROOT / "core" / "config" / "capabilities.json").read_text(encoding="utf-8"))
    cannot = " ".join(caps["cannot_do"]).lower()
    assert "edit" in cannot and "calendar event" in cannot
