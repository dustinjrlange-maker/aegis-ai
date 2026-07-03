"""Tests for tts_engine.synthesize (no real model load)."""
import threading
import time

import numpy as np


def test_synthesize_returns_wav_and_rate(monkeypatch):
    from core.voice import tts_engine

    class FakeModel:
        def tts(self, text, speaker_wav, language):
            return np.array([0.1, 0.2, 0.3], dtype=np.float32)

    monkeypatch.setattr(tts_engine, "_load_model", lambda: FakeModel())
    monkeypatch.setattr(tts_engine, "_get_reference_path", lambda: "ref.wav")

    result = tts_engine.synthesize("hello")
    assert result is not None
    wav, sr = result
    assert isinstance(wav, np.ndarray)
    assert sr == 24000


def test_synthesize_returns_none_on_missing_reference(monkeypatch):
    from core.voice import tts_engine

    def boom():
        raise FileNotFoundError("no reference")

    monkeypatch.setattr(tts_engine, "_load_model", lambda: object())
    monkeypatch.setattr(tts_engine, "_get_reference_path", boom)

    assert tts_engine.synthesize("hi") is None


def test_synthesize_serializes_model_calls(monkeypatch):
    from core.voice import tts_engine

    active = {"count": 0, "max": 0}
    guard = threading.Lock()

    class FakeModel:
        def tts(self, text, speaker_wav, language):
            with guard:
                active["count"] += 1
                active["max"] = max(active["max"], active["count"])
            time.sleep(0.05)
            with guard:
                active["count"] -= 1
            return np.array([0.1], dtype=np.float32)

    monkeypatch.setattr(tts_engine, "_load_model", lambda: FakeModel())
    monkeypatch.setattr(tts_engine, "_get_reference_path", lambda: "ref.wav")

    threads = [threading.Thread(target=tts_engine.synthesize, args=("hi",)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert active["max"] == 1  # never two model.tts calls at once
