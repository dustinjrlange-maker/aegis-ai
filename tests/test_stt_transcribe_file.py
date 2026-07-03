"""Tests for stt_engine.transcribe_file (no real model load)."""


class _Seg:
    def __init__(self, text):
        self.text = text


def test_transcribe_file_joins_segments(monkeypatch):
    from core.voice import stt_engine

    class FakeModel:
        def transcribe(self, path, **kwargs):
            return [_Seg(" Hello"), _Seg("world ")], None

    monkeypatch.setattr(stt_engine, "_load_model", lambda: FakeModel())
    assert stt_engine.transcribe_file("clip.ogg") == "Hello world"


def test_transcribe_file_empty_returns_none(monkeypatch):
    from core.voice import stt_engine

    class FakeModel:
        def transcribe(self, path, **kwargs):
            return [], None

    monkeypatch.setattr(stt_engine, "_load_model", lambda: FakeModel())
    assert stt_engine.transcribe_file("clip.ogg") is None


def test_transcribe_file_passes_str_path(monkeypatch):
    from pathlib import Path
    from core.voice import stt_engine

    seen = {}

    class FakeModel:
        def transcribe(self, path, **kwargs):
            seen["path"] = path
            return [_Seg("ok")], None

    monkeypatch.setattr(stt_engine, "_load_model", lambda: FakeModel())
    stt_engine.transcribe_file(Path("a") / "b.ogg")
    assert isinstance(seen["path"], str)  # Path coerced to str for faster-whisper
