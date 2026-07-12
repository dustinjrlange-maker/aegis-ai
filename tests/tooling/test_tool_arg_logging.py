"""Tool calls must log their ARGUMENTS, not just the fact of a call
(2026-07-09 audit D1). Otherwise a file-read exfil (path=<secret>) is invisible
in normal logs — the incident lesson: every action that matters must be
reconstructable from logs."""
import logging

import core.tooling.service as service


def _wire(monkeypatch, result="ok-result"):
    monkeypatch.setattr(service.registry, "get",
                        lambda u, t: {"trust_tier": "read_broad"})
    monkeypatch.setattr(service.catalog, "get_entry",
                        lambda t: {"id": t, "default_tier": "read_broad",
                                   "method_tiers": {"read_file": "read_broad"}})
    monkeypatch.setattr(service.trust, "check", lambda *a, **k: "allow")
    monkeypatch.setattr(service, "_ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(service.registry, "touch", lambda *a, **k: None)
    monkeypatch.setattr(service.audit, "log", lambda *a, **k: None)
    monkeypatch.setattr(service.MANAGER, "call", lambda u, t, m, args: result)


def test_successful_tool_call_logs_args(monkeypatch, caplog):
    _wire(monkeypatch)
    with caplog.at_level(logging.INFO, logger="aegis.tooling.service"):
        out = service.call_tool("switch", "filesystem", "read_file",
                                {"path": "C:/Users/dusti/data/anthropic_key"})
    assert out["status"] == "ok"
    text = caplog.text
    assert "filesystem" in text and "read_file" in text
    # the actual argument value must be visible for exfil auditability
    assert "anthropic_key" in text
