"""Canonical confirmation semantics (2026-07-09 audit — structural).

The incident's root cause was a fail-OPEN confirmation matcher: "No I want you
to send it..." contained "send it" and transmitted. Each feature had its own
subtly-different confirm/negation regex. This module is the single source of
truth: fail-closed, tested once, reused by every irreversible/outward action.
"""
from core.confirmation import is_affirmative, is_negative, NOT_A_CONFIRM

# The exact incident message + its siblings must NEVER read as confirmation.
INCIDENT = "No I want you to send it from my personal email to the switch stitch email"

AFFIRMATIVE = ["yes", "Yes.", "yeah", "yep", "sure", "ok", "okay",
               "yes please", "ok, do it", "go ahead", "confirm", "save it",
               "add it", "please do", "alright"]

NOT_CONFIRM = [
    INCIDENT,
    "wrong, I said from my personal email",
    "no, don't add that",
    "nah, not that day",
    "should I send it?",
    "maybe later",
    "actually, cancel that",
    "send it to bob instead",
    "hold off for now",
    "anyway, how's the weather",
    "",
]


def test_affirmatives_confirm():
    for t in AFFIRMATIVE:
        assert is_affirmative(t) is True, f"{t!r} should confirm"


def test_non_confirmations_fail_closed():
    for t in NOT_CONFIRM:
        assert is_affirmative(t) is False, f"{t!r} must NOT confirm"


def test_incident_message_is_not_a_confirmation():
    assert is_affirmative(INCIDENT) is False
    assert NOT_A_CONFIRM.search(INCIDENT)


def test_is_negative_catches_bare_no():
    for t in ["no", "No.", "nope", "nah", "wrong", "stop", "cancel", "don't"]:
        assert is_negative(t) is True, f"{t!r} should be negative"
    for t in ["yes", "sure", "ok"]:
        assert is_negative(t) is False
