"""
Protocol Base — Aegis AI
Abstract base class for all Aegis protocol modules.

Protocols are modular capability subsystems. Each can be enabled, disabled,
configured, and extended independently. They process inputs in priority order
and can intercept, modify, or override responses.
"""

from abc import ABC, abstractmethod


class Protocol(ABC):
    """Base class for all Aegis protocols."""

    # Priority levels — higher numbers process first
    PRIORITY_CRITICAL = 100    # Security — can override anything
    PRIORITY_HIGH = 80         # Wellness — health overrides casual chat
    PRIORITY_NORMAL = 50       # Communications — default conversation
    PRIORITY_LOW = 20          # Background tasks, logging

    def __init__(self, name, description="", priority=None):
        self.name = name
        self.description = description
        self.priority = priority if priority is not None else self.PRIORITY_NORMAL
        self._enabled = True
        self._initialized = False

    @property
    def enabled(self):
        return self._enabled

    def enable(self):
        """Enable this protocol."""
        self._enabled = True

    def disable(self):
        """Disable this protocol."""
        self._enabled = False

    def initialize(self):
        """Called once when the protocol is first registered.
        Override for setup that should only happen once."""
        self._initialized = True

    @abstractmethod
    def process_input(self, user_input, context):
        """Process user input before it reaches the LLM.

        Args:
            user_input: The raw user message string.
            context: Dict with session state (messages, memory, emotion, etc.)

        Returns:
            Dict with:
                - "input": possibly modified user input
                - "context_injection": additional context to add to the prompt (or "")
                - "intercept": if True, this protocol handles the response directly
                - "response": direct response string (only if intercept=True)
                - "priority_boost": optional float to boost/lower response priority
        """
        pass

    @abstractmethod
    def process_output(self, response, context):
        """Process the agent's response before it reaches the user.

        Args:
            response: The agent's response string.
            context: Dict with session state.

        Returns:
            Dict with:
                - "response": possibly modified response string
                - "suppress": if True, do not show this response
                - "append": additional text to append after the response
        """
        pass

    def get_status(self):
        """Return current protocol status as a dict."""
        return {
            "name": self.name,
            "enabled": self._enabled,
            "initialized": self._initialized,
            "priority": self.priority,
            "description": self.description,
        }

    def get_commands(self):
        """Return list of slash commands this protocol handles.
        Each entry is a dict with: command, description, handler (method name).
        Override in subclasses to register protocol-specific commands."""
        return []

    def __repr__(self):
        status = "ON" if self._enabled else "OFF"
        return f"<Protocol:{self.name} [{status}] priority={self.priority}>"
