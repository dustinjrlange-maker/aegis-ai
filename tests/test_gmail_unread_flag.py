"""Tests: gmail_list_messages surfaces the `unread` boolean from labelIds."""
import pytest
from unittest.mock import MagicMock, patch


def _make_fake_service(label_ids):
    """Return a minimal fake gmail service object.

    Supports the call chain:
        service.users().messages().list(userId=..., q=..., maxResults=...).execute()
        service.users().messages().get(userId=..., id=..., format=..., metadataHeaders=...).execute()
    """
    # The message returned by .get()
    fake_msg = {
        "id": "msg-001",
        "labelIds": label_ids,
        "snippet": "Hello world",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Test Subject"},
                {"name": "From", "value": "sender@example.com"},
                {"name": "Date", "value": "Mon, 07 Jul 2026 10:00:00 +0000"},
            ]
        },
    }

    # Stub for .list()
    list_execute = MagicMock(return_value={"messages": [{"id": "msg-001"}]})
    list_call = MagicMock()
    list_call.execute = list_execute

    # Stub for .get()
    get_execute = MagicMock(return_value=fake_msg)
    get_call = MagicMock()
    get_call.execute = get_execute

    messages_mock = MagicMock()
    messages_mock.list.return_value = list_call
    messages_mock.get.return_value = get_call

    users_mock = MagicMock()
    users_mock.messages.return_value = messages_mock

    service = MagicMock()
    service.users.return_value = users_mock
    return service


@patch("core.protocols.google_tools._get_gmail_service")
def test_unread_true_when_unread_label_present(mock_get_service):
    """Message with UNREAD in labelIds → unread=True."""
    from core.protocols.google_tools import gmail_list_messages

    mock_get_service.return_value = _make_fake_service(["INBOX", "UNREAD", "CATEGORY_PERSONAL"])
    result = gmail_list_messages(creds=MagicMock())

    assert len(result) == 1
    msg = result[0]
    assert msg["unread"] is True
    # Sanity-check other fields still present
    assert msg["id"] == "msg-001"
    assert msg["subject"] == "Test Subject"
    assert msg["sender"] == "sender@example.com"


@patch("core.protocols.google_tools._get_gmail_service")
def test_unread_false_when_unread_label_absent(mock_get_service):
    """Message without UNREAD in labelIds → unread=False."""
    from core.protocols.google_tools import gmail_list_messages

    mock_get_service.return_value = _make_fake_service(["INBOX", "CATEGORY_PERSONAL"])
    result = gmail_list_messages(creds=MagicMock())

    assert len(result) == 1
    assert result[0]["unread"] is False


@patch("core.protocols.google_tools._get_gmail_service")
def test_unread_false_when_label_ids_missing(mock_get_service):
    """Message with no labelIds key at all → unread=False (safe default)."""
    from core.protocols.google_tools import gmail_list_messages

    # _make_fake_service passes empty list → simulates missing key via get()
    mock_get_service.return_value = _make_fake_service([])
    result = gmail_list_messages(creds=MagicMock())

    assert len(result) == 1
    assert result[0]["unread"] is False


@patch("core.protocols.google_tools._get_gmail_service")
def test_gmail_mark_unread_calls_modify_with_add_unread_label(mock_get_service):
    """gmail_mark_unread calls messages().modify() with addLabelIds: ['UNREAD']."""
    from core.protocols.google_tools import gmail_mark_unread

    fake_service = _make_fake_service([])
    mock_get_service.return_value = fake_service

    result = gmail_mark_unread(creds=MagicMock(), message_id="msg-001")

    assert result == {"ok": True}
    # Verify the modify call was made with the correct body
    modify_mock = fake_service.users().messages().modify
    modify_mock.assert_called_once_with(
        userId="me",
        id="msg-001",
        body={"addLabelIds": ["UNREAD"]},
    )
