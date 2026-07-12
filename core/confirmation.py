"""Canonical confirmation semantics for irreversible / outward-facing actions.

ONE source of truth so no feature can re-introduce a fail-open confirm matcher.
The 2026-07-09 email incident happened because "No I want you to send it..."
contained a send word and a per-feature regex treated it as confirmation.

The rule this module encodes:
  * A confirmation is a SHORT, STANDALONE affirmative ("yes", "go ahead",
    "save it") — never a sentence that merely contains an affirmative/verb.
  * Any negation, question, deliberation, or redirect ("no", "wrong", "?",
    "maybe", "instead", "cancel") means NOT confirmed — fail closed.

Every propose->confirm path (email send, calendar events, calendar slash
command, and any future outward action) MUST gate its commit on
`is_affirmative()` rather than rolling its own matcher. Domain-specific verbs
(email's "ship it") may extend the affirmative set, but the negation guard
(`NOT_A_CONFIRM`) is shared and non-negotiable.
"""
import re

# A send/affirmative word inside a QUESTION, deliberation, NEGATION, or a
# redirect to a different action is NOT a confirmation — irreversible actions
# fail closed here. Shared by every confirm path so the semantics can't drift.
NOT_A_CONFIRM = re.compile(
    r"\?|\b(no|nope|nah|wrong|not|stop|cancel|instead|different|"
    r"should|shall|do you think|not sure|maybe|later|wait|hold off|"
    r"don't|do not|never ?mind)\b",
    re.IGNORECASE,
)

# A short, standalone affirmative — optionally led/trailed by generic filler.
# Anchored ^...$ so a trailing instruction ("...after fixing the subject")
# can't ride along.
_AFFIRMATIVE = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|ok(?:ay)?|alright|please\s+do|do\s+it|"
    r"go\s+ahead|confirm(?:ed)?|save\s+it|add\s+it|send\s+it|ship\s+it)"
    r"(?:[\s,!.]+(?:please|thanks?|now|go\s+ahead|do\s+it))*[\s,!.]*$",
    re.IGNORECASE,
)

# Bare negation at the start of a turn (for callers that want an explicit
# "user is declining" signal, distinct from "not a confirmation").
_NEGATIVE = re.compile(r"^\s*(?:no|nope|nah|wrong|stop|cancel|don'?t|never ?mind)\b",
                       re.IGNORECASE)


def is_affirmative(text, extra=None):
    """True only for a short, standalone, unambiguous confirmation.

    `extra` is an optional compiled regex of domain-specific confirmation
    phrasings (e.g. email's "ship it / fire it off"); it is accepted only when
    the message ALSO clears the shared negation guard."""
    t = (text or "").strip()
    if not t:
        return False
    if NOT_A_CONFIRM.search(t):
        return False
    if _AFFIRMATIVE.match(t):
        return True
    if extra is not None and extra.match(t):
        return True
    return False


def is_negative(text):
    """True when the turn is an explicit decline ("no", "cancel", ...)."""
    return bool(_NEGATIVE.match((text or "").strip()))
