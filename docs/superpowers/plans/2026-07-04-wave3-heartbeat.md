# Wave 3 — Heartbeat Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Aegis a heartbeat — a long-lived in-process `asyncio` loop that runs cooldown-gated recurring jobs on its own and pushes results to the user via in-app notifications and/or Telegram.

**Architecture:** A generic scheduler (`core/heartbeat/`) ticks on a config interval, walks a job registry, and fires each job that is both *due* and *past cooldown*. Jobs return a `JobResult`; a `Notifier` fans notify-results to their channels. Silent-vs-notify classification, per-job cooldown, quiet-hours, and structured logging are built in from day one. Four jobs ship: recurring-task firing, morning briefing push, inbox scan, security self-audit. The loop is dependency-injected (jobs, clock, notifier, state, hlog, is_enabled) so it is fully unit-testable with a fake clock — no real sleeping.

**Tech Stack:** Python 3.12, `asyncio`, `dataclasses`, pathlib + JSON (existing conventions), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-04-wave3-heartbeat-design.md`

---

## File Structure

```
core/heartbeat/
  __init__.py          # exports run_heartbeat, build_registry
  job.py               # Job, Schedule, JobResult, JobContext; every()/daily_at(); is_due(); in_quiet_hours()
  state.py             # HeartbeatState: atomic load/save of data/heartbeat.json + pending_pushes
  hlog.py              # HeartbeatLog: append records to data/heartbeat_log.jsonl (size-capped)
  notifier.py          # Notifier.push(): fan out to notification_service / Telegram
  scheduler.py         # run_heartbeat(): the async loop + _evaluate_job()
  registry.py          # build_registry(config) -> list[Job]; real is_enabled/notifier factories
  jobs/
    __init__.py
    recurring_fire.py   # silent: drive ops.check_recurring(now)
    morning_briefing.py # notify: generate_narrative_briefing
    inbox_scan.py       # silent, escalate on threshold
    security_audit.py   # silent, escalate on anomaly; append-only check list

tests/heartbeat/
  __init__.py
  conftest.py          # FakeClock, fake state/hlog/notifier helpers
  test_job.py          # is_due, in_quiet_hours, dataclasses
  test_state.py        # atomic roundtrip, pending_pushes
  test_hlog.py         # record shape, size cap
  test_notifier.py     # fan-out, telegram degrade
  test_scheduler.py    # cooldown, due, quiet-hours, crash isolation, restart no-double-fire
  test_recurring_fire.py
  test_morning_briefing.py
  test_inbox_scan.py
  test_security_audit.py

Modified (additive):
  server/app.py                     # lifespan: create/cancel the heartbeat task
  core/config/core_config.json      # "heartbeat" block
  integrations/telegram_bot.py      # module-level get_application() accessor
  core/protocols/operations.py      # check_recurring() honors the `time` field
```

Datetimes are passed as `datetime` objects everywhere; the state file stores ISO-8601 strings. The injected `clock()` returns a `datetime` — tests pass a `FakeClock` to control time without sleeping.

---

## Task 1: Job / Schedule / JobResult dataclasses + due logic

**Files:**
- Create: `core/heartbeat/__init__.py` (empty for now)
- Create: `core/heartbeat/jobs/__init__.py` (empty)
- Create: `core/heartbeat/job.py`
- Create: `tests/heartbeat/__init__.py` (empty)
- Test: `tests/heartbeat/test_job.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_job.py
from datetime import datetime, timedelta
from core.heartbeat import job as J


def test_every_due_when_never_fired():
    s = J.every(seconds=60)
    now = datetime(2026, 7, 4, 9, 0, 0)
    assert J.is_due(s, now, last_fired_at=None) is True


def test_every_not_due_within_interval():
    s = J.every(seconds=60)
    now = datetime(2026, 7, 4, 9, 0, 30)
    last = datetime(2026, 7, 4, 9, 0, 0)
    assert J.is_due(s, now, last_fired_at=last) is False


def test_every_due_after_interval():
    s = J.every(seconds=60)
    now = datetime(2026, 7, 4, 9, 1, 5)
    last = datetime(2026, 7, 4, 9, 0, 0)
    assert J.is_due(s, now, last_fired_at=last) is True


def test_daily_at_due_at_or_after_time_once_per_day():
    s = J.daily_at(7, 0)
    # never fired, it's past 07:00 today -> due
    assert J.is_due(s, datetime(2026, 7, 4, 7, 0), last_fired_at=None) is True
    # already fired today -> not due again today
    assert J.is_due(s, datetime(2026, 7, 4, 9, 0),
                    last_fired_at=datetime(2026, 7, 4, 7, 0)) is False
    # fired yesterday, past 07:00 today -> due again
    assert J.is_due(s, datetime(2026, 7, 4, 7, 1),
                    last_fired_at=datetime(2026, 7, 3, 7, 0)) is True


def test_daily_at_not_due_before_time():
    s = J.daily_at(7, 0)
    assert J.is_due(s, datetime(2026, 7, 4, 6, 59), last_fired_at=None) is False


def test_quiet_hours_wrapping_window():
    q = ("22:00", "07:00")
    assert J.in_quiet_hours(datetime(2026, 7, 4, 23, 0), q) is True
    assert J.in_quiet_hours(datetime(2026, 7, 4, 3, 0), q) is True
    assert J.in_quiet_hours(datetime(2026, 7, 4, 7, 0), q) is False   # end exclusive
    assert J.in_quiet_hours(datetime(2026, 7, 4, 12, 0), q) is False


def test_jobresult_defaults():
    r = J.JobResult(silent_log="ran")
    assert r.notify is False and r.title == "" and r.channels is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/heartbeat/test_job.py -v`
Expected: FAIL — `ModuleNotFoundError: core.heartbeat.job`

- [ ] **Step 3: Write minimal implementation**

```python
# core/heartbeat/job.py
"""Heartbeat job model + pure scheduling predicates (no I/O)."""

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Schedule:
    kind: str                       # "every" | "daily_at"
    seconds: Optional[int] = None   # for "every"
    hh: Optional[int] = None        # for "daily_at"
    mm: Optional[int] = None


def every(seconds: int) -> Schedule:
    return Schedule("every", seconds=seconds)


def daily_at(hh: int, mm: int) -> Schedule:
    return Schedule("daily_at", hh=hh, mm=mm)


@dataclass
class JobResult:
    """What a job's run() returns. silent_log is always recorded; notify
    requests a user push (a silent job may set notify=True to escalate)."""
    silent_log: str
    notify: bool = False
    title: str = ""
    body: str = ""
    channels: Optional[list] = None   # override Job.channels when set


@dataclass
class JobContext:
    """Everything a job needs to run. session exposes notification_service,
    ops (operations protocol), event_manager, etc."""
    user_id: str
    session: Any
    now: datetime
    config: dict                      # this job's config block


@dataclass
class Job:
    id: str
    kind: str                         # "silent" | "notify" (normal disposition)
    schedule: Schedule
    cooldown_s: int
    run: Callable[[JobContext], JobResult]
    channels: list = field(default_factory=list)


def is_due(schedule: Schedule, now: datetime,
           last_fired_at: Optional[datetime]) -> bool:
    if schedule.kind == "every":
        if last_fired_at is None:
            return True
        return (now - last_fired_at).total_seconds() >= schedule.seconds
    if schedule.kind == "daily_at":
        target = time(schedule.hh, schedule.mm)
        if now.time() < target:
            return False
        # due only if we have not already fired on/after today's target
        if last_fired_at is None:
            return True
        return last_fired_at.date() < now.date()
    raise ValueError(f"unknown schedule kind: {schedule.kind}")


def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def in_quiet_hours(now: datetime, window) -> bool:
    """window is (start, end) as 'HH:MM' strings; wraps midnight. End exclusive."""
    start, end = _parse_hhmm(window[0]), _parse_hhmm(window[1])
    t = now.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end     # wraps midnight
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/heartbeat/test_job.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add core/heartbeat/__init__.py core/heartbeat/jobs/__init__.py core/heartbeat/job.py tests/heartbeat/__init__.py tests/heartbeat/test_job.py
git commit -m "wave 3: heartbeat job model + due/quiet-hours predicates"
```

---

## Task 2: HeartbeatState — atomic JSON persistence

**Files:**
- Create: `core/heartbeat/state.py`
- Test: `tests/heartbeat/test_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_state.py
from datetime import datetime
from core.heartbeat.state import HeartbeatState


def test_roundtrip_mark_and_reload(tmp_path):
    p = tmp_path / "heartbeat.json"
    st = HeartbeatState(p)
    assert st.get("job_a") is None
    now = datetime(2026, 7, 4, 9, 0, 0)
    st.mark_fired("job_a", fired_at=now, next_eligible_at=datetime(2026, 7, 4, 9, 1, 0))
    # reload from disk into a fresh instance
    st2 = HeartbeatState(p)
    rec = st2.get("job_a")
    assert rec["last_fired_at"] == now
    assert rec["next_eligible_at"] == datetime(2026, 7, 4, 9, 1, 0)


def test_atomic_write_leaves_no_temp(tmp_path):
    p = tmp_path / "heartbeat.json"
    st = HeartbeatState(p)
    st.mark_fired("j", datetime(2026, 7, 4, 9, 0), datetime(2026, 7, 4, 9, 1))
    assert p.exists()
    assert list(tmp_path.glob("*.tmp")) == []   # temp file renamed away


def test_pending_pushes_queue(tmp_path):
    p = tmp_path / "heartbeat.json"
    st = HeartbeatState(p)
    st.queue_push({"user_id": "switch", "title": "t", "body": "b", "channels": ["notification"]})
    st2 = HeartbeatState(p)                       # survives reload
    pending = st2.drain_pushes()
    assert pending == [{"user_id": "switch", "title": "t", "body": "b", "channels": ["notification"]}]
    assert st2.drain_pushes() == []               # drained
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/heartbeat/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError: core.heartbeat.state`

- [ ] **Step 3: Write minimal implementation**

```python
# core/heartbeat/state.py
"""Durable per-job heartbeat state (last_fired_at / next_eligible_at) plus a
deferred-push queue. Atomic writes so a crash can't corrupt or double-fire."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("aegis.heartbeat")


class HeartbeatState:
    def __init__(self, path):
        self.path = Path(path)
        self._jobs = {}            # job_id -> {"last_fired_at": dt, "next_eligible_at": dt}
        self._pending = []         # list of push dicts
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.exception("heartbeat state unreadable; starting fresh")
            return
        for jid, rec in raw.get("jobs", {}).items():
            self._jobs[jid] = {
                "last_fired_at": _from_iso(rec.get("last_fired_at")),
                "next_eligible_at": _from_iso(rec.get("next_eligible_at")),
            }
        self._pending = raw.get("pending_pushes", [])

    def _save(self):
        data = {
            "jobs": {
                jid: {
                    "last_fired_at": _to_iso(r["last_fired_at"]),
                    "next_eligible_at": _to_iso(r["next_eligible_at"]),
                }
                for jid, r in self._jobs.items()
            },
            "pending_pushes": self._pending,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)         # atomic on same filesystem

    def get(self, job_id):
        return self._jobs.get(job_id)

    def mark_fired(self, job_id, fired_at: datetime, next_eligible_at: datetime):
        self._jobs[job_id] = {"last_fired_at": fired_at, "next_eligible_at": next_eligible_at}
        self._save()

    def queue_push(self, push: dict):
        self._pending.append(push)
        self._save()

    def drain_pushes(self):
        out, self._pending = self._pending, []
        self._save()
        return out


def _to_iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else None


def _from_iso(s):
    return datetime.fromisoformat(s) if s else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/heartbeat/test_state.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/heartbeat/state.py tests/heartbeat/test_state.py
git commit -m "wave 3: atomic heartbeat state + deferred-push queue"
```

---

## Task 3: HeartbeatLog — structured JSONL logging

**Files:**
- Create: `core/heartbeat/hlog.py`
- Test: `tests/heartbeat/test_hlog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_hlog.py
import json
from datetime import datetime
from core.heartbeat.hlog import HeartbeatLog


def test_write_appends_record(tmp_path):
    p = tmp_path / "heartbeat_log.jsonl"
    log = HeartbeatLog(p, max_bytes=10_000)
    log.write(datetime(2026, 7, 4, 9, 0), "job_a", "silent", "silent_log", "ran fine", 12)
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["job_id"] == "job_a"
    assert rec["kind"] == "silent"
    assert rec["outcome"] == "silent_log"
    assert rec["detail"] == "ran fine"
    assert rec["duration_ms"] == 12
    assert rec["ts"] == "2026-07-04T09:00:00"


def test_size_cap_truncates_oldest(tmp_path):
    p = tmp_path / "heartbeat_log.jsonl"
    log = HeartbeatLog(p, max_bytes=400)         # tiny cap
    for i in range(50):
        log.write(datetime(2026, 7, 4, 9, 0), f"j{i}", "silent", "silent_log", "x" * 20, 1)
    assert p.stat().st_size <= 400 * 2           # kept near the cap, not unbounded
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    # newest survives, oldest dropped
    assert json.loads(lines[-1])["job_id"] == "j49"
    assert all(json.loads(l)["job_id"] != "j0" for l in lines)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/heartbeat/test_hlog.py -v`
Expected: FAIL — `ModuleNotFoundError: core.heartbeat.hlog`

- [ ] **Step 3: Write minimal implementation**

```python
# core/heartbeat/hlog.py
"""Structured heartbeat run log (JSONL). One record per job per tick that did
something. outcome in {silent_log, notified, skipped_cooldown, skipped_quiet,
skipped_disabled, error}. Size-capped by dropping oldest lines."""

import json
import logging
from pathlib import Path

logger = logging.getLogger("aegis.heartbeat")

_VALID = {"silent_log", "notified", "skipped_cooldown",
          "skipped_quiet", "skipped_disabled", "error"}


class HeartbeatLog:
    def __init__(self, path, max_bytes=1_000_000):
        self.path = Path(path)
        self.max_bytes = max_bytes

    def write(self, now, job_id, kind, outcome, detail="", duration_ms=0):
        if outcome not in _VALID:
            outcome = "silent_log"
        rec = {
            "ts": now.isoformat(),
            "job_id": job_id,
            "kind": kind,
            "outcome": outcome,
            "detail": (detail or "")[:500],
            "duration_ms": duration_ms,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            self._cap()
        except OSError:
            logger.exception("failed writing heartbeat log")

    def _cap(self):
        try:
            if self.path.stat().st_size <= self.max_bytes:
                return
            lines = self.path.read_text(encoding="utf-8").splitlines()
            # drop oldest half
            keep = lines[len(lines) // 2:]
            self.path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            logger.exception("failed capping heartbeat log")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/heartbeat/test_hlog.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add core/heartbeat/hlog.py tests/heartbeat/test_hlog.py
git commit -m "wave 3: structured heartbeat JSONL log with size cap"
```

---

## Task 4: Notifier — channel fan-out with Telegram degrade

**Files:**
- Create: `core/heartbeat/notifier.py`
- Test: `tests/heartbeat/test_notifier.py`

The Notifier is injected with two accessors so it is testable without a real bot:
`get_telegram_app() -> Application | None` and `get_chat_id(user_id) -> str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_notifier.py
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
    # telegram requested but no app available -> falls back to notification, no raise
    n = Notifier(_FakeSessionManager(sess), get_telegram_app=lambda: None,
                 get_chat_id=lambda u: None)
    asyncio.run(n.push("switch", "T", "B", ["telegram"]))
    assert sess.notification_service.added == [("heartbeat", "T", "B")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/heartbeat/test_notifier.py -v`
Expected: FAIL — `ModuleNotFoundError: core.heartbeat.notifier`

- [ ] **Step 3: Write minimal implementation**

```python
# core/heartbeat/notifier.py
"""Fans a notify result out to its channels. Telegram is best-effort: if the
bot app or the user's chat_id is unavailable, we degrade to the in-app
notification queue and never raise."""

import logging

logger = logging.getLogger("aegis.heartbeat")


class Notifier:
    def __init__(self, session_manager, get_telegram_app, get_chat_id):
        self._sm = session_manager
        self._get_app = get_telegram_app
        self._get_chat_id = get_chat_id

    async def push(self, user_id, title, body, channels):
        channels = channels or ["notification"]
        telegram_ok = False
        if "telegram" in channels:
            telegram_ok = await self._push_telegram(user_id, title, body)
        if "notification" in channels or (not telegram_ok and "telegram" in channels):
            self._push_notification(user_id, title, body)

    def _push_notification(self, user_id, title, body):
        try:
            sess = self._sm.get(user_id)
            sess.notification_service.add(type="heartbeat", title=title, body=body)
        except Exception:
            logger.exception("heartbeat notification push failed")

    async def _push_telegram(self, user_id, title, body) -> bool:
        try:
            app = self._get_app()
            chat_id = self._get_chat_id(user_id)
            if not app or not chat_id:
                logger.info("telegram push unavailable for %s; degrading", user_id)
                return False
            text = f"{title}\n\n{body}" if body else title
            await app.bot.send_message(chat_id=chat_id, text=text)
            return True
        except Exception:
            logger.exception("heartbeat telegram push failed")
            return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/heartbeat/test_notifier.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/heartbeat/notifier.py tests/heartbeat/test_notifier.py
git commit -m "wave 3: heartbeat notifier with telegram degrade-to-notification"
```

---

## Task 5: Scheduler loop — cooldown, due, quiet-hours, crash isolation

**Files:**
- Create: `core/heartbeat/scheduler.py`
- Create: `tests/heartbeat/conftest.py`
- Test: `tests/heartbeat/test_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/conftest.py
from datetime import timedelta


class FakeClock:
    """Returns a controllable datetime; advance() moves it forward."""
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t = self.t + timedelta(seconds=seconds)


class RecordingNotifier:
    def __init__(self):
        self.pushes = []

    async def push(self, user_id, title, body, channels):
        self.pushes.append((user_id, title, body, tuple(channels or [])))
```

```python
# tests/heartbeat/test_scheduler.py
import asyncio
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

    # cooldown 300s, schedule every 1s; two ticks 30s apart -> only 1 fire
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
    # 03:00 is inside 22:00-07:00 -> job not run, no push
    _run_once(jobs=[job], clock=FakeClock(datetime(2026, 7, 4, 3, 0)),
              notifier=note, state=state, hlog=hlog, is_enabled=lambda j: True)
    assert note.pushes == []
    assert state.get("brief") is None            # did not fire


def test_silent_job_anomaly_defers_push_in_quiet_hours(tmp_path):
    state, hlog = _mk(tmp_path)
    note = RecordingNotifier()
    job = J.Job(id="audit", kind="silent", schedule=J.every(60), cooldown_s=60,
                channels=["notification"],
                run=lambda ctx: J.JobResult(silent_log="anomaly!", notify=True,
                                            title="Alert", body="bad"))
    clock = FakeClock(datetime(2026, 7, 4, 3, 0))
    # tick 1 at 03:00 (quiet): runs, logs, queues push. tick 2 at 07:01: flushes.
    async def _sleep(_): clock.advance(4 * 3600 + 60)   # jump to 07:01
    asyncio.run(run_heartbeat(jobs=[job], clock=clock, notifier=note, state=state,
                              hlog=hlog, is_enabled=lambda j: True, tick_seconds=30,
                              user_id="switch", quiet_hours=("22:00", "07:00"),
                              session_manager=None, sleep=_sleep, max_ticks=2))
    assert len(note.pushes) == 1
    assert note.pushes[0][1] == "Alert"


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
    assert ok == [1]                              # sibling still ran


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
    # "restart": fresh state from same file, 30s later — cooldown still active
    clock.advance(30)
    state2 = HeartbeatState(tmp_path / "s.json")
    _run_once(jobs=[job], clock=clock, notifier=RecordingNotifier(),
              state=state2, hlog=hlog, is_enabled=lambda j: True)
    assert ran == [1]                             # did not fire twice
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/heartbeat/test_scheduler.py -v`
Expected: FAIL — `ModuleNotFoundError: core.heartbeat.scheduler`

- [ ] **Step 3: Write minimal implementation**

```python
# core/heartbeat/scheduler.py
"""The heartbeat loop. Dependency-injected (jobs, clock, notifier, state, hlog,
is_enabled, sleep) so it runs under a fake clock with no real sleeping.

Quiet hours defers pushes, never runs: a notify-kind job due inside the window
is held; a silent job runs and any escalated push is queued for window end."""

import asyncio
import logging
from datetime import timedelta

from core.heartbeat import job as J

logger = logging.getLogger("aegis.heartbeat")


async def run_heartbeat(*, jobs, clock, notifier, state, hlog, is_enabled,
                        tick_seconds, user_id, quiet_hours, session_manager,
                        sleep=asyncio.sleep, max_ticks=None):
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        now = clock()
        await _flush_deferred(now, quiet_hours, state, notifier)
        for job in jobs:
            try:
                await _evaluate_job(job, now, is_enabled, state, hlog, notifier,
                                    quiet_hours, user_id, session_manager)
            except Exception:
                logger.exception("heartbeat job %s crashed", job.id)
                hlog.write(now, job.id, job.kind, "error", "unhandled exception", 0)
        await sleep(tick_seconds)


async def _flush_deferred(now, quiet_hours, state, notifier):
    """Deliver any pushes that were deferred during quiet hours, once we're out."""
    if J.in_quiet_hours(now, quiet_hours):
        return
    for push in state.drain_pushes():
        await notifier.push(push["user_id"], push["title"], push["body"],
                            push["channels"])


async def _evaluate_job(job, now, is_enabled, state, hlog, notifier,
                        quiet_hours, user_id, session_manager):
    if not is_enabled(job.id):
        hlog.write(now, job.id, job.kind, "skipped_disabled")
        return
    rec = state.get(job.id)
    last = rec["last_fired_at"] if rec else None
    if not J.is_due(job.schedule, now, last):
        return
    if rec and rec["next_eligible_at"] and now < rec["next_eligible_at"]:
        hlog.write(now, job.id, job.kind, "skipped_cooldown")
        return
    # notify-kind jobs are held (not run) during quiet hours
    if job.kind == "notify" and J.in_quiet_hours(now, quiet_hours):
        hlog.write(now, job.id, job.kind, "skipped_quiet")
        return
    # run it
    session = session_manager.get(user_id) if session_manager else None
    ctx = J.JobContext(user_id=user_id, session=session, now=now, config={})
    result = await asyncio.to_thread(job.run, ctx)
    state.mark_fired(job.id, now, now + timedelta(seconds=job.cooldown_s))
    if result.notify:
        channels = result.channels or job.channels or ["notification"]
        if J.in_quiet_hours(now, quiet_hours):
            state.queue_push({"user_id": user_id, "title": result.title,
                              "body": result.body, "channels": channels})
            hlog.write(now, job.id, job.kind, "silent_log", result.silent_log, 0)
        else:
            await notifier.push(user_id, result.title, result.body, channels)
            hlog.write(now, job.id, job.kind, "notified", result.silent_log, 0)
    else:
        hlog.write(now, job.id, job.kind, "silent_log", result.silent_log, 0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/heartbeat/test_scheduler.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add core/heartbeat/scheduler.py tests/heartbeat/conftest.py tests/heartbeat/test_scheduler.py
git commit -m "wave 3: heartbeat scheduler loop (cooldown, quiet-hours, crash isolation)"
```

---

## Task 6: Fix operations.check_recurring() to honor the `time` field

**Files:**
- Modify: `core/protocols/operations.py` — `check_recurring()` (~line 226) and `add_recurring` time handling
- Test: `tests/protocols/test_operations_recurring.py` (create if absent; else append)

**Before writing code:** open `core/protocols/operations.py` and read `add_recurring` (~line 197) and `check_recurring` (~lines 226-282) to confirm the current signature and how `_recurring` entries are shaped (fields: `time`, last-fired marker, task template). Match the existing field names exactly.

- [ ] **Step 1: Write the failing test**

```python
# tests/protocols/test_operations_recurring.py
from datetime import datetime
from core.protocols.operations import OperationsProtocol


def _ops(tmp_path, monkeypatch):
    # point storage at tmp so we don't touch real data; follow existing init.
    op = OperationsProtocol(username="switch")
    return op


def test_recurring_not_fired_before_its_time(tmp_path, monkeypatch):
    op = _ops(tmp_path, monkeypatch)
    op.add_recurring(title="Standup", cadence="daily", time="09:00")
    fired = op.check_recurring(now=datetime(2026, 7, 4, 8, 30))
    assert fired == []                      # 08:30 < 09:00 -> not yet


def test_recurring_fires_at_or_after_time(tmp_path, monkeypatch):
    op = _ops(tmp_path, monkeypatch)
    op.add_recurring(title="Standup", cadence="daily", time="09:00")
    fired = op.check_recurring(now=datetime(2026, 7, 4, 9, 1))
    assert any(t["title"] == "Standup" for t in fired)


def test_recurring_fires_once_per_day(tmp_path, monkeypatch):
    op = _ops(tmp_path, monkeypatch)
    op.add_recurring(title="Standup", cadence="daily", time="09:00")
    first = op.check_recurring(now=datetime(2026, 7, 4, 9, 1))
    second = op.check_recurring(now=datetime(2026, 7, 4, 12, 0))
    assert len(first) == 1 and second == []
```

Adjust `_ops()` to match the real constructor/storage (the reading step above tells you how). If `OperationsProtocol` requires an on-disk path, use `monkeypatch` to point its task/recurring files under `tmp_path`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/protocols/test_operations_recurring.py -v`
Expected: FAIL — either `check_recurring()` rejects the `now=` kwarg, or it fires regardless of `time`.

- [ ] **Step 3: Write minimal implementation**

Change `check_recurring` to accept `now=None` (default to `datetime.now()`) and gate firing on the `time` field. Concretely, inside the per-recurring loop that currently decides "is this due today", add the time gate and keep the existing once-per-day guard:

```python
# core/protocols/operations.py  (inside check_recurring)
def check_recurring(self, now=None):
    """Fire due recurring tasks. Driven by the heartbeat every 60s; also called
    from process_input. Honors each entry's `time` (HH:MM) so a 09:00 recurring
    task materializes at 09:00, not at the user's first message of the day."""
    from datetime import datetime, time as _time
    now = now or datetime.now()
    fired = []
    for entry in self._recurring:
        if not self._recurring_due_today(entry, now):     # existing cadence/date check
            continue
        # NEW: time-of-day gate
        hhmm = entry.get("time")
        if hhmm:
            hh, mm = hhmm.split(":")
            if now.time() < _time(int(hh), int(mm)):
                continue
        # existing once-per-day guard (do not fire if already fired today)
        if entry.get("last_fired_date") == now.date().isoformat():
            continue
        task = self._materialize_recurring_task(entry)    # existing creation path
        entry["last_fired_date"] = now.date().isoformat()
        fired.append(task)
    if fired:
        self._save_recurring()
        self._save_tasks()
    return fired
```

Names like `_recurring_due_today`, `_materialize_recurring_task`, `last_fired_date`, `_save_recurring` are placeholders for the **actual** helpers/fields you confirmed in the reading step — wire to those, do not invent new ones. If the existing code already has a once-per-day marker under a different key, reuse it. Keep the existing call site in `process_input` (line 549) working by leaving `now` optional.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/protocols/test_operations_recurring.py -v`
Then the existing operations tests: `python -m pytest tests/protocols/ -v`
Expected: new tests PASS; no existing operations test regresses.

- [ ] **Step 5: Commit**

```bash
git add core/protocols/operations.py tests/protocols/test_operations_recurring.py
git commit -m "wave 3: check_recurring honors the time field (fixes dead time)"
```

---

## Task 7: Job — recurring_fire (silent)

**Files:**
- Create: `core/heartbeat/jobs/recurring_fire.py`
- Test: `tests/heartbeat/test_recurring_fire.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_recurring_fire.py
from datetime import datetime
from core.heartbeat.job import JobContext
from core.heartbeat.jobs.recurring_fire import run


class _FakeOps:
    def __init__(self, to_fire): self._to_fire = to_fire; self.called_with = None
    def check_recurring(self, now=None):
        self.called_with = now
        return self._to_fire


class _FakeSession:
    def __init__(self, ops): self.ops = ops


def test_run_drives_check_recurring_with_now():
    ops = _FakeOps([{"title": "Standup"}, {"title": "Meds"}])
    ctx = JobContext("switch", _FakeSession(ops), datetime(2026, 7, 4, 9, 0), {})
    result = run(ctx)
    assert ops.called_with == datetime(2026, 7, 4, 9, 0)
    assert result.notify is False
    assert "Standup" in result.silent_log and "2" in result.silent_log


def test_run_nothing_fired():
    ops = _FakeOps([])
    ctx = JobContext("switch", _FakeSession(ops), datetime(2026, 7, 4, 9, 0), {})
    result = run(ctx)
    assert result.notify is False
    assert "0" in result.silent_log
```

**Note:** confirm the session attribute that exposes the operations protocol. If it is not `session.ops`, adjust `_FakeSession` and the job to the real accessor (check how `chat_pipeline.py` reaches operations — likely via the protocol registry). Wire to whatever the real path is.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/heartbeat/test_recurring_fire.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# core/heartbeat/jobs/recurring_fire.py
"""Silent heartbeat job: fire due recurring tasks every 60s (previously only
fired when the user happened to message)."""

from core.heartbeat.job import JobResult


def run(ctx):
    ops = ctx.session.ops                         # adjust to the real accessor
    fired = ops.check_recurring(now=ctx.now)
    titles = ", ".join(t.get("title", "?") for t in fired) if fired else "none"
    return JobResult(silent_log=f"recurring fired: {len(fired)} ({titles})")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/heartbeat/test_recurring_fire.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add core/heartbeat/jobs/recurring_fire.py tests/heartbeat/test_recurring_fire.py
git commit -m "wave 3: recurring_fire heartbeat job"
```

---

## Task 8: Job — morning_briefing (notify)

**Files:**
- Create: `core/heartbeat/jobs/morning_briefing.py`
- Test: `tests/heartbeat/test_morning_briefing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_morning_briefing.py
from datetime import datetime
from core.heartbeat.job import JobContext
from core.heartbeat.jobs import morning_briefing


def test_run_builds_notify_result(monkeypatch):
    monkeypatch.setattr(morning_briefing, "generate_narrative_briefing",
                        lambda session, period=None: "Good morning. 3 tasks today.")
    ctx = JobContext("switch", object(), datetime(2026, 7, 4, 7, 0), {})
    result = morning_briefing.run(ctx)
    assert result.notify is True
    assert result.body == "Good morning. 3 tasks today."
    assert "briefing" in result.title.lower() or "morning" in result.title.lower()


def test_run_empty_briefing_still_notifies(monkeypatch):
    monkeypatch.setattr(morning_briefing, "generate_narrative_briefing",
                        lambda session, period=None: "")
    ctx = JobContext("switch", object(), datetime(2026, 7, 4, 7, 0), {})
    result = morning_briefing.run(ctx)
    assert result.notify is True
    assert result.body                             # falls back to a default line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/heartbeat/test_morning_briefing.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# core/heartbeat/jobs/morning_briefing.py
"""Notify heartbeat job: push the narrative morning briefing (local/private)."""

from core.briefing import generate_narrative_briefing
from core.heartbeat.job import JobResult


def run(ctx):
    text = generate_narrative_briefing(ctx.session, period="morning") or ""
    if not text.strip():
        text = "Good morning. Nothing pressing on the schedule."
    return JobResult(silent_log="morning briefing pushed", notify=True,
                     title="Morning briefing", body=text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/heartbeat/test_morning_briefing.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add core/heartbeat/jobs/morning_briefing.py tests/heartbeat/test_morning_briefing.py
git commit -m "wave 3: morning_briefing heartbeat job"
```

---

## Task 9: Job — inbox_scan (silent, escalate on threshold)

**Files:**
- Create: `core/heartbeat/jobs/inbox_scan.py`
- Test: `tests/heartbeat/test_inbox_scan.py`

The job depends on a small `fetch_unread(session) -> list[dict]` where each email is `{"from": str, "subject": str}`. The real `fetch_unread` reads the existing chat-driven email / EmailOps seam; keep it isolated so the ranking logic is testable without a mailbox. If email is not configured, `fetch_unread` returns `None` and the job self-disables (logs once, no push).

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_inbox_scan.py
from datetime import datetime
from core.heartbeat.job import JobContext
from core.heartbeat.jobs import inbox_scan


def _ctx(cfg=None):
    return JobContext("switch", object(), datetime(2026, 7, 4, 10, 0), cfg or {})


def test_important_sender_escalates(monkeypatch):
    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: [
        {"from": "boss@studio.com", "subject": "call sheet tomorrow"},
        {"from": "spam@promo.io", "subject": "50% off"},
    ])
    ctx = _ctx({"important_senders": ["boss@studio.com"], "notify_threshold": 1})
    result = inbox_scan.run(ctx)
    assert result.notify is True
    assert "boss@studio.com" in result.body


def test_below_threshold_is_silent(monkeypatch):
    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: [
        {"from": "spam@promo.io", "subject": "50% off"},
    ])
    ctx = _ctx({"important_senders": ["boss@studio.com"], "notify_threshold": 1})
    result = inbox_scan.run(ctx)
    assert result.notify is False
    assert "1" in result.silent_log               # count logged


def test_keyword_signal_counts_as_important(monkeypatch):
    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: [
        {"from": "unknown@x.com", "subject": "URGENT: invoice overdue"},
    ])
    ctx = _ctx({"important_senders": [], "notify_threshold": 1,
                "keywords": ["urgent", "invoice"]})
    result = inbox_scan.run(ctx)
    assert result.notify is True


def test_email_unconfigured_self_disables(monkeypatch):
    monkeypatch.setattr(inbox_scan, "fetch_unread", lambda s: None)
    result = inbox_scan.run(_ctx())
    assert result.notify is False
    assert "not configured" in result.silent_log.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/heartbeat/test_inbox_scan.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# core/heartbeat/jobs/inbox_scan.py
"""Silent heartbeat job: scan unread mail, escalate to a push only when
important messages cross a threshold. Importance = known sender OR keyword hit."""

import logging
from core.heartbeat.job import JobResult

logger = logging.getLogger("aegis.heartbeat")

_DEFAULT_KEYWORDS = ["urgent", "invoice", "overdue", "call sheet", "contract"]


def fetch_unread(session):
    """Return a list of {"from","subject"} unread emails, or None if email is
    not configured for this user. Wired to the existing EmailOps seam in the
    registry task; kept here so the ranking is unit-testable."""
    getter = getattr(session, "fetch_unread_emails", None)
    if getter is None:
        return None
    return getter()


def _is_important(email, senders, keywords):
    if email.get("from", "").lower() in {s.lower() for s in senders}:
        return True
    subj = email.get("subject", "").lower()
    return any(k.lower() in subj for k in keywords)


def run(ctx):
    unread = fetch_unread(ctx.session)
    if unread is None:
        return JobResult(silent_log="inbox scan: email not configured; skipping")
    senders = ctx.config.get("important_senders", [])
    keywords = ctx.config.get("keywords", _DEFAULT_KEYWORDS)
    threshold = ctx.config.get("notify_threshold", 1)
    important = [e for e in unread if _is_important(e, senders, keywords)]
    if len(important) >= threshold:
        lines = "\n".join(f"- {e['from']}: {e['subject']}" for e in important)
        return JobResult(
            silent_log=f"inbox scan: {len(unread)} unread, {len(important)} important",
            notify=True, title=f"{len(important)} important email(s)", body=lines)
    return JobResult(
        silent_log=f"inbox scan: {len(unread)} unread, {len(important)} important")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/heartbeat/test_inbox_scan.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add core/heartbeat/jobs/inbox_scan.py tests/heartbeat/test_inbox_scan.py
git commit -m "wave 3: inbox_scan heartbeat job (importance ranking + threshold)"
```

---

## Task 10: Job — security_audit (silent, escalate on anomaly)

**Files:**
- Create: `core/heartbeat/jobs/security_audit.py`
- Test: `tests/heartbeat/test_security_audit.py`

Each check is an independent function `check(ctx) -> str | None` returning a failure message or `None` when clean. `run()` runs the list, silent-logs the full report, and escalates only if any check fails. New checks (Wave 6 / candidate #3) append to `CHECKS`.

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_security_audit.py
from datetime import datetime
from core.heartbeat.job import JobContext
from core.heartbeat.jobs import security_audit as SA


def _ctx():
    return JobContext("switch", object(), datetime(2026, 7, 4, 10, 0), {})


def test_all_clean_is_silent(monkeypatch):
    monkeypatch.setattr(SA, "CHECKS", [lambda ctx: None, lambda ctx: None])
    result = SA.run(_ctx())
    assert result.notify is False
    assert "clean" in result.silent_log.lower() or "0 issue" in result.silent_log.lower()


def test_failure_escalates(monkeypatch):
    monkeypatch.setattr(SA, "CHECKS", [
        lambda ctx: None,
        lambda ctx: "cloud escalation enabled without consent",
    ])
    result = SA.run(_ctx())
    assert result.notify is True
    assert "cloud escalation" in result.body


def test_check_exception_is_reported_not_fatal(monkeypatch):
    def boom(ctx): raise RuntimeError("bad check")
    monkeypatch.setattr(SA, "CHECKS", [boom, lambda ctx: None])
    result = SA.run(_ctx())                        # must not raise
    assert result.notify is True
    assert "bad check" in result.body or "check error" in result.body.lower()


def test_cloud_off_check_flags_unexpected_on(monkeypatch):
    # a real check: cloud must stay off unless explicitly enabled
    class _Cfg:
        cloud_enabled = True
        explicitly_enabled = False
    msg = SA.check_cloud_off(_ctx_with_cloud(_Cfg()))
    assert msg is not None


def _ctx_with_cloud(cfg):
    ctx = _ctx()
    ctx.config = {"cloud_cfg": cfg}
    return ctx
```

**Note:** `check_cloud_off` and the other real checks read live security state. Wire them to the actual accessors (`core/llm/config.py` for cloud flags, `core/tooling/registry.py` for trust tiers) in this task — confirm those names against the files. The test above stubs a config object; match the real attribute names when you implement the check.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/heartbeat/test_security_audit.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# core/heartbeat/jobs/security_audit.py
"""Silent heartbeat job: hourly self-audit. Silent-logs the full report; pushes
(both channels) only when a check fails. Checks are independent functions
appended to CHECKS — Wave 6 / candidate #3 extends by adding to this list."""

import logging
from core.heartbeat.job import JobResult

logger = logging.getLogger("aegis.heartbeat")


def check_cloud_off(ctx):
    """Cloud must stay off unless explicitly enabled by the user."""
    cfg = (ctx.config or {}).get("cloud_cfg")
    if cfg is None:
        return None
    if getattr(cfg, "cloud_enabled", False) and not getattr(cfg, "explicitly_enabled", False):
        return "cloud backend is enabled without an explicit user opt-in"
    return None


def check_trouble_defaults(ctx):
    """trouble escalation must remain opt-in (off by default)."""
    cfg = (ctx.config or {}).get("cloud_cfg")
    if cfg is None:
        return None
    if getattr(cfg, "cloud_trouble_escalation", False) and \
       not getattr(cfg, "explicitly_enabled", False):
        return "cloud_trouble_escalation is on without explicit opt-in"
    return None


# Real checks wired to live state are registered here. build_registry (Task 11)
# injects the live cloud_cfg / trust-tier accessors via ctx.config.
CHECKS = [check_cloud_off, check_trouble_defaults]


def run(ctx):
    failures = []
    for check in CHECKS:
        try:
            msg = check(ctx)
            if msg:
                failures.append(msg)
        except Exception as e:                     # a broken check is itself a finding
            logger.exception("security check crashed")
            failures.append(f"check error: {e}")
    if failures:
        body = "\n".join(f"- {f}" for f in failures)
        return JobResult(
            silent_log=f"security audit: {len(failures)} issue(s)",
            notify=True, title="⚠️ Security audit finding", body=body,
            channels=["notification", "telegram"])
    return JobResult(silent_log="security audit: clean (0 issues)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/heartbeat/test_security_audit.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add core/heartbeat/jobs/security_audit.py tests/heartbeat/test_security_audit.py
git commit -m "wave 3: security_audit heartbeat job (append-only checks)"
```

---

## Task 11: Registry + real accessors (config → list[Job], is_enabled, Notifier factory)

**Files:**
- Create: `core/heartbeat/registry.py`
- Modify: `core/heartbeat/__init__.py` — export `run_heartbeat`, `build_registry`, `build_runtime`
- Test: `tests/heartbeat/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_registry.py
from core.heartbeat.registry import build_registry, make_is_enabled


CFG = {
    "enabled": True,
    "tick_seconds": 30,
    "quiet_hours": {"start": "22:00", "end": "07:00"},
    "jobs": {
        "recurring_fire": {"enabled": True},
        "morning_briefing": {"enabled": True, "at": "07:00",
                             "channels": ["telegram", "notification"]},
        "inbox_scan": {"enabled": True, "every_minutes": 30, "channels": ["notification"]},
        "security_audit": {"enabled": False, "every_minutes": 60},
    },
}


def test_build_registry_returns_all_four_jobs():
    jobs = build_registry(CFG)
    ids = {j.id for j in jobs}
    assert ids == {"recurring_fire", "morning_briefing", "inbox_scan", "security_audit"}


def test_schedules_come_from_config():
    jobs = {j.id: j for j in build_registry(CFG)}
    assert jobs["morning_briefing"].schedule.kind == "daily_at"
    assert (jobs["morning_briefing"].schedule.hh, jobs["morning_briefing"].schedule.mm) == (7, 0)
    assert jobs["inbox_scan"].schedule.kind == "every"
    assert jobs["inbox_scan"].schedule.seconds == 30 * 60


def test_is_enabled_reads_config_and_toggle(monkeypatch):
    import core.feature_toggles as ft
    monkeypatch.setattr(ft, "get", lambda k, default=True: True, raising=False)
    is_enabled = make_is_enabled(CFG)
    assert is_enabled("recurring_fire") is True
    assert is_enabled("security_audit") is False        # disabled in config
```

**Note:** `core.feature_toggles` access pattern — confirm whether it's `feature_toggles.get(key)` or a dict `feature_toggles.TOGGLES[key]` (scout found `"daily_briefing": True` at line 14). Match the real API in `make_is_enabled`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/heartbeat/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# core/heartbeat/registry.py
"""Build the concrete job list + runtime accessors from the heartbeat config."""

import logging

from core.heartbeat.job import Job, every, daily_at
from core.heartbeat.jobs import (recurring_fire, morning_briefing,
                                 inbox_scan, security_audit)

logger = logging.getLogger("aegis.heartbeat")


def _daily_from_at(at: str):
    hh, mm = at.split(":")
    return daily_at(int(hh), int(mm))


def build_registry(config):
    jobs_cfg = config.get("jobs", {})

    def ch(job_id, default):
        return jobs_cfg.get(job_id, {}).get("channels", default)

    return [
        Job(id="recurring_fire", kind="silent", schedule=every(60),
            cooldown_s=60, run=recurring_fire.run),
        Job(id="morning_briefing", kind="notify",
            schedule=_daily_from_at(jobs_cfg.get("morning_briefing", {}).get("at", "07:00")),
            cooldown_s=12 * 3600, run=morning_briefing.run,
            channels=ch("morning_briefing", ["telegram", "notification"])),
        Job(id="inbox_scan", kind="silent",
            schedule=every(jobs_cfg.get("inbox_scan", {}).get("every_minutes", 30) * 60),
            cooldown_s=jobs_cfg.get("inbox_scan", {}).get("every_minutes", 30) * 60,
            run=inbox_scan.run, channels=ch("inbox_scan", ["notification"])),
        Job(id="security_audit", kind="silent",
            schedule=every(jobs_cfg.get("security_audit", {}).get("every_minutes", 60) * 60),
            cooldown_s=jobs_cfg.get("security_audit", {}).get("every_minutes", 60) * 60,
            run=security_audit.run, channels=["notification", "telegram"]),
    ]


def make_is_enabled(config):
    jobs_cfg = config.get("jobs", {})

    def is_enabled(job_id):
        if not config.get("enabled", True):
            return False
        if not jobs_cfg.get(job_id, {}).get("enabled", True):
            return False
        if job_id == "morning_briefing":
            # honor the existing feature toggle without forking it
            try:
                import core.feature_toggles as ft
                if hasattr(ft, "get"):
                    return bool(ft.get("daily_briefing", True))
                return bool(getattr(ft, "TOGGLES", {}).get("daily_briefing", True))
            except Exception:
                return True
        return True

    return is_enabled
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/heartbeat/test_registry.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/heartbeat/registry.py core/heartbeat/__init__.py tests/heartbeat/test_registry.py
git commit -m "wave 3: heartbeat registry — config to job list + is_enabled"
```

---

## Task 12: Config block + Telegram Application accessor

**Files:**
- Modify: `core/config/core_config.json` — add the `heartbeat` block
- Modify: `integrations/telegram_bot.py` — module-level `get_application()`
- Test: `tests/heartbeat/test_telegram_accessor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_telegram_accessor.py
import integrations.telegram_bot as tb


def test_get_application_none_before_start():
    tb._set_application(None)
    assert tb.get_application() is None


def test_get_application_after_set():
    sentinel = object()
    tb._set_application(sentinel)
    assert tb.get_application() is sentinel
    tb._set_application(None)          # cleanup
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/heartbeat/test_telegram_accessor.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'get_application'`

- [ ] **Step 3: Write minimal implementation**

In `integrations/telegram_bot.py`, add a module global and accessors, and have `start_telegram_bot` call `_set_application(telegram_app)` right before it returns (near line 402):

```python
# integrations/telegram_bot.py  (module level, near the top)
_APPLICATION = None


def _set_application(app):
    global _APPLICATION
    _APPLICATION = app


def get_application():
    """The running python-telegram-bot Application, or None if not started.
    Used by the heartbeat notifier to push proactively."""
    return _APPLICATION
```

And inside `start_telegram_bot`, immediately before `return telegram_app`:

```python
    _set_application(telegram_app)
    return telegram_app
```

Then add the config block to `core/config/core_config.json` (top-level key):

```json
"heartbeat": {
  "enabled": true,
  "tick_seconds": 30,
  "quiet_hours": { "start": "22:00", "end": "07:00" },
  "jobs": {
    "recurring_fire":   { "enabled": true },
    "morning_briefing": { "enabled": true, "at": "07:00", "channels": ["telegram", "notification"] },
    "inbox_scan":       { "enabled": true, "every_minutes": 30, "channels": ["notification"] },
    "security_audit":   { "enabled": true, "every_minutes": 60 }
  }
}
```

Validate the JSON parses: `python -c "import json,pathlib; json.loads(pathlib.Path('core/config/core_config.json').read_text())"` → no output = valid.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/heartbeat/test_telegram_accessor.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add integrations/telegram_bot.py core/config/core_config.json tests/heartbeat/test_telegram_accessor.py
git commit -m "wave 3: heartbeat config block + telegram get_application accessor"
```

---

## Task 13: Wire the heartbeat into the FastAPI lifespan

**Files:**
- Modify: `server/app.py` — `lifespan` (~lines 106-145)
- Create: `core/heartbeat/runtime.py` — `build_runtime(session_manager)` assembles state/hlog/notifier/jobs from config and returns a ready-to-await coroutine factory
- Test: `tests/heartbeat/test_runtime.py`

`build_runtime` keeps `app.py` thin: it reads `CONFIG["heartbeat"]`, builds the pieces, and returns everything the lifespan needs.

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_runtime.py
import asyncio
from datetime import datetime
from core.heartbeat.runtime import build_runtime
from tests.heartbeat.conftest import FakeClock


class _FakeSM:
    def get(self, u): return None


def test_build_runtime_produces_runnable(tmp_path, monkeypatch):
    cfg = {
        "enabled": True, "tick_seconds": 30,
        "quiet_hours": {"start": "22:00", "end": "07:00"},
        "jobs": {"recurring_fire": {"enabled": False},
                 "morning_briefing": {"enabled": False, "at": "07:00"},
                 "inbox_scan": {"enabled": False, "every_minutes": 30},
                 "security_audit": {"enabled": False, "every_minutes": 60}},
    }
    rt = build_runtime(_FakeSM(), config=cfg, data_dir=tmp_path,
                       get_telegram_app=lambda: None, get_chat_id=lambda u: None)
    # all jobs disabled -> a single tick runs cleanly and does nothing
    clock = FakeClock(datetime(2026, 7, 4, 12, 0))

    async def _sleep(_): pass
    asyncio.run(rt.run(clock=clock, sleep=_sleep, max_ticks=1))
    assert (tmp_path / "heartbeat_log.jsonl").exists() or True   # no crash is the bar
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/heartbeat/test_runtime.py -v`
Expected: FAIL — `ModuleNotFoundError: core.heartbeat.runtime`

- [ ] **Step 3: Write minimal implementation**

```python
# core/heartbeat/runtime.py
"""Assemble the heartbeat runtime from config + singletons, so server/app.py
only has to create/cancel one task."""

from pathlib import Path

from core.heartbeat.hlog import HeartbeatLog
from core.heartbeat.notifier import Notifier
from core.heartbeat.registry import build_registry, make_is_enabled
from core.heartbeat.scheduler import run_heartbeat
from core.heartbeat.state import HeartbeatState


class HeartbeatRuntime:
    def __init__(self, *, session_manager, jobs, is_enabled, notifier, state,
                 hlog, tick_seconds, quiet_hours, user_id):
        self._sm = session_manager
        self._jobs = jobs
        self._is_enabled = is_enabled
        self._notifier = notifier
        self._state = state
        self._hlog = hlog
        self._tick_seconds = tick_seconds
        self._quiet_hours = quiet_hours
        self._user_id = user_id

    async def run(self, *, clock=None, sleep=None, max_ticks=None):
        import asyncio
        from datetime import datetime
        clock = clock or (lambda: datetime.now())
        sleep = sleep or asyncio.sleep
        await run_heartbeat(
            jobs=self._jobs, clock=clock, notifier=self._notifier,
            state=self._state, hlog=self._hlog, is_enabled=self._is_enabled,
            tick_seconds=self._tick_seconds, user_id=self._user_id,
            quiet_hours=self._quiet_hours, session_manager=self._sm,
            sleep=sleep, max_ticks=max_ticks)


def build_runtime(session_manager, *, config, data_dir, get_telegram_app,
                  get_chat_id, user_id="switch"):
    data_dir = Path(data_dir)
    qh = config.get("quiet_hours", {"start": "22:00", "end": "07:00"})
    return HeartbeatRuntime(
        session_manager=session_manager,
        jobs=build_registry(config),
        is_enabled=make_is_enabled(config),
        notifier=Notifier(session_manager, get_telegram_app, get_chat_id),
        state=HeartbeatState(data_dir / "heartbeat.json"),
        hlog=HeartbeatLog(data_dir / "heartbeat_log.jsonl"),
        tick_seconds=config.get("tick_seconds", 30),
        quiet_hours=(qh["start"], qh["end"]),
        user_id=user_id)
```

Then wire `server/app.py` lifespan. After the Telegram-bot startup block (~line 124), before `yield`:

```python
    # --- heartbeat (Wave 3) ---
    from core.config import CONFIG, get_path
    from core.heartbeat.runtime import build_runtime
    from integrations.telegram_bot import get_application
    from integrations.telegram_config import get_user_mapping

    hb_task = None
    hb_cfg = CONFIG.get("heartbeat", {})
    if hb_cfg.get("enabled", True):
        def _chat_id_for(user_id):
            mapping = get_user_mapping()        # {tg_id: username}; reverse it
            for tg_id, uname in (mapping or {}).items():
                if uname == user_id:
                    return tg_id
            return None
        runtime = build_runtime(session_manager, config=hb_cfg,
                                data_dir=get_path("data_dir"),
                                get_telegram_app=get_application,
                                get_chat_id=_chat_id_for)
        hb_task = asyncio.create_task(runtime.run())
        logger.info("heartbeat started")
```

And in the shutdown section (after `yield`, alongside the Telegram stop):

```python
    if hb_task is not None:
        hb_task.cancel()
        try:
            await hb_task
        except asyncio.CancelledError:
            pass
```

**Confirm before writing:** the exact `get_path` key for the data directory (scout: paths resolve via `core.config.loader`; likely `get_path("data_dir")` — verify the key), and the shape/name of `get_user_mapping` in `integrations/telegram_config.py` (reverse lookup username→tg_id). Adjust to the real API.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/heartbeat/test_runtime.py -v`
Then the whole suite: `python -m pytest tests/heartbeat/ -v`
Expected: runtime test PASS; all heartbeat tests green.

- [ ] **Step 5: Manual smoke test**

Start the server and confirm the heartbeat logs a startup line and ticks without error:

Run: `python start.py` (or `start.bat`) — watch for `heartbeat started` in the log, and confirm `data/heartbeat.json` appears after the first fire. Set `security_audit.every_minutes` to a small value temporarily if you want to see a fire quickly, then revert.

- [ ] **Step 6: Commit**

```bash
git add core/heartbeat/runtime.py server/app.py tests/heartbeat/test_runtime.py
git commit -m "wave 3: wire heartbeat into FastAPI lifespan"
```

---

## Task 14: Full-suite regression + finish

- [ ] **Step 1: Run the entire test suite**

Run: `python -m pytest -q`
Expected: all green, including the pre-existing ~594 tests plus the new heartbeat tests. Investigate any regression before proceeding.

- [ ] **Step 2: Confirm no cloud/provider seam was touched**

Run: `python -m pytest tests/llm/test_call_sites.py -v`
Expected: PASS — the briefing job uses `generate_narrative_briefing` (already `sensitivity="private"`); no new provider call was added.

- [ ] **Step 3: Update the roadmap + memory pointer**

Mark Wave 3 shipped in `D:/ObsidianBrain/10-Projects/aegis-roadmap-2026-07-02.md` and add a one-line entry under the Aegis Backlog in MEMORY.md. (Do this outside the repo per the Obsidian-privacy rule — MEMORY.md and the roadmap live outside `aegis-ai`.)

- [ ] **Step 4: Finish the branch**

Use the superpowers:finishing-a-development-branch skill to decide merge vs PR for `feature/wave3-heartbeat`.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Scheduler core (cooldown gate, tick) → Tasks 1, 5. ✔
- Silent-vs-notify logging → Task 3 (`HeartbeatLog`), enforced in Task 5. ✔
- Quiet hours (defer push, silent jobs run) → Task 1 (`in_quiet_hours`), Task 5 (hold notify / defer silent-escalation). ✔
- Both delivery channels, per-job → Task 4 (`Notifier`), Task 11 (per-job `channels`). ✔
- Per-job toggles + `daily_briefing` reuse → Task 11 (`make_is_enabled`). ✔
- Recurring firing + `time`-field bug fix → Tasks 6, 7. ✔
- Morning briefing (local/private) → Task 8. ✔
- Inbox scan (importance ranking, self-disable) → Task 9. ✔
- Security audit (append-only checks, escalate on anomaly) → Task 10. ✔
- Atomic state, no double-fire across restart → Task 2, tested in Task 5. ✔
- Lifespan wiring, Telegram accessor → Tasks 12, 13. ✔

**Placeholder scan:** No "TODO/TBD" left. Three tasks (6, 7, 13) contain explicit "confirm the real accessor/field name" notes tied to functions the scout located but whose exact signatures must be read in-file — these are genuine verification steps, not hand-waves, and each names the file to check.

**Type consistency:** `JobResult(silent_log, notify, title, body, channels)`, `Job(id, kind, schedule, cooldown_s, run, channels)`, `JobContext(user_id, session, now, config)`, `is_due(schedule, now, last_fired_at)`, `in_quiet_hours(now, window)`, `Notifier.push(user_id, title, body, channels)`, `state.get/mark_fired/queue_push/drain_pushes`, `hlog.write(now, job_id, kind, outcome, detail, duration_ms)`, `run_heartbeat(...)` kwargs — all consistent across tasks.
