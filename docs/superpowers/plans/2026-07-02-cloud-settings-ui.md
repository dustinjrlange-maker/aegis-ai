# Cloud Brain Settings UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Settings-panel "Cloud Brain" section — cloud on/off toggle, masked API-key field with save/remove, and a Test button that validates the key with one real call — writing the exact files the router already reads.

**Architecture:** New module `core/llm/cloud_settings.py` owns the file writes + key test (small, unit-tested helpers). Four thin `/api/cloud*` endpoints in `server/app.py` wrap those helpers (`Depends(require_user)`). A Cloud Brain section in `ui/templates/index.html` renders in the existing async-loader pattern.

**Tech Stack:** Python 3.12, FastAPI (Pydantic request models), the existing `anthropic`-backed `CloudBackend`, vanilla JS in the LCARS UI, `pytest`.

**Spec:** `docs/superpowers/specs/2026-07-02-cloud-settings-ui-design.md`

---

## File Structure

- Create: `core/llm/cloud_settings.py` — `get_cloud_status`, `set_cloud_enabled`, `set_api_key`, `test_cloud_key`, `_friendly_error`. Owns all writes to `data/llm_router.json` + `data/anthropic_key` and the key-test call. (Reads live in `config.py`; writes live here.)
- Modify: `server/app.py` — 2 Pydantic models + 4 endpoints (`GET /api/cloud`, `POST /api/cloud/enabled`, `POST /api/cloud/key`, `POST /api/cloud/test`).
- Modify: `ui/templates/index.html` — a Cloud Brain settings section + JS (`loadCloudSettings`, `renderCloudSettings`, `saveCloudEnabled`, `saveCloudKey`, `testCloudKey`, `removeCloudKey`).
- Test: `tests/llm/test_cloud_settings.py` — unit tests for the helpers.

**Monkeypatch note (critical):** `cloud_settings.py` must reference the paths through the `config` MODULE (`_cfg._OVERRIDE_PATH`, `_cfg._KEY_FILE`, `_cfg.load_config()`, `_cfg.resolve_api_key()`) — NOT value-imports — so that monkeypatching `core.llm.config._OVERRIDE_PATH` / `._KEY_FILE` in tests affects both the writes here and the reads in `load_config()`. This mirrors how the existing config tests monkeypatch `cfgmod._OVERRIDE_PATH`.

Run tests from the project root: `python -m pytest tests/llm -v`

---

## Task 1: `set_cloud_enabled` (merge-safe) + `get_cloud_status`

**Files:**
- Create: `core/llm/cloud_settings.py`
- Test: `tests/llm/test_cloud_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_cloud_settings.py
import json
import core.llm.config as cfgmod
import core.llm.cloud_settings as cs


def test_set_cloud_enabled_writes_and_reads_back(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nokey")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cs.set_cloud_enabled(True)
    assert json.loads(override.read_text(encoding="utf-8"))["cloud_enabled"] is True
    assert cs.get_cloud_status()["cloud_enabled"] is True

    cs.set_cloud_enabled(False)
    assert cs.get_cloud_status()["cloud_enabled"] is False


def test_set_cloud_enabled_preserves_other_keys(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    override.write_text(json.dumps({
        "cloud_enabled": False,
        "cloud_opt_in_features": ["summarize"],
        "cloud_model": "claude-sonnet-4-6",
    }), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)

    cs.set_cloud_enabled(True)
    data = json.loads(override.read_text(encoding="utf-8"))
    assert data["cloud_enabled"] is True
    assert data["cloud_opt_in_features"] == ["summarize"]   # not clobbered
    assert data["cloud_model"] == "claude-sonnet-4-6"        # not clobbered


def test_set_cloud_enabled_on_corrupt_file_starts_fresh(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    override.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)
    cs.set_cloud_enabled(True)  # must not raise
    assert json.loads(override.read_text(encoding="utf-8"))["cloud_enabled"] is True


def test_get_cloud_status_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", tmp_path / "none.json")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nokey")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    st = cs.get_cloud_status()
    assert set(st.keys()) == {"cloud_enabled", "key_set", "cloud_model"}
    assert st["cloud_enabled"] is False
    assert st["key_set"] is False
    assert st["cloud_model"] == "claude-opus-4-8"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/llm/test_cloud_settings.py::test_get_cloud_status_shape -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.llm.cloud_settings'`

- [ ] **Step 3: Create `core/llm/cloud_settings.py`**

```python
# core/llm/cloud_settings.py
"""Writes the router's runtime files (data/llm_router.json, data/anthropic_key)
and a one-shot key test. Reads live in config.py; writes live here so config.py
stays read-only. Paths are accessed through the config MODULE so tests that
monkeypatch core.llm.config._OVERRIDE_PATH / ._KEY_FILE affect these writes too.
"""
from __future__ import annotations

import json
import logging

from core.llm import config as _cfg

logger = logging.getLogger(__name__)


def get_cloud_status() -> dict:
    """Cloud status for the UI. Never includes the API key value."""
    cfg = _cfg.load_config()
    return {
        "cloud_enabled": cfg.cloud_enabled,
        "key_set": _cfg.resolve_api_key() is not None,
        "cloud_model": cfg.cloud_model,
    }


def set_cloud_enabled(enabled: bool) -> None:
    """Read-modify-write data/llm_router.json, updating only cloud_enabled and
    preserving every other key. A missing/corrupt file starts from {}."""
    path = _cfg._OVERRIDE_PATH
    data = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            logger.exception("Corrupt %s — starting fresh", path)
    data["cloud_enabled"] = bool(enabled)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/llm/test_cloud_settings.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add core/llm/cloud_settings.py tests/llm/test_cloud_settings.py
git commit -m "feat: cloud_settings.set_cloud_enabled (merge-safe) + get_cloud_status"
```

---

## Task 2: `set_api_key` (write / clear)

**Files:**
- Modify: `core/llm/cloud_settings.py`
- Test: `tests/llm/test_cloud_settings.py`

- [ ] **Step 1: Write the failing test**

```python
def test_set_api_key_writes_and_status_reports_set_without_value(tmp_path, monkeypatch):
    key_file = tmp_path / "anthropic_key"
    monkeypatch.setattr(cfgmod, "_KEY_FILE", key_file)
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", tmp_path / "none.json")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cs.set_api_key("  sk-secret-123  ")
    assert key_file.read_text(encoding="utf-8") == "sk-secret-123"  # trimmed

    st = cs.get_cloud_status()
    assert st["key_set"] is True
    assert "sk-secret-123" not in json.dumps(st)   # value never exposed


def test_set_api_key_blank_removes_file(tmp_path, monkeypatch):
    key_file = tmp_path / "anthropic_key"
    key_file.write_text("sk-old", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", key_file)
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", tmp_path / "none.json")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cs.set_api_key("")
    assert not key_file.exists()
    assert cs.get_cloud_status()["key_set"] is False


def test_set_api_key_blank_when_no_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "anthropic_key")
    cs.set_api_key("")  # must not raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/llm/test_cloud_settings.py::test_set_api_key_blank_removes_file -v`
Expected: FAIL — `AttributeError: module 'core.llm.cloud_settings' has no attribute 'set_api_key'`

- [ ] **Step 3: Add `set_api_key`**

Append to `core/llm/cloud_settings.py`:

```python
def set_api_key(key: str) -> None:
    """Write the API key to data/anthropic_key (trimmed). A blank key DELETES
    the file. The key is never returned or logged."""
    key = (key or "").strip()
    path = _cfg._KEY_FILE
    if not key:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/llm/test_cloud_settings.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add core/llm/cloud_settings.py tests/llm/test_cloud_settings.py
git commit -m "feat: cloud_settings.set_api_key (write-only, blank clears)"
```

---

## Task 3: `test_cloud_key` + `_friendly_error`

**Files:**
- Modify: `core/llm/cloud_settings.py`
- Test: `tests/llm/test_cloud_settings.py`

- [ ] **Step 1: Write the failing test**

```python
class _OKBackend:
    def chat(self, messages, **kw):
        return "pong"


class _RaisingBackend:
    def __init__(self, exc):
        self._exc = exc

    def chat(self, messages, **kw):
        raise self._exc


def test_test_cloud_key_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nokey")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    called = {"n": 0}
    monkeypatch.setattr(cs, "CloudBackend", lambda: (_ for _ in ()).throw(AssertionError("should not construct")))
    assert cs.test_cloud_key() == {"ok": False, "error": "No API key set"}


def test_test_cloud_key_ok(tmp_path, monkeypatch):
    kf = tmp_path / "anthropic_key"; kf.write_text("sk-x", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", kf)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cs, "CloudBackend", _OKBackend)
    assert cs.test_cloud_key() == {"ok": True}


def test_test_cloud_key_auth_error_maps_to_rejected(tmp_path, monkeypatch):
    kf = tmp_path / "anthropic_key"; kf.write_text("sk-bad", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", kf)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cs, "CloudBackend",
                        lambda: _RaisingBackend(Exception("authentication_error: invalid x-api-key")))
    assert cs.test_cloud_key() == {"ok": False, "error": "Key rejected"}


def test_test_cloud_key_generic_error_passes_message(tmp_path, monkeypatch):
    kf = tmp_path / "anthropic_key"; kf.write_text("sk-x", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", kf)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cs, "CloudBackend",
                        lambda: _RaisingBackend(RuntimeError("something odd")))
    out = cs.test_cloud_key()
    assert out["ok"] is False and "something odd" in out["error"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/llm/test_cloud_settings.py::test_test_cloud_key_ok -v`
Expected: FAIL — `AttributeError: module 'core.llm.cloud_settings' has no attribute 'test_cloud_key'` (and `CloudBackend` not importable there yet).

- [ ] **Step 3: Add the import, `_friendly_error`, and `test_cloud_key`**

At the top of `core/llm/cloud_settings.py`, add the backend import (with the other `core.*` imports):

```python
from core.llm.backends import CloudBackend
```

Append to the file:

```python
def _friendly_error(e: Exception) -> str:
    """Map a cloud failure to a short, key-safe message for the UI."""
    text = f"{type(e).__name__}: {e}".lower()
    if "authentication" in text or "invalid x-api-key" in text or "401" in text:
        return "Key rejected"
    if "connection" in text or "network" in text or "timeout" in text:
        return "Couldn't reach Anthropic (network)"
    if "rate" in text and "limit" in text:
        return "Rate limited — try again shortly"
    return str(e) or type(e).__name__


def test_cloud_key() -> dict:
    """Validate the current key with one tiny real Claude call.
    Returns {'ok': True} or {'ok': False, 'error': <friendly str>}. Never the key."""
    if _cfg.resolve_api_key() is None:
        return {"ok": False, "error": "No API key set"}
    try:
        out = CloudBackend().chat([{"role": "user", "content": "ping"}])
    except Exception as e:  # any failure -> friendly message, never fatal
        return {"ok": False, "error": _friendly_error(e)}
    if isinstance(out, str) and out.strip():
        return {"ok": True}
    return {"ok": False, "error": "Empty response"}
```

Note: the tests monkeypatch `cs.CloudBackend`, so it MUST be referenced as the module-level name `CloudBackend` (imported at top), not `backends.CloudBackend`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/llm/test_cloud_settings.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Run the whole router suite**

Run: `python -m pytest tests/llm -v`
Expected: PASS (prior 37 + 11 new = 48).

- [ ] **Step 6: Commit**

```bash
git add core/llm/cloud_settings.py tests/llm/test_cloud_settings.py
git commit -m "feat: cloud_settings.test_cloud_key with friendly error mapping"
```

---

## Task 4: `/api/cloud*` endpoints

**Files:**
- Modify: `server/app.py`

- [ ] **Step 1: Add the Pydantic request models**

In `server/app.py`, near the existing request models (e.g. right after `SettingsUpdateRequest`, ~line 171), add:

```python
class CloudEnabledRequest(BaseModel):
    enabled: bool


class CloudKeyRequest(BaseModel):
    key: str
```

- [ ] **Step 2: Add the import**

With the other `core.*` imports at the top of `server/app.py`, add:

```python
from core.llm import cloud_settings
```

- [ ] **Step 3: Add the four endpoints**

Place these next to the existing `GET/POST /api/settings` routes, matching the file's decorator style (use `@app.get` / `@app.post` if that's what the neighboring routes use; match exactly). All take `user_id: str = Depends(require_user)` like the settings routes:

```python
@app.get("/api/cloud")
async def get_cloud(user_id: str = Depends(require_user)):
    return cloud_settings.get_cloud_status()


@app.post("/api/cloud/enabled")
async def post_cloud_enabled(req: CloudEnabledRequest, user_id: str = Depends(require_user)):
    cloud_settings.set_cloud_enabled(req.enabled)
    return {"success": True, "cloud_enabled": req.enabled}


@app.post("/api/cloud/key")
async def post_cloud_key(req: CloudKeyRequest, user_id: str = Depends(require_user)):
    cloud_settings.set_api_key(req.key)
    return {"success": True, "key_set": cloud_settings.get_cloud_status()["key_set"]}


@app.post("/api/cloud/test")
async def post_cloud_test(user_id: str = Depends(require_user)):
    return cloud_settings.test_cloud_key()
```

- [ ] **Step 4: Import-smoke the server module**

Run: `python -c "import server.app; print('ok')"`
Expected: prints `ok` (no import error — confirms the new routes/models/import are wired correctly). If importing `server.app` has heavy side effects in this environment, instead run `python -c "import ast; ast.parse(open(r'server/app.py',encoding='utf-8').read()); print('parsed')"` to at least confirm it's syntactically valid, and note that the route is verified in the manual step.

- [ ] **Step 5: Confirm the router suite still passes (no import breakage)**

Run: `python -m pytest tests/llm -v`
Expected: PASS (48).

- [ ] **Step 6: Commit**

```bash
git add server/app.py
git commit -m "feat: /api/cloud endpoints (status, enabled, key, test)"
```

---

## Task 5: Cloud Brain settings section (frontend)

**Files:**
- Modify: `ui/templates/index.html`

- [ ] **Step 1: Add the section to `renderSettings()`**

In `renderSettings()`, find the end where the Features section is added and the function finishes:

```js
    // Feature toggles (loaded async)
    html += '<div class="settings-section">';
    html += '<div class="settings-section-title">Features</div>';
    html += '<div id="featureTogglesArea"><div class="task-empty">Loading...</div></div>';
    html += '</div>';

    container.innerHTML = html;
    enumerateSettingsDevices();
    loadFeatureToggles();
}
```

Change it to add a Cloud Brain section and load it (insert the new section block before `container.innerHTML = html;`, and add `loadCloudSettings();` after `loadFeatureToggles();`):

```js
    // Feature toggles (loaded async)
    html += '<div class="settings-section">';
    html += '<div class="settings-section-title">Features</div>';
    html += '<div id="featureTogglesArea"><div class="task-empty">Loading...</div></div>';
    html += '</div>';

    // Cloud Brain (loaded async)
    html += '<div class="settings-section">';
    html += '<div class="settings-section-title">Cloud Brain</div>';
    html += '<div id="cloudBrainArea"><div class="task-empty">Loading...</div></div>';
    html += '</div>';

    container.innerHTML = html;
    enumerateSettingsDevices();
    loadFeatureToggles();
    loadCloudSettings();
}
```

- [ ] **Step 2: Add the JS functions**

Add these functions right after the `loadFeatureToggles` / `toggleFeature` functions (search for `function loadFeatureToggles`):

```js
async function loadCloudSettings() {
    var area = document.getElementById('cloudBrainArea');
    if (!area) return;
    try {
        const res = await authFetch(API + '/cloud');
        const s = await res.json();
        area.innerHTML = renderCloudSettings(s);
    } catch (e) {
        area.innerHTML = '<div class="task-empty">Cloud settings unavailable</div>';
    }
}

function renderCloudSettings(s) {
    var checked = s.cloud_enabled ? ' checked' : '';
    var keyStatus = s.key_set ? '✓ key set' : 'no key';
    var html = '';
    html += '<div class="setting-row">';
    html += '<div class="setting-label">Cloud escalation';
    html += '<div class="setting-desc">When on, general chat with Pike uses Anthropic Opus 4.8 for stronger reasoning. Your private data — email, journals, memory — always stays local.</div>';
    html += '</div>';
    html += '<div class="setting-control">';
    html += '<label class="proto-toggle"><input type="checkbox" id="cloudEnabledToggle"' + checked + ' onchange="saveCloudEnabled(this.checked)"><span class="toggle-slider"></span></label>';
    html += '<span class="setting-saved" id="cloudEnabled-saved">SAVED</span>';
    html += '</div></div>';
    html += '<div class="vault-account-field"><label>API key</label><input type="password" id="cloudKeyInput" placeholder="sk-ant-..."></div>';
    html += '<button class="vault-btn" onclick="saveCloudKey()">SAVE KEY</button>';
    html += '<button class="vault-btn" onclick="testCloudKey()">TEST</button>';
    html += '<button class="vault-btn danger" onclick="removeCloudKey()">REMOVE KEY</button>';
    html += '<span id="cloudKeyMsg" style="margin-left:8px;font-size:12px">' + keyStatus + '</span>';
    html += '<div class="setting-row"><div class="setting-label">Cloud model<div class="setting-desc">Model used when cloud escalation is on.</div></div>';
    html += '<div class="setting-control"><span style="font-size:12px;color:var(--lcars-text)">' + escapeHtml(s.cloud_model || '') + '</span></div></div>';
    return html;
}

async function saveCloudEnabled(enabled) {
    var savedEl = document.getElementById('cloudEnabled-saved');
    try {
        const res = await authFetch(API + '/cloud/enabled', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({enabled: enabled}),
        });
        const data = await res.json();
        if (data.success && savedEl) {
            savedEl.classList.add('show');
            setTimeout(function() { savedEl.classList.remove('show'); }, 1500);
        }
    } catch (e) { console.error('Cloud toggle failed:', e); }
}

async function saveCloudKey() {
    var input = document.getElementById('cloudKeyInput');
    var msg = document.getElementById('cloudKeyMsg');
    var key = input ? input.value : '';
    if (!key.trim()) { if (msg) msg.textContent = 'enter a key first'; return; }
    try {
        const res = await authFetch(API + '/cloud/key', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({key: key}),
        });
        const data = await res.json();
        if (input) input.value = '';  // never keep the key in the DOM
        if (msg) msg.textContent = data.key_set ? '✓ key set' : 'no key';
    } catch (e) { if (msg) msg.textContent = 'save failed'; }
}

async function testCloudKey() {
    var msg = document.getElementById('cloudKeyMsg');
    if (msg) msg.textContent = 'testing…';
    try {
        const res = await authFetch(API + '/cloud/test', {method: 'POST'});
        const data = await res.json();
        if (msg) msg.textContent = data.ok ? '✓ works' : ('✗ ' + (data.error || 'failed'));
    } catch (e) { if (msg) msg.textContent = '✗ test failed'; }
}

async function removeCloudKey() {
    var msg = document.getElementById('cloudKeyMsg');
    try {
        const res = await authFetch(API + '/cloud/key', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({key: ''}),
        });
        const data = await res.json();
        var input = document.getElementById('cloudKeyInput'); if (input) input.value = '';
        if (msg) msg.textContent = data.key_set ? '✓ key set' : 'no key';
    } catch (e) { if (msg) msg.textContent = 'remove failed'; }
}
```

- [ ] **Step 3: Static check the JS is well-formed**

There's no JS test harness. Do a lightweight brace/paren sanity check by opening the file region and confirming the new functions are complete (balanced braces, each `async function` closes). Optionally, if `node` is available: `node --check ui/templates/index.html` will FAIL (it's HTML, not JS) — do NOT rely on that. Instead visually confirm each added function opens and closes.

- [ ] **Step 4: Commit**

```bash
git add ui/templates/index.html
git commit -m "feat: Cloud Brain settings section (toggle, masked key, test, remove)"
```

---

## Task 6: Verification

- [ ] **Step 1: Full router suite**

Run: `python -m pytest tests/llm -v`
Expected: PASS (48).

- [ ] **Step 2: Whole project suite (no regressions)**

Run: `python -m pytest -q`
Expected: PASS (prior green count + 11 new cloud_settings tests).

- [ ] **Step 3: Key-never-exposed grep**

Run: `grep -rn "resolve_api_key\|_KEY_FILE\|anthropic_key" core/llm/cloud_settings.py`
Confirm: the key value is only ever WRITTEN (`set_api_key`) or existence-checked (`resolve_api_key() is not None`); no function returns or logs the key string. `get_cloud_status` returns only `key_set: bool`.

- [ ] **Step 4: Import smoke**

Run: `python -c "import core.llm.cloud_settings, core.llm.config, core.llm.backends; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Commit anything outstanding**

```bash
git add -A
git commit -m "chore: finalize cloud settings UI build" || echo "nothing to commit"
```

---

## Manual verification (operator, after merge — the frontend + endpoints are not auto-tested)

Launch the app (`cd electron && npm start`, or the server), open **Settings → Cloud Brain**:
1. **Toggle on** → the SAVED pip flashes; confirm `data/llm_router.json` now shows `"cloud_enabled": true`, and existing keys (if any) are preserved.
2. **Paste your key → SAVE KEY** → status shows "✓ key set"; the input clears; the key value does NOT appear anywhere in the page, the network response (check devtools → the `/api/cloud` GET returns only `key_set: true`), or the logs.
3. **TEST** → "✓ works" (or "✗ Key rejected" with a deliberately wrong key).
4. **REMOVE KEY** → status → "no key"; `data/anthropic_key` deleted.
5. **Toggle back off** → `cloud_enabled: false`.

## Notes for the implementer

- **Line numbers drift** — re-read each target region before editing.
- **Monkeypatch-safe paths:** `cloud_settings.py` accesses `_cfg._OVERRIDE_PATH` / `_cfg._KEY_FILE` / `_cfg.load_config()` / `_cfg.resolve_api_key()` through the `config` module, and `CloudBackend` as its own module-level name — this is what makes the unit tests' monkeypatching work. Do not value-import the paths.
- **Do NOT mock Ollama** (CLAUDE.md). The key-test tests monkeypatch `cloud_settings.CloudBackend` (the Anthropic backend), which is fine.
- **No real API in the suite** — every `test_cloud_key` test uses a fake backend. The one real call is the manual TEST-button step.
- **Never write a real key** into any test or the repo; `data/` is gitignored.
- Match the neighboring route decorator style in `server/app.py` (`@app.get`/`@app.post` vs a router prefix) — copy the exact style of the adjacent `/api/settings` routes.
