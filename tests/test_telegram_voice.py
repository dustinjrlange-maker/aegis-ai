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
