"""
Emotion Detection — Companion Emotional State Awareness
Uses a lightweight transformer to detect emotion in user messages.
"""

import threading

_pipeline = None
_pipeline_lock = threading.Lock()


def _get_config():
    """Import config lazily to avoid circular imports."""
    from core.config import CONFIG
    return CONFIG


def is_enabled():
    """Check if emotion detection is enabled in config."""
    try:
        config = _get_config()
        return config.get("emotion", {}).get("enabled", False)
    except Exception:
        return False


def _load_pipeline():
    """Lazy-load the emotion detection pipeline on first use."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    with _pipeline_lock:
        if _pipeline is not None:
            return _pipeline

        config = _get_config()
        emotion_config = config.get("emotion", {})

        print("  [Loading emotion detection model...]")

        from transformers import pipeline

        model_name = emotion_config.get(
            "model", "bhadresh-savani/distilbert-base-uncased-emotion"
        )

        _pipeline = pipeline(
            "text-classification",
            model=model_name,
            device=-1,  # CPU only — keep GPU free for TTS/STT
            top_k=1,
        )
        print("  [Emotion detection online]")
        return _pipeline


def detect_emotion(text):
    """
    Detect the primary emotion in text.

    Args:
        text: User input text.

    Returns:
        Dict with 'label' and 'score', or None if detection fails or is disabled.
    """
    if not is_enabled():
        return None

    # Skip very short inputs — greetings and commands don't carry meaningful emotion
    if len(text.split()) < 5:
        return None

    try:
        pipe = _load_pipeline()

        # Truncate to model's max length
        truncated = text[:512]
        results = pipe(truncated)

        if results and results[0]:
            top = results[0][0] if isinstance(results[0], list) else results[0]
            return {"label": top["label"], "score": round(top["score"], 3)}

    except Exception:
        pass

    return None


def format_emotion_tag(result):
    """
    Format emotion detection result as a context tag for the agent.

    Args:
        result: Dict from detect_emotion(), or None.

    Returns:
        String like "[Tone hint: sadness (confidence: 0.98)]" or empty string.
    """
    if result is None:
        return ""

    config = _get_config()
    threshold = config.get("emotion", {}).get("threshold", 0.6)

    if result["score"] < threshold:
        return ""

    label = result["label"]

    # Skip "joy" — it's the default state and adds no useful signal
    if label == "joy":
        return ""

    return (
        f"[Tone hint: your companion's words suggest {label}. "
        f"Use your own judgment — do NOT mention this observation or ask about it directly. "
        f"Just let it inform your tone naturally.]"
    )
