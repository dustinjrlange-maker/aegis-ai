# tests/test_reply_shaping.py
"""Mode-aware clean_reply: budgets by mode, roleplay stripped in every mode."""
from core.reply_shaping import build_filler_cleaner, MODE_SENTENCE_BUDGETS


def _cleaner():
    return build_filler_cleaner({"filler_phrases": []})


def test_budgets_table():
    assert MODE_SENTENCE_BUDGETS == {"casual": 3, "emotional": 6, "task": None}


def test_casual_caps_at_three_sentences():
    out = _cleaner()(
        "This is one. This is two. This is three. This is four. This is five."
    )
    assert out == "This is one. This is two. This is three."


def test_default_mode_is_casual():
    c = _cleaner()
    text = "This is one. This is two. This is three. This is four."
    assert c(text) == c(text, mode="casual")


def test_emotional_allows_six_sentences():
    c = _cleaner()
    text = ("That sounds heavy. You carried that all day. He mattered to you. "
            "Anyone would feel this. Take the evening slow. I'm right here.")
    assert c(text, mode="emotional") == text


def test_emotional_caps_at_six():
    c = _cleaner()
    text = ("Sentence number one. Sentence number two. Sentence number three. "
            "Sentence number four. Sentence number five. Sentence number six. "
            "Sentence number seven. Sentence number eight.")
    out = c(text, mode="emotional")
    assert out.endswith("Sentence number six.")
    assert "seven" not in out


def test_task_mode_uncapped_and_preserves_structure():
    c = _cleaner()
    text = ("Here is the full breakdown you asked for.\n\n"
            "The first consideration is timing. The second is cost. "
            "The third is the legal side. The fourth is logistics. "
            "The fifth is the fallback plan. The sixth is next steps.")
    out = c(text, mode="task")
    assert "\n" in out                    # structure preserved
    assert "next steps" in out            # nothing cut


def test_roleplay_stripped_in_every_mode():
    c = _cleaner()
    for mode in ("casual", "emotional", "task"):
        out = c("*adjusts jacket slowly* Hey there, good to see you.", mode=mode)
        assert "adjusts" not in out


def test_think_blocks_stripped_in_task_mode():
    out = _cleaner()("<think>internal reasoning</think>The actual answer here.", mode="task")
    assert "internal reasoning" not in out
    assert "actual answer" in out


def test_list_content_bypasses_cap_in_casual():
    text = ("Here is the plan for tonight.\n"
            "1. First step here\n2. Second step here\n"
            "3. Third step here\n4. Fourth step here")
    out = _cleaner()(text)
    assert "4. Fourth step here" in out


def test_session_still_wires_up():
    # core/agent.py must still export build_filler_cleaner (core/session.py imports it)
    from core.agent import build_filler_cleaner as from_agent
    assert from_agent is build_filler_cleaner
