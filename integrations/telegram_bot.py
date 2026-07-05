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


# Bounded store for long-reply text awaiting an opt-in "Play voice" tap.
# token -> (chat_id, reply_text). LRU-evicted at _PENDING_MAX entries.
_PENDING_VOICE = OrderedDict()
_PENDING_MAX = 50

# Running python-telegram-bot Application instance, set on bot start.
_APPLICATION = None


def _set_application(app):
    global _APPLICATION
    _APPLICATION = app


def get_application():
    """The running python-telegram-bot Application, or None if not started.
    Used by the heartbeat notifier to push proactively."""
    return _APPLICATION


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


def _split_message(text: str) -> list[str]:
    """Split a long message into chunks that fit Telegram's 4096-char limit.

    Splits at sentence boundaries when possible.
    """
    if len(text) <= TG_MAX_LENGTH:
        return [text]

    chunks = []
    while text:
        if len(text) <= TG_MAX_LENGTH:
            chunks.append(text)
            break

        # Try to split at a sentence boundary
        split_at = TG_MAX_LENGTH
        for sep in [". ", "! ", "? ", "\n"]:
            idx = text.rfind(sep, 0, TG_MAX_LENGTH)
            if idx > 0:
                split_at = idx + len(sep)
                break
        else:
            # No sentence boundary — split at last space
            idx = text.rfind(" ", 0, TG_MAX_LENGTH)
            if idx > 0:
                split_at = idx + 1

        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip()

    return chunks


def _build_handlers(session_manager, chat_fn: Callable):
    """Build Telegram command and message handlers."""

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start — welcome and pairing instructions."""
        tg_id = update.effective_user.id
        username = get_user_mapping(tg_id)
        if username:
            await update.message.reply_text(
                f"You're linked to Aegis account '{username}'. Send me a message to chat!"
            )
        else:
            await update.message.reply_text(
                "Welcome to Aegis AI!\n\n"
                "To link your Telegram account, send:\n"
                "/pair <username> <passcode>\n\n"
                "Use the same credentials as the web UI."
            )

    async def cmd_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /pair <username> <passcode> — link Telegram to Aegis account."""
        tg_id = update.effective_user.id

        if not is_allowed(tg_id):
            await update.message.reply_text("Your Telegram account is not authorized.")
            return

        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text("Usage: /pair <username> <passcode>")
            return

        username = args[0].lower().strip()
        passcode = args[1]

        # Delete the message containing the passcode for security
        try:
            await update.message.delete()
        except Exception:
            pass  # Bot may lack delete permission in some chats

        if verify_user(username, passcode):
            save_user_mapping(tg_id, username)
            await update.effective_chat.send_message(
                f"Linked to Aegis account '{username}'. You can now chat!"
            )
        else:
            await update.effective_chat.send_message(
                "Invalid username or passcode. Please try again."
            )

    async def cmd_unpair(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /unpair — unlink Telegram from Aegis account."""
        tg_id = update.effective_user.id
        username = get_user_mapping(tg_id)
        if username:
            remove_user_mapping(tg_id)
            await update.message.reply_text(
                f"Unlinked from Aegis account '{username}'."
            )
        else:
            await update.message.reply_text("You're not linked to any Aegis account.")

    async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Catch-all for unrecognized /commands — forward to Aegis protocols."""
        tg_id = update.effective_user.id
        username = get_user_mapping(tg_id)
        if not username:
            await update.message.reply_text(
                "You need to link your account first. Send /start for instructions."
            )
            return

        # Forward the full command text (including the /) to the chat pipeline
        await _route_message(update, username, update.message.text, chat_fn)

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular text messages — route through Aegis chat pipeline."""
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

        await _route_message(update, username, update.message.text, chat_fn)

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

    async def _route_message(update: Update, username: str, text: str, chat_fn: Callable):
        """Send a message through the Aegis pipeline and reply."""
        # Show typing indicator while LLM generates
        await update.effective_chat.send_action(ChatAction.TYPING)

        result = await chat_fn(session_manager, username, text.strip())
        reply = result.get("response", "")

        if not reply:
            reply = "(No response)"

        # Split long messages
        for chunk in _split_message(reply):
            await update.message.reply_text(chunk)

    return cmd_start, cmd_pair, cmd_unpair, handle_command, handle_message, handle_voice


async def start_telegram_bot(session_manager, chat_fn: Callable):
    """Start the Telegram bot in polling mode (non-blocking).

    Returns the Application instance for later shutdown.
    Returns None if Telegram is not enabled.
    """
    if not is_enabled():
        logger.info("Telegram integration disabled or no token configured.")
        return None

    token = get_bot_token()
    if not token:
        logger.warning("Telegram enabled but no bot token set.")
        return None

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

    # Initialize and start polling (non-blocking, shares event loop)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    logger.info("Telegram bot started (polling mode)")
    _set_application(app)
    return app


async def stop_telegram_bot(app):
    """Stop the Telegram bot gracefully."""
    if app is None:
        return
    try:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logger.info("Telegram bot stopped")
    except Exception as e:
        logger.warning("Error stopping Telegram bot: %s", e)
