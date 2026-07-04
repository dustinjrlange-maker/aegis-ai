# Escalate-on-Trouble Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Pike escalate a chat turn to cloud (Opus) only when the local 8B is visibly struggling, keeping everyday chat and private content local by default.

**Architecture:** A new stateless detector (`core/llm/trouble.py`) flags "trouble" (correction cues + a 2-in-a-row streak) and "private content". `policy.decide()` gains a `trouble` flag that escalates non-private turns when the new `cloud_trouble_escalation` config is on — independent of the main `cloud_enabled`. `server/chat_pipeline.py` owns the streak state, the private-content consent gate (two-step, session-held), and threads `trouble` into the router.

**Tech Stack:** Python 3.12, pytest, FastAPI, existing `core/llm` router.

**Design reconciliation (from reading the code):** the chat pipeline hardcodes `sensitivity="personal"`; the turn classifier only produces `mode`/`route`, not a privacy tier. So "private" for the consent gate is detected from message *content* in the pipeline. `policy.decide` additionally refuses trouble-escalation for `sensitivity="private"` call sites (tool-synthesis pins file contents to `private`) so those never leave the box.

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `core/llm/trouble.py` | Stateless detectors: `detect_trouble`, `detect_private_content` | Create |
| `core/llm/config.py` | Load `cloud_trouble_escalation`, `trouble_private_consent` | Modify |
| `core/llm/policy.py` | `decide()` gains `trouble`; escalate non-private on trouble | Modify |
| `core/llm/router.py` | Thread `trouble` through `chat_with_meta` → `decide` | Modify |
| `core/llm/cloud_settings.py` | Setters + status for the two flags | Modify |
| `server/chat_pipeline.py` | Streak state, consent gate, force escalation | Modify |
| `server/app.py` | Two settings endpoints | Modify |
| `ui/templates/index.html` | Two checkboxes | Modify |
| `tests/llm/test_trouble.py` | Detector tests | Create |
| `tests/llm/test_policy.py` | Extend for trouble branch | Modify |

---

## Task 1: Trouble + private-content detectors

**Files:**
- Create: `core/llm/trouble.py`
- Test: `tests/llm/test_trouble.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_trouble.py
import pytest
from core.llm.trouble import detect_trouble, detect_private_content


@pytest.mark.parametrize("msg", [
    "no that's wrong",
    "you made a mistake",
    "what are you talking about",
    "that's not right, it's Wednesday",
    "nope, try again",
])
def test_correction_phrases_trip_trouble(msg):
    r = detect_trouble(msg, streak=0)
    assert r.is_trouble is True
    assert r.new_streak >= 1


def test_ordinary_content_disagreement_does_not_trip():
    r = detect_trouble("I think blue is a better color than red here", streak=0)
    assert r.is_trouble is False
    assert r.new_streak == 0


def test_streak_of_two_trips_without_strong_phrase():
    # A short pushback that isn't a keyword, but it's the 2nd in a row.
    r = detect_trouble("still not it", streak=1)
    assert r.is_trouble is True
    assert r.new_streak == 2


def test_non_correction_resets_streak():
    r = detect_trouble("thanks, that's perfect", streak=3)
    assert r.is_trouble is False
    assert r.new_streak == 0


@pytest.mark.parametrize("msg,reason_kw", [
    ("my bank account number is 12345", "financial"),
    ("here's my credit card", "financial"),
    ("my therapist prescribed a new medication", "health"),
    ("my password is hunter2", "credentials"),
])
def test_private_content_detected(msg, reason_kw):
    is_priv, reason = detect_private_content(msg)
    assert is_priv is True
    assert reason_kw in reason


def test_ordinary_message_is_not_private():
    is_priv, _ = detect_private_content("add a podcast recording on wednesday")
    assert is_priv is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/llm/test_trouble.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.llm.trouble'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/llm/trouble.py
"""Stateless detectors for escalate-on-trouble mode.

`detect_trouble` flags when the user appears to be correcting or contradicting
Pike (a sign the local 8B is failing this turn). `detect_private_content` flags
messages carrying obviously sensitive data, so escalation can warn-and-confirm.

Both are pure functions — no I/O, no model — so they are fully unit-testable and
never leak. The "judge" LLM layer described in the spec is deliberately deferred.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Correction / contradiction cues. Conservative: aimed at the user pushing back
# on Pike's OWN answer, not ordinary disagreement about content.
_CORRECTION_PHRASES = (
    "that's wrong", "thats wrong", "that is wrong",
    "that's not right", "thats not right", "that's incorrect", "thats incorrect",
    "you made a mistake", "you're wrong", "youre wrong", "you are wrong",
    "fix your mistake", "fix that", "you messed up", "you got it wrong",
    "what are you talking about", "you're confused", "youre confused",
    "wrong again", "still wrong", "still not right", "not what i said",
    "that's not what i", "thats not what i", "no you didn't", "no you didnt",
    "try again", "nope", "incorrect",
)
# Bare leading "no" — "no,"/"no."/"no " at the start is a correction signal.
_LEADING_NO = re.compile(r"^\s*no[,.\s]")


@dataclass(frozen=True)
class TroubleResult:
    is_trouble: bool
    reason: str
    new_streak: int


def _looks_like_correction(lowered: str) -> bool:
    if _LEADING_NO.match(lowered):
        return True
    return any(p in lowered for p in _CORRECTION_PHRASES)


def detect_trouble(user_message: str, streak: int) -> TroubleResult:
    """Fast-path trouble detection. `streak` is the count of consecutive prior
    correction turns; pass the returned `new_streak` back in next turn."""
    lowered = (user_message or "").lower().strip()
    corrected = _looks_like_correction(lowered)
    if corrected:
        new_streak = streak + 1
        return TroubleResult(True, "correction_phrase", new_streak)
    # Escalating frustration: a 2nd short pushback in a row still counts even
    # without a keyword. Short + follows a prior correction.
    if streak >= 1 and len(lowered.split()) <= 5:
        return TroubleResult(True, "correction_streak", streak + 1)
    return TroubleResult(False, "no_trouble", 0)


# Private-content lexicon → (reason, phrases). Deterministic and conservative.
_PRIVATE_LEXICON = {
    "financial": (
        "bank account", "account number", "routing number", "credit card",
        "debit card", "sin number", "social insurance", "ssn", "social security",
        "my salary", "my income", "net worth", "my savings",
    ),
    "health": (
        "diagnosis", "diagnosed", "medication", "prescribed", "therapist",
        "my doctor", "mental health", "depression", "my meds",
    ),
    "credentials": (
        "my password", "password is", "api key", "secret key", "2fa code",
        "one-time code", "login is",
    ),
}


def detect_private_content(user_message: str):
    """Return (is_private, reason). Reason names the category (e.g. 'financial')."""
    lowered = (user_message or "").lower()
    for reason, phrases in _PRIVATE_LEXICON.items():
        if any(p in lowered for p in phrases):
            return True, reason
    return False, ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/llm/test_trouble.py -q`
Expected: PASS (all cases)

- [ ] **Step 5: Commit**

```bash
git add core/llm/trouble.py tests/llm/test_trouble.py
git commit -m "feat(llm): trouble + private-content detectors (fast path)"
```

---

## Task 2: Config flags

**Files:**
- Modify: `core/llm/config.py:19-63`
- Test: `tests/llm/test_config.py`

- [ ] **Step 1: Write the failing test** (append to `tests/llm/test_config.py`)

```python
def test_trouble_flags_default_off(tmp_path, monkeypatch):
    import core.llm.config as cfgmod
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", tmp_path / "llm_router.json")
    cfg = cfgmod.load_config()
    assert cfg.cloud_trouble_escalation is False
    assert cfg.trouble_private_consent is True


def test_trouble_flags_load_from_override(tmp_path, monkeypatch):
    import json, core.llm.config as cfgmod
    p = tmp_path / "llm_router.json"
    p.write_text(json.dumps({"cloud_trouble_escalation": True,
                             "trouble_private_consent": False}), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", p)
    cfg = cfgmod.load_config()
    assert cfg.cloud_trouble_escalation is True
    assert cfg.trouble_private_consent is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/llm/test_config.py -q -k trouble`
Expected: FAIL — `AttributeError: 'RouterConfig' object has no attribute 'cloud_trouble_escalation'`

- [ ] **Step 3: Implement** — in `core/llm/config.py`

Add two fields to `RouterConfig` (after `deep_mode: bool = False`):
```python
    cloud_trouble_escalation: bool = False
    trouble_private_consent: bool = True
```
In `load_config`, after the `deep_mode = bool(...)` default line:
```python
    cloud_trouble_escalation = bool(defaults.get("cloud_trouble_escalation", False))
    trouble_private_consent = bool(defaults.get("trouble_private_consent", True))
```
In the override block, after the `if "deep_mode" in data:` clause:
```python
            if "cloud_trouble_escalation" in data:
                cloud_trouble_escalation = bool(data["cloud_trouble_escalation"])
            if "trouble_private_consent" in data:
                trouble_private_consent = bool(data["trouble_private_consent"])
```
In the `return RouterConfig(...)` call, add:
```python
        cloud_trouble_escalation=cloud_trouble_escalation,
        trouble_private_consent=trouble_private_consent,
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/llm/test_config.py -q -k trouble`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/llm/config.py tests/llm/test_config.py
git commit -m "feat(llm): load cloud_trouble_escalation + trouble_private_consent flags"
```

---

## Task 3: Policy — trouble escalation branch

**Files:**
- Modify: `core/llm/policy.py:22-48`
- Test: `tests/llm/test_policy.py`

- [ ] **Step 1: Write the failing test** (append to `tests/llm/test_policy.py`)

```python
from dataclasses import dataclass
from core.llm.policy import decide


@dataclass
class _Cfg:
    cloud_enabled: bool = False
    cloud_opt_in_features: tuple = ()
    deep_mode: bool = False
    cloud_trouble_escalation: bool = False
    trouble_private_consent: bool = True


def test_trouble_escalates_personal_even_with_cloud_disabled():
    cfg = _Cfg(cloud_enabled=False, cloud_trouble_escalation=True)
    d = decide("personal", cfg, task="chat_casual", trouble=True)
    assert d.backend == "cloud"
    assert d.reason == "trouble_escalation"


def test_trouble_ignored_when_feature_off():
    cfg = _Cfg(cloud_enabled=False, cloud_trouble_escalation=False)
    d = decide("personal", cfg, task="chat_casual", trouble=True)
    assert d.backend == "local"


def test_trouble_never_escalates_private_sensitivity():
    cfg = _Cfg(cloud_enabled=False, cloud_trouble_escalation=True)
    d = decide("private", cfg, task="chat_casual", trouble=True)
    assert d.backend == "local"


def test_no_trouble_falls_through_to_existing_policy():
    cfg = _Cfg(cloud_enabled=False, cloud_trouble_escalation=True)
    d = decide("personal", cfg, task="chat_casual", trouble=False)
    assert d.backend == "local"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/llm/test_policy.py -q -k trouble`
Expected: FAIL — `decide()` has no `trouble` kwarg (TypeError).

- [ ] **Step 3: Implement** — in `core/llm/policy.py`, change the `decide` signature and add the branch immediately after the sensitivity validation and `offline` check, BEFORE the `cloud_enabled` gate:

```python
def decide(sensitivity, cfg, *, task=None, offline=False, trouble=False):
```
After the `if sensitivity not in VALID_SENSITIVITIES:` raise block and before `if not cfg.cloud_enabled:`, insert:
```python
    if offline:
        return RouteDecision("local", "offline", False)
    # Trouble escalation: independent of cloud_enabled, gated by its own flag.
    # Never fires for the `private` sensitivity tier (protects tool-synthesis /
    # file contents pinned to private). Chat "private content" is gated upstream
    # in the pipeline before trouble=True is ever passed.
    if (trouble and getattr(cfg, "cloud_trouble_escalation", False)
            and sensitivity != "private"):
        return RouteDecision("cloud", "trouble_escalation", True)
```
Then DELETE the now-duplicate `if offline:` block that previously sat after the `cloud_enabled` check (lines 38-39), so `offline` is only handled once (in the new position).

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/llm/test_policy.py -q`
Expected: PASS (new + existing policy tests)

- [ ] **Step 5: Commit**

```bash
git add core/llm/policy.py tests/llm/test_policy.py
git commit -m "feat(llm): decide() escalates non-private turns on trouble"
```

---

## Task 4: Router — thread `trouble` through

**Files:**
- Modify: `core/llm/router.py` (`chat_with_meta` and `chat`)
- Test: `tests/llm/test_router.py`

- [ ] **Step 1: Write the failing test** (append to `tests/llm/test_router.py`)

```python
def test_trouble_flag_routes_to_cloud(monkeypatch):
    import core.llm.router as R

    class _Cfg:
        cloud_enabled = False
        cloud_opt_in_features = ()
        cloud_model = "claude-opus-4-8"
        cloud_max_tokens = 2048
        deep_mode = False
        cloud_trouble_escalation = True
        trouble_private_consent = True

    monkeypatch.setattr(R, "load_config", lambda: _Cfg())

    class _Cloud:
        def available(self): return True
        def chat(self, messages, *, model=None, options=None, format=None):
            return "cloud says hi"

    monkeypatch.setitem(R._BACKENDS, "cloud", _Cloud())
    content, meta = R.chat_with_meta(
        [{"role": "user", "content": "no that's wrong"}],
        sensitivity="personal", task="chat_casual", trouble=True)
    assert content == "cloud says hi"
    assert meta.backend_used == "cloud"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/llm/test_router.py -q -k trouble`
Expected: FAIL — `chat_with_meta()` got an unexpected keyword argument `trouble`.

- [ ] **Step 3: Implement** — in `core/llm/router.py`:

Change `chat_with_meta` signature to add `trouble=False`:
```python
def chat_with_meta(messages, *, sensitivity, task=None, model=None,
                   options=None, format=None, trouble=False) -> tuple[str, RouteMeta]:
```
Change its `decide` call to pass it:
```python
    decision = _policy.decide(sensitivity, cfg, task=task, trouble=trouble)
```
Change `chat` signature to add `trouble=False` and forward it:
```python
def chat(messages, *, sensitivity, task=None, model=None, options=None,
         format=None, trouble=False) -> str:
```
```python
    content, _meta = chat_with_meta(
        messages, sensitivity=sensitivity, task=task, model=model,
        options=options, format=format, trouble=trouble)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/llm/test_router.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/llm/router.py tests/llm/test_router.py
git commit -m "feat(llm): thread trouble flag through router.chat_with_meta"
```

---

## Task 5: Settings setters + status

**Files:**
- Modify: `core/llm/cloud_settings.py`
- Test: `tests/llm/test_cloud_settings.py`

- [ ] **Step 1: Write the failing test** (append to `tests/llm/test_cloud_settings.py`)

```python
def test_set_and_report_trouble_flags(tmp_path, monkeypatch):
    import core.llm.config as cfgmod
    from core.llm import cloud_settings as cs
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", tmp_path / "llm_router.json")
    cs.set_trouble_escalation(True)
    cs.set_trouble_private_consent(False)
    status = cs.get_cloud_status()
    assert status["cloud_trouble_escalation"] is True
    assert status["trouble_private_consent"] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/llm/test_cloud_settings.py -q -k trouble`
Expected: FAIL — `module 'core.llm.cloud_settings' has no attribute 'set_trouble_escalation'`

- [ ] **Step 3: Implement** — in `core/llm/cloud_settings.py`:

In `get_cloud_status`, add to the returned dict:
```python
        "cloud_trouble_escalation": cfg.cloud_trouble_escalation,
        "trouble_private_consent": cfg.trouble_private_consent,
```
After `set_deep_mode`, add:
```python
def set_trouble_escalation(enabled: bool) -> None:
    """Toggle escalate-on-trouble mode (independent of cloud_enabled)."""
    _write_override_key("cloud_trouble_escalation", bool(enabled))


def set_trouble_private_consent(enabled: bool) -> None:
    """Toggle the warn-and-confirm gate for private-content trouble turns."""
    _write_override_key("trouble_private_consent", bool(enabled))
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/llm/test_cloud_settings.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/llm/cloud_settings.py tests/llm/test_cloud_settings.py
git commit -m "feat(llm): setters + status for trouble-escalation flags"
```

---

## Task 6: Pipeline integration — streak, consent gate, forced escalation

**Files:**
- Modify: `server/chat_pipeline.py` (imports; top-of-`process_chat` consent resolution; pre-router trouble computation; router call at 179-185)
- Modify: `core/session.py` (init `self._pending_escalation = None`, `self._correction_streak = 0`)
- Test: `tests/test_chat_pipeline_trouble.py` (create)

> Detection/policy are unit-covered in Tasks 1–5. This task wires them in. Because
> `process_chat` is async and session-heavy, the test drives the *decision helper*
> extracted below rather than the whole coroutine.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_pipeline_trouble.py
from server.chat_pipeline import evaluate_escalation


class _Cfg:
    def __init__(self, esc, consent):
        self.cloud_trouble_escalation = esc
        self.trouble_private_consent = consent


def test_non_private_trouble_escalates():
    out = evaluate_escalation("no that's wrong", streak=0,
                              cfg=_Cfg(True, True), key_present=True)
    assert out.action == "escalate"
    assert out.new_streak == 1


def test_private_trouble_with_consent_prompts():
    out = evaluate_escalation("no, my bank account number is wrong", streak=0,
                              cfg=_Cfg(True, True), key_present=True)
    assert out.action == "consent"
    assert "financial" in out.reason


def test_private_trouble_without_consent_escalates():
    out = evaluate_escalation("no, my bank account is wrong", streak=0,
                              cfg=_Cfg(True, False), key_present=True)
    assert out.action == "escalate"


def test_no_key_stays_local():
    out = evaluate_escalation("no that's wrong", streak=0,
                              cfg=_Cfg(True, True), key_present=False)
    assert out.action == "local"


def test_feature_off_stays_local():
    out = evaluate_escalation("no that's wrong", streak=0,
                              cfg=_Cfg(False, True), key_present=True)
    assert out.action == "local"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_chat_pipeline_trouble.py -q`
Expected: FAIL — `cannot import name 'evaluate_escalation'`

- [ ] **Step 3: Implement the pure helper** — add near the top of `server/chat_pipeline.py` (after imports):

```python
from dataclasses import dataclass
from core.llm.trouble import detect_trouble, detect_private_content


@dataclass(frozen=True)
class EscalationPlan:
    action: str      # "local" | "escalate" | "consent"
    new_streak: int
    reason: str


def evaluate_escalation(user_message, *, streak, cfg, key_present) -> EscalationPlan:
    """Decide how a chat turn should route under trouble mode. Pure — no I/O."""
    t = detect_trouble(user_message, streak)
    if not (cfg.cloud_trouble_escalation and key_present and t.is_trouble):
        return EscalationPlan("local", t.new_streak, t.reason)
    is_private, priv_reason = detect_private_content(user_message)
    if is_private and cfg.trouble_private_consent:
        return EscalationPlan("consent", t.new_streak, priv_reason)
    return EscalationPlan("escalate", t.new_streak, priv_reason or t.reason)
```

> Note the test constructs `evaluate_escalation("...", streak=..., cfg=..., key_present=...)`
> with keyword args; keep the `*` in the signature.

- [ ] **Step 4: Run to verify the helper passes**

Run: `python -m pytest tests/test_chat_pipeline_trouble.py -q`
Expected: PASS

- [ ] **Step 5: Wire the helper into `process_chat`** (no unit test — covered by the helper + manual smoke)

In `core/session.py` `UserSession.__init__`, near `self._pending_file_context = None`:
```python
        self._pending_escalation = None   # {"message": str, "ts": datetime} or None
        self._correction_streak = 0
```

In `server/chat_pipeline.py`, add near the other `core.llm` imports:
```python
from core.llm.config import load_config as _load_router_config, resolve_api_key as _resolve_key
```

At the TOP of `process_chat`, right after `session` is obtained and before emotion detection, resolve a pending consent:
```python
    _pending = getattr(session, "_pending_escalation", None)
    _force_trouble_cloud = False
    if _pending:
        from datetime import datetime as _dt, timedelta as _td
        fresh = (_dt.now() - _pending["ts"]) < _td(minutes=5)
        affirmatives = ("yes", "yes use cloud", "use cloud", "go ahead", "ok",
                        "okay", "allow", "allowed", "do it", "sure")
        if fresh and user_input.strip().lower() in affirmatives:
            user_input = _pending["message"]     # re-run the ORIGINAL turn
            _force_trouble_cloud = True
        session._pending_escalation = None
```

After `task_tag = route_task_tag(turn)` (line ~123), compute the plan:
```python
    _rcfg = _load_router_config()
    _plan = evaluate_escalation(
        user_input, streak=getattr(session, "_correction_streak", 0),
        cfg=_rcfg, key_present=_resolve_key() is not None)
    session._correction_streak = _plan.new_streak
    if _plan.action == "consent" and not _force_trouble_cloud:
        session._pending_escalation = {"message": user_input, "ts": __import__("datetime").datetime.now()}
        return {
            "agent_name": session.agent_name,
            "response": (f"⚠ I'm struggling with this, and it looks like it involves "
                         f"private info ({_plan.reason}). I can get better help from the cloud, "
                         f"but that sends it to Anthropic. Reply “yes, use cloud” to allow "
                         f"it just this once — otherwise I'll keep trying locally."),
            "emotion": emotion_result, "wellness_flag": False, "bracket_actions": [],
        }
    _trouble_cloud = _force_trouble_cloud or (_plan.action == "escalate")
```

Change the router call (currently sensitivity="personal") to pass the flag:
```python
        reply_content, route_meta = await asyncio.to_thread(
            router_chat_with_meta,
            messages_to_send,
            sensitivity="personal",
            task=task_tag,
            model=CONFIG["model"]["chat"],
            trouble=_trouble_cloud,
        )
```

> The `emotion_result` referenced in the consent-return must be computed before that
> return. Move the consent block to AFTER `emotion_result = emotion.detect_emotion(...)`
> (line 114) and after `task_tag` — i.e. place the plan/consent block just below line 123.
> The affirmative-resolution block at the top only rewrites `user_input`; classification
> then runs on the resolved message as normal.

- [ ] **Step 6: Run the full suite + manual smoke**

Run: `python -m pytest -q`
Expected: PASS (all).

Manual smoke (with a key present and the toggle on): send "no that's wrong" after a normal turn; confirm the reply shows the ☁ cloud-brain marker. Send a message containing "my bank account number" while struggling; confirm the ⚠ consent prompt, then "yes, use cloud" re-runs on cloud.

- [ ] **Step 7: Commit**

```bash
git add server/chat_pipeline.py core/session.py tests/test_chat_pipeline_trouble.py
git commit -m "feat(chat): wire trouble escalation + private-content consent gate"
```

---

## Task 7: Settings API + UI toggles

**Files:**
- Modify: `server/app.py` (after the `/api/cloud/deep` endpoint at ~1715)
- Modify: `ui/templates/index.html` (cloud settings render ~11636, save handlers ~11667)
- Test: `tests/test_server_security.py` or `tests/llm/test_cloud_settings.py` already cover the setters; endpoints are thin.

- [ ] **Step 1: Add API endpoints** — in `server/app.py`, after the `set_deep_mode` endpoint:

```python
@app.post("/api/cloud/trouble")
async def set_cloud_trouble(req: ToggleRequest, user_id: str = Depends(require_user)):
    cloud_settings.set_trouble_escalation(req.enabled)
    return {"success": True}


@app.post("/api/cloud/trouble-consent")
async def set_cloud_trouble_consent(req: ToggleRequest, user_id: str = Depends(require_user)):
    cloud_settings.set_trouble_private_consent(req.enabled)
    return {"success": True}
```

> Use the SAME request model the existing `/api/cloud/deep` endpoint uses (it has an
> `enabled: bool` field — reuse that class name; do not invent a new one). Confirm the
> class name by reading the `/api/cloud/deep` handler (~line 1715) and reuse it verbatim.

- [ ] **Step 2: Add UI checkboxes** — in `ui/templates/index.html`, in the cloud-settings render (after the deep-mode toggle block ~11654), append:

```javascript
    var troubleChecked = s.cloud_trouble_escalation ? ' checked' : '';
    var consentChecked = s.trouble_private_consent ? ' checked' : '';
    html += '<div class="setting-row"><span>Escalate to cloud when I\'m struggling</span>';
    html += '<label class="proto-toggle"><input type="checkbox" id="troubleToggle"' + troubleChecked + ' onchange="saveTrouble(this.checked)"><span class="toggle-slider"></span></label>';
    html += '<span class="setting-saved" id="trouble-saved">SAVED</span></div>';
    html += '<div class="setting-row"><span>Ask before sending private info to cloud</span>';
    html += '<label class="proto-toggle"><input type="checkbox" id="troubleConsentToggle"' + consentChecked + ' onchange="saveTroubleConsent(this.checked)"><span class="toggle-slider"></span></label>';
    html += '<span class="setting-saved" id="troubleConsent-saved">SAVED</span></div>';
```

> Match the exact wrapper markup used by the `cloudEnabledToggle` block above it (read
> ~11645); the snippet mirrors it. Add save handlers next to `saveDeepMode` (~11682):

```javascript
async function saveTrouble(v) {
    await authFetch(API + '/cloud/trouble', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({enabled: v})});
    var el = document.getElementById('trouble-saved'); if (el) { el.style.opacity = 1; setTimeout(function(){ el.style.opacity = 0; }, 1200); }
}
async function saveTroubleConsent(v) {
    await authFetch(API + '/cloud/trouble-consent', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({enabled: v})});
    var el = document.getElementById('troubleConsent-saved'); if (el) { el.style.opacity = 1; setTimeout(function(){ el.style.opacity = 0; }, 1200); }
}
```

- [ ] **Step 2b: Verify JS parses**

Run (extract inline script + node --check), same method used previously:
```bash
python -c "import re; html=open('ui/templates/index.html',encoding='utf-8').read(); b=re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.DOTALL); open('/tmp/x.js','w',encoding='utf-8').write('\n;\n'.join(b))"
node --check /tmp/x.js && echo "JS OK"
```
Expected: `JS OK`

- [ ] **Step 3: Commit**

```bash
git add server/app.py ui/templates/index.html
git commit -m "feat(ui): settings toggles for trouble escalation + consent"
```

---

## Task 8: Full verification + restart

- [ ] **Step 1: Full suite**

Run: `python -m pytest -q`
Expected: PASS (all).

- [ ] **Step 2: Restart the Electron app** to load the new backend (files on disk don't hot-reload a running server):

```bash
# PowerShell: taskkill /F /IM electron.exe /T   then relaunch:
./electron/node_modules/.bin/electron ./electron
```

- [ ] **Step 3: Live smoke** — with the toggle ON and a key present: correction turn → ☁; private-content struggling turn → ⚠ consent → "yes, use cloud" → ☁. With the toggle OFF: everything stays local.

- [ ] **Step 4: Final commit if any fixups**

---

## Self-Review

- **Spec coverage:** config flags (T2), fast-path detection (T1), 3-way routing incl. private protection (T3/T6), independent toggle (T3), private consent flow (T6), announcements (reused ☁, T6), settings UI (T7). Judge path explicitly deferred (spec Future work). ✓
- **Placeholder scan:** none — every step has concrete code/commands. ✓
- **Type consistency:** `detect_trouble`→`TroubleResult(is_trouble,reason,new_streak)`; `evaluate_escalation`→`EscalationPlan(action,new_streak,reason)`; `decide(..., trouble=)`; `chat_with_meta(..., trouble=)` all consistent across tasks. ✓
- **Known follow-up:** the `ToggleRequest` model name in Task 7 must be confirmed against the existing `/api/cloud/deep` handler before use (noted inline).
