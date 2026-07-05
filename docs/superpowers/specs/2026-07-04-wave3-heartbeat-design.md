# Wave 3 — Heartbeat: Aegis acts on its own

**Status:** Design approved 2026-07-04
**Scope:** Generic cooldown-gated job scheduler + four first jobs at full depth.
**Roadmap:** Phase 4B candidate #1 (`D:/ObsidianBrain/10-Projects/aegis-roadmap-2026-07-02.md`, Wave 3). Downstream: Wave 6 "dreaming" memory consolidation and the hourly security self-audit ride on this scheduler.

## Goal

Give Aegis a heartbeat: a long-lived background loop that runs recurring jobs on its own, without waiting for the user to send a message. The loop is a **generic scheduler** — not a briefing timer — so later waves (memory "dreaming", extended security audit) drop new jobs onto it without touching the core.

Two hard constraints are committed from day one:

1. **Per-job cooldown gate** (`last_fired_at` / `next_eligible_at`) — the OpenClaw "10s-runaway" guard. Enforced structurally: checked before a run and stamped after.
2. **Structured response logging with a silent-log vs user-notification distinction** — every job run is classified and logged.

## Non-goals

- Cron-expression scheduling. Four jobs need only interval and time-of-day; we do not add a cron parser or APScheduler.
- A distributed / multi-process scheduler. The FastAPI server is a single long-lived process (Wave 7 home server is its natural host); the heartbeat is one in-process `asyncio` task.
- The *full* extended security audit. This increment ships a real, comprehensive-enough audit, but candidate #3's deeper checks remain a later append.

## Approach (chosen: A — in-process asyncio)

One `asyncio` task launched in the FastAPI `lifespan` (`server/app.py` ~line 124, right after the Telegram-bot block), cancelled on shutdown. It ticks on a config interval, walks a job registry, and fires jobs that are both **due** and **past cooldown**. No new dependencies; matches existing patterns (pathlib + JSON storage, in-process singletons, dependency-injected control flow like `run_tool_loop`).

Rejected alternatives:
- **APScheduler** — new dependency (CLAUDE.md discourages), and we would still hand-write the cooldown gate, silent/notify classification, quiet hours, and channel fan-out. Buys cron syntax we do not need.
- **OS scheduler → HTTP endpoint** — splits logic across OS + app, fragile, hard to test; the always-on server already gives us a host.

## Architecture

New package `core/heartbeat/`, laid out like `core/tooling/`:

```
core/heartbeat/
  __init__.py
  job.py         # Job + Schedule + JobResult dataclasses (no logic)
  scheduler.py   # the async loop; DI'd (jobs, clock, notifier, state store)
  notifier.py    # Notifier: fans a notify result to its channels
  state.py       # load/save data/heartbeat.json (atomic write)
  hlog.py        # structured heartbeat_log.jsonl writer
  registry.py    # builds the list[Job] from config
  jobs/
    __init__.py
    recurring_fire.py
    morning_briefing.py
    inbox_scan.py
    security_audit.py
```

### Job model (`job.py`)

Plain dataclasses, no behavior:

```python
Schedule            # tagged: every(seconds=...) OR daily_at(hh, mm)
Job(
  id:         str            # "recurring_fire", "morning_briefing", ...
  kind:       str            # "silent" | "notify"  (normal disposition)
  schedule:   Schedule
  cooldown_s: int            # hard floor between fires — runaway guard
  channels:   list[str]      # ["notification","telegram"]; ignored for silent
  run:        Callable       # run(ctx) -> JobResult
)
JobResult(
  silent_log: str            # always written to the structured log
  notify:     bool           # request a user push (a silent job may set this)
  title:      str = ""
  body:       str = ""
  channels:   list[str] | None = None   # override job.channels if set
)
```

`kind` is the job's *normal* disposition and drives default logging. A silent job may still set `notify=True` on its result to **escalate on anomaly** (the security-audit case). This keeps the silent-vs-notify distinction explicit while allowing a silent job to ping only when something is wrong.

### Scheduler loop (`scheduler.py`)

Dependency-injected so it is unit-testable with a fake clock (no real sleeping), same pattern as `core/tooling/autocall.py::run_tool_loop`:

```python
async def run_heartbeat(*, jobs, clock, notifier, state, hlog, config,
                        tick_seconds, quiet_hours, sleep=asyncio.sleep):
```

Per tick, for each job:

1. **Enabled?** — config `heartbeat.jobs.<id>.enabled` (and, for the briefing, the existing `feature_toggles["daily_briefing"]`). If off → `outcome=skipped_disabled`, continue.
2. **Due?** — `every(...)`: `now - last_fired_at >= interval`. `daily_at(hh,mm)`: the wall-clock time has reached `hh:mm` today and it has not fired today. If not due → no log, continue.
3. **Cooldown gate** — if `now < next_eligible_at` → `outcome=skipped_cooldown`, continue. (Belt-and-suspenders with the schedule; the guarantee is a job can never fire twice inside `cooldown_s`.)
4. **Quiet hours** — quiet hours never stops a job from *running*; it only defers a *push*.
   - A **notify-kind** job coming due inside the window is held (not run) and becomes eligible at window end — its whole purpose is the push, so deferring the push defers the job. `outcome=skipped_quiet`.
   - A **silent** job always runs. If its result escalates to `notify` during the window (e.g. a 3am security anomaly), the run's silent-log is written immediately and the **push is queued and delivered at window end** — no 3am ping, but the finding is recorded the moment it is found.
5. **Run** — inside `try/except`. On success: stamp `last_fired_at=now`, `next_eligible_at=now + cooldown_s`, persist state, write the structured log, and if the result requests notify, hand it to the `Notifier`. On exception: `logger.exception`, `outcome=error`; the loop and sibling jobs are unaffected (graceful degradation, CLAUDE.md).

The loop never lets one job's failure kill the tick. State is persisted after every fire so a restart cannot double-fire.

### Delivery (`notifier.py`)

`Notifier.push(user_id, title, body, channels)` fans out:

- `"notification"` → `session.notification_service.add(type=, title=, body=)` (exists today; used in `chat_pipeline.py:326`).
- `"telegram"` → send via the bot `Application`. The ref is currently lifespan-local (`app.py:119`); we expose a module accessor `integrations/telegram_bot.py::get_application()` and resolve `user_id → chat_id` through `integrations/telegram_config.get_user_mapping`. If Telegram is not wired for that user, degrade to notification-only and log it (never raise).

### Quiet hours

Config window (default `22:00–07:00`). Quiet hours defers pushes, never runs. A notify-kind job coming due inside the window is **held**, not dropped: it becomes eligible when the window closes, so the 07:00 briefing lands exactly as the window ends. Silent jobs (recurring firing, the audit's normal path) always run; if a silent job escalates to a push during the window, the finding is logged immediately and the push is queued for window end.

## The four jobs

### 1. Recurring-task firing — `jobs/recurring_fire.py`
- **kind** silent · **schedule** `every(60s)` · **cooldown** 60s · **channels** — (silent)
- Drives `ops.check_recurring(now)` from the heartbeat instead of only on a user message.
- **Bug fix:** `core/protocols/operations.py::check_recurring()` (line 226) currently matches on date only; the `time` field (stored in `add_recurring`, line 197) is never read. We extend `check_recurring` to accept `now` and honor `time`, so a recurring task set for 09:00 materializes at 09:00, not at the user's first message of the day.
- Silent-logs what it fired; created tasks surface through existing task/notification generation.

### 2. Morning briefing push — `jobs/morning_briefing.py`
- **kind** notify · **schedule** `daily_at(07:00)` · **cooldown** 12h · **channels** `["telegram","notification"]`
- Calls `core/briefing.py::generate_narrative_briefing(session, period="morning")` (line 227; already `sensitivity="private"`, stays local).
- Honors the existing `feature_toggles["daily_briefing"]`. Lands as the quiet window closes.

### 3. Inbox scan — `jobs/inbox_scan.py`
- **kind** notify-conditional · **schedule** `every(30m)` · **cooldown** 30m · **channels** `["notification"]`
- Reuses the existing chat-driven email / EmailOps seam to pull unread, then applies an **importance ranking** (known/important senders + keyword signals) held in a small, swappable function.
- Notifies past a threshold; otherwise silent-logs the unread count.
- If email is not configured for the user, the job self-disables (config or a one-time flag) and logs once — it does not raise or spam.

### 4. Security self-audit — `jobs/security_audit.py`
- **kind** silent, escalates on anomaly · **schedule** `every(1h)` · **cooldown** 1h
- A list of independent check functions (append-only, so Wave 6 / candidate #3 extends without a rewrite):
  - cloud stays off unless explicitly enabled;
  - no unexpected tool trust-tier escalations;
  - `cloud_trouble_escalation` / `trouble_private_consent` defaults intact;
  - cloud-key file present-and-sane only when cloud is on;
  - data-dir sanity;
  - no lingering PIN bypass.
- Always silent-logs the full report. Sets `notify=True` (both channels) **only if a check fails.**
- Largest sub-piece; kept cleanly severable into its own implementation plan if scope grows.

All four are assembled into a single `list[Job]` by `registry.py` from config.

## Config

New block in `core/config/core_config.json`:

```json
"heartbeat": {
  "enabled": true,
  "tick_seconds": 30,
  "quiet_hours": { "start": "22:00", "end": "07:00" },
  "jobs": {
    "recurring_fire":   { "enabled": true },
    "morning_briefing": { "enabled": true, "at": "07:00", "channels": ["telegram","notification"] },
    "inbox_scan":       { "enabled": true, "every_minutes": 30, "channels": ["notification"] },
    "security_audit":   { "enabled": true, "every_minutes": 60 }
  }
}
```

Per-job `enabled` is the user-facing toggle. The briefing job additionally honors `feature_toggles["daily_briefing"]` (not forked). All schedule params are config-driven — nothing hardcoded.

## State & logging

- **State** — `data/heartbeat.json`: `{ job_id: { last_fired_at, next_eligible_at } }`. Loaded at startup, rewritten after each fire via atomic temp-file + `os.replace` so a crash mid-write cannot corrupt it or cause a double-fire.
- **Structured log** — `data/heartbeat_log.jsonl`, one record per job per tick that did something: `{ts, job_id, kind, outcome, detail, duration_ms}` with `outcome ∈ {silent_log, notified, skipped_cooldown, skipped_quiet, skipped_disabled, error}`. Size-capped with simple truncation. This is the roadmap's structured silent-log-vs-notify record.
- **Operational log** — `logging.getLogger("aegis.heartbeat")`, `logger.exception` on any job error.

## Wiring

`server/app.py` lifespan (~line 124): after the Telegram-bot block and before `yield`, build the registry from config and `task = asyncio.create_task(run_heartbeat(...))`. In shutdown (after `yield`): `task.cancel()` and await it. The `session_manager` singleton (`app.py:60`) is in scope and gives jobs direct access to session state, `notification_service`, `ops`, and `event_manager`.

## Testing (`tests/heartbeat/`, pytest)

All scheduler tests use an **injected fake clock** — no real sleeping. LLM-touching bits use injected fakes; no real Ollama (CLAUDE.md).

- **Scheduler:** cooldown gate blocks a re-fire inside the window; `every` / `daily_at` due-logic; quiet-hours holds a notify job then fires it at window end; one job raising does not kill the loop or siblings; persisted state prevents double-fire across a simulated restart; disabled job skipped.
- **Notifier:** fans to both channels; Telegram-missing degrades to notification-only; silent job never pushes; anomaly result escalates to a push.
- **Jobs:**
  - `recurring_fire` honors the `time` field — fires at/after the time, not before (the bug-fix assertion);
  - `morning_briefing` invokes `generate_narrative_briefing`;
  - `inbox_scan` ranking / threshold / self-disable-when-no-email;
  - `security_audit` — each check, silent-log on a clean run, push on an injected failure.
- **Integration:** a light test that lifespan startup creates the task and shutdown cancels it.

## Files touched

- **New:** `core/heartbeat/` package (8 modules + `jobs/` with 4 job files), `tests/heartbeat/`.
- **Edited (additive):** `server/app.py` (lifespan wiring), `core/config/core_config.json` (heartbeat block), `integrations/telegram_bot.py` (`get_application()` accessor), `core/protocols/operations.py` (`check_recurring` honors `time`).

## Open risks

- **Inbox scan** has the fuzziest external dependency (email config + the ranking heuristic is net-new). Kept behind a self-disable so an unconfigured mailbox degrades quietly.
- **Security audit** is the largest sub-piece; if implementation shows it sprawling, split it into its own plan and ship the scheduler + other three jobs first — the job model makes this severable.
