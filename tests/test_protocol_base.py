"""
Tests for Protocol ABC (core/protocols/base.py).

Validates that the abstract base class enforces the correct contract,
provides working enable/disable, status reporting, and priority constants.
"""
import pytest
from core.protocols.base import Protocol


# --- Concrete subclass for testing the ABC ---

class _StubProtocol(Protocol):
    """Minimal concrete implementation of Protocol for testing."""

    def __init__(self, name="stub", priority=None):
        super().__init__(name=name, description="Stub for tests", priority=priority)

    def process_input(self, user_input, context):
        return {
            "input": user_input,
            "context_injection": "",
            "intercept": False,
            "response": "",
        }

    def process_output(self, response, context):
        return {
            "response": response,
            "suppress": False,
            "append": "",
        }


# --- Incomplete subclass (missing abstract methods) ---

class _IncompleteProtocol(Protocol):
    """Deliberately missing process_input and process_output."""

    def __init__(self):
        super().__init__(name="incomplete", description="Missing methods")


# =============================================================================
# Tests
# =============================================================================

class TestProtocolABCContract:
    """The Protocol ABC must require subclasses to implement abstract methods."""

    def test_cannot_instantiate_incomplete_subclass(self):
        """A subclass that omits process_input/process_output cannot be created."""
        with pytest.raises(TypeError):
            _IncompleteProtocol()

    def test_can_instantiate_complete_subclass(self):
        """A subclass that implements all abstract methods works fine."""
        proto = _StubProtocol()
        assert proto.name == "stub"

    def test_cannot_instantiate_abc_directly(self):
        """Protocol itself cannot be instantiated."""
        with pytest.raises(TypeError):
            Protocol(name="raw", description="should fail")


class TestEnableDisable:
    """Enable/disable toggling on a protocol instance."""

    def test_protocol_enabled_by_default(self):
        proto = _StubProtocol()
        assert proto.enabled is True

    def test_disable(self):
        proto = _StubProtocol()
        proto.disable()
        assert proto.enabled is False

    def test_enable_after_disable(self):
        proto = _StubProtocol()
        proto.disable()
        proto.enable()
        assert proto.enabled is True

    def test_enable_is_idempotent(self):
        proto = _StubProtocol()
        proto.enable()
        proto.enable()
        assert proto.enabled is True


class TestGetStatus:
    """get_status() must return a dict with the documented keys."""

    def test_status_has_required_keys(self):
        proto = _StubProtocol(name="test_proto")
        status = proto.get_status()
        assert isinstance(status, dict)
        assert "name" in status
        assert "enabled" in status
        assert "initialized" in status
        assert "priority" in status
        assert "description" in status

    def test_status_values_match_instance(self):
        proto = _StubProtocol(name="alpha", priority=Protocol.PRIORITY_HIGH)
        proto.initialize()
        status = proto.get_status()
        assert status["name"] == "alpha"
        assert status["enabled"] is True
        assert status["initialized"] is True
        assert status["priority"] == Protocol.PRIORITY_HIGH
        assert status["description"] == "Stub for tests"

    def test_status_reflects_disabled_state(self):
        proto = _StubProtocol()
        proto.disable()
        assert proto.get_status()["enabled"] is False


class TestGetCommands:
    """Default get_commands() returns an empty list."""

    def test_default_get_commands_returns_list(self):
        proto = _StubProtocol()
        cmds = proto.get_commands()
        assert isinstance(cmds, list)

    def test_default_get_commands_is_empty(self):
        proto = _StubProtocol()
        assert proto.get_commands() == []


class TestPriorityConstants:
    """Priority level constants must exist and be ordered correctly."""

    def test_priority_critical_exists(self):
        assert hasattr(Protocol, "PRIORITY_CRITICAL")
        assert Protocol.PRIORITY_CRITICAL == 100

    def test_priority_high_exists(self):
        assert hasattr(Protocol, "PRIORITY_HIGH")
        assert Protocol.PRIORITY_HIGH == 80

    def test_priority_normal_exists(self):
        assert hasattr(Protocol, "PRIORITY_NORMAL")
        assert Protocol.PRIORITY_NORMAL == 50

    def test_priority_low_exists(self):
        assert hasattr(Protocol, "PRIORITY_LOW")
        assert Protocol.PRIORITY_LOW == 20

    def test_priority_ordering(self):
        assert (
            Protocol.PRIORITY_CRITICAL
            > Protocol.PRIORITY_HIGH
            > Protocol.PRIORITY_NORMAL
            > Protocol.PRIORITY_LOW
        )

    def test_default_priority_is_normal(self):
        proto = _StubProtocol()
        assert proto.priority == Protocol.PRIORITY_NORMAL


class TestInitialize:
    """Protocol.initialize() sets _initialized flag."""

    def test_not_initialized_by_default(self):
        proto = _StubProtocol()
        assert proto._initialized is False

    def test_initialize_sets_flag(self):
        proto = _StubProtocol()
        proto.initialize()
        assert proto._initialized is True


class TestRepr:
    """__repr__ should include name, status, and priority."""

    def test_repr_enabled(self):
        proto = _StubProtocol(name="alpha")
        r = repr(proto)
        assert "alpha" in r
        assert "ON" in r

    def test_repr_disabled(self):
        proto = _StubProtocol(name="alpha")
        proto.disable()
        r = repr(proto)
        assert "OFF" in r
