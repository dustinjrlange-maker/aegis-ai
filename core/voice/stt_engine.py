"""
Speech-to-Text Engine — faster-whisper
Lets the companion speak to the Aegis agent via microphone.
"""

import threading
import numpy as np

_model = None
_model_lock = threading.Lock()


def _get_config():
    """Import config lazily to avoid circular imports."""
    from core.config import CONFIG
    return CONFIG


def is_enabled():
    """Check if STT is enabled in config."""
    try:
        config = _get_config()
        return config.get("voice", {}).get("stt", {}).get("enabled", False)
    except Exception:
        return False


def _load_model():
    """Lazy-load the faster-whisper model on first use."""
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model

        config = _get_config()
        stt_config = config["voice"]["stt"]

        print("  [Loading STT model... this may take a moment]")

        from faster_whisper import WhisperModel

        model_size = stt_config.get("model", "base.en")
        device = stt_config.get("device", "cuda")
        compute_type = stt_config.get("compute_type", "float16")

        _model = WhisperModel(model_size, device=device, compute_type=compute_type)
        print("  [STT model loaded]")
        return _model


def record_audio(silence_timeout=None):
    """
    Record audio from the microphone until Enter is pressed or silence is detected.

    Returns:
        numpy array of recorded audio, or None if nothing recorded.
    """
    import sounddevice as sd
    import msvcrt

    config = _get_config()
    stt_config = config["voice"]["stt"]
    if silence_timeout is None:
        silence_timeout = stt_config.get("silence_timeout", 2.0)

    sample_rate = 16000  # Whisper expects 16kHz
    block_size = 1024
    silence_threshold = 0.01
    chunks = []
    silent_blocks = 0
    max_silent_blocks = int(silence_timeout * sample_rate / block_size)
    has_speech = False

    print("  [Listening... press Enter to stop]")

    # Start recording stream
    stream = sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        blocksize=block_size,
    )
    stream.start()

    try:
        while True:
            # Check for Enter key (non-blocking on Windows)
            if msvcrt.kbhit():
                key = msvcrt.getwch()
                if key in ("\r", "\n"):
                    break

            # Read audio block
            data, overflowed = stream.read(block_size)
            chunks.append(data.copy())

            # Check for silence
            level = np.abs(data).mean()
            if level > silence_threshold:
                has_speech = True
                silent_blocks = 0
            else:
                silent_blocks += 1

            # Stop after sustained silence (only if we've heard speech)
            if has_speech and silent_blocks >= max_silent_blocks:
                break

    except KeyboardInterrupt:
        pass
    finally:
        stream.stop()
        stream.close()

    if not chunks or not has_speech:
        return None

    audio = np.concatenate(chunks, axis=0).flatten()
    return audio


def transcribe(audio):
    """
    Transcribe audio using faster-whisper.

    Args:
        audio: numpy array of audio data at 16kHz.

    Returns:
        Transcribed text string, or None if no speech detected.
    """
    model = _load_model()

    segments, info = model.transcribe(
        audio,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text if text else None


def listen_and_transcribe():
    """
    Full pipeline: record from mic and transcribe.

    Returns:
        Transcribed text string, or None if no speech detected.
    """
    audio = record_audio()
    if audio is None:
        print("  [No speech detected]")
        return None

    print("  [Transcribing...]")
    text = transcribe(audio)

    if text:
        print(f"  [Heard: {text}]")
    else:
        print("  [Could not transcribe audio]")

    return text
