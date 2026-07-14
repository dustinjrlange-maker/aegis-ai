"""Text-based detection of self-harm / hopeless-ideation language.

Deliberately text-based (not emotion-classifier-based): the classifier is
miscalibrated, and this signal is too important to depend on it. Conservative
but safety-biased — a few benign false positives are acceptable because the
response is a gentle check-in, not an alarm.
"""
import re

_PATTERNS = [
    r"\bbetter off without me\b",
    r"\beveryone('?s| is| would be|'d be)\b[^.?!]{0,20}\bbetter off\b",
    r"\bdon'?t (?:want to|wanna) (?:be here|live|exist|wake up)\b",
    r"\b(?:want|wanting) to (?:die|disappear)(?=\s*(?:[.?!,]|$))",
    r"\bcan'?t go on\b",
    r"\bend(?:ing)? (?:it all|my life|things)\b",
    r"\b(?:kill|hurt|harm)(?:ing)? (?:myself|my ?self)\b",
    r"\bsuicid",
    r"\bno (?:point|reason) (?:in|to)\b[^.?!]{0,20}\b(?:living|going on|any of (?:it|this)|anymore)\b",
    r"\bwhat'?s the point\b[^.?!]{0,20}\b(?:go on|living|anymore|any of (?:it|this))\b",
]
_CRISIS_RE = re.compile("|".join(_PATTERNS), re.IGNORECASE)


def detect_crisis(text):
    """True when *text* contains hopeless / self-harm ideation language."""
    return bool(_CRISIS_RE.search(text or ""))
