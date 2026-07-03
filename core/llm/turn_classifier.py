# core/llm/turn_classifier.py
"""Deterministic per-turn classification for the chat pipeline.

mode  — how the reply should be shaped: casual | emotional | task
route — explicit user override: auto | force_local | force_cloud

No LLM involvement by design: qwen3:8b is documented-unreliable at exactly
this meta-judgment, and deterministic rules stay legible to the user.
The one-sentence rule: task-shaped requests go to the big brain;
conversation and feelings stay home.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

EMOTION_VETO_LABELS = ("sadness", "fear", "anger")
EMOTION_VETO_THRESHOLD = 0.75  # tuning-session knob
MIN_TASK_WORDS = 4

_FORCE_CLOUD = ("think harder", "think hard", "big brain", "best answer", "use the cloud")
_FORCE_LOCAL = ("just you", "keep it local", "no cloud", "keep it simple")
_NEGATORS = ("don't", "dont", "do not", "never", "no need")

_TASK_PATTERNS = re.compile(
    r"\b(help me|can you|could you|i need you to|write|draft|analyze|analyse|plan|"
    r"summarize|summarise|research|compare|review|outline|design|debug|"
    r"break down|figure out|walk me through|explain how)\b"
)


@dataclass(frozen=True)
class TurnClass:
    """Classification of one user turn."""
    mode: str    # "casual" | "emotional" | "task"
    route: str   # "auto" | "force_local" | "force_cloud"
    reason: str


def _matches_override(lowered: str, phrases) -> bool:
    """True if any phrase occurs NOT preceded by a negator (send-guard lesson)."""
    for phrase in phrases:
        for m in re.finditer(re.escape(phrase), lowered):
            window = lowered[max(0, m.start() - 12):m.start()]
            if any(neg in window for neg in _NEGATORS):
                continue
            return True
    return False


def classify(text: str, emotion_label: str | None = None,
             emotion_score: float = 0.0) -> TurnClass:
    """Classify one user turn. Overrides set route only; veto beats task."""
    lowered = (text or "").lower()

    route = "auto"
    if _matches_override(lowered, _FORCE_CLOUD):
        route = "force_cloud"
    elif _matches_override(lowered, _FORCE_LOCAL):
        route = "force_local"

    if emotion_label in EMOTION_VETO_LABELS and emotion_score >= EMOTION_VETO_THRESHOLD:
        return TurnClass("emotional", route, f"emotion_veto:{emotion_label}")

    if len(lowered.split()) >= MIN_TASK_WORDS and _TASK_PATTERNS.search(lowered):
        return TurnClass("task", route, "task_pattern")

    return TurnClass("casual", route, "default")
