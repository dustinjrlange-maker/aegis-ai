"""Regression tests for the 2026-07-09 email incident.

The incident: "send an email from my personal email to the switch stitch
email ... body to say: <exact text>" was drafted from the WRONG account, to a
HALLUCINATED address, with a REWRITTEN body — then sent on a message that
began with "No".

Fixes under test: fail-closed send confirmation, recipient grounding against
known addresses, verbatim dictated bodies, explicit From-account resolution.
"""
import json
import logging

import core.email_assistant as ea
from core.accounts.manager import AccountManager
from core.protocols import google_tools as gt
from tests.test_email_ops import (
    _FakeSession,
    _pending_reply,
    _proto,
    _session_with_accounts,
    _wire_send_classifier,
)

INCIDENT_TURN_1 = (
    "hey pike, can you send an email from my personal email to the switch "
    "stitch email just as a test, subject body saying Practical test and then "
    "the body of the email to say: if this works we can start using this more"
)
INCIDENT_TURN_3 = (
    "No I want you to send it from my personal email to the switch stitch email"
)


def _incident_accounts(tmp_path):
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": [
        {"id": "google-personal", "label": "Personal",
         "email": "dustin.jr.lange@gmail.com", "is_default": True,
         "represent_as": {"name": "Dustin", "signoff": "Dustin"}},
        {"id": "google-stitch", "label": "SwitchStitch",
         "email": "theswitchstitch@gmail.com",
         "represent_as": {"name": "Switch", "signoff": "Switch"}},
    ]}), encoding="utf-8")
    return AccountManager(tmp_path)


def _capture_draft_new(monkeypatch):
    """Monkeypatch ea.draft_new to capture every call (kwargs included)."""
    calls = []

    def fake_draft_new(session, to, intent, account_id=None,
                       subject_hint=None, body_verbatim=None, **kw):
        calls.append({"to": to, "intent": intent, "account_id": account_id,
                      "subject_hint": subject_hint,
                      "body_verbatim": body_verbatim})
        return {"success": True, "draft_id": f"d{len(calls)}", "to": to,
                "subject": subject_hint or "LLM SUBJECT",
                "body": body_verbatim or "LLM COMPOSED BODY"}

    monkeypatch.setattr(ea, "draft_new", fake_draft_new)
    return calls


def test_send_not_fired_by_correction_starting_with_no(monkeypatch):
    """The exact incident message: a correction that CONTAINS 'send it' but
    begins with 'No' must never transmit the held draft."""
    p = _proto(_FakeSession())
    called = _wire_send_classifier(monkeypatch, p)
    p.process_input(INCIDENT_TURN_3, {})
    assert called["sent"] is False
    assert p._pending is not None


def test_send_confirmation_must_be_standalone(monkeypatch):
    """Only a short, explicit send command may transmit. Sentences that merely
    contain a send word (corrections, instructions) re-confirm instead."""
    good = ["send it", "Send it.", "yes send it", "ok, send it now",
            "ship it", "yeah, send it"]
    bad = [INCIDENT_TURN_3,
           "wrong, I said from my personal email",
           "can you send it to bob instead",
           "I want you to send it after fixing the subject"]
    for text in good:
        p = _proto(_FakeSession())
        called = _wire_send_classifier(monkeypatch, p)
        p.process_input(text, {})
        assert called["sent"] is True, f"should have sent on {text!r}"
    for text in bad:
        p = _proto(_FakeSession())
        called = _wire_send_classifier(monkeypatch, p)
        p.process_input(text, {})
        assert called["sent"] is False, f"must NOT send on {text!r}"
        assert p._pending is not None


def test_reconfirm_prompt_states_from_and_to(tmp_path, monkeypatch):
    """The re-confirm question must show WHICH account the draft goes out as,
    so a wrong From is visible before the irreversible send."""
    p = _proto(_session_with_accounts(_incident_accounts(tmp_path)))
    _pending_reply(p)
    p._pending["account_id"] = "google-stitch"
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=send | REF=- | INSTRUCTION=-")
    result = p.process_input("yeah ok do it", {})
    assert "SwitchStitch" in result["response"]
    assert "theswitchstitch@gmail.com" in result["response"]
    assert "John <j@x.ca>" in result["response"]


def test_send_correction_naming_other_account_switches(tmp_path, monkeypatch):
    """'...send it from my personal email...' while the pending draft is under
    another account must REDRAFT under the named account, not send."""
    p = _proto(_session_with_accounts(_incident_accounts(tmp_path)))
    p._pending = {"draft_id": "old1", "kind": "new", "message_id": None,
                  "to": "theswitchstitch@gmail.com", "subject": "Practical test",
                  "intent": INCIDENT_TURN_1, "account_id": "google-stitch",
                  "subject_hint": "Practical test",
                  "body_verbatim": "if this works we can start using this more"}
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=send | REF=- | INSTRUCTION=-")
    monkeypatch.setattr(gt, "gmail_delete_draft", lambda creds, draft_id: None)
    sent = {"called": False}
    monkeypatch.setattr(ea, "send_draft",
                        lambda session, draft_id, account_id=None:
                        sent.update(called=True) or {"success": True})
    calls = _capture_draft_new(monkeypatch)
    result = p.process_input(INCIDENT_TURN_3, {})
    assert sent["called"] is False
    assert calls, "draft must be recreated under the corrected account"
    assert calls[-1]["account_id"] == "google-personal"
    assert calls[-1]["body_verbatim"] == "if this works we can start using this more"
    assert p._pending["account_id"] == "google-personal"
    assert "Personal" in result["response"]


def test_new_grounds_near_miss_recipient_to_known_address(tmp_path, monkeypatch):
    """A classifier-invented address the user never typed must be repaired to
    the closest KNOWN address (linked account) instead of trusted blindly."""
    p = _proto(_session_with_accounts(_incident_accounts(tmp_path)))
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(
        ea, "_llm", lambda messages, **kw:
        "ACTION=new | REF=- | TO=switchstitch@gmail.com | ACCOUNT=- | INSTRUCTION=test note")
    calls = _capture_draft_new(monkeypatch)
    p.process_input("send a test email to the switch stitch email saying hi", {})
    assert calls
    assert calls[-1]["to"] == "theswitchstitch@gmail.com"


def test_new_hallucinated_unknown_recipient_asks(tmp_path, monkeypatch):
    """An address the user never typed that matches nothing known must not be
    drafted to — ask for the address instead."""
    p = _proto(_session_with_accounts(_incident_accounts(tmp_path)))
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(
        ea, "_llm", lambda messages, **kw:
        "ACTION=new | REF=- | TO=boss@company.com | ACCOUNT=- | INSTRUCTION=about the meeting")
    calls = _capture_draft_new(monkeypatch)
    result = p.process_input("email my boss about the meeting", {})
    assert not calls
    assert "address" in result["response"].lower()
    assert p._pending is None


def test_new_user_typed_address_is_trusted(monkeypatch):
    """An address the user literally typed is used as-is, known or not."""
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(
        ea, "_llm", lambda messages, **kw:
        "ACTION=new | REF=- | TO=bob@nowhere.xyz | INSTRUCTION=say hi")
    calls = _capture_draft_new(monkeypatch)
    p.process_input("email bob@nowhere.xyz and say hi", {})
    assert calls
    assert calls[-1]["to"] == "bob@nowhere.xyz"


def test_new_from_phrase_overrides_panel_account(tmp_path, monkeypatch):
    """'from my personal email' must beat BOTH the Mail panel's active account
    and a wrong classifier ACCOUNT= hint."""
    session = _session_with_accounts(_incident_accounts(tmp_path))
    session.current_mail_account = "google-stitch"     # panel on the brand acct
    p = _proto(session)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(
        ea, "_llm", lambda messages, **kw:
        "ACTION=new | REF=- | TO=bob@x.ca | ACCOUNT=google-stitch | INSTRUCTION=say hi")
    calls = _capture_draft_new(monkeypatch)
    p.process_input("send an email from my personal email to bob@x.ca saying hi", {})
    assert calls
    assert calls[-1]["account_id"] == "google-personal"


def test_new_from_phrase_unresolvable_asks(tmp_path, monkeypatch):
    """If the user names a From-account we can't match, ask — never silently
    compose from whatever account happens to be active."""
    p = _proto(_session_with_accounts(_incident_accounts(tmp_path)))
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(
        ea, "_llm", lambda messages, **kw:
        "ACTION=new | REF=- | TO=bob@x.ca | ACCOUNT=- | INSTRUCTION=say hi")
    calls = _capture_draft_new(monkeypatch)
    result = p.process_input("send an email from my work email to bob@x.ca saying hi", {})
    assert not calls
    assert "work" in result["response"].lower()
    assert p._pending is None


def test_dictated_body_and_subject_pass_verbatim(monkeypatch):
    """Dictated wording ('body ... to say: <text>') must reach draft_new as
    body_verbatim/subject_hint, not be paraphrased via intent alone."""
    p = _proto(_FakeSession())
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(
        ea, "_llm", lambda messages, **kw:
        "ACTION=new | REF=- | TO=bob@x.ca | INSTRUCTION=practical test")
    calls = _capture_draft_new(monkeypatch)
    p.process_input(
        "send an email to bob@x.ca with the subject saying Practical test and "
        "then the body of the email to say: if this works we can start using "
        "this more", {})
    assert calls
    assert calls[-1]["subject_hint"] == "Practical test"
    assert calls[-1]["body_verbatim"] == "if this works we can start using this more"


def test_classifier_output_is_logged(monkeypatch, caplog):
    """The classifier's decision must be auditable from logs."""
    p = _proto(_FakeSession())
    _pending_reply(p)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(ea, "_llm",
                        lambda messages, **kw: "ACTION=send | REF=- | INSTRUCTION=-")
    with caplog.at_level(logging.INFO, logger="core.protocols.email_ops"):
        p.process_input("hm, maybe send it later", {})
    assert "classif" in caplog.text.lower()
    assert "send" in caplog.text.lower()


def test_incident_regression_full_conversation(tmp_path, monkeypatch):
    """Replay the 2026-07-09 conversation end-to-end against a worst-case
    classifier. Fixed behavior: draft from Personal, to the real SwitchStitch
    address, verbatim body; corrections never send; plain 'send it' does."""
    session = _session_with_accounts(_incident_accounts(tmp_path))
    session.current_mail_account = "google-stitch"     # panel was on the brand acct
    p = _proto(session)
    monkeypatch.setattr(ea, "_creds_from_session", lambda s, account_id=None: "CREDS")
    monkeypatch.setattr(gt, "gmail_list_messages",
                        lambda creds, max_results, categories: [])
    monkeypatch.setattr(gt, "gmail_delete_draft", lambda creds, draft_id: None)

    canned = iter([
        # worst case: no ACCOUNT hint, hallucinated TO
        "ACTION=new | REF=- | TO=switchstitch@gmail.com | ACCOUNT=- | "
        "INSTRUCTION=practical test - if this works we can start using this more",
        "ACTION=send | REF=- | INSTRUCTION=-",   # 'wrong, I said from my personal email'
        "ACTION=send | REF=- | INSTRUCTION=-",   # 'No I want you to send it from...'
        "ACTION=send | REF=- | INSTRUCTION=-",   # 'send it'
    ])
    monkeypatch.setattr(ea, "_llm", lambda messages, **kw: next(canned))
    calls = _capture_draft_new(monkeypatch)
    sent = {}
    monkeypatch.setattr(ea, "send_draft",
                        lambda session, draft_id, account_id=None:
                        sent.update(draft_id=draft_id, account_id=account_id)
                        or {"success": True})

    # Turn 1 — draft correctly despite the classifier
    r1 = p.process_input(INCIDENT_TURN_1, {})
    assert calls[-1]["account_id"] == "google-personal"
    assert calls[-1]["to"] == "theswitchstitch@gmail.com"
    assert calls[-1]["body_verbatim"] == "if this works we can start using this more"
    assert calls[-1]["subject_hint"] == "Practical test"
    assert "Personal" in r1["response"]
    assert not sent

    # Turn 2 — correction, must not send
    p.process_input("wrong, I said from my personal email", {})
    assert not sent
    assert p._pending is not None

    # Turn 3 — 'No ... send it from my personal email ...' must not send
    p.process_input(INCIDENT_TURN_3, {})
    assert not sent
    assert p._pending is not None
    assert p._pending["account_id"] == "google-personal"

    # Turn 4 — explicit standalone confirmation sends, as Personal
    p.process_input("send it", {})
    assert sent["account_id"] == "google-personal"
    assert p._pending is None
