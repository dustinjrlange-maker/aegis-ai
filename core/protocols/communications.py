"""
Communications Protocol — Aegis AI
The core conversation engine. Handles natural language interaction,
emotion detection integration, and voice I/O coordination.
"""

import re
from core.protocols.base import Protocol


class CommunicationsProtocol(Protocol):
    """Handles the core conversation experience."""

    def __init__(self):
        super().__init__(
            name="communications",
            description="Core conversation engine — natural language, emotion, voice",
            priority=Protocol.PRIORITY_NORMAL,
        )

    def _detect_style_violations(self, last_response):
        """Analyze the last assistant response for structural problems.

        Returns a short correction hint string, or empty string.
        Kept minimal to avoid overloading the 7B model's context.
        """
        stripped = last_response.strip()
        hints = []

        if stripped.endswith("?"):
            hints.append("No question this turn.")

        sentences = re.split(r'[.!?]+', stripped)
        sentences = [s.strip() for s in sentences if s.strip()]
        if len(sentences) > 2:
            hints.append("Shorter.")

        return " ".join(hints)

    def process_input(self, user_input, context):
        """Communications protocol passes input through with anti-repetition
        and style enforcement guidance.

        The actual LLM call happens in the agent loop, not here.
        This protocol feeds recent assistant responses back as context
        hints to prevent repetition and correct structural patterns.
        """
        result = {
            "input": user_input,
            "context_injection": "",
            "intercept": False,
            "response": "",
        }

        messages = context.get("messages", [])
        recent_assistant = [
            m["content"] for m in messages
            if m.get("role") == "assistant" and m.get("content")
        ][-5:]  # last 5

        injection_parts = []

        # --- Style enforcement (kept very short to avoid context overload) ---
        if recent_assistant:
            hint = self._detect_style_violations(recent_assistant[-1])
            if hint:
                injection_parts.append(f"[{hint}]")

        # --- Anti-repetition injection ---
        if len(recent_assistant) >= 3:
            # Only include last 3 snippets, truncated short
            snippets = []
            for msg in recent_assistant[-3:]:
                snippet = msg[:40].strip()
                if len(msg) > 40:
                    snippet += "..."
                snippets.append(f'"{snippet}"')
            injection_parts.append(
                "[Don't repeat: " + " / ".join(snippets) + "]"
            )

        if injection_parts:
            result["context_injection"] = " ".join(injection_parts)

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
