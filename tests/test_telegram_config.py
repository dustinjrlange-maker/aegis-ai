"""Tests for integrations/telegram_config helpers."""

import integrations.telegram_config as tc


def test_get_chat_id_for_reverse_lookup(monkeypatch):
    """get_chat_id_for maps an Aegis username back to its Telegram chat_id (int)."""
    monkeypatch.setattr(
        tc, "_load_config",
        lambda: {"user_mappings": {"12345": "switch", "67890": "krunch"}},
    )
    assert tc.get_chat_id_for("switch") == 12345
    assert tc.get_chat_id_for("krunch") == 67890


def test_get_chat_id_for_unknown_returns_none(monkeypatch):
    """Unmapped usernames return None."""
    monkeypatch.setattr(
        tc, "_load_config",
        lambda: {"user_mappings": {"12345": "switch"}},
    )
    assert tc.get_chat_id_for("nobody") is None


def test_get_chat_id_for_empty_mappings(monkeypatch):
    """No mappings at all returns None, no raise."""
    monkeypatch.setattr(tc, "_load_config", lambda: {})
    assert tc.get_chat_id_for("switch") is None


def test_is_allowed_empty_whitelist_denies_everyone(monkeypatch):
    """FAIL-CLOSED (2026-07-09 audit): the old 'open mode' let anyone who
    discovered the bot token talk to the bot and probe /pair. An empty
    whitelist now denies all — enable by adding your ID to telegram.json."""
    monkeypatch.setattr(tc, "_load_config",
                        lambda: {"allowed_telegram_ids": []})
    assert tc.is_allowed(123809272) is False


def test_is_allowed_listed_id_allowed(monkeypatch):
    monkeypatch.setattr(tc, "_load_config",
                        lambda: {"allowed_telegram_ids": [123809272]})
    assert tc.is_allowed(123809272) is True
    assert tc.is_allowed(999) is False
