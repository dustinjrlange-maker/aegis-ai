import asyncio
import json
from datetime import datetime
from core.heartbeat import job as J
from core.heartbeat.state import HeartbeatState
from core.heartbeat.hlog import HeartbeatLog
from core.heartbeat.scheduler import run_heartbeat
from tests.heartbeat.conftest import FakeClock, RecordingNotifier


def _mk(tmp_path):
    return (HeartbeatState(tmp_path / "s.json"),
            HeartbeatLog(tmp_path / "l.jsonl"))


def _run_once(**kw):
    kw.setdefault("tick_seconds", 30)
    kw.setdefault("user_id", "switch")
    kw.setdefault("quiet_hours", ("22:00", "07:00"))
    kw.setdefault("session_manager", None)

    async def _noop_sleep(_): pass
    kw["sleep"] = _noop_sleep
    kw["max_ticks"] = kw.pop("max_ticks", 1)
    return asyncio.run(run_heartbeat(**kw))


def test_due_silent_job_runs_and_logs(tmp_path):
    ran = []
    state, hlog = _mk(tmp_path)
    job = J.Job(id="j", kind="silent", schedule=J.every(60), cooldown_s=60,
                run=lambda ctx: (ran.append(ctx.now) or J.JobResult(silent_log="ok")))
    _run_once(jobs=[job], clock=FakeClock(datetime(2026, 7, 4, 12, 0)),
              notifier=RecordingNotifier(), state=state, hlog=hlog,
              is_enabled=lambda jid: True)
    assert len(ran) == 1
    assert state.get("j")["last_fired_at"] == datetime(2026, 7, 4, 12, 0)


def test_cooldown_blocks_refire(tmp_path):
    n = {"c": 0}
    state, hlog = _mk(tmp_path)
    clock = FakeClock(datetime(2026, 7, 4, 12, 0, 0))

    def run(ctx):
        n["c"] += 1
        return J.JobResult(silent_log="ok")

    job = J.Job(id="j", kind="silent", schedule=J.every(1), cooldown_s=300, run=run)

    async def _sleep(_): clock.advance(30)
    asyncio.run(run_heartbeat(jobs=[job], clock=clock, notifier=RecordingNotifier(),
                              state=state, hlog=hlog, is_enabled=lambda j: True,
                              tick_seconds=30, user_id="switch",
                              quiet_hours=("22:00", "07:00"), session_manager=None,
                              sleep=_sleep, max_ticks=2))
    assert n["c"] == 1


def test_notify_job_held_in_quiet_hours(tmp_path):
    state, hlog = _mk(tmp_path)
    note = RecordingNotifier()
    job = J.Job(id="brief", kind="notify", schedule=J.every(60), cooldown_s=60,
                channels=["notification"],
                run=lambda ctx: J.JobResult(silent_log="b", notify=True,
                                            title="T", body="B"))
    _run_once(jobs=[job], clock=FakeClock(datetime(2026, 7, 4, 3, 0)),
              notifier=note, state=state, hlog=hlog, is_enabled=lambda j: True)
    assert note.pushes == []
    assert state.get("brief") is None


def test_silent_job_anomaly_defers_push_in_quiet_hours(tmp_path):
    state, hlog = _mk(tmp_path)
    note = RecordingNotifier()
    job = J.Job(id="audit", kind="silent", schedule=J.every(60), cooldown_s=60,
                channels=["notification"],
                run=lambda ctx: J.JobResult(silent_log="anomaly!", notify=True,
                                            title="Alert", body="bad"))
    clock = FakeClock(datetime(2026, 7, 4, 3, 0))
    async def _sleep(_): clock.advance(4 * 3600 + 60)
    asyncio.run(run_heartbeat(jobs=[job], clock=clock, notifier=note, state=state,
                              hlog=hlog, is_enabled=lambda j: True, tick_seconds=30,
                              user_id="switch", quiet_hours=("22:00", "07:00"),
                              session_manager=None, sleep=_sleep, max_ticks=2))
    assert len(note.pushes) == 1
    assert note.pushes[0][1] == "Alert"
    recs = [json.loads(l) for l in
            (tmp_path / "l.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(r["outcome"] == "deferred" for r in recs)


def test_one_job_crash_does_not_kill_siblings(tmp_path):
    state, hlog = _mk(tmp_path)
    ok = []

    def boom(ctx): raise RuntimeError("kaboom")

    j1 = J.Job(id="bad", kind="silent", schedule=J.every(60), cooldown_s=60, run=boom)
    j2 = J.Job(id="good", kind="silent", schedule=J.every(60), cooldown_s=60,
               run=lambda ctx: (ok.append(1) or J.JobResult(silent_log="ok")))
    _run_once(jobs=[j1, j2], clock=FakeClock(datetime(2026, 7, 4, 12, 0)),
              notifier=RecordingNotifier(), state=state, hlog=hlog,
              is_enabled=lambda j: True)
    assert ok == [1]


def test_disabled_job_skipped(tmp_path):
    state, hlog = _mk(tmp_path)
    ran = []
    job = J.Job(id="j", kind="silent", schedule=J.every(60), cooldown_s=60,
                run=lambda ctx: (ran.append(1) or J.JobResult(silent_log="ok")))
    _run_once(jobs=[job], clock=FakeClock(datetime(2026, 7, 4, 12, 0)),
              notifier=RecordingNotifier(), state=state, hlog=hlog,
              is_enabled=lambda jid: False)
    assert ran == []


def test_persisted_state_prevents_double_fire_across_restart(tmp_path):
    ran = []
    clock = FakeClock(datetime(2026, 7, 4, 12, 0))
    state1, hlog = _mk(tmp_path)
    job = J.Job(id="j", kind="silent", schedule=J.every(3600), cooldown_s=3600,
                run=lambda ctx: (ran.append(1) or J.JobResult(silent_log="ok")))
    _run_once(jobs=[job], clock=clock, notifier=RecordingNotifier(),
              state=state1, hlog=hlog, is_enabled=lambda j: True)
    clock.advance(30)
    state2 = HeartbeatState(tmp_path / "s.json")
    _run_once(jobs=[job], clock=clock, notifier=RecordingNotifier(),
              state=state2, hlog=hlog, is_enabled=lambda j: True)
    assert ran == [1]


def test_job_config_flows_into_ctx(tmp_path):
    """Job.config must reach ctx.config inside run()."""
    captured = []
    state, hlog = _mk(tmp_path)
    job = J.Job(id="cfg_job", kind="silent", schedule=J.every(60), cooldown_s=60,
                config={"marker": 42},
                run=lambda ctx: (captured.append(ctx.config) or
                                 J.JobResult(silent_log="ok")))
    _run_once(jobs=[job], clock=FakeClock(datetime(2026, 7, 4, 12, 0)),
              notifier=RecordingNotifier(), state=state, hlog=hlog,
              is_enabled=lambda jid: True)
    assert len(captured) == 1
    assert captured[0].get("marker") == 42


def test_flush_deferred_crash_does_not_kill_loop(tmp_path):
    """A _flush_deferred crash must not stop the tick or subsequent job runs (FIX I4)."""
    state, hlog = _mk(tmp_path)
    ran = []

    class _CrashingNotifier:
        """Notifier whose push always raises, simulating a broken delivery path."""
        async def push(self, *a, **kw):
            raise RuntimeError("notifier exploded")

    # Queue a deferred push so _flush_deferred has something to deliver
    state.queue_push({"user_id": "switch", "title": "T", "body": "B",
                      "channels": ["notification"]})

    job = J.Job(id="sibling", kind="silent", schedule=J.every(60), cooldown_s=60,
                run=lambda ctx: (ran.append(1) or J.JobResult(silent_log="ok")))

    # Run outside quiet hours so _flush_deferred actually attempts delivery
    _run_once(jobs=[job], clock=FakeClock(datetime(2026, 7, 4, 12, 0)),
              notifier=_CrashingNotifier(), state=state, hlog=hlog,
              is_enabled=lambda jid: True)
    # The sibling job must still have run despite the flush crash.
    assert ran == [1]
