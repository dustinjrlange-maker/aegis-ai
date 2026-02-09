"""
Input Router — Manages text and voice input modes.
Handles /voice commands and routes input to the appropriate source.
"""

from core.voice import stt_engine

# Current input mode: "text" or "voice"
_mode = "text"


def get_input():
    """
    Get user input from the appropriate source.

    Returns:
        Tuple of (text, source) where source is "text" or "voice".
        Returns (None, None) on EOF/interrupt.
    """
    global _mode

    if _mode == "voice":
        return _get_voice_input()

    # Text mode — standard input with voice command support
    try:
        raw = input("You: ").strip()
    except (KeyboardInterrupt, EOFError):
        return None, None

    if not raw:
        return "", "text"

    # Handle /voice commands
    lower = raw.lower()
    if lower in ("/voice", "/v"):
        return _single_voice_input()
    elif lower == "/voice on":
        return _enable_voice_mode()
    elif lower == "/voice off":
        return _disable_voice_mode()

    return raw, "text"


def _single_voice_input():
    """Record a single voice message and return to text mode."""
    if not stt_engine.is_enabled():
        print("  [Voice input is disabled in config]")
        return "", "text"

    text = stt_engine.listen_and_transcribe()
    if text:
        return text, "voice"
    return "", "text"


def _get_voice_input():
    """Get input in persistent voice mode."""
    global _mode

    print("[Voice mode — speak, or type /voice off to switch back]")

    # Still allow typed commands in voice mode
    import msvcrt
    import time

    # Brief check: if user starts typing, switch to text
    print("You: ", end="", flush=True)
    time.sleep(0.3)

    if msvcrt.kbhit():
        # User is typing — read as text input
        try:
            raw = input("").strip()
        except (KeyboardInterrupt, EOFError):
            return None, None

        lower = raw.lower()
        if lower == "/voice off":
            return _disable_voice_mode()
        return raw, "text"

    # No typing detected — record voice
    text = stt_engine.listen_and_transcribe()
    if text:
        return text, "voice"

    return "", "voice"


def _enable_voice_mode():
    """Switch to persistent voice input mode."""
    global _mode
    if not stt_engine.is_enabled():
        print("  [Voice input is disabled in config]")
        return "", "text"

    _mode = "voice"
    print("  [Voice mode ON — all input via microphone]")
    print("  [Type /voice off to return to text mode]")

    # Immediately capture first voice input
    text = stt_engine.listen_and_transcribe()
    if text:
        return text, "voice"
    return "", "voice"


def _disable_voice_mode():
    """Switch back to text input mode."""
    global _mode
    _mode = "text"
    print("  [Voice mode OFF — back to text input]")
    return "", "text"


def get_mode():
    """Return the current input mode."""
    return _mode
