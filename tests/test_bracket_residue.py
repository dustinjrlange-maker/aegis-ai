# tests/test_bracket_residue.py
"""Stripped bracket tags must not leave orphaned punctuation in the reply.

Regression for the live Stage-6 finding: the notetaker directive makes Pike
emit [REMEMBER: ...] on emotional disclosures, and stripping the tag left
"Take your time. ." in exactly the tender moments.
"""
from core.protocols.bracket_commands import BracketCommandProtocol


def _proto():
    p = BracketCommandProtocol()
    p.register_handler("REMEMBER", lambda arg: "saved")
    return p


def test_stripped_tag_leaves_no_orphan_period():
    out = _proto().process_output(
        "Take your time. [REMEMBER: his dad passed away recently].", {}
    )
    assert out["response"] == "Take your time."


def test_stripped_tag_mid_sentence_no_double_space_period():
    out = _proto().process_output(
        "I'm here. [REMEMBER: rough day at work] Get some rest.", {}
    )
    assert ". ." not in out["response"]
    assert ".." not in out["response"]
    assert "I'm here." in out["response"] and "Get some rest." in out["response"]


def test_no_tags_reply_unchanged():
    out = _proto().process_output("Night. Get some rest.", {})
    assert out["response"] == "Night. Get some rest."
