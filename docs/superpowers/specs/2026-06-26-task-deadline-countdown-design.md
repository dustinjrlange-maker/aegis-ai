# Task Deadline Countdown — Design Spec

**Date**: 2026-06-26
**Status**: Approved, ready for implementation plan
**Owner**: Switch

## Overview

Add an optional deadline visualization to Aegis tasks: per-card live countdown text, a hybrid (background drain + bottom strip) progress bar that drains as the deadline approaches, optional `green→amber→red` color shift over the task's lifetime, and a top-of-Task-Manager "NEXT DEADLINE" bar showing the soonest-upcoming deadline across all pending tasks.

Renders on the Task Manager and Daily Briefing surfaces only. Sidebar widget intentionally untouched.

## Goals

- Show task urgency at a glance without clicking into a task
- Live countdown for the spotlight ("next") task
- Two display modes — text countdown and graphical progress bar — independently toggleable
- One new optional data field, no schema migration

## Non-goals

- Per-task color or mode overrides — all controls are global settings
- Recurring-task awareness — first instance is treated like any other task
- Sidebar widget rendering — kept clean per the design call
- Browser-tab title or system-tray badging — out of scope for v1

## Data model

Add one optional field to each task record:

| Field      | Type             | Notes                                            |
|------------|------------------|--------------------------------------------------|
| `due_time` | `str` (nullable) | `"HH:MM"` 24h local. Optional, default `null`.   |

Existing fields are unchanged. Specifically:
- `due` stays `"YYYY-MM-DD"`
- `created` stays an ISO datetime string
- Tasks missing `due_time` are treated as `23:59` local for countdown math (end-of-day default)
- The progress bar's range is `created → due+due_time`. No new `start_at` field needed.

No migration required — the field is nullable.

## Backend changes

**`core/protocols/operations.py`**
- `update_task` allowed-fields set adds `"due_time"`.
- `_parse_natural_date` extended to capture optional trailing time: `"thursday at 5pm"`, `"by 2:30pm"`, `"june 28 at 9"` return both a date and a time component.
- Helper returns `(date_str, time_str_or_none)` instead of just a date when time is present; callers updated.

**`core/session.py`**
- `_handle_add_task` pipe-suffix parser preserves a `"| time: HH:MM"` form when the LLM bracket includes one.

**`server/app.py`**
- `TaskUpdateRequest` model adds `due_time: Optional[str]`.
- `/api/tasks` list endpoint already returns full task records — `due_time` rides along, no code change needed.

No new endpoints. No new auth surface.

## UI: Input

The existing date picker in both the task add form and the per-task edit form gets a sibling `<input type="time">`:

- Optional. Blank by default.
- Same LCARS-blue hue-rotated picker chrome as the existing date input (already shipped in Phase B1.b).
- Form submission sends `due_time` only when set; an empty value sends `null`.

## UI: Per-card visuals (Task Manager + Briefing)

Each task card with a non-null `due` renders up to three additional layers, each gated by its global settings toggle.

### Background drain layer

- Absolutely positioned `<div>`, `inset: 0`, behind the title text.
- `width: <remaining_fraction × 100>%`, low opacity (~12%).
- `pointer-events: none` so clicks pass through to the card.
- CSS transition drives the drain (see Animation lifecycle).

### Bottom strip

- 3px bar pinned to the bottom of the card.
- `width: <remaining_fraction × 100>%`, full opacity, mild glow via `box-shadow`.
- Same CSS transition.

### Countdown text

A chip on the meta row next to the due-date badge. Format scales with remaining time:

| Remaining       | Format     | Tick interval |
|-----------------|------------|---------------|
| > 1 day         | `2d 14h`   | 30s           |
| 1h to 24h       | `5h 22m`   | 30s           |
| Under 1 hour    | `42m 18s`  | 1s            |
| Past deadline   | hidden     | n/a           |

The 1s tick on sub-hour deadlines makes the seconds visibly tick — that's the "live clock" feel. Tick intervals are managed by two global `setInterval`s:

- **1 Hz interval** — updates the top "NEXT DEADLINE" bar and any visible card whose remaining time is under 1 hour.
- **~30 s interval** — updates the rest.

Both intervals iterate visible cards only (`renderTasks` tracks them) so cost stays bounded.

### Remaining-fraction math

`remaining_fraction = (deadline - now) / (deadline - created)`, clamped to `[0, 1]`. Where:

- `deadline` is `due` combined with `due_time` (or `23:59` local if no `due_time`).
- `created` and `deadline` are both treated as local-time `Date` instances.

When `created >= deadline` (the imported-task edge case), `remaining_fraction` is forced to `0`.

### Color logic (shared by all three layers)

- **Static mode**: solid color from the user's `Bar color` setting (LCARS palette).
- **Shift mode**: HSL `hsl(hue, 70%, 55%)` where `hue = 120 × remaining_fraction`. Hue interpolates from `120°` (green) at 100% remaining → `0°` (red) at 0%. Smooth gradient through the lifecycle. No breakpoint thresholds.

The same computed color applies to the background layer (with reduced opacity), the bottom strip, and the text chip.

## UI: Top "NEXT DEADLINE" bar

Pinned to the top of the Task Manager panel, between the title row and the New Task input.

- Format: `NEXT DEADLINE  ▸  <task title>  ·  Xd Yh Zm`
- Countdown ticks every **1s** — this is the spotlight element, accuracy matters.
- Color follows the same Static/Shift logic as the per-card bar.
- Source: pending tasks ordered by `due+due_time` ascending. Take the first task whose deadline is not in the past.
- Hidden when zero qualifying tasks.
- Click handler: scrolls the task list to that card and briefly pulses the card outline (CSS animation) so the user can find it.
- Gated by the `Show countdown text` setting (conceptually it's a countdown, just promoted to its own row).

## UI: Briefing parity

The same hybrid bar treatment renders on Daily Briefing task cards. Implementation:

- A shared helper `_renderTaskDeadlineLayer(t, settings)` is extracted to compute the three layers' markup (or empty string when disabled).
- Both `renderTasks` (Task Manager) and `_briefingTaskCard` (Briefing) call it.
- Visuals are identical across both surfaces.

The Daily Briefing does **not** get its own "NEXT DEADLINE" top bar — Task Manager owns that.

## UI: Settings menu

Surfaced via the existing gear button in the Task Manager panel header.

| Control               | Type            | Default     | Notes                                             |
|-----------------------|-----------------|-------------|---------------------------------------------------|
| Show countdown text   | toggle          | ON          | Also gates the top "NEXT DEADLINE" bar.           |
| Show progress bar     | toggle          | ON          | Gates both background drain and bottom strip.     |
| Color mode            | radio           | Shift       | `Shift` or `Static`.                              |
| Bar color             | palette picker  | LCARS blue  | Visible only when `Color mode = Static`.          |

LCARS palette options for the picker: blue, cyan, amber, orange, red, purple.

Persisted to `localStorage` under key `aegis_task_deadline_settings` as a single JSON blob. Change events fire an immediate re-render of all visible bars.

## Animation lifecycle

- On card render: bars set `width` and `transition-duration` once.
- Transition: `transition: width <remaining_seconds>s linear, background-color <remaining_seconds>s linear`.
- Width starts at current `remaining_fraction × 100%` and animates to `0%` over `remaining_seconds`.
- The browser handles the drain frame-by-frame — no per-frame JS work for the bar.
- A single `visibilitychange` listener on the renderer re-renders all visible bars when the tab becomes visible, correcting drift if the laptop slept through part of the transition.
- Task edits, settings changes, and the existing `_refreshAfterChat()` helper all trigger re-renders naturally.

## Overdue behavior

- Once `now > deadline`, the background drain layer and bottom strip are removed from the DOM.
- The countdown text is hidden.
- The existing overdue treatment (red border, OVERDUE badge in the meta row, Daily Briefing's "Overdue" bucket) takes over unchanged.
- No new post-zero animation. Bar simply disappears.

## Edge cases

| Case                                | Behavior                                                          |
|-------------------------------------|-------------------------------------------------------------------|
| `due` set, `due_time` not set       | Treated as `23:59` local for math.                                |
| No `due` at all                     | Bar and countdown both absent. Card renders as today.             |
| Task created with deadline past     | Renders directly in overdue state. No animation.                  |
| `created > due` (imported tasks)    | Clamp `remaining_fraction` to 0. Bar/countdown absent.            |
| Settings toggle flipped mid-session | All visible cards re-render immediately.                          |

## Implementation surfaces

Files expected to change:

- `core/protocols/operations.py` — `update_task` allowed-fields; `_parse_natural_date` time tail; small NLP regex extensions for "at HH:MM" forms.
- `core/session.py` — `_handle_add_task` parses `"| time: HH:MM"` suffix when LLM brackets include one.
- `server/app.py` — `TaskUpdateRequest` adds `due_time: Optional[str]`.
- `ui/templates/index.html` —
  - Task add/edit form: `<input type="time">` sibling next to existing date input.
  - New `_renderTaskDeadlineLayer(t, settings)` helper.
  - New top "NEXT DEADLINE" bar markup + render function.
  - Settings panel additions for the four new controls.
  - CSS for the two bar layers + transitions.
  - `visibilitychange` listener.
  - `localStorage` helpers for `aegis_task_deadline_settings`.
- `requirements.txt` — no new deps.
- No backend migration, no schema bump, no new endpoints.

## Defaults summary (first-run UX)

| Setting             | Default      |
|---------------------|--------------|
| Show countdown text | ON           |
| Show progress bar   | ON           |
| Color mode          | Shift        |
| Bar color           | LCARS blue   |

First-run users with no deadlined tasks see no new visual chrome. Adding a deadline to any task immediately produces the full hybrid treatment.

## Out of scope (for v1)

- Per-task color overrides
- Customizable color shift breakpoints
- Sound or system-tray notifications when deadlines approach (notifications system handled separately)
- Recurring task lookahead (treat each instance as its own task)
- Browser tab-title countdown
