"""Tests for core.response_length."""
import pytest

from core.response_length import wants_detailed_answer, effective_shaping_mode


@pytest.mark.parametrize("text", [
    "give me a detailed summary",
    "can you itemize the tasks",
    "list them out for me",
    "I want the full breakdown",
    "give it to me in full",
    "tell me everything about the project",
    "expand on that",
    "elaborate please",
    "give me more detail",
    "the longer version please",
    "break it down for me",
    "itemized and detailed summary of the 5 unread emails",
])
def test_positive_detail_requests(text):
    assert wants_detailed_answer(text) is True


@pytest.mark.parametrize("text", [
    "hey pike how's it going",
    "what's the weather",
    "can you send me more coffee",     # 'more' alone must NOT trigger
    "add one more task",               # 'more' alone must NOT trigger
    "summarize my inbox",
    "",
    "   ",
])
def test_negative_plain_requests(text):
    assert wants_detailed_answer(text) is False


def test_none_is_false():
    assert wants_detailed_answer(None) is False


def test_shaping_mode_lifts_cap_on_casual_detail_request():
    assert effective_shaping_mode("casual", "give me a detailed list") == "task"


def test_shaping_mode_leaves_plain_casual_alone():
    assert effective_shaping_mode("casual", "how's it going") == "casual"


def test_shaping_mode_never_lengthens_emotional():
    # A grieving turn that happens to contain a detail cue must STAY emotional.
    assert effective_shaping_mode("emotional", "tell me everything") == "emotional"


def test_shaping_mode_task_stays_task():
    assert effective_shaping_mode("task", "anything") == "task"
