"""Tests for the recurring_fire heartbeat job."""

from datetime import datetime

from core.heartbeat.job import JobContext
from core.heartbeat.jobs.recurring_fire import run


class _FakeOps:
    """Minimal stand-in for OperationsProtocol."""

    def __init__(self, to_fire):
        self._to_fire = to_fire
        self.called_with = None

    def check_recurring(self, now=None):
        """Record the injected `now` and return the canned fired list."""
        self.called_with = now
        return self._to_fire


class _FakeRegistry:
    """Minimal stand-in for ProtocolRegistry."""

    def __init__(self, ops):
        self._ops = ops

    def get(self, name):
        """Return the ops stand-in for 'operations', None otherwise."""
        return self._ops if name == "operations" else None


class _FakeSession:
    """Minimal stand-in for UserSession (real accessor: session.protocol_registry.get('operations'))."""

    def __init__(self, ops):
        self.protocol_registry = _FakeRegistry(ops)


def test_run_drives_check_recurring_with_now():
    """Job passes ctx.now to check_recurring and reports the count."""
    ops = _FakeOps([{"text": "Standup"}, {"text": "Meds"}])
    ctx = JobContext("switch", _FakeSession(ops), datetime(2026, 7, 4, 9, 0), {})
    result = run(ctx)
    assert ops.called_with == datetime(2026, 7, 4, 9, 0)
    assert result.notify is False
    assert "2" in result.silent_log


def test_run_nothing_fired():
    """Job handles an empty fired list gracefully."""
    ops = _FakeOps([])
    ctx = JobContext("switch", _FakeSession(ops), datetime(2026, 7, 4, 9, 0), {})
    result = run(ctx)
    assert result.notify is False
    assert "0" in result.silent_log
