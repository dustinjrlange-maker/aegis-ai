# core/llm/trouble.py
"""Stateless detectors for escalate-on-trouble mode.

`detect_trouble` flags when the user appears to be correcting or contradicting
Pike (a sign the local 8B is failing this turn). `detect_private_content` flags
messages carrying obviously sensitive data, so escalation can warn-and-confirm.

Both are pure functions — no I/O, no model — so they are fully unit-testable and
never leak. The "judge" LLM layer described in the spec is deliberately deferred.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Correction / contradiction cues. Conservative: aimed at the user pushing back
# on Pike's OWN answer, not ordinary disagreement about content.
_CORRECTION_PHRASES = (
    "that's wrong", "thats wrong", "that is wrong",
    "that's not right", "thats not right", "that's incorrect", "thats incorrect",
    "you made a mistake", "you're wrong", "youre wrong", "you are wrong",
    "fix your mistake", "fix that", "you messed up", "you got it wrong",
    "what are you talking about", "you're confused", "youre confused",
    "wrong again", "still wrong", "still not right", "not what i said",
    "that's not what i", "thats not what i", "no you didn't", "no you didnt",
    "try again", "nope", "incorrect",
)
# Bare leading "no" — "no,"/"no."/"no " at the start is a correction signal.
_LEADING_NO = re.compile(r"^\s*no[,.\s]")

# Positive-signal words that preempt streak-based escalation.
_AFFIRMING = (
    "thanks", "thank you", "perfect", "great", "good", "nice", "correct",
    "exactly", "got it", "makes sense", "that works", "love it",
)
# Negation cues. A negated affirming word ("not correct") is a correction, not
# praise, so the affirming guard must not fire when a negation is present.
_NEGATION = re.compile(r"\b(?:not|no|never|isn't|isnt|wasn't|wasnt|don't|dont)\b")


@dataclass(frozen=True)
class TroubleResult:
    is_trouble: bool
    reason: str
    new_streak: int


def _looks_like_correction(lowered: str) -> bool:
    if _LEADING_NO.match(lowered):
        return True
    return any(p in lowered for p in _CORRECTION_PHRASES)


def detect_trouble(user_message: str, streak: int) -> TroubleResult:
    """Fast-path trouble detection. `streak` is the count of consecutive prior
    correction turns; pass the returned `new_streak` back in next turn."""
    lowered = (user_message or "").lower().strip()
    corrected = _looks_like_correction(lowered)
    if corrected:
        new_streak = streak + 1
        return TroubleResult(True, "correction_phrase", new_streak)
    # Escalating frustration: a 2nd short pushback in a row still counts even
    # without a keyword. Short + follows a prior correction.
    # But affirming words preempt — "thanks, perfect" resets even from high streak.
    if streak >= 1 and len(lowered.split()) <= 5:
        # Whole-word match so "goodbye" doesn't count as "good"; and a negated
        # affirming word ("not correct") is a correction, not praise.
        affirming = not _NEGATION.search(lowered) and any(
            re.search(r"\b" + re.escape(a) + r"\b", lowered) for a in _AFFIRMING
        )
        if not affirming:
            return TroubleResult(True, "correction_streak", streak + 1)
    return TroubleResult(False, "no_trouble", 0)


# Private-content lexicon → (reason, phrases). Deterministic and conservative.
_PRIVATE_LEXICON = {
    "financial": (
        "bank account", "account number", "routing number", "credit card",
        "debit card", "sin number", "social insurance", "ssn", "social security",
        "my salary", "my income", "net worth", "my savings",
    ),
    "health": (
        "diagnosis", "diagnosed", "medication", "prescribed", "therapist",
        "my doctor", "mental health", "depression", "my meds",
    ),
    "credentials": (
        "my password", "password is", "api key", "secret key", "2fa code",
        "one-time code", "login is",
    ),
}


def detect_private_content(user_message: str) -> tuple[bool, str]:
    """Return (is_private, reason). Reason names the category (e.g. 'financial')."""
    lowered = (user_message or "").lower()
    for reason, phrases in _PRIVATE_LEXICON.items():
        if any(p in lowered for p in phrases):
            return True, reason
    return False, ""
