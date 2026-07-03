"""Trust tier decisions + PIN escalation stash."""
from datetime import datetime, timedelta

import pytest


FS_CATALOG = {
    "default_tier": "read_broad",
    "method_tiers": {"write_file": "write_destructive"},
}
TIME_CATALOG = {"default_tier": "read_scoped", "method_tiers": {}}


def test_required_tier_uses_method_map_then_default():
    from core.tooling import trust
    assert trust.required_tier(FS_CATALOG, "write_file") == "write_destructive"
    assert trust.required_tier(FS_CATALOG, "read_file") == "read_broad"
    assert trust.required_tier(TIME_CATALOG, "get_current_time") == "read_scoped"


def test_check_allows_within_tier():
    from core.tooling import trust
    assert trust.check("read_broad", FS_CATALOG, "read_file") == "allow"
    assert trust.check("read_scoped", TIME_CATALOG, "get_current_time") == "allow"


def test_check_escalates_above_tier():
    from core.tooling import trust
    assert trust.check("read_broad", FS_CATALOG, "write_file") == "needs_pin"


def test_stash_and_pin_confirm_executes_once(monkeypatch):
    from core.tooling import trust
    trust._pending.clear()
    monkeypatch.setattr(trust, "verify_vault_pin", lambda u, p: p == "1234")
    monkeypatch.setattr(trust, "has_vault_pin", lambda u: True)

    trust.stash_pending("switch", "filesystem", "write_file", {"path": "x"})
    ok, entry = trust.confirm_with_pin("switch", "9999")
    assert ok is False and "PIN" in entry            # wrong pin -> message, stash kept
    ok, entry = trust.confirm_with_pin("switch", "1234")
    assert ok is True and entry["method"] == "write_file"
    ok, entry = trust.confirm_with_pin("switch", "1234")
    assert ok is False                                # consumed — nothing pending


def test_pending_expires(monkeypatch):
    from core.tooling import trust
    trust._pending.clear()
    monkeypatch.setattr(trust, "verify_vault_pin", lambda u, p: True)
    monkeypatch.setattr(trust, "has_vault_pin", lambda u: True)
    trust.stash_pending("switch", "filesystem", "write_file", {})
    trust._pending["switch"]["expires"] = datetime.now() - timedelta(seconds=1)
    ok, msg = trust.confirm_with_pin("switch", "1234")
    assert ok is False and "expired" in msg.lower()


def test_no_pin_set_gives_guidance(monkeypatch):
    from core.tooling import trust
    trust._pending.clear()
    monkeypatch.setattr(trust, "has_vault_pin", lambda u: False)
    trust.stash_pending("switch", "filesystem", "write_file", {})
    ok, msg = trust.confirm_with_pin("switch", "1234")
    assert ok is False and "vault pin" in msg.lower()
