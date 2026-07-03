# Telegram Voice MVP — Design (Wave 1)

**Date:** 2026-07-02
**Status:** Approved, ready for implementation plan
**Branch:** `feature/telegram-voice-mvp`

## Goal

Send Pike a Telegram voice note from anywhere → get a spoken reply back. This is the
fastest path to "some version of Jarvis in my ear" — it needs **no TLS and no new
infrastructure** because the Telegram bot already pipes text into `process_chat`. Phone
+ earbud + Telegram = talking to Pike from anywhere on today's stack.

## Scope

**In scope**
- Telegram voice note → Whisper STT → `process_chat` → reply.
- Reply delivered as **voice + text** (voice note plus the full text, prefixed with what
  Pike heard).
- Short replies auto-synthesize a voice note; long replies send text immediately with an
  opt-in **🔊 Play voice** button so the user decides per-message whether the synthesis
  wait is worth it.

**Out of scope (deferred)**
- `/api/tts` HTTP endpoint and browser audio playback → Wave 4.
- Wake word / continuous conversation loop → Wave 4.
- VRAM arbitration changes → existing Command protocol is sufficient for single-user MVP.

## Approach

Approach A — a thin voice layer on the existing bot. Reuse STT, TTS, and `process_chat`
untouched except for extracting a reusable synthesis function. All Telegram glue lives in
`integrations/telegram_bot.py`.

## Architecture

Three small additions; everything else is reused:

1. **`core/voice/tts_engine.py` — `synthesize(text) -> (wav, sample_rate) | None`**
   Extract the pure-synthesis half of the existing `_speak_impl` (load model, resolve
   reference wav, `model.tts`, normalize). `_speak_impl` is refactored to call
   `synthesize()` then play — no behavior change to local playback. Returns `None` on
   failure (missing reference, model error) so callers can fall back to text.

2. **`core/voice/audio_io.py` — new module**
   - `wav_to_ogg(wav, sample_rate, out_path) -> Path`: pipe the raw float32 PCM samples
     to an `ffmpeg` subprocess via stdin
     (`ffmpeg -f f32le -ar <sr> -ac 1 -i - -c:a libopus <out>.ogg`) and encode to
     OGG/Opus (Telegram voice-note format). ffmpeg is already present on the box
     (torchcodec dependency). No new Python dependency. Raises on non-zero exit; callers
     catch and degrade to text.

3. **`core/voice/stt_engine.py` — `transcribe_file(path) -> str | None`**
   faster-whisper decodes OGG/Opus natively via PyAV, so no manual resample is needed.
   Calls `model.transcribe(str(path), ...)` reusing the existing VAD parameters. Returns
   the joined text, or `None` if no speech.

All Telegram wiring (voice handler, reply delivery, play-voice callback) stays in
`integrations/telegram_bot.py`.

## Data Flow

```
voice note
  → download to temp .ogg
  → stt_engine.transcribe_file()  →  heard_text
  → process_chat(session_manager, username, heard_text)  →  reply_text
  → deliver:
      reply_text length ≤ voice_char_cap:
          synthesize(reply_text) → wav_to_ogg → bot.send_voice(...)
          bot.send_message("heard: <heard_text>\n\n<reply_text>")
      reply_text length > voice_char_cap:
          sent = bot.send_message("heard: <heard_text>\n\n<reply_text>",
                                   reply_markup=[🔊 Play voice])
          stash[sent.message_id] = reply_text
          on button tap (callback "tts:<message_id>"):
              synthesize(reply_text) → wav_to_ogg → bot.send_voice(...)
```

## Key Details

### Non-blocking execution
STT and TTS are blocking, GPU-bound calls. The Telegram bot shares the FastAPI event
loop, so a blocking call would freeze the whole server. Every `transcribe_file`,
`synthesize`, and `wav_to_ogg` call in the handlers is wrapped in
`await asyncio.to_thread(...)`. While working, the handler shows the
`ChatAction.RECORD_VOICE` indicator.

### Play-voice button
- The text reply is stored in a bounded in-memory dict keyed by the **sent message's
  `message_id`**.
- Inline keyboard button carries `callback_data = "tts:<message_id>"` (well under
  Telegram's 64-byte limit).
- A `CallbackQueryHandler` (pattern `^tts:`) looks up the stashed text, synthesizes, and
  sends the voice note. It answers the callback query (removes the "loading" spinner) and
  may edit the button to a disabled/"sent" state.
- The dict is capped (~50 entries) with LRU eviction (`collections.OrderedDict`,
  `move_to_end` + `popitem(last=False)`). If a token is evicted before tapping, the
  callback replies "that reply expired — send the voice note again."

### Config
New block in `core/config/core_config.json` under `voice`:
```json
"telegram": { "voice_replies": true, "voice_char_cap": 600 }
```
- Feature gated on `tts.enabled && stt.enabled`. If TTS is off but STT is on, voice notes
  are still transcribed and answered with text (no voice reply).
- `voice_char_cap` (default 600) is the short/long threshold.
- A helper reads these with safe defaults so a missing block behaves as
  `voice_replies=true, cap=600`.

### Temp files
Voice downloads and rendered ogg files are written under a temp directory and `unlink`ed
in a `finally` block, whether or not delivery succeeded.

## Error Handling (graceful degradation)

Per CLAUDE.md — a failed feature must never crash the agent:
- **Empty / failed STT** → reply "I couldn't make out any speech — try again?" (text).
- **TTS `synthesize` returns None, or `wav_to_ogg`/ffmpeg fails** → log the error, send
  the **text reply only** (the text always exists). No crash.
- **Voice download failure** → friendly error reply, logged.
- **Evicted play-voice token** → "that reply expired — send the voice note again."
- All handler bodies wrapped so an unexpected exception logs and sends a generic failure
  message rather than bubbling into the bot loop.

## Testing

No GPU or model loads in tests (CLAUDE.md rule — skip model-dependent paths):
- `wav_to_ogg` on a synthetic sine-wave numpy array → asserts a non-empty OGG file with
  an OggS header. Skipped if `ffmpeg` is not on PATH.
- Reply-routing with `synthesize`/`transcribe_file` mocked and fake `Update`/`Context`
  objects:
  - reply ≤ cap → `send_voice` invoked, text sent.
  - reply > cap → no `send_voice`; inline keyboard attached; text stashed under
    `message_id`.
- Play-voice callback: stashed token → `send_voice` invoked; missing/evicted token →
  expiry message, no crash.
- Token-store bounding and LRU eviction.
- `transcribe_file` / `synthesize` happy paths that require a loaded model are marked
  `@pytest.mark.skip`.

## VRAM Note

`base.en` (~1 GB) and XTTS-v2 (~2 GB) load lazily on the first voice note and stay warm
alongside qwen3:8b. Acceptable for single-user MVP. The Command protocol's existing VRAM
arbitration is available if pressure appears; no changes in Wave 1.

## Files Touched

- `core/voice/tts_engine.py` — add `synthesize()`, refactor `_speak_impl` to use it.
- `core/voice/stt_engine.py` — add `transcribe_file()`.
- `core/voice/audio_io.py` — new: `wav_to_ogg()`.
- `integrations/telegram_bot.py` — voice handler, reply delivery, play-voice callback,
  handler registration.
- `core/config/core_config.json` — add `voice.telegram` block.
- `tests/test_telegram_voice.py`, `tests/test_audio_io.py` — new.

No new Python dependencies (raw PCM is piped to the existing ffmpeg binary).
