"""Fans a notify result out to its channels. Telegram is best-effort: if the
bot app or the user's chat_id is unavailable, we degrade to the in-app
notification queue and never raise."""

import logging

logger = logging.getLogger("aegis.heartbeat")


class Notifier:
    """Delivers a heartbeat job's user-facing push to one or both channels.

    Injected with two accessors so it is testable without a real bot:
    ``get_telegram_app`` returns the live ``Application`` or ``None``, and
    ``get_chat_id`` maps a user_id to its Telegram chat_id or ``None``.
    """

    def __init__(self, session_manager, get_telegram_app, get_chat_id):
        self._sm = session_manager
        self._get_app = get_telegram_app
        self._get_chat_id = get_chat_id

    async def push(self, user_id: str, title: str, body: str, channels: list[str]) -> None:
        """Push *title* / *body* to every channel in *channels* for *user_id*.

        Supported channel names: ``"notification"`` (in-app queue) and
        ``"telegram"``.  When only ``"telegram"`` is requested and it is
        unavailable (no app or no chat_id), the in-app notification fires as a
        fallback.  When both channels are requested and telegram succeeds, both
        still fire — explicit fan-out is honoured.  Telegram failures never
        propagate; they log and degrade.
        """
        channels = channels or ["notification"]
        telegram_ok = False
        if "telegram" in channels:
            telegram_ok = await self._push_telegram(user_id, title, body)
        if "notification" in channels or (not telegram_ok and "telegram" in channels):
            self._push_notification(user_id, title, body)

    def _push_notification(self, user_id: str, title: str, body: str) -> None:
        try:
            sess = self._sm.get(user_id)
            sess.notification_service.add(type="heartbeat", title=title, body=body)
        except Exception:
            logger.exception("heartbeat notification push failed")

    async def _push_telegram(self, user_id: str, title: str, body: str) -> bool:
        try:
            app = self._get_app()
            chat_id = self._get_chat_id(user_id)
            if not app or not chat_id:
                logger.info("telegram push unavailable for %s; degrading", user_id)
                return False
            text = f"{title}\n\n{body}" if body else title
            await app.bot.send_message(chat_id=chat_id, text=text)
            return True
        except Exception:
            logger.exception("heartbeat telegram push failed")
            return False
