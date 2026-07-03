# tests/llm/test_turn_classifier.py
"""Deterministic turn classification: mode (casual|emotional|task) + route override."""
from core.llm.turn_classifier import classify, TurnClass


class TestOverrides:
    def test_think_harder_forces_cloud(self):
        assert classify("think harder about this one").route == "force_cloud"

    def test_just_you_forces_local(self):
        assert classify("keep this between us, just you").route == "force_local"

    def test_negated_override_ignored(self):
        assert classify("don't think hard about it").route == "auto"

    def test_never_use_the_cloud_ignored(self):
        assert classify("never use the cloud for this").route == "auto"

    def test_force_cloud_does_not_change_mode(self):
        tc = classify("think harder", emotion_label="sadness", emotion_score=0.9)
        assert tc.route == "force_cloud"
        assert tc.mode == "emotional"


class TestEmotionalVeto:
    def test_sad_message_is_emotional(self):
        tc = classify(
            "today was really rough and I miss him so much",
            emotion_label="sadness", emotion_score=0.93,
        )
        assert tc.mode == "emotional"

    def test_veto_beats_task_pattern(self):
        tc = classify(
            "I can't figure out how to deal with losing him",
            emotion_label="sadness", emotion_score=0.88,
        )
        assert tc.mode == "emotional"

    def test_below_threshold_is_not_emotional(self):
        tc = classify(
            "that movie made me feel sad I guess",
            emotion_label="sadness", emotion_score=0.5,
        )
        assert tc.mode != "emotional"

    def test_joy_never_vetoes(self):
        tc = classify(
            "help me draft the announcement, today rules",
            emotion_label="joy", emotion_score=0.99,
        )
        assert tc.mode == "task"

    def test_no_emotion_result_defaults_fine(self):
        assert classify("night pike").mode == "casual"


class TestTaskDetection:
    def test_draft_request_is_task(self):
        assert classify("help me draft the L-1A argument").mode == "task"

    def test_walk_me_through_is_task(self):
        assert classify("walk me through incorporating in BC").mode == "task"

    def test_long_vent_without_work_verbs_is_casual(self):
        text = (
            "today was such a long day at work and everyone kept wanting things "
            "from me and I barely had a minute to breathe or eat anything at all"
        )
        assert classify(text).mode == "casual"

    def test_short_message_is_never_task(self):
        assert classify("plan?").mode == "casual"


class TestDefaults:
    def test_greeting_is_casual(self):
        assert classify("hey pike") == TurnClass("casual", "auto", "default")

    def test_empty_input_is_casual(self):
        assert classify("").mode == "casual"
