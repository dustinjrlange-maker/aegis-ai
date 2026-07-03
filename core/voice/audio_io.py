"""
Audio I/O helpers — format conversion for voice messaging.
Encodes synthesized waveforms to OGG/Opus for Telegram voice notes.
"""

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np

# WinGet FFmpeg shared build (same location tts_engine prepends to PATH at import).
_FFMPEG_WINGET = os.path.expanduser(
    r"~\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg.Shared_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.0.1-full_build-shared\bin"
)


def _resolve_ffmpeg():
    """Locate the ffmpeg binary. Returns a path/name, or None if not found.

    Self-contained — does not rely on tts_engine's import-time PATH side effect,
    so audio_io works when imported standalone (e.g. in tests).
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidate = Path(_FFMPEG_WINGET) / "ffmpeg.exe"
    if candidate.exists():
        return str(candidate)
    return None


def wav_to_ogg(wav, sample_rate, out_path):
    """Encode a mono float32 waveform to an OGG/Opus file via ffmpeg.

    Args:
        wav: numpy float32 array in [-1, 1], mono.
        sample_rate: sample rate of wav (Hz).
        out_path: destination .ogg path.

    Returns:
        Path to the written file.

    Raises:
        RuntimeError: if ffmpeg is missing or encoding fails.
    """
    ffmpeg = _resolve_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found on PATH or WinGet location")

    out_path = Path(out_path)
    wav = np.asarray(wav, dtype=np.float32)

    cmd = [
        ffmpeg, "-y",
        "-f", "f32le", "-ar", str(sample_rate), "-ac", "1",
        "-i", "-",
        "-c:a", "libopus", "-b:a", "48k",
        str(out_path),
    ]
    proc = subprocess.run(
        cmd,
        input=wav.tobytes(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "ignore")[:500]
        raise RuntimeError(f"ffmpeg encode failed: {detail}")
    return out_path
