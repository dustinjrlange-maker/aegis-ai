# tests/llm/test_route_tags.py
"""Pure mapping from TurnClass to the router task tag + mode hints."""
from core.llm.turn_classifier import TurnClass, route_task_tag
from server.chat_pipeline import _MODE_HINTS


def test_task_mode_maps_chat_task():
    assert route_task_tag(TurnClass("task", "auto", "x")) == "chat_task"


def test_emotional_maps_chat_emotional():
    assert route_task_tag(TurnClass("emotional", "auto", "x")) == "chat_emotional"


def test_casual_maps_chat_casual():
    assert route_task_tag(TurnClass("casual", "auto", "x")) == "chat_casual"


def test_force_local_wins_over_task_mode():
    assert route_task_tag(TurnClass("task", "force_local", "x")) == "chat_casual"


def test_force_cloud_on_task_maps_chat_task():
    assert route_task_tag(TurnClass("casual", "force_cloud", "x")) == "chat_task"


def test_force_cloud_on_emotional_stays_deep_mode_gated():
    # Privacy exception: an emotional turn under an explicit override still maps
    # to chat_emotional, so Deep Mode (not the override) decides whether feelings
    # may leave the machine. "feelings never leave this machine" stays literal.
    assert route_task_tag(TurnClass("emotional", "force_cloud", "x")) == "chat_emotional"


def test_hints_exist_for_non_casual_modes():
    assert "emotional" in _MODE_HINTS and "task" in _MODE_HINTS
    assert "casual" not in _MODE_HINTS
    for hint in _MODE_HINTS.values():
        assert hint.startswith("[Response mode:")
        assert len(hint.splitlines()) == 1   # qwen injection-fragility: one line
