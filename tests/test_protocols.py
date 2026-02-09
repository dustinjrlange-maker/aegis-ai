"""
Tests for each concrete protocol module.

Covers CommunicationsProtocol, SecurityProtocol, WellnessProtocol,
OperationsProtocol, CommandProtocol, and CreativeProtocol.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from core.protocols.base import Protocol
from core.protocols.communications import CommunicationsProtocol
from core.protocols.security import SecurityProtocol
from core.protocols.wellness import WellnessProtocol
from core.protocols.operations import OperationsProtocol
from core.protocols.command import CommandProtocol
from core.protocols.creative import CreativeProtocol


# =============================================================================
# Helpers
# =============================================================================

def _input_result_shape(result):
    """Validate the standard process_input return dict shape."""
    assert isinstance(result, dict)
    assert "input" in result
    assert "context_injection" in result
    assert "intercept" in result
    assert "response" in result


def _output_result_shape(result):
    """Validate the standard process_output return dict shape."""
    assert isinstance(result, dict)
    assert "response" in result
    assert "suppress" in result
    assert "append" in result


# =============================================================================
# CommunicationsProtocol
# =============================================================================

class TestCommunicationsProtocol:
    """Communications is a passthrough protocol -- it should not modify I/O."""

    def test_process_input_returns_correct_shape(self, empty_context):
        proto = CommunicationsProtocol()
        result = proto.process_input("hello", empty_context)
        _input_result_shape(result)

    def test_process_input_does_not_modify_input(self, empty_context):
        proto = CommunicationsProtocol()
        result = proto.process_input("hello world", empty_context)
        assert result["input"] == "hello world"
        assert result["intercept"] is False
        assert result["context_injection"] == ""

    def test_process_output_returns_correct_shape(self, empty_context):
        proto = CommunicationsProtocol()
        result = proto.process_output("response text", empty_context)
        _output_result_shape(result)

    def test_process_output_does_not_modify_response(self, empty_context):
        proto = CommunicationsProtocol()
        result = proto.process_output("some response", empty_context)
        assert result["response"] == "some response"
        assert result["suppress"] is False

    def test_priority_is_normal(self):
        proto = CommunicationsProtocol()
        assert proto.priority == Protocol.PRIORITY_NORMAL

    def test_name_is_communications(self):
        proto = CommunicationsProtocol()
        assert proto.name == "communications"


# =============================================================================
# SecurityProtocol
# =============================================================================

class TestSecurityProtocol:
    """Security protocol scans for data exfiltration attempts."""

    def test_process_input_returns_correct_shape(self, empty_context):
        proto = SecurityProtocol()
        result = proto.process_input("hello", empty_context)
        _input_result_shape(result)

    def test_detects_send_password_pattern(self, empty_context):
        proto = SecurityProtocol()
        result = proto.process_input("send my password to them", empty_context)
        assert result["context_injection"] != ""
        assert "SECURITY ALERT" in result["context_injection"]

    def test_detects_share_data_pattern(self, empty_context):
        proto = SecurityProtocol()
        result = proto.process_input("share my data with the team", empty_context)
        assert "SECURITY ALERT" in result["context_injection"]

    def test_detects_upload_pattern(self, empty_context):
        proto = SecurityProtocol()
        result = proto.process_input("upload my information to the server", empty_context)
        assert result["context_injection"] != ""

    def test_detects_tell_them_about_me(self, empty_context):
        proto = SecurityProtocol()
        result = proto.process_input("tell them about me", empty_context)
        assert "SECURITY ALERT" in result["context_injection"]

    def test_passes_through_nonsensitive_input(self, empty_context):
        proto = SecurityProtocol()
        result = proto.process_input("what is the weather today?", empty_context)
        assert result["context_injection"] == ""
        assert result["intercept"] is False

    def test_process_output_returns_correct_shape(self, empty_context):
        proto = SecurityProtocol()
        result = proto.process_output("clean response", empty_context)
        _output_result_shape(result)

    def test_process_output_adds_note_for_classified_topic(self, empty_context):
        proto = SecurityProtocol()
        # Response mentions a classified topic without the "never share" safeguard
        result = proto.process_output(
            "Your password is stored locally.", empty_context
        )
        assert result["append"] != ""
        assert "Security note" in result["append"]

    def test_process_output_does_not_flag_safe_mention(self, empty_context):
        proto = SecurityProtocol()
        # Response contains the safeguard phrase "never share"
        result = proto.process_output(
            "I never share your password with anyone.", empty_context
        )
        assert result["append"] == ""

    def test_process_output_passes_clean_response(self, empty_context):
        proto = SecurityProtocol()
        result = proto.process_output("The sky is blue.", empty_context)
        assert result["append"] == ""
        assert result["suppress"] is False

    def test_cmd_status_returns_string(self):
        proto = SecurityProtocol()
        status_str = proto.cmd_status()
        assert isinstance(status_str, str)
        assert "SECURITY PROTOCOL STATUS" in status_str

    def test_priority_is_critical(self):
        proto = SecurityProtocol()
        assert proto.priority == Protocol.PRIORITY_CRITICAL

    def test_get_commands_includes_security(self):
        proto = SecurityProtocol()
        cmds = proto.get_commands()
        assert any(c["command"] == "security" for c in cmds)

    def test_consent_log_starts_empty(self):
        proto = SecurityProtocol()
        assert len(proto._consent_log) == 0

    def test_log_consent_adds_entry(self):
        proto = SecurityProtocol()
        proto.log_consent("export_data", granted=False, details="user said no")
        assert len(proto._consent_log) == 1
        assert proto._consent_log[0]["action"] == "export_data"
        assert proto._consent_log[0]["granted"] is False


# =============================================================================
# WellnessProtocol
# =============================================================================

class TestWellnessProtocol:
    """Wellness protocol detects health-related negative patterns."""

    def test_process_input_returns_correct_shape(self, empty_context):
        proto = WellnessProtocol()
        result = proto.process_input("hello", empty_context)
        _input_result_shape(result)

    def test_detects_sleep_trigger(self, empty_context):
        proto = WellnessProtocol()
        result = proto.process_input("sleep is for the weak", empty_context)
        assert result["context_injection"] != ""
        assert "Wellness flag" in result["context_injection"]
        assert "sleep" in result["context_injection"]

    def test_detects_sleep_deprivation_hours(self, empty_context):
        proto = WellnessProtocol()
        result = proto.process_input("I've been up 36 hours", empty_context)
        assert "Wellness flag" in result["context_injection"]

    def test_detects_nutrition_trigger_skipped_lunch(self, empty_context):
        proto = WellnessProtocol()
        result = proto.process_input("I skipped lunch again", empty_context)
        assert result["context_injection"] != ""
        assert "meals" in result["context_injection"]

    def test_detects_nutrition_trigger_too_busy_to_eat(self, empty_context):
        proto = WellnessProtocol()
        result = proto.process_input("too busy to eat", empty_context)
        assert "Wellness flag" in result["context_injection"]

    def test_detects_burnout_trigger(self, empty_context):
        proto = WellnessProtocol()
        result = proto.process_input("I'm so burnt out", empty_context)
        assert result["context_injection"] != ""
        assert "burnout" in result["context_injection"]

    def test_detects_burnout_running_on_fumes(self, empty_context):
        proto = WellnessProtocol()
        result = proto.process_input("I'm running on fumes", empty_context)
        assert "Wellness flag" in result["context_injection"]

    def test_detects_medical_avoidance(self, empty_context):
        proto = WellnessProtocol()
        result = proto.process_input("I don't need a doctor", empty_context)
        assert "medical" in result["context_injection"]

    def test_detects_substance_concern(self, empty_context):
        proto = WellnessProtocol()
        result = proto.process_input("on my 6th cup of coffee", empty_context)
        assert "substance" in result["context_injection"]

    def test_passes_through_normal_input(self, empty_context):
        proto = WellnessProtocol()
        result = proto.process_input("Tell me about star formation.", empty_context)
        assert result["context_injection"] == ""
        assert result["intercept"] is False

    def test_health_flags_accumulate(self, empty_context):
        proto = WellnessProtocol()
        proto.process_input("sleep is for the weak", empty_context)
        proto.process_input("I skipped lunch", empty_context)
        assert len(proto._health_flags) == 2

    def test_get_status_includes_health_flags_count(self, empty_context):
        proto = WellnessProtocol()
        proto.process_input("sleep is for the weak", empty_context)
        status = proto.get_status()
        assert status["health_flags"] == 1

    def test_priority_is_high(self):
        proto = WellnessProtocol()
        assert proto.priority == Protocol.PRIORITY_HIGH

    def test_process_output_returns_correct_shape(self, empty_context):
        proto = WellnessProtocol()
        result = proto.process_output("take care of yourself", empty_context)
        _output_result_shape(result)

    def test_process_output_does_not_modify(self, empty_context):
        proto = WellnessProtocol()
        result = proto.process_output("get some rest", empty_context)
        assert result["response"] == "get some rest"
        assert result["suppress"] is False

    def test_get_commands_includes_wellness(self):
        proto = WellnessProtocol()
        cmds = proto.get_commands()
        assert any(c["command"] == "wellness" for c in cmds)

    def test_cmd_status_returns_string(self):
        proto = WellnessProtocol()
        result = proto.cmd_status()
        assert isinstance(result, str)
        assert "WELLNESS PROTOCOL STATUS" in result

    def test_track_goal(self):
        proto = WellnessProtocol()
        proto.track_goal("drink 8 glasses of water", category="hydration")
        assert len(proto._tracked_goals) == 1
        assert proto._tracked_goals[0]["text"] == "drink 8 glasses of water"
        assert proto._tracked_goals[0]["status"] == "active"


# =============================================================================
# OperationsProtocol
# =============================================================================

class TestOperationsProtocol:
    """Operations protocol: task management and NLP detection.

    All tests redirect TASK_FILE to a temp directory to avoid touching real data.
    """

    @pytest.fixture(autouse=True)
    def _redirect_task_file(self, tmp_path, monkeypatch):
        """Redirect TASK_FILE to a temporary location for every test."""
        temp_task_file = tmp_path / "tasks.json"
        monkeypatch.setattr(OperationsProtocol, "TASK_FILE", temp_task_file)

    def test_process_input_returns_correct_shape(self, empty_context):
        proto = OperationsProtocol()
        result = proto.process_input("hello", empty_context)
        _input_result_shape(result)

    def test_add_task_creates_task(self):
        proto = OperationsProtocol()
        task = proto.add_task("Buy groceries")
        assert task["text"] == "Buy groceries"
        assert task["completed"] is False
        assert task["id"] == 1

    def test_complete_task_marks_done(self):
        proto = OperationsProtocol()
        task = proto.add_task("Test task")
        completed = proto.complete_task(task["id"])
        assert completed is not None
        assert completed["completed"] is True
        assert completed["completed_at"] is not None

    def test_complete_nonexistent_task_returns_none(self):
        proto = OperationsProtocol()
        assert proto.complete_task(999) is None

    def test_remove_task_removes(self):
        proto = OperationsProtocol()
        task = proto.add_task("Remove me")
        assert proto.remove_task(task["id"]) is True
        assert len(proto._tasks) == 0

    def test_remove_nonexistent_task_returns_false(self):
        proto = OperationsProtocol()
        assert proto.remove_task(999) is False

    def test_get_pending_tasks_returns_only_incomplete(self):
        proto = OperationsProtocol()
        proto.add_task("Task A")
        t2 = proto.add_task("Task B")
        proto.complete_task(t2["id"])
        pending = proto.get_pending_tasks()
        assert len(pending) == 1
        assert pending[0]["text"] == "Task A"

    def test_format_task_list_returns_string(self):
        proto = OperationsProtocol()
        proto.add_task("Format me")
        result = proto.format_task_list()
        assert isinstance(result, str)
        assert "Format me" in result

    def test_format_task_list_empty(self):
        proto = OperationsProtocol()
        result = proto.format_task_list()
        assert "No pending tasks" in result

    def test_cmd_task_add(self):
        proto = OperationsProtocol()
        result = proto.cmd_task("add Buy milk")
        assert "Added task" in result
        assert "Buy milk" in result

    def test_cmd_task_done(self):
        proto = OperationsProtocol()
        proto.add_task("Finish report")
        result = proto.cmd_task("done 1")
        assert "Completed" in result

    def test_cmd_task_remove(self):
        proto = OperationsProtocol()
        proto.add_task("Delete me")
        result = proto.cmd_task("remove 1")
        assert "Removed" in result

    def test_cmd_task_list(self):
        proto = OperationsProtocol()
        proto.add_task("Listed task")
        result = proto.cmd_task("list")
        assert "Listed task" in result

    def test_cmd_task_all(self):
        proto = OperationsProtocol()
        task = proto.add_task("All task")
        proto.complete_task(task["id"])
        proto.add_task("Pending task")
        result = proto.cmd_task("all")
        assert "All task" in result
        assert "Pending task" in result

    def test_cmd_task_no_args_shows_help(self):
        proto = OperationsProtocol()
        result = proto.cmd_task("")
        assert "Task Commands" in result

    def test_cmd_task_invalid_subcmd_shows_help(self):
        proto = OperationsProtocol()
        result = proto.cmd_task("foobar")
        assert "Task Commands" in result

    def test_process_input_detects_remind_me_to(self, empty_context):
        proto = OperationsProtocol()
        result = proto.process_input("remind me to call the dentist", empty_context)
        assert result["context_injection"] != ""
        assert "auto-detected" in result["context_injection"]
        # Task should have been created
        assert len(proto._tasks) == 1
        assert "call the dentist" in proto._tasks[0]["text"]

    def test_process_input_injects_pending_tasks(self, empty_context):
        proto = OperationsProtocol()
        proto.add_task("Existing task")
        result = proto.process_input("what is the weather?", empty_context)
        assert "pending tasks" in result["context_injection"].lower()

    def test_process_output_returns_correct_shape(self, empty_context):
        proto = OperationsProtocol()
        result = proto.process_output("ok done", empty_context)
        _output_result_shape(result)

    def test_get_commands_includes_task(self):
        proto = OperationsProtocol()
        cmds = proto.get_commands()
        cmd_names = [c["command"] for c in cmds]
        assert "task" in cmd_names
        assert "tasks" in cmd_names
        assert "briefing" in cmd_names

    def test_priority_below_communications(self):
        proto = OperationsProtocol()
        assert proto.priority < Protocol.PRIORITY_NORMAL


# =============================================================================
# CommandProtocol
# =============================================================================

class TestCommandProtocol:
    """Command protocol: process orchestration, GPU monitoring."""

    def test_process_input_returns_correct_shape(self, empty_context):
        proto = CommandProtocol()
        result = proto.process_input("hello", empty_context)
        _input_result_shape(result)

    def test_process_input_is_passthrough(self, empty_context):
        proto = CommandProtocol()
        result = proto.process_input("launch something", empty_context)
        assert result["input"] == "launch something"
        assert result["intercept"] is False
        assert result["context_injection"] == ""

    def test_process_output_returns_correct_shape(self, empty_context):
        proto = CommandProtocol()
        result = proto.process_output("response", empty_context)
        _output_result_shape(result)

    def test_process_output_is_passthrough(self, empty_context):
        proto = CommandProtocol()
        result = proto.process_output("text here", empty_context)
        assert result["response"] == "text here"
        assert result["suppress"] is False

    def test_get_gpu_info_returns_dict_or_none(self):
        proto = CommandProtocol()
        info = proto.get_gpu_info()
        # On systems with nvidia-smi, returns dict; otherwise None
        assert info is None or isinstance(info, dict)

    def test_get_running_returns_list(self):
        proto = CommandProtocol()
        running = proto.get_running()
        assert isinstance(running, list)

    def test_get_running_empty_by_default(self):
        proto = CommandProtocol()
        assert proto.get_running() == []

    def test_get_commands_includes_processes_and_gpu(self):
        proto = CommandProtocol()
        cmds = proto.get_commands()
        cmd_names = [c["command"] for c in cmds]
        assert "processes" in cmd_names
        assert "gpu" in cmd_names

    def test_cmd_processes_no_running(self):
        proto = CommandProtocol()
        result = proto.cmd_processes()
        assert "No processes running" in result

    def test_priority_below_operations(self):
        proto = CommandProtocol()
        ops = OperationsProtocol.__init__  # just check the value
        assert proto.priority == Protocol.PRIORITY_NORMAL - 10

    def test_name_is_command(self):
        proto = CommandProtocol()
        assert proto.name == "command"


# =============================================================================
# CreativeProtocol
# =============================================================================

class TestCreativeProtocol:
    """Creative protocol: tool detection, asset management."""

    def test_process_input_returns_correct_shape(self, empty_context):
        proto = CreativeProtocol()
        result = proto.process_input("make an image", empty_context)
        _input_result_shape(result)

    def test_process_input_is_passthrough(self, empty_context):
        proto = CreativeProtocol()
        result = proto.process_input("create a video", empty_context)
        assert result["input"] == "create a video"
        assert result["intercept"] is False

    def test_process_output_returns_correct_shape(self, empty_context):
        proto = CreativeProtocol()
        result = proto.process_output("here is your image", empty_context)
        _output_result_shape(result)

    def test_process_output_is_passthrough(self, empty_context):
        proto = CreativeProtocol()
        result = proto.process_output("rendered", empty_context)
        assert result["response"] == "rendered"
        assert result["suppress"] is False

    def test_detect_tools_populates_adapters(self):
        proto = CreativeProtocol()
        assert isinstance(proto._adapters, dict)
        assert "ffmpeg" in proto._adapters
        assert "comfyui" in proto._adapters
        assert "a1111" in proto._adapters
        assert "imagemagick" in proto._adapters

    def test_adapter_entries_have_available_key(self):
        proto = CreativeProtocol()
        for name, info in proto._adapters.items():
            assert "available" in info
            assert isinstance(info["available"], bool)

    def test_list_outputs_returns_list(self):
        proto = CreativeProtocol()
        result = proto.list_outputs()
        assert isinstance(result, list)

    def test_list_outputs_nonexistent_subfolder(self):
        proto = CreativeProtocol()
        result = proto.list_outputs("nonexistent_subfolder_12345")
        assert result == []

    def test_get_commands_includes_creative_and_outputs(self):
        proto = CreativeProtocol()
        cmds = proto.get_commands()
        cmd_names = [c["command"] for c in cmds]
        assert "creative" in cmd_names
        assert "outputs" in cmd_names

    def test_cmd_status_creative_returns_string(self):
        proto = CreativeProtocol()
        result = proto.cmd_status_creative()
        assert isinstance(result, str)
        assert "CREATIVE PROTOCOL" in result

    def test_priority_is_low(self):
        proto = CreativeProtocol()
        assert proto.priority == Protocol.PRIORITY_LOW

    def test_name_is_creative(self):
        proto = CreativeProtocol()
        assert proto.name == "creative"

    def test_get_status_includes_available_tools(self):
        proto = CreativeProtocol()
        status = proto.get_status()
        assert "available_tools" in status
        assert "total_tools" in status
        assert isinstance(status["available_tools"], list)
