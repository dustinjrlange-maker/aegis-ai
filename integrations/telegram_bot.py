"""
Telegram Integration — Bot Handlers & Lifecycle
Runs as a background polling task inside the FastAPI server process.
"""

import logging
from typing import Callable

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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

logger = logging.getLogger("aegis.telegram.bot")

# Max Telegram message length
TG_MAX_LENGTH = 4096


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

    return cmd_start, cmd_pair, cmd_unpair, handle_command, handle_message


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

    cmd_start, cmd_pair, cmd_unpair, handle_command, handle_message = _build_handlers(
        session_manager, chat_fn
    )

    app = Application.builder().token(token).build()

    # Register handlers — specific commands first, then catch-all
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pair", cmd_pair))
    app.add_handler(CommandHandler("unpair", cmd_unpair))
    # Catch-all for any other /command — forward to Aegis
    app.add_handler(MessageHandler(filters.COMMAND, handle_command))
    # Regular text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Initialize and start polling (non-blocking, shares event loop)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    logger.info("Telegram bot started (polling mode)")
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
