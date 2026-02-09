"""
Text-to-Speech Engine — XTTS-v2 Voice Synthesis
Voice output for the Aegis AI companion. Voice reference loaded from active voice pack.
"""

import os
import threading
import numpy as np

# Auto-accept Coqui CPML license (non-commercial use)
os.environ["COQUI_TOS_AGREED"] = "1"

# Ensure FFmpeg shared DLLs are findable (needed by torchcodec on Windows).
# Python 3.8+ requires os.add_dll_directory() — PATH alone doesn't work.
_ffmpeg_shared = os.path.expanduser(
    r"~\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg.Shared_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.0.1-full_build-shared\bin"
)
if os.path.isdir(_ffmpeg_shared):
    os.add_dll_directory(_ffmpeg_shared)
    os.environ["PATH"] = _ffmpeg_shared + os.pathsep + os.environ.get("PATH", "")

_model = None
_model_lock = threading.Lock()
_speak_lock = threading.Lock()
_speaking = False
_current_stream = None


def _get_config():
    """Import config lazily to avoid circular imports."""
    from core.config import CONFIG, get_path
    return CONFIG, get_path


def is_enabled():
    """Check if TTS is enabled in config."""
    try:
        config, _ = _get_config()
        return config.get("voice", {}).get("tts", {}).get("enabled", False)
    except Exception:
        return False


def _load_model():
    """Lazy-load the XTTS-v2 model on first use."""
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model

        config, get_path = _get_config()
        tts_config = config["voice"]["tts"]

        print("  [Loading TTS model... this may take a moment]")

        from TTS.api import TTS

        model_name = tts_config.get("model", "tts_models/multilingual/multi-dataset/xtts_v2")
        device = tts_config.get("device", "cuda")

        _model = TTS(model_name).to(device)
        print("  [TTS model loaded]")
        return _model


def _get_reference_path():
    """Get the voice reference audio file path from the active voice pack."""
    from core.config import CONFIG, PROJECT_ROOT

    pack_config = CONFIG.get("packs", {})
    active_voice = pack_config.get("active_voice", "default")
    pack_dir = pack_config.get("pack_directory", "packs")

    # Look for reference in voice pack first
    voice_pack_ref = PROJECT_ROOT / pack_dir / "voices" / active_voice / "reference.wav"
    if voice_pack_ref.exists():
        return str(voice_pack_ref)

    # Fall back to legacy location
    from core.config import get_path
    ref_dir = get_path(CONFIG, "voice_reference")
    ref_file = ref_dir / "reference.wav"
    if ref_file.exists():
        return str(ref_file)

    raise FileNotFoundError(
        f"Voice reference not found for pack '{active_voice}'.\n"
        f"  Expected: {voice_pack_ref}\n"
        "  Place a clean audio reference clip there."
    )


def speak(text, blocking=False):
    """
    Synthesize speech from text using the active voice pack and play it.

    Args:
        text: The text to speak.
        blocking: If True, wait for speech to finish. Default False (daemon thread).
    """
    if not is_enabled():
        return

    if blocking:
        _speak_impl(text)
    else:
        t = threading.Thread(target=_speak_impl, args=(text,), daemon=True)
        t.start()


def _speak_impl(text):
    """Internal: synthesize and play audio."""
    global _speaking, _current_stream

    if not _speak_lock.acquire(timeout=0.1):
        return  # Another speech is in progress, skip

    try:
        _speaking = True
        import sounddevice as sd

        model = _load_model()
        ref_path = _get_reference_path()
        config, _ = _get_config()
        sample_rate = config["voice"]["tts"].get("sample_rate", 24000)

        # Synthesize audio
        wav = model.tts(
            text=text,
            speaker_wav=ref_path,
            language="en"
        )

        # Convert to numpy array if needed
        if not isinstance(wav, np.ndarray):
            wav = np.array(wav)

        # Normalize to [-1, 1] float32
        wav = wav.astype(np.float32)
        if wav.max() > 1.0 or wav.min() < -1.0:
            wav = wav / max(abs(wav.max()), abs(wav.min()))

        # Play audio
        sd.play(wav, samplerate=sample_rate)
        sd.wait()

    except FileNotFoundError as e:
        print(f"  [TTS: {e}]")
    except Exception as e:
        print(f"  [TTS error — falling back to text only: {e}]")
    finally:
        _speaking = False
        _current_stream = None
        _speak_lock.release()


def stop():
    """Stop any currently playing speech."""
    global _speaking
    try:
        import sounddevice as sd
        sd.stop()
        _speaking = False
    except Exception:
        pass


def is_speaking():
    """Check if speech is currently playing."""
    return _speaking
