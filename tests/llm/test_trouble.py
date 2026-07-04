import pytest
from core.llm.trouble import detect_trouble, detect_private_content


@pytest.mark.parametrize("msg", [
    "no that's wrong",
    "you made a mistake",
    "what are you talking about",
    "that's not right, it's Wednesday",
    "nope, try again",
])
def test_correction_phrases_trip_trouble(msg):
    r = detect_trouble(msg, streak=0)
    assert r.is_trouble is True
    assert r.new_streak >= 1


def test_ordinary_content_disagreement_does_not_trip():
    r = detect_trouble("I think blue is a better color than red here", streak=0)
    assert r.is_trouble is False
    assert r.new_streak == 0


def test_streak_of_two_trips_without_strong_phrase():
    r = detect_trouble("still not it", streak=1)
    assert r.is_trouble is True
    assert r.new_streak == 2


def test_non_correction_resets_streak():
    r = detect_trouble("thanks, that's perfect", streak=3)
    assert r.is_trouble is False
    assert r.new_streak == 0


def test_correction_substring_of_affirming_still_trips():
    # "correct" is a substring inside "not correct"; whole-word affirming guard
    # must not treat this pushback as an affirmation.
    r = detect_trouble("still not correct", streak=1)
    assert r.is_trouble is True
    assert r.new_streak == 2


def test_short_pushback_escalates_but_affirming_does_not():
    pushback = detect_trouble("still not it", streak=1)
    assert pushback.is_trouble is True
    assert pushback.new_streak == 2

    affirming = detect_trouble("that's correct", streak=1)
    assert affirming.is_trouble is False
    assert affirming.new_streak == 0


@pytest.mark.parametrize("msg,reason_kw", [
    ("my bank account number is 12345", "financial"),
    ("here's my credit card", "financial"),
    ("my therapist prescribed a new medication", "health"),
    ("my password is hunter2", "credentials"),
])
def test_private_content_detected(msg, reason_kw):
    is_priv, reason = detect_private_content(msg)
    assert is_priv is True
    assert reason_kw in reason


def test_ordinary_message_is_not_private():
    is_priv, _ = detect_private_content("add a podcast recording on wednesday")
    assert is_priv is False
