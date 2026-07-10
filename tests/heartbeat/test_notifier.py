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


def test_telegram_private_body_withheld():
    """Push bodies traverse Telegram's servers — private content (email
    subjects like 'Bank account statement', med reminders) must be replaced
    with a generic notice, full detail stays in-app (2026-07-09 audit)."""
    sess = _FakeSession()
    app = _FakeApp()
    n = Notifier(_FakeSessionManager(sess), get_telegram_app=lambda: app,
                 get_chat_id=lambda u: "12345")
    body = "Important email from RBC: 'Your bank account statement is ready'"
    asyncio.run(n.push("switch", "Inbox scan", body, ["telegram"]))
    assert len(app.bot.sent) == 1
    _, text = app.bot.sent[0]
    assert "bank account" not in text.lower()
    assert "Inbox scan" in text
    assert "Aegis" in text          # points the user at the app for details


def test_telegram_clean_body_sent_verbatim():
    sess = _FakeSession()
    app = _FakeApp()
    n = Notifier(_FakeSessionManager(sess), get_telegram_app=lambda: app,
                 get_chat_id=lambda u: "12345")
    asyncio.run(n.push("switch", "Morning briefing", "3 tasks today.", ["telegram"]))
    assert app.bot.sent == [("12345", "Morning briefing\n\n3 tasks today.")]


def test_inapp_notification_keeps_full_private_body():
    """The privacy guard is Telegram-only — in-app keeps the real content."""
    sess = _FakeSession()
    n = Notifier(_FakeSessionManager(sess), get_telegram_app=lambda: None,
                 get_chat_id=lambda u: None)
    body = "Your bank account statement is ready"
    asyncio.run(n.push("switch", "Inbox scan", body, ["notification"]))
    assert sess.notification_service.added == [("heartbeat", "Inbox scan", body)]
