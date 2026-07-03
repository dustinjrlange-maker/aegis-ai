# Telegram Voice MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user send Pike a Telegram voice note and get a spoken reply back — short replies auto-synthesize, long replies offer an opt-in 🔊 Play voice button.

**Architecture:** A thin voice layer over the existing Telegram bot. Reuse STT, TTS, and `process_chat` untouched except for extracting a reusable, thread-safe `synthesize()` from TTS. New encode helper (`audio_io.wav_to_ogg`) pipes raw PCM to ffmpeg. All Telegram glue (voice handler, reply delivery, Play-voice callback) lives in `integrations/telegram_bot.py`.

**Tech Stack:** Python 3.12, python-telegram-bot 22.5, faster-whisper 1.x, Coqui XTTS-v2, ffmpeg (subprocess), pytest.

**Spec:** `docs/superpowers/specs/2026-07-02-telegram-voice-mvp-design.md`

**Note — one refinement from the spec:** the spec keys the Play-voice stash by `(chat_id, message_id)`. This plan uses a **random token** (`secrets.token_urlsafe`) instead. It satisfies the same requirement (no cross-chat collision) more simply and avoids an extra `edit_reply_markup` round-trip, since `callback_data` must be set at send time before a `message_id` exists. The chat_id is stored in the stash value so the callback still sends to the right chat.

---

## File Structure

- `core/voice/tts_engine.py` — **modify**: add `synthesize()` + `_synth_lock`; refactor `_speak_impl` to call it.
- `core/voice/stt_engine.py` — **modify**: add `transcribe_file()`.
- `core/voice/audio_io.py` — **create**: `wav_to_ogg()`, `_resolve_ffmpeg()`.
- `core/config/core_config.json` — **modify**: add `voice.telegram` block.
- `integrations/telegram_bot.py` — **modify**: imports, `get_voice_settings()`, stash helpers, `_synthesize_and_send()`, `_deliver_voice_reply()`, `handle_voice`, `on_play_voice()`, handler registration.
- `tests/test_tts_synthesize.py` — **create**.
- `tests/test_stt_transcribe_file.py` — **create**.
- `tests/test_audio_io.py` — **create**.
- `tests/test_telegram_voice.py` — **create**.

---

## Task 1: Thread-safe `synthesize()` in TTS

**Files:**
- Modify: `core/voice/tts_engine.py`
- Test: `tests/test_tts_synthesize.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tts_synthesize.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tts_synthesize.py -v`
Expected: FAIL — `AttributeError: module 'core.voice.tts_engine' has no attribute 'synthesize'`

- [ ] **Step 3: Add `_synth_lock` and `synthesize()`**

In `core/voice/tts_engine.py`, add the lock near the other module globals (after `_speak_lock = threading.Lock()`):

```python
_synth_lock = threading.Lock()
```

Add the `synthesize` function above `speak()`:

```python
def synthesize(text):
    """Synthesize speech to a float32 waveform.

    Returns (wav, sample_rate) on success, or None on failure so callers can
    fall back to text. Thread-safe: serializes concurrent model.tts calls via
    _synth_lock (the XTTS model instance is not safe for concurrent use, and a
    Telegram synth can collide with local playback).
    """
    try:
        with _synth_lock:
            model = _load_model()
            ref_path = _get_reference_path()
            config, _ = _get_config()
            sample_rate = config["voice"]["tts"].get("sample_rate", 24000)
            wav = model.tts(text=text, speaker_wav=ref_path, language="en")

        if not isinstance(wav, np.ndarray):
            wav = np.array(wav)
        wav = wav.astype(np.float32)
        if wav.size == 0:
            return None
        peak = max(abs(float(wav.max())), abs(float(wav.min())))
        if peak > 1.0:
            wav = wav / peak
        return wav, sample_rate
    except FileNotFoundError as e:
        print(f"  [TTS: {e}]")
        return None
    except Exception as e:
        print(f"  [TTS synthesis error: {e}]")
        return None
```

- [ ] **Step 4: Refactor `_speak_impl` to reuse `synthesize()`**

Replace the body of `_speak_impl` (the synthesis+normalize block) so it calls `synthesize()`:

```python
def _speak_impl(text):
    """Internal: synthesize and play audio."""
    global _speaking, _current_stream

    if not _speak_lock.acquire(timeout=0.1):
        return  # Another speech is in progress, skip

    try:
        _speaking = True
        import sounddevice as sd

        result = synthesize(text)
        if result is None:
            return
        wav, sample_rate = result

        # Play audio
        sd.play(wav, samplerate=sample_rate)
        sd.wait()

    except Exception as e:
        print(f"  [TTS error — falling back to text only: {e}]")
    finally:
        _speaking = False
        _current_stream = None
        _speak_lock.release()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_tts_synthesize.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add core/voice/tts_engine.py tests/test_tts_synthesize.py
git commit -m "wave 1: extract thread-safe synthesize() from TTS _speak_impl"
```

---

## Task 2: `transcribe_file()` in STT

**Files:**
- Modify: `core/voice/stt_engine.py`
- Test: `tests/test_stt_transcribe_file.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stt_transcribe_file.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_stt_transcribe_file.py -v`
Expected: FAIL — `AttributeError: module 'core.voice.stt_engine' has no attribute 'transcribe_file'`

- [ ] **Step 3: Add `transcribe_file()`**

In `core/voice/stt_engine.py`, add after `transcribe(audio)`:

```python
def transcribe_file(path):
    """Transcribe an audio file (any ffmpeg-decodable format) via faster-whisper.

    faster-whisper decodes the file natively (PyAV), so OGG/Opus voice notes need
    no manual resample. Returns the transcribed text, or None if no speech.
    """
    model = _load_model()

    segments, info = model.transcribe(
        str(path),
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text if text else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_stt_transcribe_file.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/voice/stt_engine.py tests/test_stt_transcribe_file.py
git commit -m "wave 1: add stt_engine.transcribe_file for voice-note files"
```

---

## Task 3: `audio_io.wav_to_ogg` (PCM → OGG/Opus via ffmpeg)

**Files:**
- Create: `core/voice/audio_io.py`
- Test: `tests/test_audio_io.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audio_io.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_audio_io.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.voice.audio_io'`

- [ ] **Step 3: Create `core/voice/audio_io.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_audio_io.py -v`
Expected: PASS (3 passed; the ogg test runs since ffmpeg is present on this box)

- [ ] **Step 5: Commit**

```bash
git add core/voice/audio_io.py tests/test_audio_io.py
git commit -m "wave 1: add audio_io.wav_to_ogg (PCM to OGG/Opus via ffmpeg)"
```

---

## Task 4: Config block + `get_voice_settings()`

**Files:**
- Modify: `core/config/core_config.json`
- Modify: `integrations/telegram_bot.py`
- Test: `tests/test_telegram_voice.py`

- [ ] **Step 1: Add the config block**

In `core/config/core_config.json`, inside the `"voice"` object, add a `"telegram"` key alongside `"tts"` and `"stt"`:

```json
    "telegram": {
      "voice_replies": true,
      "voice_char_cap": 600,
      "max_duration": 300
    }
```

(Place it after the `"stt"` block, still inside `"voice"`. Keep valid JSON — add a comma after the preceding `"stt"` block.)

- [ ] **Step 2: Write the failing test**

Create `tests/test_telegram_voice.py` with this first test (more tests appended in Task 5):

```python
"""Tests for Telegram voice MVP (no GPU / no real Telegram)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock


def test_get_voice_settings_defaults(monkeypatch):
    import core.config
    from integrations import telegram_bot

    monkeypatch.setattr(core.config, "CONFIG", {"voice": {}})
    s = telegram_bot.get_voice_settings()
    assert s["voice_replies"] is True
    assert s["voice_char_cap"] == 600
    assert s["max_duration"] == 300
    assert s["tts_enabled"] is False
    assert s["stt_enabled"] is False


def test_get_voice_settings_reads_config(monkeypatch):
    import core.config
    from integrations import telegram_bot

    monkeypatch.setattr(core.config, "CONFIG", {
        "voice": {
            "tts": {"enabled": True},
            "stt": {"enabled": True},
            "telegram": {"voice_replies": False, "voice_char_cap": 42, "max_duration": 120},
        }
    })
    s = telegram_bot.get_voice_settings()
    assert s["voice_replies"] is False
    assert s["voice_char_cap"] == 42
    assert s["max_duration"] == 120
    assert s["tts_enabled"] is True
    assert s["stt_enabled"] is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_telegram_voice.py::test_get_voice_settings_defaults -v`
Expected: FAIL — `AttributeError: module 'integrations.telegram_bot' has no attribute 'get_voice_settings'`

- [ ] **Step 4: Add imports and `get_voice_settings()` to `telegram_bot.py`**

Replace the top import block of `integrations/telegram_bot.py` with:

```python
"""
Telegram Integration — Bot Handlers & Lifecycle
Runs as a background polling task inside the FastAPI server process.
"""

import asyncio
import logging
import secrets
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from integrations.telegram_config import (
    get_bot_token,
    is_enabled,
    is_allowed,
    get_user_mapping,
    save_user_mapping,
    remove_user_mapping,
)
from core.auth import verify_user
from core.voice import stt_engine, tts_engine, audio_io

logger = logging.getLogger("aegis.telegram.bot")

# Max Telegram message length
TG_MAX_LENGTH = 4096
```

Then add `get_voice_settings()` right after the `TG_MAX_LENGTH` constant:

```python
def get_voice_settings():
    """Read Telegram voice settings from core config, with safe defaults."""
    from core.config import CONFIG
    voice = CONFIG.get("voice", {})
    tg = voice.get("telegram", {})
    return {
        "voice_replies": tg.get("voice_replies", True),
        "voice_char_cap": tg.get("voice_char_cap", 600),
        "max_duration": tg.get("max_duration", 300),
        "tts_enabled": voice.get("tts", {}).get("enabled", False),
        "stt_enabled": voice.get("stt", {}).get("enabled", False),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_telegram_voice.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add core/config/core_config.json integrations/telegram_bot.py tests/test_telegram_voice.py
git commit -m "wave 1: add voice.telegram config block + get_voice_settings"
```

---

## Task 5: Stash helpers + reply delivery + voice handler + callback

**Files:**
- Modify: `integrations/telegram_bot.py`
- Test: `tests/test_telegram_voice.py` (append)

- [ ] **Step 1: Write the failing tests (append to `tests/test_telegram_voice.py`)**

```python
def _make_update_context():
    update = MagicMock()
    update.effective_chat.id = 999
    update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=42))
    update.effective_chat.send_action = AsyncMock()
    context = MagicMock()
    context.bot.send_voice = AsyncMock()
    context.bot.send_chat_action = AsyncMock()
    return update, context


def _settings(**over):
    base = {
        "voice_replies": True,
        "voice_char_cap": 600,
        "max_duration": 300,
        "tts_enabled": True,
        "stt_enabled": True,
    }
    base.update(over)
    return base


def test_stash_bounding_evicts_oldest():
    from integrations import telegram_bot as tb
    tb._PENDING_VOICE.clear()
    for i in range(tb._PENDING_MAX + 10):
        tb._stash_voice(f"t{i}", 1, f"text{i}")
    assert len(tb._PENDING_VOICE) == tb._PENDING_MAX
    assert tb._pop_voice("t0") is None                 # oldest evicted
    assert tb._pop_voice(f"t{tb._PENDING_MAX + 9}") == (1, f"text{tb._PENDING_MAX + 9}")


def test_pop_removes_entry_so_second_tap_is_empty():
    from integrations import telegram_bot as tb
    tb._PENDING_VOICE.clear()
    tb._stash_voice("abc", 5, "hello")
    assert tb._pop_voice("abc") == (5, "hello")
    assert tb._pop_voice("abc") is None


def test_deliver_short_reply_sends_voice(monkeypatch):
    from integrations import telegram_bot as tb
    sent = []

    async def fake_send(bot, chat_id, text):
        sent.append((chat_id, text)); return True

    monkeypatch.setattr(tb, "_synthesize_and_send", fake_send)
    update, context = _make_update_context()
    asyncio.run(tb._deliver_voice_reply(update, context, "hi there", "short reply", _settings()))
    assert sent == [(999, "short reply")]
    assert update.message.reply_text.await_count >= 1


def test_deliver_long_reply_uses_button_no_autosynth(monkeypatch):
    from integrations import telegram_bot as tb
    tb._PENDING_VOICE.clear()
    sent = []

    async def fake_send(bot, chat_id, text):
        sent.append(text); return True

    monkeypatch.setattr(tb, "_synthesize_and_send", fake_send)
    update, context = _make_update_context()
    long_reply = "x" * 50
    asyncio.run(tb._deliver_voice_reply(update, context, "heard", long_reply, _settings(voice_char_cap=10)))

    assert sent == []                                   # no auto synthesis
    _, kwargs = update.message.reply_text.call_args
    assert "reply_markup" in kwargs                     # button attached
    assert len(tb._PENDING_VOICE) == 1
    _, (chat_id, text) = next(iter(tb._PENDING_VOICE.items()))
    assert text == long_reply and chat_id == 999


def test_deliver_voice_disabled_is_text_only(monkeypatch):
    from integrations import telegram_bot as tb
    sent = []

    async def fake_send(bot, chat_id, text):
        sent.append(text); return True

    monkeypatch.setattr(tb, "_synthesize_and_send", fake_send)
    update, context = _make_update_context()
    asyncio.run(tb._deliver_voice_reply(update, context, "h", "reply", _settings(voice_replies=False)))
    assert sent == []
    assert update.message.reply_text.await_count >= 1


def test_on_play_voice_answers_before_synth_and_consumes(monkeypatch):
    from integrations import telegram_bot as tb
    tb._PENDING_VOICE.clear()
    tb._stash_voice("tok", 7, "speak me")
    order = []

    query = MagicMock()
    query.data = "tts:tok"
    query.answer = AsyncMock(side_effect=lambda *a, **k: order.append("answer"))
    query.edit_message_reply_markup = AsyncMock()
    query.message.reply_text = AsyncMock()
    update = MagicMock(); update.callback_query = query
    context = MagicMock(); context.bot.send_chat_action = AsyncMock()

    async def fake_send(bot, chat_id, text):
        order.append(("synth", chat_id, text)); return True

    monkeypatch.setattr(tb, "_synthesize_and_send", fake_send)
    asyncio.run(tb.on_play_voice(update, context))

    assert order[0] == "answer"                          # answered before slow synth
    assert ("synth", 7, "speak me") in order
    assert tb._pop_voice("tok") is None                  # consumed


def test_on_play_voice_expired_token(monkeypatch):
    from integrations import telegram_bot as tb
    tb._PENDING_VOICE.clear()

    query = MagicMock()
    query.data = "tts:missing"
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message.reply_text = AsyncMock()
    update = MagicMock(); update.callback_query = query
    context = MagicMock(); context.bot.send_chat_action = AsyncMock()

    called = []

    async def fake_send(bot, chat_id, text):
        called.append(text); return True

    monkeypatch.setattr(tb, "_synthesize_and_send", fake_send)
    asyncio.run(tb.on_play_voice(update, context))

    query.message.reply_text.assert_awaited()            # expiry message sent
    assert called == []                                  # no synthesis
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_telegram_voice.py -v`
Expected: FAIL — `AttributeError: ... has no attribute '_PENDING_VOICE'` (and friends)

- [ ] **Step 3: Add stash helpers + module-level async helpers to `telegram_bot.py`**

Add after `get_voice_settings()`:

```python
# Bounded store for long-reply text awaiting an opt-in "Play voice" tap.
# token -> (chat_id, reply_text). LRU-evicted at _PENDING_MAX entries.
_PENDING_VOICE: "OrderedDict[str, tuple]" = OrderedDict()
_PENDING_MAX = 50


def _stash_voice(token, chat_id, text):
    """Store reply text keyed by a random token; evict oldest past the cap."""
    _PENDING_VOICE[token] = (chat_id, text)
    _PENDING_VOICE.move_to_end(token)
    while len(_PENDING_VOICE) > _PENDING_MAX:
        _PENDING_VOICE.popitem(last=False)


def _pop_voice(token):
    """Remove and return (chat_id, text) for a token, or None if absent."""
    return _PENDING_VOICE.pop(token, None)


async def _synthesize_and_send(bot, chat_id, text):
    """Synthesize text to OGG and send it as a Telegram voice note.

    Runs blocking TTS/ffmpeg work off the event loop via asyncio.to_thread.
    Returns True on success, False on any failure (caller has already sent the
    text, so a failure degrades silently to text-only).
    """
    tmp_dir = Path(tempfile.gettempdir()) / "aegis_voice"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ogg_path = tmp_dir / f"out_{secrets.token_urlsafe(6)}.ogg"
    try:
        result = await asyncio.to_thread(tts_engine.synthesize, text)
        if result is None:
            return False
        wav, sample_rate = result
        await asyncio.to_thread(audio_io.wav_to_ogg, wav, sample_rate, ogg_path)
        with open(ogg_path, "rb") as fh:
            await bot.send_voice(chat_id=chat_id, voice=fh)
        return True
    except Exception as e:
        logger.warning("Voice synthesis/send failed: %s", e)
        return False
    finally:
        try:
            ogg_path.unlink(missing_ok=True)
        except Exception:
            pass


async def _deliver_voice_reply(update, context, heard, reply, settings):
    """Deliver a chat reply to a voice note as voice + text, honoring the cap."""
    text_out = f"heard: {heard}\n\n{reply}"

    # Voice off (feature disabled or TTS disabled) -> text only.
    if not settings["voice_replies"] or not settings["tts_enabled"]:
        for chunk in _split_message(text_out):
            await update.message.reply_text(chunk)
        return

    if len(reply) <= settings["voice_char_cap"]:
        # Short reply: auto voice note + full text.
        await _synthesize_and_send(context.bot, update.effective_chat.id, reply)
        for chunk in _split_message(text_out):
            await update.message.reply_text(chunk)
    else:
        # Long reply: text now, opt-in Play-voice button on the last chunk.
        chunks = _split_message(text_out)
        for chunk in chunks[:-1]:
            await update.message.reply_text(chunk)
        token = secrets.token_urlsafe(8)
        _stash_voice(token, update.effective_chat.id, reply)
        await update.message.reply_text(
            chunks[-1],
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔊 Play voice", callback_data=f"tts:{token}")]]
            ),
        )


async def on_play_voice(update, context):
    """Handle a 🔊 Play voice button tap."""
    query = update.callback_query
    # Answer immediately — callback queries time out (~15s) while XTTS can take 20-40s.
    await query.answer()

    data = query.data or ""
    token = data.split(":", 1)[1] if ":" in data else ""
    entry = _pop_voice(token)  # pop so a second tap can't re-trigger synthesis

    # Remove the button regardless (spent or expired).
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    if entry is None:
        await query.message.reply_text("That reply expired — send the voice note again.")
        return

    chat_id, text = entry
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
    ok = await _synthesize_and_send(context.bot, chat_id, text)
    if not ok:
        await query.message.reply_text("Sorry — I couldn't generate the voice for that one.")
```

- [ ] **Step 4: Add the `handle_voice` closure inside `_build_handlers`**

In `_build_handlers`, add `handle_voice` after `handle_message` (before the `_route_message` inner function), so it captures `session_manager` and `chat_fn`:

```python
    async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle voice notes — transcribe, chat, reply with voice + text."""
        tg_id = update.effective_user.id

        if not is_allowed(tg_id):
            await update.message.reply_text("Your Telegram account is not authorized.")
            return

        username = get_user_mapping(tg_id)
        if not username:
            await update.message.reply_text(
                "You need to link your account first. Send /start for instructions."
            )
            return

        settings = get_voice_settings()
        if not settings["stt_enabled"]:
            await update.message.reply_text("Voice input is turned off right now.")
            return

        voice = update.message.voice
        if voice and voice.duration and voice.duration > settings["max_duration"]:
            await update.message.reply_text(
                f"That voice note is too long (max {settings['max_duration'] // 60} min). "
                "Send a shorter one?"
            )
            return

        await update.effective_chat.send_action(ChatAction.RECORD_VOICE)

        tmp_dir = Path(tempfile.gettempdir()) / "aegis_voice"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        ogg_path = tmp_dir / f"in_{tg_id}_{update.message.message_id}.ogg"
        heard = None
        try:
            tg_file = await context.bot.get_file(voice.file_id)
            await tg_file.download_to_drive(str(ogg_path))
            heard = await asyncio.to_thread(stt_engine.transcribe_file, ogg_path)
        except Exception as e:
            logger.warning("Voice download/transcribe failed: %s", e)
            await update.message.reply_text("I couldn't process that voice note — try again?")
            return
        finally:
            try:
                ogg_path.unlink(missing_ok=True)
            except Exception:
                pass

        if not heard:
            await update.message.reply_text("I couldn't make out any speech — try again?")
            return

        result = await chat_fn(session_manager, username, heard)
        reply = result.get("response", "") or "(No response)"

        await _deliver_voice_reply(update, context, heard, reply, settings)
```

Then update the `_build_handlers` return statement to include `handle_voice`:

```python
    return cmd_start, cmd_pair, cmd_unpair, handle_command, handle_message, handle_voice
```

- [ ] **Step 5: Register the new handlers in `start_telegram_bot`**

Update the unpack and handler registration in `start_telegram_bot`:

```python
    cmd_start, cmd_pair, cmd_unpair, handle_command, handle_message, handle_voice = _build_handlers(
        session_manager, chat_fn
    )

    app = Application.builder().token(token).build()

    # Register handlers — specific commands first, then catch-all
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pair", cmd_pair))
    app.add_handler(CommandHandler("unpair", cmd_unpair))
    # Catch-all for any other /command — forward to Aegis
    app.add_handler(MessageHandler(filters.COMMAND, handle_command))
    # Voice notes — transcribe + voice reply
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    # Regular text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Play-voice button taps
    app.add_handler(CallbackQueryHandler(on_play_voice, pattern=r"^tts:"))
```

- [ ] **Step 6: Run the voice tests to verify they pass**

Run: `python -m pytest tests/test_telegram_voice.py -v`
Expected: PASS (all voice tests green)

- [ ] **Step 7: Commit**

```bash
git add integrations/telegram_bot.py tests/test_telegram_voice.py
git commit -m "wave 1: telegram voice handler, reply delivery, play-voice callback"
```

---

## Task 6: Full-suite verification + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `python -m pytest -q`
Expected: All previously-passing tests still pass (416 baseline) plus the new voice tests. No failures, no import errors.

- [ ] **Step 2: Byte-check the import graph loads**

Run: `python -c "import integrations.telegram_bot; import core.voice.audio_io; print('imports OK')"`
Expected: `imports OK` (catches syntax/import errors without starting the server)

- [ ] **Step 3: Manual smoke (requires the running server + Telegram, do with the user)**

Only if TTS+STT enabled and `@my_pike_bot` is live:
1. Start the server (`python start.py` / `start.bat`).
2. From Telegram, send a short spoken question as a voice note.
3. Expect: a voice note reply **plus** a text message beginning `heard: ...`.
4. Send a long-answer prompt (e.g. "explain how a suppressor works in detail").
5. Expect: text arrives immediately with a 🔊 Play voice button; tapping it returns a voice note; the button disappears; a second tap (if still visible) says the reply expired.

Note in the session log whether latency/VRAM is acceptable on the 2070.

- [ ] **Step 4: Final commit (if any smoke-driven tweaks were needed)**

```bash
git add -A
git commit -m "wave 1: telegram voice MVP verified"
```

---

## Definition of Done

- `synthesize()`, `transcribe_file()`, `wav_to_ogg()` exist with passing unit tests.
- `voice.telegram` config block present; `get_voice_settings()` reads it with defaults.
- Voice note → transcribe → chat → voice+text reply works end-to-end.
- Long replies gated behind the Play-voice button; short replies auto-synthesize.
- Auth gates + duration cap enforced on the voice handler.
- All TTS/STT/ffmpeg blocking calls run via `asyncio.to_thread`.
- Failures degrade to text; no handler crashes the bot.
- Full pytest suite green.
