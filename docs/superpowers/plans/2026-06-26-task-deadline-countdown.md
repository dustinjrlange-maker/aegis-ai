# Task Deadline Countdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the task deadline countdown feature per `docs/superpowers/specs/2026-06-26-task-deadline-countdown-design.md` — optional `due_time` field, per-card hybrid progress bar with countdown text, top-of-Task-Manager "NEXT DEADLINE" bar, settings, and animation lifecycle.

**Architecture:** Small backend change (one optional field + NLP parser extension). Bulk of work is frontend in `ui/templates/index.html` — pure helpers for math/color, one rendering helper shared by Task Manager and Daily Briefing, two-interval tick system with CSS-driven bar animation, plus settings UI and the new top "NEXT DEADLINE" bar.

**Tech Stack:** Python 3.12, FastAPI, pytest for backend tests, plain JS in a single PWA HTML file (no frontend framework, no JS test runner — manual smoke tests for UI).

---

## File Structure

**Create:**
- `tests/test_operations_deadline.py` — backend pytest covering `due_time` field roundtrip and the datetime NLP parser

**Modify:**
- `core/protocols/operations.py` — `_load_tasks` migration, `add_task` signature, `update_task` allowed-fields, new `_parse_natural_datetime` classmethod
- `core/session.py` — `_handle_add_task` parses optional `| time: HH:MM` suffix
- `server/app.py` — `TaskUpdateRequest` adds `due_time`; `/api/tasks` add path forwards `due_time`
- `ui/templates/index.html` —
  - Add form: `<input type="time">` sibling to `taskDueDate`
  - Edit form: same
  - `addTask()` and `saveTask()`: read and forward `due_time`
  - New pure helpers: `_computeTaskDeadline`, `_computeRemainingFraction`, `_formatCountdown`, `_deadlineColor`
  - New rendering helper: `_renderTaskDeadlineLayer(t, settings)`
  - Wire helper into `renderTasks` and `_briefingTaskCard`
  - New top "NEXT DEADLINE" bar markup + `renderNextDeadlineBar()` function
  - Settings panel additions + localStorage helpers (`aegis_task_deadline_settings`)
  - CSS for bar layers, transitions, and pulse animation
  - Two `setInterval`s (1Hz, 30s) and one `visibilitychange` listener

---

## Task 1: Backend data model — add `due_time` field

**Files:**
- Modify: `core/protocols/operations.py:142-156` (`_load_tasks` defaults), `:618-678` (`add_task`), `:748-756` (`update_task` allowed-fields)
- Modify: `server/app.py:226-233` (`TaskUpdateRequest`)
- Modify: `server/app.py` (task add action — search for `action == "add"` inside the `/api/tasks` endpoint and ensure `due_time` is forwarded)
- Create: `tests/test_operations_deadline.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_operations_deadline.py`:

```python
import tempfile
import pytest
from core.protocols.operations import OperationsProtocol


def _make_ops():
    td = tempfile.TemporaryDirectory()
    ops = OperationsProtocol(data_dir=td.name)
    # Keep the TemporaryDirectory alive for the lifetime of the ops object
    ops._tmpdir = td
    return ops


def test_add_task_accepts_due_time():
    ops = _make_ops()
    task = ops.add_task("Smoke", due="2026-06-30", due_time="17:00")
    assert task["due"] == "2026-06-30"
    assert task["due_time"] == "17:00"


def test_add_task_due_time_defaults_to_none():
    ops = _make_ops()
    task = ops.add_task("No time")
    assert task.get("due_time") is None


def test_update_task_due_time():
    ops = _make_ops()
    task = ops.add_task("Edit me")
    updated = ops.update_task(task["id"], due_time="09:30")
    assert updated["due_time"] == "09:30"


def test_due_time_persists_to_disk():
    ops = _make_ops()
    ops.add_task("Persist", due="2026-06-30", due_time="17:00")
    # Reload from same dir
    ops2 = OperationsProtocol(data_dir=ops._tmpdir.name)
    assert ops2._tasks[-1]["due_time"] == "17:00"


def test_due_time_migration_backfills_none_on_old_tasks():
    """Tasks loaded from a tasks.json without due_time should get None."""
    import json, pathlib
    ops = _make_ops()
    old_task = {
        "id": 99, "text": "Legacy", "priority": "normal",
        "category": "general", "due": None, "created": "2026-01-01T00:00:00",
        "completed": False, "completed_at": None,
        "subtasks": [], "starred": False, "activity_type": "general",
        "notes": "", "attachments": []
    }
    p = pathlib.Path(ops._tmpdir.name) / "tasks.json"
    p.write_text(json.dumps([old_task]))
    ops2 = OperationsProtocol(data_dir=ops._tmpdir.name)
    assert "due_time" in ops2._tasks[0]
    assert ops2._tasks[0]["due_time"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_operations_deadline.py -v`
Expected: All 5 tests fail because `add_task` doesn't accept `due_time` and the field doesn't exist.

- [ ] **Step 3: Implement in `core/protocols/operations.py`**

In `_load_tasks` (around line 150), add the new setdefault inside the loop:

```python
        for task in self._tasks:
            task.setdefault("subtasks", [])
            task.setdefault("starred", False)
            task.setdefault("activity_type", "general")
            task.setdefault("notes", "")
            task.setdefault("attachments", [])
            task.setdefault("due_time", None)
```

In `add_task` (line 618), change the signature and the task dict:

```python
    def add_task(self, text, priority="normal", due=None, due_time=None,
                 category="general", activity_type="general"):
```

Inside the same method, in the task dict construction:

```python
        task = {
            "id": len(self._tasks) + 1,
            "text": text,
            "priority": priority,
            "category": category,
            "due": due,
            "due_time": due_time,
            "created": now_ts.isoformat(),
            "completed": False,
            "completed_at": None,
            "subtasks": [],
            "notes": "",
            "starred": False,
            "activity_type": activity_type,
        }
```

In `update_task` (~line 750), extend the allowed-fields set:

```python
        allowed = {"text", "priority", "due", "due_time", "activity_type", "starred", "notes"}
```

- [ ] **Step 4: Implement in `server/app.py`**

Update `TaskUpdateRequest` (line 226):

```python
class TaskUpdateRequest(BaseModel):
    task_id: int
    text: Optional[str] = None
    priority: Optional[str] = None
    due: Optional[str] = None
    due_time: Optional[str] = None
    activity_type: Optional[str] = None
    starred: Optional[bool] = None
    notes: Optional[str] = None
```

Find the `/api/tasks` POST endpoint's `action == "add"` branch and ensure `due_time` is read from `payload` and passed to `ops.add_task(...)`. Concretely, locate the `add_task(...)` call inside the endpoint and add `due_time=payload.get("due_time")` to its kwargs.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_operations_deadline.py -v`
Expected: All 5 tests pass.

- [ ] **Step 6: Commit**

```bash
git add core/protocols/operations.py server/app.py tests/test_operations_deadline.py
git commit -m "feat: add due_time field to task model

Optional HH:MM 24h local string. Persists, round-trips through
add_task and update_task, backfilled to None on legacy load."
```

---

## Task 2: Backend NLP — parse "thursday at 5pm" into date + time

**Files:**
- Modify: `core/protocols/operations.py` (add `_parse_natural_datetime` classmethod after the existing `_parse_natural_date`)
- Modify: `core/session.py:155-188` (`_handle_add_task` parses `| time: HH:MM`)
- Modify: `tests/test_operations_deadline.py` (add NLP tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_operations_deadline.py`:

```python
from core.protocols.operations import OperationsProtocol


def test_parse_datetime_at_5pm():
    d, t = OperationsProtocol._parse_natural_datetime("thursday at 5pm")
    assert d is not None
    assert t == "17:00"


def test_parse_datetime_at_2_30pm():
    d, t = OperationsProtocol._parse_natural_datetime("tomorrow at 2:30pm")
    assert d is not None
    assert t == "14:30"


def test_parse_datetime_at_2_pm_spaced():
    d, t = OperationsProtocol._parse_natural_datetime("tomorrow at 2 pm")
    assert d is not None
    assert t == "14:00"


def test_parse_datetime_by_9am():
    d, t = OperationsProtocol._parse_natural_datetime("friday by 9am")
    assert d is not None
    assert t == "09:00"


def test_parse_datetime_midnight():
    d, t = OperationsProtocol._parse_natural_datetime("today at 12am")
    assert d is not None
    assert t == "00:00"


def test_parse_datetime_noon():
    d, t = OperationsProtocol._parse_natural_datetime("today at 12pm")
    assert d is not None
    assert t == "12:00"


def test_parse_datetime_no_time():
    d, t = OperationsProtocol._parse_natural_datetime("thursday")
    assert d is not None
    assert t is None


def test_parse_datetime_only_time_returns_no_date():
    d, t = OperationsProtocol._parse_natural_datetime("at 5pm")
    # Bare time without a date is ambiguous; spec says return (None, None)
    assert d is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_operations_deadline.py -v -k parse_datetime`
Expected: All 8 tests fail — method doesn't exist.

- [ ] **Step 3: Implement `_parse_natural_datetime` in operations.py**

Add this classmethod immediately after the existing `_parse_natural_date` method (around line 335):

```python
    @classmethod
    def _parse_natural_datetime(cls, text):
        """Parse "<date> at <time>" or "<date> by <time>" into (date_str, time_str).
        Either component can be None. If the text has no recognizable date but
        only a time, returns (None, None) since bare times are ambiguous.
        Time is always returned as 24h "HH:MM"."""
        if not text:
            return (None, None)
        # Split off trailing "at TIME" or "by TIME"
        m = re.search(
            r"\s+(?:at|by)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$",
            text.strip(),
            re.IGNORECASE,
        )
        time_str = None
        date_text = text
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            ampm = (m.group(3) or "").lower()
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            elif not ampm:
                # No am/pm — accept as 24h if hour > 12, else treat as 24h
                pass
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                time_str = f"{hour:02d}:{minute:02d}"
                date_text = text[: m.start()].strip()
            else:
                time_str = None  # invalid time, ignore
        date_str = cls._parse_natural_date(date_text) if date_text else None
        return (date_str, time_str)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_operations_deadline.py -v -k parse_datetime`
Expected: All 8 pass.

- [ ] **Step 5: Update `_handle_add_task` in `core/session.py`**

In `_handle_add_task` (line 155), extend the pipe-suffix parser to also recognize `| time: HH:MM`. Replace the existing loop:

```python
        due = None
        due_time = None
        if "|" in task_text:
            head, tail = task_text.split("|", 1)
            head = head.strip()
            tail_lower = tail.strip().lower()
            for prefix in ("due:", "deadline:", "by:"):
                if tail_lower.startswith(prefix):
                    due_text = tail.strip()[len(prefix):].strip()
                    parser = getattr(ops, "_parse_natural_datetime", None)
                    if parser:
                        parsed_d, parsed_t = parser(due_text)
                        due = parsed_d or due_text
                        if parsed_t:
                            due_time = parsed_t
                    else:
                        due = due_text
                    task_text = head
                    break
            if tail_lower.startswith("time:"):
                t_raw = tail.strip()[len("time:"):].strip()
                m = re.match(r"^(\d{1,2}):(\d{2})$", t_raw)
                if m:
                    hh, mm = int(m.group(1)), int(m.group(2))
                    if 0 <= hh <= 23 and 0 <= mm <= 59:
                        due_time = f"{hh:02d}:{mm:02d}"
                task_text = head

        task = ops.add_task(task_text, due=due, due_time=due_time)
```

Add `import re` at the top of session.py if it's not already imported (check first).

- [ ] **Step 6: Smoke test the session handler**

Append to `tests/test_operations_deadline.py`:

```python
def test_session_handler_parses_time_suffix(monkeypatch):
    """The | time: HH:MM suffix in [ADD_TASK:...] gets routed to due_time."""
    from core.session import SessionManager
    ops = _make_ops()
    # Minimal stub session manager — bypass init since we only need the handler
    sm = SessionManager.__new__(SessionManager)
    sm.protocol_registry = {"operations": ops}
    result = sm._handle_add_task("Pay hydro | due: friday | time: 17:00")
    # Locate the created task
    pending = [t for t in ops._tasks if "Pay hydro" in t["text"]]
    assert len(pending) == 1
    assert pending[0]["due_time"] == "17:00"
```

Note: `protocol_registry` is a dict on the session manager. If the actual class uses `protocol_registry.get(...)`, the dict pattern works. If it uses a different attribute name, adjust the stub. Quick verify: `grep -n 'protocol_registry' core/session.py` and confirm before running.

Run: `pytest tests/test_operations_deadline.py -v`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add core/protocols/operations.py core/session.py tests/test_operations_deadline.py
git commit -m "feat: NLP datetime parsing — capture optional time tail

Adds _parse_natural_datetime returning (date, time). Recognizes
'at 5pm', 'at 2:30pm', 'by 9am', noon/midnight. Session handler
parses | time: HH:MM suffix from bracket commands so the LLM can
emit a precise time alongside an existing date."
```

---

## Task 3: HTML time picker on add + edit forms

**Files:**
- Modify: `ui/templates/index.html:6225-6234` (task add row)
- Modify: `ui/templates/index.html` (search for the per-task edit form — `task-edit-form` / `taskEditDueDate` — and add a sibling time input)
- Modify: `ui/templates/index.html:8925` (`addTask()`)
- Modify: `ui/templates/index.html` (`saveTask()`)

- [ ] **Step 1: Add time input to the add row**

In the add row (around line 6233), insert a new `<input type="time">` immediately after `taskDueDate`:

```html
            <input type="date" id="taskDueDate" aria-label="Due date" style="width:140px;" onclick="if(this.showPicker)this.showPicker()" onfocus="if(this.showPicker)this.showPicker()">
            <input type="time" id="taskDueTime" aria-label="Due time (optional)" style="width:100px;" onclick="if(this.showPicker)this.showPicker()">
            <button onclick="addTask()">ADD</button>
```

- [ ] **Step 2: Update `addTask()` to forward `due_time`**

In `addTask()` (line 8925), find where it reads `dueEl.value` and add a time read + send. Locate the existing fetch body that includes `action: 'add', text: ...` and add `due_time` to the JSON body:

```javascript
async function addTask() {
    var input = document.getElementById('taskInput');
    var priEl = document.getElementById('taskPriority');
    var dueEl = document.getElementById('taskDueDate');
    var timeEl = document.getElementById('taskDueTime');
    var text = input.value.trim();
    if (!text) return;
    var body = {
        action: 'add',
        text: text,
        priority: priEl.value,
        due: dueEl.value || null,
        due_time: (timeEl && timeEl.value) || null,
    };
    var res = await authFetch(API + '/tasks', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
    });
    if (res.ok) {
        input.value = '';
        dueEl.value = '';
        if (timeEl) timeEl.value = '';
        await loadTasks();
        _refreshBriefingIfOpen();
    }
}
```

Note: the exact existing function body may differ slightly — preserve any existing fields (priority, etc.) and only add `due_time` and the `timeEl.value = ''` reset line.

- [ ] **Step 3: Add time input to the edit form**

Search the file for `taskEditDueDate` (or whatever id the edit form uses). Add an `<input type="time">` next to it with id `taskEditDueTime` and the same `showPicker` handler.

If the edit form lives inside a template string in `_editTask` / `_renderTaskEdit` / similar, modify the template to interpolate `t.due_time || ''` into the new time input's value.

- [ ] **Step 4: Update `saveTask()` to forward `due_time`**

Find `saveTask(id)`. After it reads the date input, also read the time input and include `due_time` in the JSON body sent to `/api/tasks/update`:

```javascript
    var dueTimeEl = document.getElementById('taskEditDueTime_' + id) || document.getElementById('taskEditDueTime');
    var due_time = (dueTimeEl && dueTimeEl.value) || null;
    // ... existing body construction ...
    body.due_time = due_time;
```

Use whichever id pattern the existing edit form follows — match the existing date input's id construction exactly.

- [ ] **Step 5: Manual smoke test**

1. Restart Aegis (tray Exit → relaunch). Open Task Manager.
2. Add a task with both a date AND a time. Confirm: appears in the list (no countdown yet — that's later).
3. Click EDIT on the task. Confirm: the time field shows the saved value.
4. Change the time, save. Confirm: change persists across a Ctrl+R reload.
5. Add a task with date only (no time). Confirm: saves cleanly, edit form shows empty time field.

- [ ] **Step 6: Commit**

```bash
git add ui/templates/index.html
git commit -m "feat: time picker on task add and edit forms

Optional <input type='time'> sibling to the existing date picker.
addTask and saveTask forward due_time through to the API."
```

---

## Task 4: Settings panel + localStorage helpers

**Files:**
- Modify: `ui/templates/index.html` — add settings panel section, localStorage helpers, defaults

- [ ] **Step 1: Add the localStorage helper pair**

Insert near the top of the `<script>` block (look for other `localStorage.getItem('aegis_...')` helpers, group with them):

```javascript
const DEADLINE_SETTINGS_KEY = 'aegis_task_deadline_settings';
const DEADLINE_SETTINGS_DEFAULTS = {
    showText: true,
    showBar: true,
    colorMode: 'shift',     // 'shift' or 'static'
    staticColor: '#5599ff', // LCARS blue default
};

function getDeadlineSettings() {
    try {
        var raw = localStorage.getItem(DEADLINE_SETTINGS_KEY);
        if (!raw) return Object.assign({}, DEADLINE_SETTINGS_DEFAULTS);
        var parsed = JSON.parse(raw);
        return Object.assign({}, DEADLINE_SETTINGS_DEFAULTS, parsed);
    } catch (e) {
        return Object.assign({}, DEADLINE_SETTINGS_DEFAULTS);
    }
}

function setDeadlineSettings(patch) {
    var current = getDeadlineSettings();
    var next = Object.assign({}, current, patch);
    localStorage.setItem(DEADLINE_SETTINGS_KEY, JSON.stringify(next));
    _applyDeadlineSettings();
    return next;
}

function _applyDeadlineSettings() {
    // Triggered after any settings change. Re-renders the visible task surfaces.
    if (typeof loadTasks === 'function') loadTasks();
    if (typeof _refreshBriefingIfOpen === 'function') _refreshBriefingIfOpen();
    if (typeof renderNextDeadlineBar === 'function') renderNextDeadlineBar();
}
```

- [ ] **Step 2: Add the settings UI block to the Task Manager settings panel**

Locate where the Task Manager panel's settings/gear button opens its menu. Likely under a `#taskSettingsPanel` or similar id, or inline in the panel's settings popup. If the gear opens a generic settings panel for all panels, find the section that's task-specific.

Add this block inside the Task Manager settings section:

```html
<div class="setting-group">
    <h4 class="setting-group-title">Deadline visualization</h4>

    <label class="setting-row">
        <span class="setting-label">Show countdown text</span>
        <input type="checkbox" id="settingDeadlineShowText"
               onchange="setDeadlineSettings({showText: this.checked})">
    </label>

    <label class="setting-row">
        <span class="setting-label">Show progress bar</span>
        <input type="checkbox" id="settingDeadlineShowBar"
               onchange="setDeadlineSettings({showBar: this.checked})">
    </label>

    <div class="setting-row">
        <span class="setting-label">Color mode</span>
        <span>
            <label><input type="radio" name="deadlineColorMode" value="shift"
                   onchange="setDeadlineSettings({colorMode: this.value})"> Shift</label>
            <label><input type="radio" name="deadlineColorMode" value="static"
                   onchange="setDeadlineSettings({colorMode: this.value})"> Static</label>
        </span>
    </div>

    <div class="setting-row" id="settingDeadlineStaticRow">
        <span class="setting-label">Bar color</span>
        <span class="deadline-color-picker">
            <button data-c="#5599ff" onclick="setDeadlineSettings({staticColor:this.dataset.c})" style="background:#5599ff" title="Blue"></button>
            <button data-c="#5dd9d9" onclick="setDeadlineSettings({staticColor:this.dataset.c})" style="background:#5dd9d9" title="Cyan"></button>
            <button data-c="#ffc850" onclick="setDeadlineSettings({staticColor:this.dataset.c})" style="background:#ffc850" title="Amber"></button>
            <button data-c="#ff8a50" onclick="setDeadlineSettings({staticColor:this.dataset.c})" style="background:#ff8a50" title="Orange"></button>
            <button data-c="#ff5566" onclick="setDeadlineSettings({staticColor:this.dataset.c})" style="background:#ff5566" title="Red"></button>
            <button data-c="#b487ff" onclick="setDeadlineSettings({staticColor:this.dataset.c})" style="background:#b487ff" title="Purple"></button>
        </span>
    </div>
</div>
```

CSS for the color picker (add to the existing `<style>` block):

```css
.deadline-color-picker { display: inline-flex; gap: 4px; }
.deadline-color-picker button {
    width: 22px; height: 22px; border: 2px solid transparent;
    cursor: pointer; padding: 0; border-radius: 3px;
}
.deadline-color-picker button:hover { border-color: rgba(255,255,255,0.4); }
```

- [ ] **Step 3: Add a function to hydrate the settings UI from localStorage**

```javascript
function _hydrateDeadlineSettingsUI() {
    var s = getDeadlineSettings();
    var textCb = document.getElementById('settingDeadlineShowText');
    var barCb = document.getElementById('settingDeadlineShowBar');
    var modeRadios = document.getElementsByName('deadlineColorMode');
    var staticRow = document.getElementById('settingDeadlineStaticRow');
    if (textCb) textCb.checked = s.showText;
    if (barCb) barCb.checked = s.showBar;
    for (var i = 0; i < modeRadios.length; i++) {
        modeRadios[i].checked = (modeRadios[i].value === s.colorMode);
    }
    if (staticRow) staticRow.style.display = (s.colorMode === 'static') ? '' : 'none';
}
```

Wire `_hydrateDeadlineSettingsUI()` into whatever existing function opens the Task Manager settings panel (search for the gear-button onclick).

Also add a stub-safe call so `_applyDeadlineSettings` toggling colorMode shows/hides the static row immediately. Add this inside `_applyDeadlineSettings`:

```javascript
    var staticRow = document.getElementById('settingDeadlineStaticRow');
    if (staticRow) {
        var mode = (getDeadlineSettings().colorMode);
        staticRow.style.display = (mode === 'static') ? '' : 'none';
    }
```

- [ ] **Step 4: Manual smoke test**

1. Reload the renderer (Ctrl+R).
2. Open Task Manager gear/settings. Confirm the new "Deadline visualization" section appears with 4 controls.
3. Toggle each checkbox — verify `localStorage.getItem('aegis_task_deadline_settings')` reflects the change (DevTools console).
4. Switch color mode to Static — verify the `Bar color` row appears. Switch back to Shift — verify it hides.
5. Click each color swatch — verify staticColor updates.

(Visuals don't change yet — that's Task 6/7.)

- [ ] **Step 5: Commit**

```bash
git add ui/templates/index.html
git commit -m "feat: deadline settings panel + localStorage helpers

aegis_task_deadline_settings key persists 4 controls: showText,
showBar, colorMode (shift|static), staticColor. UI block lives in
Task Manager settings under 'Deadline visualization'. Static color
row reveals only when colorMode === 'static'."
```

---

## Task 5: Pure helper functions — math, format, color

**Files:**
- Modify: `ui/templates/index.html` (add pure helper functions; they have no DOM dependencies so they're trivial to verify in browser DevTools)

- [ ] **Step 1: Add the math/format helpers**

Insert into the `<script>` block near other task helpers (search for `function renderTasks` and add these immediately above it):

```javascript
/** Combine due (YYYY-MM-DD) + due_time (HH:MM, default 23:59) into local epoch ms.
 *  Returns null if t has no due date. */
function _computeTaskDeadline(t) {
    if (!t || !t.due) return null;
    var datePart = (t.due || '').substring(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(datePart)) return null;
    var timePart = t.due_time || '23:59';
    var m = /^(\d{2}):(\d{2})$/.exec(timePart);
    if (!m) return null;
    var parts = datePart.split('-');
    var d = new Date(
        parseInt(parts[0], 10),
        parseInt(parts[1], 10) - 1,
        parseInt(parts[2], 10),
        parseInt(m[1], 10),
        parseInt(m[2], 10),
        0, 0
    );
    return d.getTime();
}

/** Created date as epoch ms, falls back to (deadline - 24h) when missing. */
function _computeTaskCreated(t) {
    if (t && t.created) {
        var ms = Date.parse(t.created);
        if (!isNaN(ms)) return ms;
    }
    var dl = _computeTaskDeadline(t);
    return (dl !== null) ? (dl - 24 * 3600 * 1000) : null;
}

/** Fraction of time remaining: 1.0 fresh, 0.0 at deadline, clamped. */
function _computeRemainingFraction(t, nowMs) {
    var dl = _computeTaskDeadline(t);
    if (dl === null) return null;
    var created = _computeTaskCreated(t);
    if (created === null || created >= dl) return 0;
    var now = (typeof nowMs === 'number') ? nowMs : Date.now();
    var frac = (dl - now) / (dl - created);
    if (frac > 1) frac = 1;
    if (frac < 0) frac = 0;
    return frac;
}

/** Remaining seconds (can be negative if overdue). null if no deadline. */
function _computeRemainingSeconds(t, nowMs) {
    var dl = _computeTaskDeadline(t);
    if (dl === null) return null;
    var now = (typeof nowMs === 'number') ? nowMs : Date.now();
    return Math.floor((dl - now) / 1000);
}

/** Human-friendly countdown. Returns '' when overdue/null. */
function _formatCountdown(remainingSec) {
    if (remainingSec === null || remainingSec === undefined || remainingSec < 0) return '';
    var days = Math.floor(remainingSec / 86400);
    var hours = Math.floor((remainingSec % 86400) / 3600);
    var mins = Math.floor((remainingSec % 3600) / 60);
    var secs = remainingSec % 60;
    if (days >= 1) return days + 'd ' + hours + 'h';
    if (hours >= 1) return hours + 'h ' + mins + 'm';
    return mins + 'm ' + secs + 's';
}

/** Returns a CSS color string for the given remaining fraction + settings.
 *  Shift mode: HSL hue interpolated 120 (green) → 0 (red).
 *  Static mode: settings.staticColor unchanged. */
function _deadlineColor(remainingFraction, settings) {
    if (!settings || settings.colorMode === 'static') {
        return (settings && settings.staticColor) || '#5599ff';
    }
    var frac = Math.max(0, Math.min(1, remainingFraction || 0));
    var hue = Math.round(120 * frac);
    return 'hsl(' + hue + ', 70%, 55%)';
}
```

- [ ] **Step 2: Smoke test in DevTools console**

Open DevTools after Ctrl+R:

```javascript
// Should return a positive epoch ms for a future date
_computeTaskDeadline({due: '2026-12-31', due_time: '17:00'})

// Should be near 1.0 for a fresh task
_computeRemainingFraction({due: '2026-12-31', due_time: '17:00', created: new Date().toISOString()})

// Should produce readable strings
_formatCountdown(86400 * 2 + 3600 * 14)  // -> "2d 14h"
_formatCountdown(3600 * 5 + 60 * 22)     // -> "5h 22m"
_formatCountdown(60 * 42 + 18)           // -> "42m 18s"

// Should return hsl(...) for shift mode
_deadlineColor(0.6, {colorMode: 'shift'})  // hue ~72
_deadlineColor(0.6, {colorMode: 'static', staticColor: '#5599ff'})  // '#5599ff'
```

If any return wrong values, debug before committing.

- [ ] **Step 3: Commit**

```bash
git add ui/templates/index.html
git commit -m "feat: pure helpers for deadline math, format, color

Adds _computeTaskDeadline, _computeTaskCreated, _computeRemaining*,
_formatCountdown, _deadlineColor. All pure functions, no DOM deps,
testable from DevTools console."
```

---

## Task 6: `_renderTaskDeadlineLayer` helper + CSS + wire into both render sites

**Files:**
- Modify: `ui/templates/index.html` — add the render helper, CSS, and call from both `renderTasks` and `_briefingTaskCard`

- [ ] **Step 1: Add the render helper**

Insert immediately after the pure helpers from Task 5:

```javascript
/** Returns HTML for the three deadline layers (bg drain, bottom strip, text chip)
 *  inside a task card. Empty string when no deadline / overdue / settings off.
 *  Returned snippet expects to live inside a position:relative parent. */
function _renderTaskDeadlineLayer(t, settings) {
    if (!t || !t.due) return '';
    var s = settings || getDeadlineSettings();
    var dl = _computeTaskDeadline(t);
    var now = Date.now();
    if (dl === null || dl <= now) return ''; // overdue handled by existing CSS
    var frac = _computeRemainingFraction(t, now);
    var remSec = _computeRemainingSeconds(t, now);
    var color = _deadlineColor(frac, s);
    var widthPct = (frac * 100).toFixed(2);
    var remSecForCss = Math.max(1, remSec); // avoid 0-duration transition
    var html = '';
    if (s.showBar) {
        // Background drain layer (low opacity)
        html += '<div class="task-deadline-fill-bg" style="'
              + 'width:' + widthPct + '%;'
              + 'background:' + color + ';'
              + 'transition: width ' + remSecForCss + 's linear, background-color ' + remSecForCss + 's linear;'
              + '" data-target-width="0"></div>';
        // Bottom strip
        html += '<div class="task-deadline-fill-bar" style="'
              + 'width:' + widthPct + '%;'
              + 'background:' + color + ';'
              + 'box-shadow: 0 0 8px ' + color + '66;'
              + 'transition: width ' + remSecForCss + 's linear, background-color ' + remSecForCss + 's linear;'
              + '" data-target-width="0"></div>';
    }
    if (s.showText) {
        html += '<span class="task-deadline-text" data-deadline-ms="' + dl + '" '
              + 'style="color:' + color + '">'
              + _formatCountdown(remSec) + '</span>';
    }
    return html;
}

/** Kick off the CSS transitions once the layers are in the DOM. Without this
 *  the browser may render width at the initial value indefinitely. Called
 *  from a requestAnimationFrame so the initial width has been painted. */
function _startDeadlineTransitions(rootEl) {
    requestAnimationFrame(function() {
        var nodes = (rootEl || document).querySelectorAll(
            '.task-deadline-fill-bg[data-target-width], .task-deadline-fill-bar[data-target-width]'
        );
        nodes.forEach(function(n) {
            n.style.width = n.getAttribute('data-target-width') + '%';
            n.removeAttribute('data-target-width');
        });
    });
}
```

- [ ] **Step 2: Add CSS**

In the `<style>` block, add:

```css
/* Task deadline countdown layers */
.task-deadline-fill-bg {
    position: absolute;
    inset: 0;
    opacity: 0.12;
    pointer-events: none;
    z-index: 0;
    border-radius: inherit;
}
.task-deadline-fill-bar {
    position: absolute;
    left: 0;
    bottom: 0;
    height: 3px;
    pointer-events: none;
    z-index: 1;
}
.task-deadline-text {
    display: inline-block;
    margin-left: 8px;
    font-size: 11px;
    letter-spacing: 0.06em;
    font-variant-numeric: tabular-nums;
    position: relative;
    z-index: 2;
}
/* Ensure title and meta sit above the bg drain */
.task-item > *,
.briefing-task-card > * {
    position: relative;
    z-index: 2;
}
.task-item, .briefing-task-card {
    position: relative;
    overflow: hidden;
}
```

- [ ] **Step 3: Wire into `renderTasks`**

Find the `.task-item` template construction inside `renderTasks`. Inject the layer HTML inside the task-item div but before the close tag. For example, if the current template ends with `'</div>'`, change to:

```javascript
        // ... existing template construction ...
        var dlLayer = _renderTaskDeadlineLayer(t);
        // Insert dlLayer as a direct child of the .task-item div, before close
        html += dlLayer;
        html += '</div>'; // close .task-item
```

After the `container.innerHTML = ...` line, call `_startDeadlineTransitions(container)` to kick off the CSS animation.

- [ ] **Step 4: Wire into `_briefingTaskCard`**

Find `_briefingTaskCard(t, klass)`. At the bottom of the card markup (before the closing `</div>`), inject:

```javascript
    cardHtml += _renderTaskDeadlineLayer(t);
```

After `renderBriefing` finishes setting `container.innerHTML`, call `_startDeadlineTransitions(container)`.

- [ ] **Step 5: Manual smoke test**

1. Restart Aegis (tray Exit → relaunch) so backend serves new `due_time`.
2. Add a task with due date today + due time set to 1 hour from now.
3. Verify in Task Manager: card shows the background drain (subtle), bottom strip (visible), and `42m 18s`-ish countdown text.
4. Open Daily Briefing: same card has the same treatment.
5. Watch for 30+ seconds: the bar's width visibly shrinks (smooth CSS transition).
6. Open settings, toggle off "Show progress bar" — bg + strip vanish, text stays.
7. Toggle off "Show countdown text" — text vanishes, bg + strip stay.
8. Switch color mode to Static, pick red — both bar and text turn red.
9. Switch back to Shift — color reflects time-remaining (mostly green if hours left).
10. Edit the task to clear the due date — bar and text vanish.

- [ ] **Step 6: Commit**

```bash
git add ui/templates/index.html
git commit -m "feat: per-card deadline progress bar + countdown text

Hybrid treatment: low-opacity background drain + 3px bottom strip,
plus a countdown text chip on the meta row. CSS-driven drain
animation set on first render. Shared helper called from both
renderTasks (Task Manager) and _briefingTaskCard (Daily Briefing).
Three layers controlled independently by the two settings toggles."
```

---

## Task 7: Two-interval tick + visibilitychange re-render

**Files:**
- Modify: `ui/templates/index.html` — add interval setup + visibilitychange listener

- [ ] **Step 1: Add the tick functions**

Insert near the pure helpers:

```javascript
/** Updates countdown text on all visible cards. CSS handles bar width.
 *  Called from the two interval ticks. */
function _tickDeadlineText(includeSlow) {
    var now = Date.now();
    var oneHourMs = 3600 * 1000;
    var nodes = document.querySelectorAll('.task-deadline-text[data-deadline-ms]');
    nodes.forEach(function(n) {
        var dl = parseInt(n.getAttribute('data-deadline-ms'), 10);
        if (isNaN(dl)) return;
        var remMs = dl - now;
        // Fast tick (1Hz) updates only sub-hour cards. Slow tick (30s) updates all.
        if (!includeSlow && remMs > oneHourMs) return;
        var remSec = Math.floor(remMs / 1000);
        if (remSec < 0) {
            n.textContent = '';
            n.style.display = 'none';
            return;
        }
        n.textContent = _formatCountdown(remSec);
        n.style.display = '';
    });
}

var _deadlineFastInterval = null;
var _deadlineSlowInterval = null;
function _startDeadlineTicks() {
    if (_deadlineFastInterval) clearInterval(_deadlineFastInterval);
    if (_deadlineSlowInterval) clearInterval(_deadlineSlowInterval);
    _deadlineFastInterval = setInterval(function() {
        _tickDeadlineText(false);
        if (typeof _tickNextDeadlineBar === 'function') _tickNextDeadlineBar();
    }, 1000);
    _deadlineSlowInterval = setInterval(function() {
        _tickDeadlineText(true);
    }, 30000);
}

// visibilitychange — recompute everything after the tab/window wakes up
document.addEventListener('visibilitychange', function() {
    if (!document.hidden) {
        if (typeof loadTasks === 'function') loadTasks();
        if (typeof _refreshBriefingIfOpen === 'function') _refreshBriefingIfOpen();
        if (typeof renderNextDeadlineBar === 'function') renderNextDeadlineBar();
    }
});
```

- [ ] **Step 2: Start the ticks at app boot**

Find the existing app-init flow (search for a `DOMContentLoaded` handler or an `init()` function called at startup). Add a call to `_startDeadlineTicks()` after the initial `loadTasks()`.

- [ ] **Step 3: Manual smoke test**

1. Restart Aegis.
2. Add a task due in 1 minute. Watch the countdown text:
   - First few seconds: `0h 59m` (slow tick updates every 30s)
   - Once under 1 hour, fast tick kicks in: `59m 58s`, `59m 57s`, ... ticking every 1s.
3. Wait until 0 seconds: text vanishes, bar should be at 0, overdue treatment kicks in (red border per existing CSS).
4. Sleep the laptop for 2 minutes, wake. `visibilitychange` fires — bars and text snap to correct positions. (Without this, the CSS transition would be ahead/behind.)

- [ ] **Step 4: Commit**

```bash
git add ui/templates/index.html
git commit -m "feat: deadline tick intervals + visibilitychange sync

1Hz interval updates sub-hour countdown text + the top NEXT DEADLINE
bar. 30s interval updates everything else. visibilitychange listener
re-renders task surfaces when the tab wakes from background so bar
positions correct for sleep drift."
```

---

## Task 8: Top "NEXT DEADLINE" bar

**Files:**
- Modify: `ui/templates/index.html` — markup, render function, click handler, pulse CSS

- [ ] **Step 1: Add the bar markup to the Task Manager panel**

Find the Task Manager panel HTML (search for the `task-add-row` we modified in Task 3). Immediately above it (or below the panel header bar — wherever feels right visually), insert:

```html
<div id="nextDeadlineBar" class="next-deadline-bar" style="display:none" onclick="_onClickNextDeadline()">
    <span class="next-deadline-label">NEXT DEADLINE</span>
    <span class="next-deadline-arrow">▸</span>
    <span class="next-deadline-title" id="nextDeadlineTitle">—</span>
    <span class="next-deadline-dot">·</span>
    <span class="next-deadline-countdown" id="nextDeadlineCountdown">—</span>
</div>
```

- [ ] **Step 2: CSS**

```css
.next-deadline-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    margin: 4px 0;
    background: rgba(85, 153, 255, 0.08);
    border-left: 3px solid currentColor;
    border-radius: 2px;
    font-size: 12px;
    letter-spacing: 0.06em;
    cursor: pointer;
    user-select: none;
    transition: background-color 0.2s;
}
.next-deadline-bar:hover { background: rgba(85, 153, 255, 0.18); }
.next-deadline-label { color: var(--lcars-text-dim); font-weight: bold; }
.next-deadline-arrow { color: currentColor; }
.next-deadline-title { color: var(--lcars-text); font-weight: bold; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.next-deadline-dot { color: var(--lcars-text-dim); }
.next-deadline-countdown { color: currentColor; font-variant-numeric: tabular-nums; }

/* Pulse the destination card when clicked */
@keyframes deadline-pulse {
    0%, 100% { box-shadow: 0 0 0 0 transparent; }
    50% { box-shadow: 0 0 0 3px rgba(85, 153, 255, 0.6); }
}
.task-item.deadline-pulse { animation: deadline-pulse 0.6s ease-in-out 3; }
```

- [ ] **Step 3: Add the render + tick functions**

```javascript
var _nextDeadlineTaskId = null;

/** Find the soonest non-past pending task and update the bar.
 *  Called on initial render and on task-list refresh. */
function renderNextDeadlineBar() {
    var bar = document.getElementById('nextDeadlineBar');
    if (!bar) return;
    var s = getDeadlineSettings();
    if (!s.showText) { bar.style.display = 'none'; return; }
    // Use the cached task list if available, otherwise hide and skip
    var tasks = (window._lastTasks || []);
    var now = Date.now();
    var soonest = null;
    var soonestDl = Infinity;
    for (var i = 0; i < tasks.length; i++) {
        var t = tasks[i];
        if (t.completed) continue;
        var dl = _computeTaskDeadline(t);
        if (dl === null || dl <= now) continue;
        if (dl < soonestDl) {
            soonest = t;
            soonestDl = dl;
        }
    }
    if (!soonest) { bar.style.display = 'none'; _nextDeadlineTaskId = null; return; }
    _nextDeadlineTaskId = soonest.id;
    document.getElementById('nextDeadlineTitle').textContent = soonest.text || '';
    var color = _deadlineColor(_computeRemainingFraction(soonest, now), s);
    bar.style.color = color;
    bar.style.display = '';
    _tickNextDeadlineBar(now);
}

/** 1Hz countdown update for the top bar. Called from the fast tick interval. */
function _tickNextDeadlineBar(nowMs) {
    var bar = document.getElementById('nextDeadlineBar');
    if (!bar || bar.style.display === 'none') return;
    var tasks = (window._lastTasks || []);
    var t = null;
    for (var i = 0; i < tasks.length; i++) {
        if (tasks[i].id === _nextDeadlineTaskId) { t = tasks[i]; break; }
    }
    if (!t) { renderNextDeadlineBar(); return; }
    var now = (typeof nowMs === 'number') ? nowMs : Date.now();
    var remSec = _computeRemainingSeconds(t, now);
    if (remSec === null || remSec < 0) { renderNextDeadlineBar(); return; }
    document.getElementById('nextDeadlineCountdown').textContent = _formatCountdown(remSec);
}

function _onClickNextDeadline() {
    if (_nextDeadlineTaskId === null) return;
    var card = document.querySelector('.task-item[data-task-id="' + _nextDeadlineTaskId + '"]');
    if (!card) return;
    card.scrollIntoView({behavior: 'smooth', block: 'center'});
    card.classList.remove('deadline-pulse');
    void card.offsetWidth; // restart animation
    card.classList.add('deadline-pulse');
    setTimeout(function() { card.classList.remove('deadline-pulse'); }, 1900);
}
```

- [ ] **Step 4: Cache the task list and call `renderNextDeadlineBar`**

Find `loadTasks()` or `renderTasks(tasks)`. Right after the task list is fetched/set, cache it and re-render the bar:

```javascript
    window._lastTasks = tasks;
    renderNextDeadlineBar();
```

- [ ] **Step 5: Manual smoke test**

1. Restart Aegis.
2. Add 3 tasks with future deadlines: today+2h, today+5h, tomorrow.
3. Top of Task Manager shows: `NEXT DEADLINE ▸ <today+2h title> · 1h 59m 58s` ticking down each second.
4. Complete the today+2h task. Bar updates to today+5h's task. (Trigger by hitting the checkbox.)
5. Click the top bar. Task list scrolls to that card, card pulses 3x.
6. Toggle off "Show countdown text" — bar hides.
7. Delete all deadlined tasks — bar hides.
8. Switch to Static color, pick red — bar's accents (label color, countdown, border) turn red.
9. Switch back to Shift — color reflects time remaining for the spotlight task.

- [ ] **Step 6: Commit**

```bash
git add ui/templates/index.html
git commit -m "feat: top-of-Task-Manager NEXT DEADLINE bar

Pinned bar shows the soonest-upcoming pending deadline with a 1Hz
countdown. Click scrolls the list to that card and triple-pulses
its outline. Hidden when zero qualifying tasks. Gated by the
'Show countdown text' setting."
```

---

## Task 9: Polish — apply settings to top bar's static-row visibility, manual end-to-end smoke

**Files:**
- Modify: `ui/templates/index.html` (small cleanup of `_applyDeadlineSettings`)
- No new files

- [ ] **Step 1: Ensure full re-render on any settings change**

Make sure `_applyDeadlineSettings` (Task 4 step 1) calls both `loadTasks()` AND `renderNextDeadlineBar()`. It already does per the Task 4 implementation — sanity-check by changing the color mode and confirming the top bar's color and per-card colors both update immediately, without a manual refresh.

If anything lags, add an explicit `loadTasks().then(renderNextDeadlineBar)` or similar sequencing.

- [ ] **Step 2: End-to-end smoke checklist**

Run through this list. Each item should pass.

- [ ] Add a task with date only — no countdown, no bar, no change vs. pre-feature.
- [ ] Add a task with date + time 1 hour from now — card shows bg drain, bottom strip, `59m XXs` text ticking. Top bar shows this task with 1Hz countdown.
- [ ] Add a second task with deadline 2 hours from now — top bar still shows the 1-hour task (soonest wins).
- [ ] Complete the 1-hour task via checkbox — top bar swaps to the 2-hour task within a frame.
- [ ] Click top bar — list scrolls + pulses the 2-hour card.
- [ ] Wait for the 2-hour task's deadline to pass (or set one ~1 min out): once past, bar/text on that card vanish; overdue red treatment takes over; top bar drops to the next-soonest or hides.
- [ ] Toggle "Show progress bar" off — bg + strip vanish on every visible card; text stays; top bar still shows.
- [ ] Toggle "Show countdown text" off — text + top bar both vanish; bg + strip stay if their toggle is on.
- [ ] Color mode Static → red — every per-card bar and the top bar's accents go red.
- [ ] Color mode Shift — colors interpolate green→amber→red based on each task's remaining fraction. Long deadlines green, near deadlines red.
- [ ] Open Daily Briefing — same hybrid bar treatment on briefing cards.
- [ ] Briefing does NOT show its own NEXT DEADLINE top bar (it lives only in Task Manager).
- [ ] Add a deadline via chat: *"hey pike, make a task to test the deadline thursday at 5pm"* — created with `due` Thursday and `due_time` `17:00`. Bar renders correctly.
- [ ] Reload renderer (Ctrl+R) — settings persist, bars re-render to correct widths.
- [ ] Sleep laptop ~2 minutes, wake — `visibilitychange` fires, bars snap to correct widths.

If any item fails, fix before committing.

- [ ] **Step 3: Commit + push**

```bash
git add ui/templates/index.html
git commit -m "polish: deadline countdown end-to-end smoke verified"
git push origin main
```

---

## Self-Review

**Spec coverage** — every section of `2026-06-26-task-deadline-countdown-design.md` has a task:

| Spec section                    | Task(s)         |
|---------------------------------|-----------------|
| Data model (`due_time` field)   | Task 1          |
| Backend NLP / `_parse_natural_datetime` | Task 2  |
| Input (time picker on forms)    | Task 3          |
| Per-card visuals + color logic  | Tasks 5, 6      |
| Top "NEXT DEADLINE" bar         | Task 8          |
| Briefing parity                 | Task 6 (wired into `_briefingTaskCard`) |
| Settings menu                   | Task 4          |
| Animation lifecycle             | Tasks 6, 7      |
| Overdue behavior                | Task 6 (early return when `dl <= now`) |
| Edge cases                      | Tasks 5, 6 (math helpers handle them) |

**Placeholder scan** — no TBDs, no "implement later", every step has runnable code or commands.

**Type consistency** — helper names match across tasks: `_computeTaskDeadline`, `_computeTaskCreated`, `_computeRemainingFraction`, `_computeRemainingSeconds`, `_formatCountdown`, `_deadlineColor`, `_renderTaskDeadlineLayer`, `_startDeadlineTransitions`, `_startDeadlineTicks`, `_tickDeadlineText`, `_tickNextDeadlineBar`, `renderNextDeadlineBar`, `_onClickNextDeadline`. Settings keys: `aegis_task_deadline_settings` (consistent everywhere). Settings shape: `{showText, showBar, colorMode, staticColor}` (consistent). Task field name: `due_time` (consistent across backend and frontend).
