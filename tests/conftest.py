"""
Shared fixtures for Aegis AI test suite.
"""
import sys
from pathlib import Path
import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def empty_context():
    """Minimal context dict for protocol testing."""
    return {
        "messages": [{"role": "system", "content": "test"}],
        "memory": None,
        "char_memory": None,
        "agent_name": "TestAgent",
    }


@pytest.fixture
def sample_messages():
    """Sample conversation messages for testing."""
    return [
        {"role": "system", "content": "You are a test agent."},
        {"role": "user", "content": "Hello there."},
        {"role": "assistant", "content": "Hello. How can I help you?"},
        {"role": "user", "content": "What is the weather?"},
        {"role": "assistant", "content": "I don't have weather data, but I hope it's nice out."},
    ]
