"""
Tests for ProtocolRegistry (core/protocols/registry.py).

Validates registration, priority ordering, input/output pipeline routing,
command dispatch, enable/disable, and status reporting.
"""
import pytest
from core.protocols.base import Protocol
from core.protocols.registry import ProtocolRegistry


# --- Helper protocols ---

class _HighProto(Protocol):
    """High-priority test protocol."""

    def __init__(self):
        super().__init__(name="high", description="High", priority=Protocol.PRIORITY_HIGH)

    def process_input(self, user_input, context):
        return {
            "input": user_input,
            "context_injection": "[HIGH]",
            "intercept": False,
            "response": "",
        }

    def process_output(self, response, context):
        return {"response": response, "suppress": False, "append": "[HIGH-OUT]"}

    def get_commands(self):
        return [{"command": "high_cmd", "description": "Test", "handler": "cmd_high"}]

    def cmd_high(self, args=""):
        return f"high handled: {args}"


class _LowProto(Protocol):
    """Low-priority test protocol."""

    def __init__(self):
        super().__init__(name="low", description="Low", priority=Protocol.PRIORITY_LOW)

    def process_input(self, user_input, context):
        return {
            "input": user_input,
            "context_injection": "[LOW]",
            "intercept": False,
            "response": "",
        }

    def process_output(self, response, context):
        return {"response": response, "suppress": False, "append": "[LOW-OUT]"}


class _InterceptProto(Protocol):
    """Protocol that intercepts input (never lets it reach LLM)."""

    def __init__(self, priority=Protocol.PRIORITY_CRITICAL):
        super().__init__(name="interceptor", description="Intercepts", priority=priority)

    def process_input(self, user_input, context):
        return {
            "input": user_input,
            "context_injection": "",
            "intercept": True,
            "response": "Intercepted!",
        }

    def process_output(self, response, context):
        return {"response": response, "suppress": False, "append": ""}


class _SuppressProto(Protocol):
    """Protocol that suppresses output."""

    def __init__(self):
        super().__init__(name="suppressor", description="Suppresses", priority=Protocol.PRIORITY_CRITICAL)

    def process_input(self, user_input, context):
        return {"input": user_input, "context_injection": "", "intercept": False, "response": ""}

    def process_output(self, response, context):
        return {"response": response, "suppress": True, "append": ""}


class _PassthroughProto(Protocol):
    """Protocol that does nothing — pure passthrough."""

    def __init__(self, name="passthrough"):
        super().__init__(name=name, description="Passthrough", priority=Protocol.PRIORITY_NORMAL)

    def process_input(self, user_input, context):
        return {"input": user_input, "context_injection": "", "intercept": False, "response": ""}

    def process_output(self, response, context):
        return {"response": response, "suppress": False, "append": ""}


# =============================================================================
# Tests
# =============================================================================

class TestRegisterUnregister:
    """Protocol registration and removal."""

    def test_register_adds_protocol(self):
        reg = ProtocolRegistry()
        reg.register(_PassthroughProto())
        assert "passthrough" in reg.list_protocols()

    def test_register_initializes_protocol(self):
        reg = ProtocolRegistry()
        p = _PassthroughProto()
        assert p._initialized is False
        reg.register(p)
        assert p._initialized is True

    def test_unregister_removes_protocol(self):
        reg = ProtocolRegistry()
        reg.register(_PassthroughProto())
        reg.unregister("passthrough")
        assert "passthrough" not in reg.list_protocols()

    def test_unregister_nonexistent_is_noop(self):
        reg = ProtocolRegistry()
        reg.unregister("nonexistent")  # should not raise

    def test_get_returns_registered_protocol(self):
        reg = ProtocolRegistry()
        p = _PassthroughProto()
        reg.register(p)
        assert reg.get("passthrough") is p

    def test_get_returns_none_for_missing(self):
        reg = ProtocolRegistry()
        assert reg.get("missing") is None


class TestPriorityOrdering:
    """Protocols process in highest-priority-first order."""

    def test_order_is_highest_first(self):
        reg = ProtocolRegistry()
        reg.register(_LowProto())
        reg.register(_HighProto())
        order = reg.list_protocols()
        assert order.index("high") < order.index("low")

    def test_order_updates_on_register(self):
        reg = ProtocolRegistry()
        reg.register(_LowProto())
        order1 = reg.list_protocols()
        assert order1 == ["low"]

        reg.register(_HighProto())
        order2 = reg.list_protocols()
        assert order2[0] == "high"

    def test_order_updates_on_unregister(self):
        reg = ProtocolRegistry()
        reg.register(_HighProto())
        reg.register(_LowProto())
        reg.unregister("high")
        assert reg.list_protocols() == ["low"]


class TestProcessInput:
    """Input pipeline aggregation and interception."""

    def test_aggregates_context_injections(self):
        reg = ProtocolRegistry()
        reg.register(_HighProto())
        reg.register(_LowProto())
        result = reg.process_input("hello", {})
        assert "[HIGH]" in result["context_injections"]
        assert "[LOW]" in result["context_injections"]

    def test_intercept_stops_processing(self):
        """When a protocol intercepts, lower-priority protocols do not run."""
        reg = ProtocolRegistry()
        reg.register(_InterceptProto(priority=Protocol.PRIORITY_CRITICAL))
        reg.register(_LowProto())
        result = reg.process_input("hello", {})
        assert result["intercept"] is True
        assert result["response"] == "Intercepted!"
        assert result["intercepted_by"] == "interceptor"
        # Low protocol's injection should NOT be present because processing stopped
        assert "[LOW]" not in result["context_injections"]

    def test_disabled_protocol_is_skipped(self):
        reg = ProtocolRegistry()
        high = _HighProto()
        high.disable()
        reg.register(high)
        reg.register(_LowProto())
        result = reg.process_input("hello", {})
        assert "[HIGH]" not in result["context_injections"]
        assert "[LOW]" in result["context_injections"]

    def test_input_passes_through_unmodified_by_default(self):
        reg = ProtocolRegistry()
        reg.register(_PassthroughProto())
        result = reg.process_input("hello world", {})
        assert result["input"] == "hello world"
        assert result["intercept"] is False

    def test_empty_registry_passes_through(self):
        reg = ProtocolRegistry()
        result = reg.process_input("test", {})
        assert result["input"] == "test"
        assert result["context_injections"] == []


class TestProcessOutput:
    """Output pipeline aggregation and suppression."""

    def test_aggregates_appended_text(self):
        reg = ProtocolRegistry()
        reg.register(_HighProto())
        reg.register(_LowProto())
        result = reg.process_output("base response", {})
        # Both appended texts should appear in final response
        assert "[HIGH-OUT]" in result["response"]
        assert "[LOW-OUT]" in result["response"]

    def test_suppress_stops_processing(self):
        reg = ProtocolRegistry()
        reg.register(_SuppressProto())
        reg.register(_LowProto())
        result = reg.process_output("base response", {})
        assert result["suppress"] is True

    def test_suppress_prevents_appended_text(self):
        """When suppressed, appended text is not added to response."""
        reg = ProtocolRegistry()
        low = _LowProto()
        # Suppressor has CRITICAL priority, runs first
        reg.register(_SuppressProto())
        reg.register(low)
        result = reg.process_output("base response", {})
        assert result["suppress"] is True
        # The final response should NOT have the low-out append because suppress=True
        assert "[LOW-OUT]" not in result["response"]

    def test_disabled_protocol_is_skipped_on_output(self):
        reg = ProtocolRegistry()
        high = _HighProto()
        high.disable()
        reg.register(high)
        result = reg.process_output("base", {})
        assert "[HIGH-OUT]" not in result["response"]

    def test_empty_registry_passes_response_through(self):
        reg = ProtocolRegistry()
        result = reg.process_output("hello", {})
        assert result["response"] == "hello"
        assert result["suppress"] is False


class TestHandleCommand:
    """Slash command routing."""

    def test_routes_to_correct_protocol(self):
        reg = ProtocolRegistry()
        reg.register(_HighProto())
        handled, response = reg.handle_command("high_cmd", "some args")
        assert handled is True
        assert "high handled" in response
        assert "some args" in response

    def test_unknown_command_returns_false(self):
        reg = ProtocolRegistry()
        reg.register(_HighProto())
        handled, response = reg.handle_command("nonexistent_cmd")
        assert handled is False
        assert response == ""

    def test_disabled_protocol_commands_not_routed(self):
        reg = ProtocolRegistry()
        high = _HighProto()
        high.disable()
        reg.register(high)
        handled, response = reg.handle_command("high_cmd")
        assert handled is False

    def test_empty_registry_returns_false(self):
        reg = ProtocolRegistry()
        handled, response = reg.handle_command("anything")
        assert handled is False
        assert response == ""


class TestRegistryEnableDisable:
    """Enable/disable protocols via the registry."""

    def test_enable_returns_true_for_registered(self):
        reg = ProtocolRegistry()
        p = _PassthroughProto()
        p.disable()
        reg.register(p)
        assert reg.enable("passthrough") is True
        assert p.enabled is True

    def test_disable_returns_true_for_registered(self):
        reg = ProtocolRegistry()
        reg.register(_PassthroughProto())
        assert reg.disable("passthrough") is True

    def test_enable_returns_false_for_missing(self):
        reg = ProtocolRegistry()
        assert reg.enable("ghost") is False

    def test_disable_returns_false_for_missing(self):
        reg = ProtocolRegistry()
        assert reg.disable("ghost") is False


class TestListProtocols:
    """list_protocols() returns names in priority order."""

    def test_returns_list(self):
        reg = ProtocolRegistry()
        assert isinstance(reg.list_protocols(), list)

    def test_empty_registry(self):
        reg = ProtocolRegistry()
        assert reg.list_protocols() == []

    def test_returns_names_in_priority_order(self):
        reg = ProtocolRegistry()
        reg.register(_LowProto())
        reg.register(_HighProto())
        names = reg.list_protocols()
        assert names == ["high", "low"]


class TestGetAllStatus:
    """get_all_status() returns status dicts for all protocols."""

    def test_returns_list_of_dicts(self):
        reg = ProtocolRegistry()
        reg.register(_HighProto())
        reg.register(_LowProto())
        statuses = reg.get_all_status()
        assert isinstance(statuses, list)
        assert len(statuses) == 2
        for s in statuses:
            assert isinstance(s, dict)
            assert "name" in s

    def test_status_order_matches_priority(self):
        reg = ProtocolRegistry()
        reg.register(_LowProto())
        reg.register(_HighProto())
        statuses = reg.get_all_status()
        assert statuses[0]["name"] == "high"
        assert statuses[1]["name"] == "low"

    def test_empty_registry(self):
        reg = ProtocolRegistry()
        assert reg.get_all_status() == []
