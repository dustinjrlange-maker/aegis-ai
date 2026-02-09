"""
Communications Protocol — Aegis AI
The core conversation engine. Handles natural language interaction,
emotion detection integration, and voice I/O coordination.
"""

from core.protocols.base import Protocol


class CommunicationsProtocol(Protocol):
    """Handles the core conversation experience."""

    def __init__(self):
        super().__init__(
            name="communications",
            description="Core conversation engine — natural language, emotion, voice",
            priority=Protocol.PRIORITY_NORMAL,
        )

    def process_input(self, user_input, context):
        """Communications protocol passes input through with emotion context.

        The actual LLM call happens in the agent loop, not here.
        This protocol adds emotion-based context injection.
        """
        result = {
            "input": user_input,
            "context_injection": "",
            "intercept": False,
            "response": "",
        }

        # Emotion detection is handled by the agent loop and injected into context
        # This protocol doesn't modify input, it just ensures it flows through

        return result

    def process_output(self, response, context):
        """Communications protocol doesn't modify output —
        response cleaning is handled by the pack-specific filler cleaner."""
        return {
            "response": response,
            "suppress": False,
            "append": "",
        }

    def get_status(self):
        """Extended status with voice subsystem info."""
        status = super().get_status()

        # Import lazily to avoid circular deps
        try:
            from core.voice import tts_engine, stt_engine, input_router, emotion
            status["tts_enabled"] = tts_engine.is_enabled()
            status["stt_enabled"] = stt_engine.is_enabled()
            status["emotion_enabled"] = emotion.is_enabled()
            status["input_mode"] = input_router.get_mode()
        except Exception:
            pass

        return status
