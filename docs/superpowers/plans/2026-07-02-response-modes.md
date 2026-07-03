# Response Modes + Escalation + Cloud Announcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-turn classification (casual/emotional/task) that drives both cloud routing (task→Opus, feelings/chat→local, Deep Mode opt-in) and mode-aware reply length (lifting the 3-sentence muzzle for emotional and task turns), with a ☁ marker on cloud replies and a `/cloud` payload-preview command.

**Architecture:** New pure modules `core/llm/turn_classifier.py` (deterministic classification) and `core/reply_shaping.py` (mode-aware `clean_reply`, extracted from `core/agent.py`). Policy gains task-tag gating for the `personal` tier + a `deep_mode` config flag. Router gains `chat_with_meta()` so the pipeline knows which backend answered. `server/chat_pipeline.py` wires it together; PR #6's Cloud Brain settings section gains a Deep Mode toggle.

**Tech Stack:** Python 3.12, FastAPI, pytest, vanilla JS (LCARS UI). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-02-response-modes-design.md`

---

## Prerequisites (do these BEFORE Task 1)

1. PR #5 (`fix/security-hardening`) must be merged to `main`.
2. On this branch: `git merge origin/main` (brings in PR #5's notetaker pack sections + security tests). Resolve conflicts if any (none expected — different regions).
3. Run `py -3.12 -m pytest -q` from the branch root. Expected: all green (≈355). This is the baseline.

Run all tests from the project root with: `py -3.12 -m pytest <path> -v`

---

## File Structure

- Create: `core/llm/turn_classifier.py` — pure classification (mode + route override), no I/O, no LLM.
- Create: `core/reply_shaping.py` — `build_filler_cleaner` moved from `core/agent.py`; returned cleaner becomes `clean_reply(text, mode="casual")`.
- Modify: `core/llm/config.py` — `deep_mode` field.
- Modify: `core/llm/policy.py` — task-tag gating for `personal`.
- Modify: `core/llm/router.py` — `RouteMeta` + `chat_with_meta()`.
- Modify: `core/llm/__init__.py` — export `chat_with_meta`.
- Modify: `core/llm/cloud_settings.py` — `set_deep_mode`, `deep_mode` in status.
- Modify: `server/chat_pipeline.py` — classifier wiring, mode hint, marker, `/cloud` command, payload store.
- Modify: `core/agent.py` — function moved out; console loop uses classifier + modes.
- Modify: `server/app.py` — `POST /api/cloud/deep`.
- Modify: `ui/templates/index.html` — Deep Mode toggle + caption update.
- Modify: `packs/personalities/pike/personality.txt`, `packs/personalities/default/personality.txt`.
- Tests: `tests/llm/test_turn_classifier.py`, `tests/test_reply_shaping.py`, `tests/llm/test_route_tags.py`, plus additions to `tests/llm/test_cloud_settings.py`, `tests/llm/test_policy.py`, `tests/llm/test_router.py`.

---

## Task 1: Turn classifier

**Files:**
- Create: `core/llm/turn_classifier.py`
- Test: `tests/llm/test_turn_classifier.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/llm/test_turn_classifier.py
"""Deterministic turn classification: mode (casual|emotional|task) + route override."""
from core.llm.turn_classifier import classify, TurnClass


class TestOverrides:
    def test_think_harder_forces_cloud(self):
        assert classify("think harder about this one").route == "force_cloud"

    def test_just_you_forces_local(self):
        assert classify("keep this between us, just you").route == "force_local"

    def test_negated_override_ignored(self):
        assert classify("don't think hard about it").route == "auto"

    def test_never_use_the_cloud_ignored(self):
        assert classify("never use the cloud for this").route == "auto"

    def test_force_cloud_does_not_change_mode(self):
        tc = classify("think harder", emotion_label="sadness", emotion_score=0.9)
        assert tc.route == "force_cloud"
        assert tc.mode == "emotional"


class TestEmotionalVeto:
    def test_sad_message_is_emotional(self):
        tc = classify(
            "today was really rough and I miss him so much",
            emotion_label="sadness", emotion_score=0.93,
        )
        assert tc.mode == "emotional"

    def test_veto_beats_task_pattern(self):
        tc = classify(
            "I can't figure out how to deal with losing him",
            emotion_label="sadness", emotion_score=0.88,
        )
        assert tc.mode == "emotional"

    def test_below_threshold_is_not_emotional(self):
        tc = classify(
            "that movie made me feel sad I guess",
            emotion_label="sadness", emotion_score=0.5,
        )
        assert tc.mode != "emotional"

    def test_joy_never_vetoes(self):
        tc = classify(
            "help me draft the announcement, today rules",
            emotion_label="joy", emotion_score=0.99,
        )
        assert tc.mode == "task"

    def test_no_emotion_result_defaults_fine(self):
        assert classify("night pike").mode == "casual"


class TestTaskDetection:
    def test_draft_request_is_task(self):
        assert classify("help me draft the L-1A argument").mode == "task"

    def test_walk_me_through_is_task(self):
        assert classify("walk me through incorporating in BC").mode == "task"

    def test_long_vent_without_work_verbs_is_casual(self):
        text = (
            "today was such a long day at work and everyone kept wanting things "
            "from me and I barely had a minute to breathe or eat anything at all"
        )
        assert classify(text).mode == "casual"

    def test_short_message_is_never_task(self):
        assert classify("plan?").mode == "casual"


class TestDefaults:
    def test_greeting_is_casual(self):
        assert classify("hey pike") == TurnClass("casual", "auto", "default")

    def test_empty_input_is_casual(self):
        assert classify("").mode == "casual"
```

- [ ] **Step 2: Run to verify it fails**

Run: `py -3.12 -m pytest tests/llm/test_turn_classifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.llm.turn_classifier'`

- [ ] **Step 3: Create `core/llm/turn_classifier.py`**

```python
# core/llm/turn_classifier.py
"""Deterministic per-turn classification for the chat pipeline.

mode  — how the reply should be shaped: casual | emotional | task
route — explicit user override: auto | force_local | force_cloud

No LLM involvement by design: qwen3:8b is documented-unreliable at exactly
this meta-judgment, and deterministic rules stay legible to the user.
The one-sentence rule: task-shaped requests go to the big brain;
conversation and feelings stay home.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

EMOTION_VETO_LABELS = ("sadness", "fear", "anger")
EMOTION_VETO_THRESHOLD = 0.75  # tuning-session knob
MIN_TASK_WORDS = 4

_FORCE_CLOUD = ("think harder", "think hard", "big brain", "best answer", "use the cloud")
_FORCE_LOCAL = ("just you", "keep it local", "no cloud", "keep it simple")
_NEGATORS = ("don't", "dont", "do not", "never", "no need")

_TASK_PATTERNS = re.compile(
    r"\b(help me|can you|could you|i need you to|write|draft|analyze|analyse|plan|"
    r"summarize|summarise|research|compare|review|outline|design|debug|"
    r"break down|figure out|walk me through|explain how)\b"
)


@dataclass(frozen=True)
class TurnClass:
    """Classification of one user turn."""
    mode: str    # "casual" | "emotional" | "task"
    route: str   # "auto" | "force_local" | "force_cloud"
    reason: str


def _matches_override(lowered: str, phrases) -> bool:
    """True if any phrase occurs NOT preceded by a negator (send-guard lesson)."""
    for phrase in phrases:
        for m in re.finditer(re.escape(phrase), lowered):
            window = lowered[max(0, m.start() - 12):m.start()]
            if any(neg in window for neg in _NEGATORS):
                continue
            return True
    return False


def classify(text: str, emotion_label: str | None = None,
             emotion_score: float = 0.0) -> TurnClass:
    """Classify one user turn. Overrides set route only; veto beats task."""
    lowered = (text or "").lower()

    route = "auto"
    if _matches_override(lowered, _FORCE_CLOUD):
        route = "force_cloud"
    elif _matches_override(lowered, _FORCE_LOCAL):
        route = "force_local"

    if emotion_label in EMOTION_VETO_LABELS and emotion_score >= EMOTION_VETO_THRESHOLD:
        return TurnClass("emotional", route, f"emotion_veto:{emotion_label}")

    if len(lowered.split()) >= MIN_TASK_WORDS and _TASK_PATTERNS.search(lowered):
        return TurnClass("task", route, "task_pattern")

    return TurnClass("casual", route, "default")
```

- [ ] **Step 4: Run to verify it passes**

Run: `py -3.12 -m pytest tests/llm/test_turn_classifier.py -v`
Expected: PASS (16 tests).

- [ ] **Step 5: Commit**

```bash
git add core/llm/turn_classifier.py tests/llm/test_turn_classifier.py
git commit -m "feat: deterministic turn classifier (mode + route override, negation-aware)"
```

---

## Task 2: `deep_mode` config + settings write

**Files:**
- Modify: `core/llm/config.py`
- Modify: `core/llm/cloud_settings.py`
- Test: `tests/llm/test_cloud_settings.py` (additions + one existing-test update)

- [ ] **Step 1: Write the failing tests** (append to `tests/llm/test_cloud_settings.py`)

```python
def test_deep_mode_defaults_false(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", tmp_path / "none.json")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nokey")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cfgmod.load_config().deep_mode is False
    assert cs.get_cloud_status()["deep_mode"] is False


def test_set_deep_mode_writes_and_preserves(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    override.write_text(json.dumps({"cloud_enabled": True}), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nokey")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cs.set_deep_mode(True)
    data = json.loads(override.read_text(encoding="utf-8"))
    assert data["deep_mode"] is True
    assert data["cloud_enabled"] is True   # not clobbered
    assert cfgmod.load_config().deep_mode is True

    cs.set_deep_mode(False)
    assert cfgmod.load_config().deep_mode is False
```

- [ ] **Step 2: Update the existing status-shape test in the same file**

Find `test_get_cloud_status_shape` and change its keys assertion to:

```python
    assert set(st.keys()) == {"cloud_enabled", "key_set", "cloud_model", "deep_mode"}
```

- [ ] **Step 3: Run to verify failures**

Run: `py -3.12 -m pytest tests/llm/test_cloud_settings.py -v`
Expected: the two new tests FAIL (`RouterConfig` has no `deep_mode` / no `set_deep_mode`); the shape test FAILS on the missing key.

- [ ] **Step 4: Implement**

In `core/llm/config.py`, add the field to `RouterConfig`:

```python
@dataclass
class RouterConfig:
    """Resolved router settings used by policy.decide() and router.chat()."""
    cloud_enabled: bool = False
    cloud_opt_in_features: tuple = field(default_factory=tuple)
    cloud_model: str = "claude-opus-4-8"
    cloud_max_tokens: int = 2048
    deep_mode: bool = False
```

In `load_config()`, add after the `cloud_max_tokens` default line:

```python
    deep_mode = bool(defaults.get("deep_mode", False))
```

inside the override-file block, alongside the other keys:

```python
            if "deep_mode" in data:
                deep_mode = bool(data["deep_mode"])
```

and include it in the returned `RouterConfig(...)`:

```python
        deep_mode=deep_mode,
```

In `core/llm/cloud_settings.py`, refactor `set_cloud_enabled` to share a merge-safe writer, and add `set_deep_mode`:

```python
def _write_override_key(key: str, value) -> None:
    """Read-modify-write data/llm_router.json, updating only `key` and
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
    data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def set_cloud_enabled(enabled: bool) -> None:
    """Toggle cloud escalation (merge-safe write to data/llm_router.json)."""
    _write_override_key("cloud_enabled", bool(enabled))


def set_deep_mode(enabled: bool) -> None:
    """Toggle Deep Mode — emotional turns become cloud-eligible (default off)."""
    _write_override_key("deep_mode", bool(enabled))
```

(The existing body of `set_cloud_enabled` becomes `_write_override_key`; keep its docstring semantics.)

In `get_cloud_status()`, add to the returned dict:

```python
        "deep_mode": cfg.deep_mode,
```

- [ ] **Step 5: Run to verify green**

Run: `py -3.12 -m pytest tests/llm/test_cloud_settings.py -v`
Expected: PASS (all, including the updated shape test).

- [ ] **Step 6: Commit**

```bash
git add core/llm/config.py core/llm/cloud_settings.py tests/llm/test_cloud_settings.py
git commit -m "feat: deep_mode config flag + cloud_settings.set_deep_mode"
```

---

## Task 3: Policy — task-tag gating for `personal`

**Files:**
- Modify: `core/llm/policy.py`
- Test: `tests/llm/test_policy.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/llm/test_policy.py`)

```python
class _Cfg:
    def __init__(self, cloud_enabled=True, opt_in=(), deep_mode=False):
        self.cloud_enabled = cloud_enabled
        self.cloud_opt_in_features = tuple(opt_in)
        self.deep_mode = deep_mode


class TestPersonalTaskGating:
    def test_personal_chat_task_goes_cloud(self):
        d = decide("personal", _Cfg(), task="chat_task")
        assert d.backend == "cloud"
        assert d.would_send_cloud is True

    def test_personal_casual_stays_local_even_when_enabled(self):
        d = decide("personal", _Cfg(), task="chat_casual")
        assert (d.backend, d.reason) == ("local", "personal_local_default")

    def test_personal_emotional_local_without_deep_mode(self):
        d = decide("personal", _Cfg(deep_mode=False), task="chat_emotional")
        assert (d.backend, d.reason) == ("local", "personal_local_default")

    def test_personal_emotional_cloud_with_deep_mode(self):
        d = decide("personal", _Cfg(deep_mode=True), task="chat_emotional")
        assert (d.backend, d.reason) == ("cloud", "deep_mode")

    def test_legacy_chat_tag_stays_local(self):
        d = decide("personal", _Cfg(), task="chat")
        assert d.backend == "local"

    def test_cloud_disabled_beats_chat_task(self):
        d = decide("personal", _Cfg(cloud_enabled=False), task="chat_task")
        assert (d.backend, d.reason) == ("local", "cloud_disabled")

    def test_public_tier_unchanged(self):
        d = decide("public", _Cfg(), task="summarize")
        assert d.backend == "cloud"

    def test_private_tier_unchanged(self):
        d = decide("private", _Cfg(), task="draft")
        assert (d.backend, d.reason) == ("local", "private_local_default")
```

(Use the existing `decide` import at the top of the file.)

- [ ] **Step 2: Run to verify failures**

Run: `py -3.12 -m pytest tests/llm/test_policy.py -v`
Expected: the new `personal` gating tests FAIL (today `personal` + enabled → cloud). Some *existing* tests asserting `personal` → cloud may also fail after Step 3 — that's expected; Step 4 updates them.

- [ ] **Step 3: Implement in `core/llm/policy.py`**

Replace the final two rules of `decide()` (the `private` check and the fall-through `cloud_eligible` return) with:

```python
    if sensitivity == "private" and task not in cfg.cloud_opt_in_features:
        return RouteDecision("local", "private_local_default", False)
    if sensitivity == "personal":
        if task == "chat_task":
            return RouteDecision("cloud", "cloud_eligible", True)
        if task == "chat_emotional" and getattr(cfg, "deep_mode", False):
            return RouteDecision("cloud", "deep_mode", True)
        return RouteDecision("local", "personal_local_default", False)
    return RouteDecision("cloud", "cloud_eligible", True)
```

Update the `decide()` docstring to note: *personal routes cloud only for task="chat_task" (or "chat_emotional" with cfg.deep_mode); everything else personal stays local.*

- [ ] **Step 4: Fix any pre-existing personal-tier tests**

Run: `py -3.12 -m pytest tests/llm/test_policy.py tests/llm -v`

Any pre-existing test that asserted `personal` + `cloud_enabled` → `cloud` (find with: `grep -n "personal" tests/llm/test_policy.py tests/llm/test_router.py`) must be updated: either change its `task=` to `"chat_task"` (if it's testing the cloud path) or change its expectation to `("local", "personal_local_default")` (if it's testing default chat). Keep the test's original intent — cloud-path tests should keep testing the cloud path via `task="chat_task"`.

- [ ] **Step 5: Run the full llm suite green**

Run: `py -3.12 -m pytest tests/llm -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/llm/policy.py tests/llm/test_policy.py tests/llm/test_router.py
git commit -m "feat: policy gates personal tier by task tag (chat_task / deep_mode emotional)"
```

---

## Task 4: Router `chat_with_meta`

**Files:**
- Modify: `core/llm/router.py`
- Modify: `core/llm/__init__.py`
- Test: `tests/llm/test_router.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/llm/test_router.py`)

```python
from core.llm.router import chat_with_meta, RouteMeta
import core.llm.router as router_mod


class _FakeBackend:
    def __init__(self, reply="ok", is_available=True, exc=None):
        self._reply, self._available, self._exc = reply, is_available, exc

    def available(self):
        return self._available

    def chat(self, messages, **kw):
        if self._exc:
            raise self._exc
        return self._reply


class _MetaCfg:
    cloud_enabled = True
    cloud_opt_in_features = ()
    deep_mode = False
    cloud_model = "claude-opus-4-8"


class TestChatWithMeta:
    def _patch(self, monkeypatch, local, cloud):
        monkeypatch.setattr(router_mod, "load_config", lambda: _MetaCfg())
        monkeypatch.setitem(router_mod._BACKENDS, "local", local)
        monkeypatch.setitem(router_mod._BACKENDS, "cloud", cloud)

    def test_cloud_pick_returns_cloud_meta(self, monkeypatch):
        self._patch(monkeypatch, _FakeBackend("local-ans"), _FakeBackend("cloud-ans"))
        content, meta = chat_with_meta(
            [{"role": "user", "content": "x"}], sensitivity="personal", task="chat_task",
        )
        assert content == "cloud-ans"
        assert meta.backend_used == "cloud"
        assert meta.cloud_model == "claude-opus-4-8"

    def test_local_pick_returns_local_meta(self, monkeypatch):
        self._patch(monkeypatch, _FakeBackend("local-ans"), _FakeBackend("cloud-ans"))
        content, meta = chat_with_meta(
            [{"role": "user", "content": "x"}], sensitivity="personal", task="chat_casual",
        )
        assert content == "local-ans"
        assert meta.backend_used == "local"
        assert meta.decision_reason == "personal_local_default"

    def test_cloud_failure_falls_back_with_local_meta(self, monkeypatch):
        self._patch(monkeypatch, _FakeBackend("local-ans"),
                    _FakeBackend(exc=RuntimeError("boom")))
        content, meta = chat_with_meta(
            [{"role": "user", "content": "x"}], sensitivity="personal", task="chat_task",
        )
        assert content == "local-ans"
        assert meta.backend_used == "local"
        assert meta.decision_reason == "cloud_failed_fallback"

    def test_cloud_unavailable_falls_back_with_local_meta(self, monkeypatch):
        self._patch(monkeypatch, _FakeBackend("local-ans"),
                    _FakeBackend(is_available=False))
        content, meta = chat_with_meta(
            [{"role": "user", "content": "x"}], sensitivity="personal", task="chat_task",
        )
        assert content == "local-ans"
        assert meta.backend_used == "local"
        assert meta.decision_reason == "cloud_unavailable_fallback"

    def test_plain_chat_still_returns_string(self, monkeypatch):
        self._patch(monkeypatch, _FakeBackend("local-ans"), _FakeBackend("cloud-ans"))
        out = router_mod.chat(
            [{"role": "user", "content": "x"}], sensitivity="personal", task="chat_casual",
        )
        assert out == "local-ans"
```

- [ ] **Step 2: Run to verify failures**

Run: `py -3.12 -m pytest tests/llm/test_router.py -v`
Expected: FAIL — `ImportError: cannot import name 'chat_with_meta'`.

- [ ] **Step 3: Implement in `core/llm/router.py`**

Add near the top (after imports):

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteMeta:
    """Which backend actually served one call (for the ☁ announcement)."""
    backend_used: str        # "local" | "cloud"
    decision_reason: str
    cloud_model: str | None = None
```

Rewrite the body so `chat_with_meta` holds the existing logic and `chat` wraps it:

```python
def chat_with_meta(messages, *, sensitivity, task=None, model=None,
                   options=None, format=None) -> tuple[str, RouteMeta]:
    """Route one LLM call; return (content, RouteMeta). Meta reports the backend
    that ACTUALLY answered — a cloud pick that falls back reports local."""
    cfg = load_config()
    decision = _policy.decide(sensitivity, cfg, task=task)
    backend = _BACKENDS[decision.backend]
    reason = decision.reason

    if decision.backend == "cloud":
        if backend.available():
            try:
                content = backend.chat(messages, model=model, options=options, format=format)
                return content, RouteMeta("cloud", reason, cfg.cloud_model)
            except Exception as e:
                logger.warning(
                    "[llm-router] cloud call failed (%s: %s) sensitivity=%s task=%s "
                    "— falling back to local",
                    type(e).__name__, e, sensitivity, task,
                    exc_info=True,
                )
                backend = _BACKENDS["local"]
                reason = "cloud_failed_fallback"
        else:
            logger.info(
                "[llm-router] cloud escalation preview: sensitivity=%s task=%s "
                "policy_reason=%s — cloud unavailable, executing locally",
                sensitivity, task, decision.reason,
            )
            backend = _BACKENDS["local"]
            reason = "cloud_unavailable_fallback"

    content = backend.chat(messages, model=model, options=options, format=format)
    return content, RouteMeta("local", reason, None)


def chat(messages, *, sensitivity, task=None, model=None, options=None, format=None) -> str:
    """Route one LLM call and return the response content string.

    sensitivity: "private" | "personal" | "public" (required — every site tags).
    task: routing tag — for personal chat use chat_task / chat_emotional / chat_casual.
    Config is re-read each call so toggles change at runtime without restart.
    """
    content, _meta = chat_with_meta(
        messages, sensitivity=sensitivity, task=task,
        model=model, options=options, format=format,
    )
    return content
```

In `core/llm/__init__.py`, alongside the existing `chat` export, add `chat_with_meta` and `RouteMeta` (match the file's existing export style — check it with `grep -n "" core/llm/__init__.py` first).

- [ ] **Step 4: Run to verify green**

Run: `py -3.12 -m pytest tests/llm -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/llm/router.py core/llm/__init__.py tests/llm/test_router.py
git commit -m "feat: router chat_with_meta — expose which backend served the reply"
```

---

## Task 5: Mode-aware reply shaping (`core/reply_shaping.py`)

**Files:**
- Create: `core/reply_shaping.py`
- Modify: `core/agent.py` (remove `build_filler_cleaner`, re-import it)
- Test: `tests/test_reply_shaping.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reply_shaping.py
"""Mode-aware clean_reply: budgets by mode, roleplay stripped in every mode."""
from core.reply_shaping import build_filler_cleaner, MODE_SENTENCE_BUDGETS


def _cleaner():
    return build_filler_cleaner({"filler_phrases": []})


def test_budgets_table():
    assert MODE_SENTENCE_BUDGETS == {"casual": 3, "emotional": 6, "task": None}


def test_casual_caps_at_three_sentences():
    out = _cleaner()(
        "This is one. This is two. This is three. This is four. This is five."
    )
    assert out == "This is one. This is two. This is three."


def test_default_mode_is_casual():
    c = _cleaner()
    text = "This is one. This is two. This is three. This is four."
    assert c(text) == c(text, mode="casual")


def test_emotional_allows_six_sentences():
    c = _cleaner()
    text = ("That sounds heavy. You carried that all day. He mattered to you. "
            "Anyone would feel this. Take the evening slow. I'm right here.")
    assert c(text, mode="emotional") == text


def test_emotional_caps_at_six():
    c = _cleaner()
    text = ("Sentence number one. Sentence number two. Sentence number three. "
            "Sentence number four. Sentence number five. Sentence number six. "
            "Sentence number seven. Sentence number eight.")
    out = c(text, mode="emotional")
    assert out.endswith("Sentence number six.")
    assert "seven" not in out


def test_task_mode_uncapped_and_preserves_structure():
    c = _cleaner()
    text = ("Here is the full breakdown you asked for.\n\n"
            "The first consideration is timing. The second is cost. "
            "The third is the legal side. The fourth is logistics. "
            "The fifth is the fallback plan. The sixth is next steps.")
    out = c(text, mode="task")
    assert "\n" in out                    # structure preserved
    assert "next steps" in out            # nothing cut


def test_roleplay_stripped_in_every_mode():
    c = _cleaner()
    for mode in ("casual", "emotional", "task"):
        out = c("*adjusts jacket slowly* Hey there, good to see you.", mode=mode)
        assert "adjusts" not in out


def test_think_blocks_stripped_in_task_mode():
    out = _cleaner()("<think>internal reasoning</think>The actual answer here.", mode="task")
    assert "internal reasoning" not in out
    assert "actual answer" in out


def test_list_content_bypasses_cap_in_casual():
    text = ("Here is the plan for tonight.\n"
            "1. First step here\n2. Second step here\n"
            "3. Third step here\n4. Fourth step here")
    out = _cleaner()(text)
    assert "4. Fourth step here" in out


def test_session_still_wires_up():
    # core/agent.py must still export build_filler_cleaner (core/session.py imports it)
    from core.agent import build_filler_cleaner as from_agent
    assert from_agent is build_filler_cleaner
```

- [ ] **Step 2: Run to verify it fails**

Run: `py -3.12 -m pytest tests/test_reply_shaping.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.reply_shaping'`

- [ ] **Step 3: Create `core/reply_shaping.py`**

Move the ENTIRE `build_filler_cleaner` function from `core/agent.py` (currently `core/agent.py:48-136`) into the new file **verbatim**, then apply exactly two changes to the inner function:

```python
# core/reply_shaping.py
"""Pack-driven reply cleaning with per-mode length budgets.

All persona cleaning (think-blocks, emoji, curly quotes, asterisk narration,
exclamation->period, filler phrases, word replacements) applies in EVERY mode —
the anti-roleplay defenses are deliberately decoupled from length. Only the
sentence cap + newline collapse vary by mode:

  casual    3 sentences (the historical behavior, byte-identical)
  emotional 6 sentences — room for presence, not padding
  task      uncapped, structure preserved (cloud drafts must survive intact)
"""
import re

MODE_SENTENCE_BUDGETS = {"casual": 3, "emotional": 6, "task": None}


def build_filler_cleaner(personality_pack):
    """Build a response cleaner from the personality pack's filler phrases."""
    # ... [the existing body from core/agent.py, unchanged] ...

    def clean_reply(text, mode="casual"):
        """Post-process agent response using pack-specific filters."""
        # ... [all existing cleaning steps, unchanged, down to the final
        #      whitespace cleanup at the old core/agent.py:108] ...

        # Mode-aware length budget. Models ignore "keep it short" instructions,
        # so the cap is enforced here; task mode is uncapped so escalated
        # drafts survive intact.
        budget = MODE_SENTENCE_BUDGETS.get(mode, 3)
        if budget is not None:
            has_list = bool(re.search(r'(?m)^[\s]*(?:\d+\.|[-*])\s', text))
            if not has_list:
                text = re.sub(r'\s*\n\s*', ' ', text)
                sentences = re.split(r'(?<=[.?])\s+', text)
                sentences = [s for s in sentences if s.strip()]
                if len(sentences) > budget:
                    sentences = sentences[:budget]
                while sentences and (
                    len(sentences[-1].split()) <= 2
                    and not sentences[-1].rstrip('.').endswith(('?', '.'))
                ):
                    sentences.pop()
                if sentences:
                    text = ' '.join(sentences)
                    if not text.endswith(('.', '?')):
                        text += '.'

        return text.strip()

    return clean_reply
```

The two changes vs the original: (1) signature `clean_reply(text, mode="casual")`, (2) the hard-coded `3` becomes `budget` and the whole cap block is skipped when `budget is None`. Everything else is a verbatim move.

- [ ] **Step 4: Update `core/agent.py`**

Delete the moved function (lines 48–136) and add with the other `core.*` imports:

```python
from core.reply_shaping import build_filler_cleaner
```

Verify nothing else broke: `grep -rn "build_filler_cleaner" core/ server/ tests/` — `core/session.py` imports it from `core.agent` (the re-import keeps that working); if it imports from anywhere else, update that import to `core.reply_shaping`.

- [ ] **Step 5: Run to verify green (full suite — this touches the session path)**

Run: `py -3.12 -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add core/reply_shaping.py core/agent.py tests/test_reply_shaping.py
git commit -m "feat: mode-aware clean_reply (casual 3 / emotional 6 / task uncapped), roleplay stripping in all modes"
```

---

## Task 6: Pipeline wiring — classify, hint, marker, `/cloud`, console

**Files:**
- Modify: `server/chat_pipeline.py`
- Modify: `core/agent.py` (console loop, ~lines 384–392)
- Test: `tests/llm/test_route_tags.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/llm/test_route_tags.py
"""Pure mapping from TurnClass to the router task tag + mode hints."""
from core.llm.turn_classifier import TurnClass
from server.chat_pipeline import route_task_tag, _MODE_HINTS


def test_task_mode_maps_chat_task():
    assert route_task_tag(TurnClass("task", "auto", "x")) == "chat_task"


def test_emotional_maps_chat_emotional():
    assert route_task_tag(TurnClass("emotional", "auto", "x")) == "chat_emotional"


def test_casual_maps_chat_casual():
    assert route_task_tag(TurnClass("casual", "auto", "x")) == "chat_casual"


def test_force_local_wins_over_task_mode():
    assert route_task_tag(TurnClass("task", "force_local", "x")) == "chat_casual"


def test_force_cloud_wins_over_emotional_mode():
    assert route_task_tag(TurnClass("emotional", "force_cloud", "x")) == "chat_task"


def test_hints_exist_for_non_casual_modes():
    assert "emotional" in _MODE_HINTS and "task" in _MODE_HINTS
    assert "casual" not in _MODE_HINTS
    for hint in _MODE_HINTS.values():
        assert hint.startswith("[Response mode:")
        assert len(hint.splitlines()) == 1   # qwen injection-fragility: one line
```

- [ ] **Step 2: Run to verify it fails**

Run: `py -3.12 -m pytest tests/llm/test_route_tags.py -v`
Expected: FAIL — `ImportError: cannot import name 'route_task_tag'`.

- [ ] **Step 3: Add the mapping + hints to `server/chat_pipeline.py`**

New imports at the top (with the existing `core.*` imports):

```python
from datetime import datetime

from core.llm import chat_with_meta as router_chat_with_meta
from core.llm.turn_classifier import classify
```

Module-level, below the logger:

```python
_TASK_TAGS = {"casual": "chat_casual", "emotional": "chat_emotional", "task": "chat_task"}

_MODE_HINTS = {
    "emotional": "[Response mode: emotional support — you may take up to 5-6 sentences. Stay specific to their words, no advice, no cheerleading, no roleplay.]",
    "task": "[Response mode: task — give the complete, structured answer; take the length it needs.]",
}


def route_task_tag(turn) -> str:
    """Map a TurnClass to the router task tag. Explicit user override wins."""
    if turn.route == "force_local":
        return "chat_casual"
    if turn.route == "force_cloud":
        return "chat_task"
    return _TASK_TAGS[turn.mode]
```

- [ ] **Step 4: Run to verify the mapping tests pass**

Run: `py -3.12 -m pytest tests/llm/test_route_tags.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Wire the pipeline (`server/chat_pipeline.py::process_chat`)**

(a) **`/cloud` command** — in the slash-command block, immediately after `cmd_name` / `cmd_args` are computed and BEFORE `protocol_registry.handle_command`, add:

```python
        if cmd_name == "cloud" and not cmd_args:
            payload = getattr(session, "last_cloud_payload", None)
            if payload:
                preview = (
                    f"Last cloud call — {payload['model']} at {payload['at']}, "
                    f"{payload['message_count']} messages sent.\n\n"
                    f"Final message sent:\n{payload['last_user_message']}"
                )
            else:
                preview = "No cloud calls this session."
            return {
                "agent_name": session.agent_name,
                "response": preview,
                "emotion": None,
                "wellness_flag": False,
            }
```

(b) **Classify** — right after the emotion-detection lines (`emotion_result = ...` / `emotion_tag = ...`):

```python
    # Per-turn classification: drives routing (task tag) + reply shaping (mode)
    turn = classify(
        user_input,
        emotion_label=(emotion_result or {}).get("label"),
        emotion_score=(emotion_result or {}).get("score", 0.0),
    )
    task_tag = route_task_tag(turn)
```

(c) **Mode hint** — where `context_parts` is being built, right after `context_parts.append(emotion_tag)`'s block:

```python
    mode_hint = _MODE_HINTS.get(turn.mode)
    if mode_hint:
        context_parts.append(mode_hint)
```

(d) **Router call** — replace the existing `router_chat` call + `clean_reply` line:

```python
        reply_content, route_meta = await asyncio.to_thread(
            router_chat_with_meta,
            messages_to_send,
            sensitivity="personal",
            task=task_tag,
            model=CONFIG["model"]["chat"],
        )
        reply = session.clean_reply(reply_content, mode=turn.mode)
```

(e) **Marker + payload store** — after the bracket-actions/notifications block and the `session.messages.append({"role": "assistant", "content": reply})` line (history stores the CLEAN reply, without the marker), and before the `return`:

```python
        display_reply = reply
        if route_meta.backend_used == "cloud":
            display_reply = f"{reply}\n\n☁ cloud brain"
            # RAM-only; overwritten each cloud call; never persisted.
            session.last_cloud_payload = {
                "model": route_meta.cloud_model,
                "at": datetime.now().isoformat(timespec="seconds"),
                "message_count": len(messages_to_send),
                "last_user_message": messages_to_send[-1]["content"],
            }
```

and change the success `return`'s response to `"response": display_reply,`.

First check there is no existing `/cloud` slash command: `grep -rn '"cloud"' core/protocols/` — expected: no command registration hits.

(f) **Console** (`core/agent.py` run loop, the `router_chat(` call at ~line 385): update the import at the top from `from core.llm import chat as router_chat` to `from core.llm import chat_with_meta as router_chat_with_meta`, and replace the call block:

```python
            _emo = emotion.detect_emotion(user_input) or {}
            turn = classify(
                user_input,
                emotion_label=_emo.get("label"),
                emotion_score=_emo.get("score", 0.0),
            )
            reply_content, route_meta = router_chat_with_meta(
                messages_to_send,
                sensitivity="personal",
                task=route_task_tag(turn),
                model=CONFIG["model"]["chat"],
            )

            reply = clean_reply(reply_content, mode=turn.mode)
```

with the imports `from core.llm.turn_classifier import classify` and `from server.chat_pipeline import route_task_tag` added at the top of `core/agent.py`. (If the run loop already computes an emotion result before this point, reuse that variable instead of `_emo`.) After the output-protocol block where the reply is printed, append the marker:

```python
            if route_meta.backend_used == "cloud":
                reply = f"{reply}\n\n☁ cloud brain"
```

(place it right before `print(f"{agent_name}: {reply}")`).

- [ ] **Step 6: Full suite + import smoke**

Run: `py -3.12 -m pytest -q`
Expected: PASS.
Run: `py -3.12 -c "import server.chat_pipeline, core.agent; print('ok')"`
Expected: `ok`.

- [ ] **Step 7: Commit**

```bash
git add server/chat_pipeline.py core/agent.py tests/llm/test_route_tags.py
git commit -m "feat: wire turn classifier into chat pipeline + console — task tags, mode hints, cloud marker, /cloud preview"
```

---

## Task 7: `/api/cloud/deep` endpoint + Deep Mode toggle UI

**Files:**
- Modify: `server/app.py`
- Modify: `ui/templates/index.html`

- [ ] **Step 1: Add the endpoint**

In `server/app.py`, next to `CloudEnabledRequest` add:

```python
class CloudDeepModeRequest(BaseModel):
    enabled: bool
```

Next to the existing `/api/cloud/*` routes add:

```python
@app.post("/api/cloud/deep")
async def post_cloud_deep(req: CloudDeepModeRequest, user_id: str = Depends(require_user)):
    cloud_settings.set_deep_mode(req.enabled)
    return {"success": True, "deep_mode": req.enabled}
```

- [ ] **Step 2: Import smoke**

Run: `py -3.12 -c "import server.app; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Update the Cloud Brain section in `ui/templates/index.html`**

In `renderCloudSettings(s)`:

(a) Replace the caption string

```
When on, general chat with Pike uses Anthropic Opus 4.8 for stronger reasoning. Your private data — email, journals, memory — always stays local.
```

with

```
When on, task-shaped requests (drafts, analysis, planning) use Anthropic Opus 4.8. Conversation and emotional support stay local. Your private data — email, journals, memory — always stays local.
```

(b) Immediately after the escalation-toggle `</div></div>` block (before the API-key field), insert:

```js
    var deepChecked = s.deep_mode ? ' checked' : '';
    html += '<div class="setting-row">';
    html += '<div class="setting-label">Deep Mode';
    html += '<div class="setting-desc">Heavy emotional conversations may also use the cloud brain. Off = feelings never leave this machine.</div>';
    html += '</div>';
    html += '<div class="setting-control">';
    html += '<label class="proto-toggle"><input type="checkbox" id="deepModeToggle"' + deepChecked + ' onchange="saveDeepMode(this.checked)"><span class="toggle-slider"></span></label>';
    html += '<span class="setting-saved" id="deepMode-saved">SAVED</span>';
    html += '</div></div>';
```

(`var deepChecked` goes at the top of the function with `var checked`; the html lines go in render order.)

(c) After `saveCloudEnabled`, add:

```js
async function saveDeepMode(enabled) {
    var savedEl = document.getElementById('deepMode-saved');
    try {
        const res = await authFetch(API + '/cloud/deep', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({enabled: enabled}),
        });
        const data = await res.json();
        if (data.success && savedEl) {
            savedEl.classList.add('show');
            setTimeout(function() { savedEl.classList.remove('show'); }, 1500);
        }
    } catch (e) { console.error('Deep Mode toggle failed:', e); }
}
```

(d) Visually confirm balanced braces on the added function (no JS harness).

- [ ] **Step 4: Commit**

```bash
git add server/app.py ui/templates/index.html
git commit -m "feat: Deep Mode toggle (/api/cloud/deep + Cloud Brain settings row) + caption reflects task-gated escalation"
```

---

## Task 8: Personality pack updates (pike + default)

**Files:**
- Modify: `packs/personalities/pike/personality.txt`
- Modify: `packs/personalities/default/personality.txt`

- [ ] **Step 1: pike — relax the blanket caps, add the emotional section**

In `packs/personalities/pike/personality.txt`:

(a) In `=== HOW TO RESPOND ===`, replace the line

```
- 1-2 sentences. Almost always. If you wrote 3 or more, cut some.
```

with

```
- 1-2 sentences by default. When a [Response mode: ...] note gives you room, use it — more sentences, never more filler.
```

(b) In `=== HARD RULES ===`, replace the line

```
- Max 2 sentences. Always.
```

with

```
- 1-2 sentences by default. A [Response mode: ...] note is the ONLY thing that gives you more room.
```

(c) Insert a new section immediately BEFORE `=== NOTETAKER DUTY ===` (present after the PR #5 merge; if absent, insert before `=== WHEN YOU CAN'T DO SOMETHING ===`):

```
=== EMOTIONAL PRESENCE ===
When they open up — grief, fear, stress, something heavy — you have room. A [Response mode: emotional support] note means up to 5-6 sentences.
- Use the room for presence, not padding. Reflect their SPECIFIC words back. Sit with what they said.
- At most one real question, and only if it helps them keep talking.
- The bans still hold: no generic advice, no cheerleading, no "you've got this", no fixing unless they ask.
- Never roleplay it. No scene-setting, no actions, no props. Just talk to them like someone who cares.

```

- [ ] **Step 2: default pack — same changes, lighter voice**

In `packs/personalities/default/personality.txt`:

(a) In `=== HOW TO RESPOND ===`, replace

```
- 1-2 sentences default. Longer only for practical help (plans, recipes, advice).
```

with

```
- 1-2 sentences default. Longer only for practical help, or when a [Response mode: ...] note gives you room.
```

(b) Insert immediately BEFORE `=== NOTETAKER DUTY ===` (or `=== WHEN YOU CAN'T DO SOMETHING ===` if absent):

```
=== EMOTIONAL PRESENCE ===
When they open up about something heavy, a [Response mode: emotional support] note means up to 5-6 sentences.
- Presence, not padding. Reflect their specific words. Sit with it.
- At most one real question. No generic advice, no cheerleading, no roleplay.

```

- [ ] **Step 3: Sanity run (packs load at session build)**

Run: `py -3.12 -m pytest tests/test_protocols.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add packs/personalities/pike/personality.txt packs/personalities/default/personality.txt
git commit -m "feat: emotional-presence pack sections — mode notes grant room, roleplay/filler bans stay absolute"
```

---

## Task 9: Verification

- [ ] **Step 1: Full suite**

Run: `py -3.12 -m pytest -q`
Expected: PASS (baseline + ~40 new).

- [ ] **Step 2: Privacy invariants unchanged**

Run: `grep -rn "sensitivity=\"private\"" core/ server/`
Confirm: same four private sites as before (email_assistant, fact_extractor, journal, briefing) — untouched by this build.

Run: `grep -rn "chat_task\|chat_emotional\|chat_casual" core/ server/ | grep -v test`
Confirm: tags are produced ONLY in `server/chat_pipeline.py` / `core/agent.py` (via `route_task_tag`) and consumed ONLY in `core/llm/policy.py`.

- [ ] **Step 3: Import smoke**

Run: `py -3.12 -c "import server.app, server.chat_pipeline, core.agent, core.llm.turn_classifier, core.reply_shaping; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit anything outstanding + push + PR**

```bash
git add -A
git commit -m "chore: finalize response-modes build" || echo "nothing to commit"
git push -u origin feat/response-modes
gh pr create --title "Response modes: task-tier escalation + cloud announcement + emotional depth (hybrid brain 2+3/3)" --body "See docs/superpowers/specs/2026-07-02-response-modes-design.md"
```

---

## Manual verification (operator, after merge)

Cloud OFF (default): everything behaves as before, except emotional turns may now run to ~6 sentences and task-shaped asks keep their structure. Confirm a casual "hey" still gets a short reply.

Cloud ON (Settings → Cloud Brain):
1. "hey pike, how's it going" → local, no ☁.
2. "help me draft a cover letter for X" → ☁ marker, full structured reply, no 3-sentence squash.
3. `/cloud` → shows model, time, message count, final sent message.
4. A heavy emotional message → NO ☁ (local), longer-than-2-sentence warm reply, no advice, no roleplay.
5. "don't think hard about this one" → no ☁.
6. "think harder — what's really going on with this plan" → ☁.
7. Deep Mode ON → repeat (4): now ☁ appears. Toggle back OFF.
8. Confirm the marker does not appear in the next turn's context (history stores the clean reply).

## Stage 6 — live emotional-tuning session (with Switch, cannot be automated)

The acceptance test for this feature's actual purpose. Agenda:
- Run 6–8 real scenarios (casual check-in, heavy grief turn, stressed-work vent, mixed "sad + asking for help", task ask, override phrases).
- Tune: `EMOTION_VETO_THRESHOLD` (start 0.75), emotional budget (start 6), the `_MODE_HINTS["emotional"]` wording, and the EMOTIONAL PRESENCE pack text — until Pike feels present rather than clipped or rambly.
- Watch for: roleplay regression (should be impossible — stripper is mode-independent), advice creep, the 8B overrunning the budget (cap catches it; confirm the truncation doesn't cut mid-thought awkwardly too often).
