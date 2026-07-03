"""Tests for audio_io — ffmpeg resolution and OGG encoding."""
import numpy as np
import pytest


def test_resolve_ffmpeg_returns_str_or_none():
    from core.voice import audio_io
    result = audio_io._resolve_ffmpeg()
    assert result is None or isinstance(result, str)


def _ffmpeg_available():
    from core.voice import audio_io
    return audio_io._resolve_ffmpeg() is not None


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not on PATH")
def test_wav_to_ogg_writes_valid_ogg(tmp_path):
    from core.voice import audio_io

    sr = 24000
    t = np.linspace(0, 1, sr, endpoint=False)
    wav = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    out = tmp_path / "out.ogg"
    result = audio_io.wav_to_ogg(wav, sr, out)

    assert result == out
    assert out.exists()
    data = out.read_bytes()
    assert len(data) > 0
    assert data[:4] == b"OggS"  # Ogg container magic


def test_wav_to_ogg_raises_when_ffmpeg_missing(monkeypatch, tmp_path):
    from core.voice import audio_io

    monkeypatch.setattr(audio_io, "_resolve_ffmpeg", lambda: None)
    with pytest.raises(RuntimeError):
        audio_io.wav_to_ogg(np.zeros(10, dtype=np.float32), 24000, tmp_path / "x.ogg")
