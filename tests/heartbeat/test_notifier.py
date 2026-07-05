import asyncio
from core.heartbeat.notifier import Notifier


class _FakeNotifSvc:
    def __init__(self): self.added = []
    def add(self, type, title, body): self.added.append((type, title, body))


class _FakeSession:
    def __init__(self): self.notification_service = _FakeNotifSvc()


class _FakeSessionManager:
    def __init__(self, session): self._s = session
    def get(self, user_id): return self._s


class _FakeBot:
    def __init__(self): self.sent = []
    async def send_message(self, chat_id, text): self.sent.append((chat_id, text))


class _FakeApp:
    def __init__(self): self.bot = _FakeBot()


def test_notification_channel_adds_to_service():
    sess = _FakeSession()
    n = Notifier(_FakeSessionManager(sess), get_telegram_app=lambda: None,
                 get_chat_id=lambda u: None)
    asyncio.run(n.push("switch", "Title", "Body", ["notification"]))
    assert sess.notification_service.added == [("heartbeat", "Title", "Body")]


def test_telegram_channel_sends_message():
    sess = _FakeSession()
    app = _FakeApp()
    n = Notifier(_FakeSessionManager(sess), get_telegram_app=lambda: app,
                 get_chat_id=lambda u: "12345")
    asyncio.run(n.push("switch", "Title", "Body", ["telegram"]))
    assert app.bot.sent == [("12345", "Title\n\nBody")]


def test_telegram_missing_degrades_to_notification():
    sess = _FakeSession()
    n = Notifier(_FakeSessionManager(sess), get_telegram_app=lambda: None,
                 get_chat_id=lambda u: None)
    asyncio.run(n.push("switch", "T", "B", ["telegram"]))
    assert sess.notification_service.added == [("heartbeat", "T", "B")]
