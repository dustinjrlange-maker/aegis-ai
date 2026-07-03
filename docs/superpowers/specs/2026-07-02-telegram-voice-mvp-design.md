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
   **Synthesis lock:** `synthesize()` acquires a dedicated `_synth_lock` around the
   `model.tts` call — the XTTS model instance is not safe for concurrent calls, and two
   quick voice notes (or a Telegram synth colliding with local playback) would otherwise
   run `model.tts` concurrently on the 8GB card. `_speak_impl` gets this protection for
   free by calling `synthesize()`; its existing `_speak_lock` continues to guard playback
   only.

2. **`core/voice/audio_io.py` — new module**
   - `wav_to_ogg(wav, sample_rate, out_path) -> Path`: pipe the raw float32 PCM samples
     to an `ffmpeg` subprocess via stdin
     (`ffmpeg -f f32le -ar <sr> -ac 1 -i - -c:a libopus <out>.ogg`) and encode to
     OGG/Opus (Telegram voice-note format). ffmpeg is already present on the box
     (torchcodec dependency). No new Python dependency. Raises on non-zero exit; callers
     catch and degrade to text.
   - **ffmpeg resolution is self-contained:** today ffmpeg resolves partly because
     `tts_engine.py` prepends the WinGet shared-build dir to `PATH` at import time.
     `audio_io` must not depend on that side effect (tests import it standalone): it
     resolves ffmpeg itself via `shutil.which("ffmpeg")` with the known WinGet shared
     dir as an explicit fallback.

3. **`core/voice/stt_engine.py` — `transcribe_file(path) -> str | None`**
   faster-whisper decodes OGG/Opus natively via PyAV, so no manual resample is needed.
   Calls `model.transcribe(str(path), ...)` reusing the existing VAD parameters. Returns
   the joined text, or `None` if no speech.

All Telegram wiring (voice handler, reply delivery, play-voice callback) stays in
`integrations/telegram_bot.py`.

## Data Flow

```
voice note
  → auth gates: is_allowed(tg_id) + get_user_mapping(tg_id)   (mirrors text handler
    exactly — voice must not be an auth bypass)
  → duration gate: voice.duration > 300s → friendly rejection, no download
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
          stash[(chat_id, sent.message_id)] = reply_text
          on button tap (callback "tts:<chat_id>:<message_id>"):
              answer() immediately → pop stash → RECORD_VOICE action
              → synthesize(reply_text) → wav_to_ogg → bot.send_voice(...)
```

## Key Details

### Non-blocking execution
STT and TTS are blocking, GPU-bound calls. The Telegram bot shares the FastAPI event
loop, so a blocking call would freeze the whole server. Every `transcribe_file`,
`synthesize`, and `wav_to_ogg` call in the handlers is wrapped in
`await asyncio.to_thread(...)`. While working, the handler shows the
`ChatAction.RECORD_VOICE` indicator. Concurrent synthesis requests serialize on
`_synth_lock` inside `synthesize()` (see Architecture #1).

### Auth + input gates on the voice handler
The voice handler mirrors the text handler's gates exactly: `is_allowed(tg_id)` first,
then `get_user_mapping(tg_id)` (unpaired → pairing instructions). Without this, voice
notes would bypass authorization entirely. Additionally, incoming voice notes with
`voice.duration > 300` seconds are rejected with a friendly message **before download**,
so a stray 20-minute recording can't pin Whisper/the GPU.

### Play-voice button
- The text reply is stored in a bounded in-memory dict keyed by
  **`(chat_id, message_id)`** — Telegram `message_id`s are per-chat, not global, so
  keying by `message_id` alone could collide across chats and leak one user's reply to
  another.
- Inline keyboard button carries `callback_data = "tts:<chat_id>:<message_id>"` (still
  well under Telegram's 64-byte limit).
- A `CallbackQueryHandler` (pattern `^tts:`) processes taps in this **strict order**:
  1. `answer()` the callback query **immediately** — Telegram callback queries time out
     in ~15s while XTTS synthesis can take 20–40s; answering late leaves the user with a
     stuck spinner and Telegram may redeliver the callback.
  2. **Pop** the stash entry (not just read) — a second tap finds nothing and gets the
     expiry message instead of queuing a duplicate synthesis. Also remove the inline
     keyboard from the message (`edit_message_reply_markup`).
  3. Show `RECORD_VOICE` chat action, then synthesize → `wav_to_ogg` → `send_voice`,
     all via `asyncio.to_thread`.
- The dict is capped (~50 entries) with LRU eviction (`collections.OrderedDict`,
  `move_to_end` + `popitem(last=False)`). If a token is evicted (or already consumed)
  before tapping, the callback replies "that reply expired — send the voice note again."

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
- Play-voice callback: stashed token → `answer()` called before synthesis, entry popped,
  `send_voice` invoked; missing/evicted token → expiry message, no crash; **second tap on
  the same button → expiry message, no duplicate synthesis**.
- Stash keying: entries under `(chat_id, message_id)` — same `message_id` in two chats
  does not collide.
- Token-store bounding and LRU eviction.
- Auth gates: unauthorized `tg_id` and unpaired user get the same rejections as the text
  handler; voice note with `duration > 300` rejected without download.
- `synthesize()` serializes on `_synth_lock` (two threads, mocked model, assert no
  concurrent entry).
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
