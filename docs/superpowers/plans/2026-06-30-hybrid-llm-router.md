# Hybrid Local/Cloud LLM Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single `core/llm/` seam every LLM call routes through, with a 3-tier sensitivity policy that keeps everything local this build (cloud adapter stubbed).

**Architecture:** New infra package `core/llm/` sits below the protocol layer. A pure `policy.decide()` function chooses a backend from `(sensitivity, config, task)`; `router.chat()` executes on the chosen backend, falling back to local (and logging a preview) whenever cloud is selected but unavailable. All 7 existing `ollama.chat` call sites are refactored to call `router.chat()` with a sensitivity tag.

**Tech Stack:** Python 3.12, `ollama` package, `pytest`, stdlib `dataclasses`/`json`/`logging`.

**Spec:** `docs/superpowers/specs/2026-06-30-hybrid-llm-router-design.md`

---

## File Structure

- Create: `core/llm/__init__.py` — exports `chat`.
- Create: `core/llm/policy.py` — `RouteDecision` + pure `decide()`.
- Create: `core/llm/config.py` — `RouterConfig` + `load_config()` (defaults + `data/llm_router.json` override).
- Create: `core/llm/backends.py` — `LocalBackend` (real), `CloudBackend` (stub).
- Create: `core/llm/router.py` — `chat()` entry point, backend selection + fallback logging.
- Modify: `core/config/core_config.json` — add `llm_router` defaults block.
- Modify (call sites): `core/email_assistant.py`, `core/agent.py`, `server/chat_pipeline.py`, `core/memory/fact_extractor.py`, `core/memory/journal.py`, `core/briefing.py`, `core/protocols/web.py`.
- Create (tests): `tests/llm/test_policy.py`, `tests/llm/test_config.py`, `tests/llm/test_backends.py`, `tests/llm/test_router.py`, `tests/llm/test_call_sites.py`.

Run all tests from the project root: `cd C:/Users/dusti/Projects/aegis-ai && python -m pytest tests/llm -v`

---

## Task 1: Routing policy (pure decision function)

**Files:**
- Create: `core/llm/policy.py`
- Test: `tests/llm/test_policy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_policy.py
import pytest
from core.llm.policy import decide, RouteDecision, VALID_SENSITIVITIES


class _Cfg:
    """Minimal stand-in for RouterConfig (policy only reads two attrs)."""
    def __init__(self, cloud_enabled=False, cloud_opt_in_features=()):
        self.cloud_enabled = cloud_enabled
        self.cloud_opt_in_features = tuple(cloud_opt_in_features)


def test_cloud_disabled_forces_local_for_every_tier():
    cfg = _Cfg(cloud_enabled=False)
    for tier in VALID_SENSITIVITIES:
        d = decide(tier, cfg)
        assert d.backend == "local"
        assert d.reason == "cloud_disabled"
        assert d.would_send_cloud is False


def test_offline_forces_local_even_when_cloud_enabled():
    cfg = _Cfg(cloud_enabled=True)
    d = decide("public", cfg, offline=True)
    assert d.backend == "local"
    assert d.reason == "offline"
    assert d.would_send_cloud is False


def test_private_stays_local_by_default_when_cloud_enabled():
    cfg = _Cfg(cloud_enabled=True)
    d = decide("private", cfg, task="summarize")
    assert d.backend == "local"
    assert d.reason == "private_local_default"
    assert d.would_send_cloud is False


def test_private_escalates_only_when_task_opted_in():
    cfg = _Cfg(cloud_enabled=True, cloud_opt_in_features=("summarize",))
    d = decide("private", cfg, task="summarize")
    assert d.backend == "cloud"
    assert d.would_send_cloud is True


def test_personal_and_public_are_cloud_eligible_when_enabled():
    cfg = _Cfg(cloud_enabled=True)
    for tier in ("personal", "public"):
        d = decide(tier, cfg)
        assert d.backend == "cloud"
        assert d.reason == "cloud_eligible"
        assert d.would_send_cloud is True


def test_invalid_sensitivity_raises():
    with pytest.raises(ValueError):
        decide("secret", _Cfg())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/llm/test_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.llm'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/llm/policy.py
"""Pure routing-decision logic for the hybrid LLM router.

No I/O, no Ollama — a lookup over (sensitivity, config, task). Kept separate
from router.py so the whole policy is unit-testable without a model.
"""
from __future__ import annotations

from dataclasses import dataclass

VALID_SENSITIVITIES = ("private", "personal", "public")


@dataclass(frozen=True)
class RouteDecision:
    """The router's choice for one call."""
    backend: str            # "local" | "cloud"
    reason: str
    would_send_cloud: bool  # True when policy picked cloud (drives the preview log)


def decide(sensitivity, cfg, *, task=None, offline=False):
    """Choose a backend for one LLM call.

    cfg must expose .cloud_enabled (bool) and .cloud_opt_in_features (iterable
    of task tags). `task` is used only as the per-feature opt-in key for the
    `private` tier — it does NOT drive tier escalation this build.
    """
    if sensitivity not in VALID_SENSITIVITIES:
        raise ValueError(
            f"Unknown sensitivity {sensitivity!r}; expected one of {VALID_SENSITIVITIES}"
        )
    if not cfg.cloud_enabled:
        return RouteDecision("local", "cloud_disabled", False)
    if offline:
        return RouteDecision("local", "offline", False)
    if sensitivity == "private" and task not in cfg.cloud_opt_in_features:
        return RouteDecision("local", "private_local_default", False)
    return RouteDecision("cloud", "cloud_eligible", True)
```

Also create an empty package marker:

```python
# core/llm/__init__.py
"""Hybrid local/cloud LLM router (see docs/superpowers/specs/2026-06-30-hybrid-llm-router-design.md)."""
```

And ensure the tests package dir exists:

```python
# tests/llm/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/llm/test_policy.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add core/llm/__init__.py core/llm/policy.py tests/llm/__init__.py tests/llm/test_policy.py
git commit -m "feat: add pure routing policy for hybrid LLM router"
```

---

## Task 2: Router config (defaults + runtime override)

**Files:**
- Create: `core/llm/config.py`
- Modify: `core/config/core_config.json` (add `llm_router` block)
- Test: `tests/llm/test_config.py`

- [ ] **Step 1: Add defaults to `core/config/core_config.json`**

Add a top-level key (sibling of the existing `"model"` block). Keep JSON valid — add a comma after the preceding block:

```json
  "llm_router": {
    "cloud_enabled": false,
    "cloud_opt_in_features": []
  }
```

- [ ] **Step 2: Write the failing test**

```python
# tests/llm/test_config.py
import json
import core.llm.config as cfgmod
from core.llm.config import RouterConfig, load_config


def test_defaults_are_local_only():
    cfg = load_config()
    assert cfg.cloud_enabled is False
    assert cfg.cloud_opt_in_features == ()


def test_override_file_flips_toggle(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    override.write_text(json.dumps(
        {"cloud_enabled": True, "cloud_opt_in_features": ["summarize"]}
    ), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)
    cfg = load_config()
    assert cfg.cloud_enabled is True
    assert cfg.cloud_opt_in_features == ("summarize",)


def test_corrupt_override_falls_back_to_defaults(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    override.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)
    cfg = load_config()  # must not raise
    assert cfg.cloud_enabled is False
    assert cfg.cloud_opt_in_features == ()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/llm/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.llm.config'`

- [ ] **Step 4: Write minimal implementation**

```python
# core/llm/config.py
"""Loads router settings: core_config.json defaults, overlaid by an optional
runtime override at data/llm_router.json (gitignored, created out-of-band)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from core.config import CONFIG, PROJECT_ROOT

logger = logging.getLogger(__name__)

_OVERRIDE_PATH = PROJECT_ROOT / "data" / "llm_router.json"


@dataclass
class RouterConfig:
    cloud_enabled: bool = False
    cloud_opt_in_features: tuple = field(default_factory=tuple)


def load_config():
    """Build a RouterConfig from config defaults + optional override file.

    A missing or corrupt override file is logged and ignored (never crashes).
    """
    defaults = CONFIG.get("llm_router", {})
    cloud_enabled = bool(defaults.get("cloud_enabled", False))
    opt_in = list(defaults.get("cloud_opt_in_features", []))

    if _OVERRIDE_PATH.exists():
        try:
            data = json.loads(_OVERRIDE_PATH.read_text(encoding="utf-8"))
            if "cloud_enabled" in data:
                cloud_enabled = bool(data["cloud_enabled"])
            if "cloud_opt_in_features" in data:
                opt_in = list(data["cloud_opt_in_features"])
        except Exception:
            logger.exception("Bad %s — using config defaults", _OVERRIDE_PATH)

    return RouterConfig(cloud_enabled=cloud_enabled,
                        cloud_opt_in_features=tuple(opt_in))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/llm/test_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add core/llm/config.py core/config/core_config.json tests/llm/test_config.py
git commit -m "feat: add router config with runtime override + safe fallback"
```

---

## Task 3: Backends (local real, cloud stub)

**Files:**
- Create: `core/llm/backends.py`
- Test: `tests/llm/test_backends.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_backends.py
import pytest
import core.llm.backends as backends
from core.llm.backends import LocalBackend, CloudBackend


def test_cloud_backend_is_unavailable_and_refuses():
    cb = CloudBackend()
    assert cb.available() is False
    with pytest.raises(NotImplementedError):
        cb.chat([{"role": "user", "content": "hi"}], model="x")


def test_local_backend_is_available():
    assert LocalBackend().available() is True


def test_local_backend_passes_params_and_returns_content(monkeypatch):
    captured = {}

    def fake_ollama_chat(**kwargs):
        captured.update(kwargs)
        return {"message": {"content": "pong"}}

    monkeypatch.setattr(backends.ollama, "chat", fake_ollama_chat)
    out = LocalBackend().chat(
        [{"role": "user", "content": "ping"}],
        model="qwen3:8b", options={"temperature": 0.2}, format="json",
    )
    assert out == "pong"
    assert captured["model"] == "qwen3:8b"
    assert captured["messages"] == [{"role": "user", "content": "ping"}]
    assert captured["options"] == {"temperature": 0.2}
    assert captured["format"] == "json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/llm/test_backends.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.llm.backends'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/llm/backends.py
"""LLM backends behind the router.

LocalBackend wraps ollama.chat (real). CloudBackend is a stub this build:
available() is False so the router never routes to it — the later cloud build
fills in the Anthropic adapter here.
"""
from __future__ import annotations

import ollama

from core.config import CONFIG


class LocalBackend:
    """Ollama-backed local inference."""
    name = "local"

    def available(self):
        return True

    def chat(self, messages, *, model=None, options=None, format=None):
        kwargs = {
            "model": model or CONFIG["model"]["chat"],
            "messages": messages,
        }
        if options:
            kwargs["options"] = options
        if format:
            kwargs["format"] = format
        response = ollama.chat(**kwargs)
        return response["message"]["content"]


class CloudBackend:
    """Placeholder for the future Claude API adapter. Not wired this build."""
    name = "cloud"

    def available(self):
        return False

    def chat(self, messages, *, model=None, options=None, format=None):
        raise NotImplementedError("Cloud backend is not wired yet (local-only build)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/llm/test_backends.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/llm/backends.py tests/llm/test_backends.py
git commit -m "feat: add local (ollama) backend and cloud stub for router"
```

---

## Task 4: Router entry point (`chat`) + fallback logging

**Files:**
- Create: `core/llm/router.py`
- Modify: `core/llm/__init__.py` (export `chat`)
- Test: `tests/llm/test_router.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_router.py
import pytest
import core.llm.router as router


class _FakeBackend:
    def __init__(self, name, available):
        self.name = name
        self._available = available
        self.calls = []

    def available(self):
        return self._available

    def chat(self, messages, *, model=None, options=None, format=None):
        self.calls.append({"messages": messages, "model": model,
                           "options": options, "format": format})
        return f"reply-from-{self.name}"


def _patch_backends(monkeypatch, local, cloud):
    monkeypatch.setattr(router, "_BACKENDS", {"local": local, "cloud": cloud})


def _cfg(monkeypatch, cloud_enabled, opt_in=()):
    class C:
        pass
    c = C()
    c.cloud_enabled = cloud_enabled
    c.cloud_opt_in_features = tuple(opt_in)
    monkeypatch.setattr(router, "load_config", lambda: c)


def test_local_when_cloud_disabled(monkeypatch):
    local = _FakeBackend("local", True)
    cloud = _FakeBackend("cloud", False)
    _patch_backends(monkeypatch, local, cloud)
    _cfg(monkeypatch, cloud_enabled=False)

    out = router.chat([{"role": "user", "content": "hi"}], sensitivity="personal")
    assert out == "reply-from-local"
    assert len(local.calls) == 1
    assert len(cloud.calls) == 0


def test_cloud_decision_falls_back_to_local_when_unavailable(monkeypatch, caplog):
    local = _FakeBackend("local", True)
    cloud = _FakeBackend("cloud", False)  # unavailable -> fallback
    _patch_backends(monkeypatch, local, cloud)
    _cfg(monkeypatch, cloud_enabled=True)

    with caplog.at_level("INFO"):
        out = router.chat([{"role": "user", "content": "hi"}], sensitivity="public")
    assert out == "reply-from-local"        # executed locally
    assert len(cloud.calls) == 0            # cloud never actually called
    assert any("cloud escalation preview" in r.message for r in caplog.records)


def test_params_pass_through_to_backend(monkeypatch):
    local = _FakeBackend("local", True)
    cloud = _FakeBackend("cloud", False)
    _patch_backends(monkeypatch, local, cloud)
    _cfg(monkeypatch, cloud_enabled=False)

    router.chat([{"role": "user", "content": "x"}], sensitivity="private",
                model="m1", options={"temperature": 0.1}, format="json")
    call = local.calls[0]
    assert call["model"] == "m1"
    assert call["options"] == {"temperature": 0.1}
    assert call["format"] == "json"


def test_bad_sensitivity_raises(monkeypatch):
    _patch_backends(monkeypatch, _FakeBackend("local", True), _FakeBackend("cloud", False))
    _cfg(monkeypatch, cloud_enabled=False)
    with pytest.raises(ValueError):
        router.chat([{"role": "user", "content": "x"}], sensitivity="topsecret")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/llm/test_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.llm.router'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/llm/router.py
"""The single seam every Aegis LLM call routes through.

chat() picks a backend via policy.decide(), then executes it. When policy
picks cloud but the cloud backend is unavailable (always, this build), it logs
a transparency preview and executes locally — the same path the later cloud
build reuses for offline fallback.
"""
from __future__ import annotations

import logging

from core.llm import policy as _policy
from core.llm.backends import CloudBackend, LocalBackend
from core.llm.config import load_config

logger = logging.getLogger(__name__)

_BACKENDS = {"local": LocalBackend(), "cloud": CloudBackend()}


def chat(messages, *, sensitivity, task=None, model=None, options=None, format=None):
    """Route one LLM call and return the response content string.

    sensitivity: "private" | "personal" | "public" (required — every site tags).
    task: opt-in / intent tag, logged; inert for tier escalation this build.
    model/options/format: passthrough to the backend (ollama semantics).
    """
    cfg = load_config()
    decision = _policy.decide(sensitivity, cfg, task=task)
    backend = _BACKENDS[decision.backend]

    if decision.backend == "cloud" and not backend.available():
        logger.info(
            "[llm-router] cloud escalation preview: sensitivity=%s task=%s "
            "reason=%s — cloud unavailable, executing locally",
            sensitivity, task, decision.reason,
        )
        backend = _BACKENDS["local"]

    return backend.chat(messages, model=model, options=options, format=format)
```

Update the package export:

```python
# core/llm/__init__.py
"""Hybrid local/cloud LLM router (see docs/superpowers/specs/2026-06-30-hybrid-llm-router-design.md)."""
from core.llm.router import chat  # noqa: F401
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/llm/test_router.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest tests/llm -v`
Expected: PASS (16 passed)

- [ ] **Step 6: Commit**

```bash
git add core/llm/router.py core/llm/__init__.py tests/llm/test_router.py
git commit -m "feat: add router chat() entry point with cloud-preview fallback"
```

---

## Task 5: Refactor the email seam

**Files:**
- Modify: `core/email_assistant.py:48-62`

- [ ] **Step 1: Replace the `_llm` body to delegate to the router**

Current (lines 48-62) wraps `ollama.chat` directly. Replace the function body so it calls the router, keeping the existing signature and the `sensitivity`/`task` kwargs it already carries:

```python
def _llm(messages: list[dict], *, sensitivity: str = "private",
         task: str | None = None) -> str:
    """Call the chat model via the central router and return the content.

    Email is personal content: tagged sensitivity="private" so it stays local
    by default even after the cloud backend is wired (see router spec).
    """
    return _router_chat(messages, sensitivity=sensitivity, task=task)
```

Add the import near the top (with the other `core.*` imports, replacing the now-unused `import ollama` if nothing else in the file uses it — verify with a grep):

```python
from core.llm import chat as _router_chat
```

Note: the old default was `sensitivity="local"`; change it to `"private"` to match the valid 3-tier taxonomy (`"local"` is not a valid sensitivity and would raise).

- [ ] **Step 2: Verify no other `ollama` use remains in the file**

Run: `grep -n "ollama" core/email_assistant.py`
Expected: no matches. If none, delete the `import ollama` line.

- [ ] **Step 3: Sanity-import the module**

Run: `python -c "import core.email_assistant"`
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add core/email_assistant.py
git commit -m "refactor: route email LLM calls through central router (private)"
```

---

## Task 6: Refactor the two main chat loops

**Files:**
- Modify: `core/agent.py:384-390`
- Modify: `server/chat_pipeline.py:137-144`

- [ ] **Step 1: Refactor `core/agent.py`**

Replace the direct call (lines ~384-390):

```python
        try:
            reply_content = router_chat(
                messages_to_send,
                sensitivity="personal",
                task="chat",
                model=CONFIG["model"]["chat"],
            )

            reply = clean_reply(reply_content)
```

Add the import alongside the existing `import ollama` line (line 17). Keep `import ollama` only if still used elsewhere in the file (verify in Step 3):

```python
from core.llm import chat as router_chat
```

- [ ] **Step 2: Refactor `server/chat_pipeline.py`**

Keep the thread offload (router.chat is synchronous like ollama.chat). Replace lines ~137-144:

```python
    try:
        # Run the (synchronous) router in a thread so we don't block the loop
        reply_content = await asyncio.to_thread(
            router_chat,
            messages_to_send,
            sensitivity="personal",
            task="chat",
            model=CONFIG["model"]["chat"],
        )
        reply = session.clean_reply(reply_content)
```

Add the import near the top (with existing imports; `asyncio.to_thread` needs a callable + kwargs — it accepts kwargs directly):

```python
from core.llm import chat as router_chat
```

- [ ] **Step 3: Verify remaining `ollama` usage in each file**

Run: `grep -n "ollama" core/agent.py server/chat_pipeline.py`
Expected: only lines that still legitimately use ollama, if any. Remove now-dead `import ollama` from any file where no match remains.

- [ ] **Step 4: Sanity-import**

Run: `python -c "import core.agent, server.chat_pipeline"`
Expected: no error.

- [ ] **Step 5: Commit**

```bash
git add core/agent.py server/chat_pipeline.py
git commit -m "refactor: route main chat loops through central router (personal)"
```

---

## Task 7: Refactor the memory + briefing sites

**Files:**
- Modify: `core/memory/fact_extractor.py:76-79`
- Modify: `core/memory/journal.py:70-73`
- Modify: `core/briefing.py:250-256`

- [ ] **Step 1: Refactor `core/memory/fact_extractor.py`**

Replace lines ~76-79:

```python
    raw_content = router_chat(
        [{"role": "user", "content": prompt}],
        sensitivity="private",
        task="extract",
        model=CONFIG["model"]["fact_extraction"],
    )
```

Then update the following lines that read `response["message"]["content"]` to use `raw_content` directly. Add import:

```python
from core.llm import chat as router_chat
```

- [ ] **Step 2: Refactor `core/memory/journal.py`**

Replace lines ~70-73:

```python
    summary_content = router_chat(
        [{"role": "user", "content": prompt}],
        sensitivity="private",
        task="summarize",
        model=CONFIG["model"]["summary"],
    )
```

Update the next line to strip `<think>` from `summary_content` instead of `response["message"]["content"]`. Add import:

```python
from core.llm import chat as router_chat
```

- [ ] **Step 3: Refactor `core/briefing.py`**

Replace lines ~250-256:

```python
        briefing_content = router_chat(
            [
                {"role": "system", "content": session.system_prompt_base},
                {"role": "user", "content": user_prompt},
            ],
            sensitivity="private",
            task="summarize",
            model=CONFIG["model"]["chat"],
        )
```

Update the subsequent use of `response["message"]["content"]` to `briefing_content`. Add import:

```python
from core.llm import chat as router_chat
```

- [ ] **Step 4: Remove dead `import ollama` where unused**

Run: `grep -n "ollama" core/memory/fact_extractor.py core/memory/journal.py core/briefing.py`
Expected: remove any `import ollama` line with no remaining usage in its file.

- [ ] **Step 5: Sanity-import**

Run: `python -c "import core.memory.fact_extractor, core.memory.journal, core.briefing"`
Expected: no error.

- [ ] **Step 6: Commit**

```bash
git add core/memory/fact_extractor.py core/memory/journal.py core/briefing.py
git commit -m "refactor: route fact/journal/briefing LLM calls through router (private)"
```

---

## Task 8: Refactor the web news-summary site

**Files:**
- Modify: `core/protocols/web.py:686,703-707`

- [ ] **Step 1: Refactor the summary call**

At line ~686 there is a local `import ollama` and at ~703 the call. Replace the call (lines ~703-707):

```python
            summary = router_chat(
                [{"role": "user", "content": prompt}],
                sensitivity="public",
                task="summarize",
                model=model,
            )
```

`summary` now holds the content string directly (the old `response["message"]["content"]` assignment is removed). Add the import at the top of the file with the other imports and delete the local `import ollama` at line ~686 (verify no other ollama use in the file):

```python
from core.llm import chat as router_chat
```

- [ ] **Step 2: Verify remaining `ollama` usage**

Run: `grep -n "ollama" core/protocols/web.py`
Expected: no matches → remove any leftover `import ollama`.

- [ ] **Step 3: Sanity-import**

Run: `python -c "import core.protocols.web"`
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add core/protocols/web.py
git commit -m "refactor: route web news summary through router (public)"
```

---

## Task 9: Call-site tagging regression tests

Prove each refactored site calls the router with the correct sensitivity, without hitting a model.

**Files:**
- Test: `tests/llm/test_call_sites.py`

- [ ] **Step 1: Write the tests**

```python
# tests/llm/test_call_sites.py
"""Assert each refactored site calls router.chat with the right sensitivity.

These patch the router so no Ollama call happens. They target the module-level
`router_chat`/`_router_chat` names the call sites import.
"""
import core.email_assistant as email_assistant


def test_email_llm_tags_private(monkeypatch):
    captured = {}

    def fake_chat(messages, *, sensitivity, task=None, **kw):
        captured["sensitivity"] = sensitivity
        captured["task"] = task
        return "ok"

    monkeypatch.setattr(email_assistant, "_router_chat", fake_chat)
    out = email_assistant._llm([{"role": "user", "content": "hi"}], task="draft")
    assert out == "ok"
    assert captured["sensitivity"] == "private"
    assert captured["task"] == "draft"
```

- [ ] **Step 2: Run to verify it passes**

Run: `python -m pytest tests/llm/test_call_sites.py -v`
Expected: PASS (1 passed)

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest tests/llm -v`
Expected: PASS (17 passed)

- [ ] **Step 4: Commit**

```bash
git add tests/llm/test_call_sites.py
git commit -m "test: assert email seam tags LLM calls private"
```

---

## Task 10: Final verification

- [ ] **Step 1: Run the full router suite**

Run: `python -m pytest tests/llm -v`
Expected: all pass.

- [ ] **Step 2: Confirm no stray direct `ollama.chat` remains in refactored sites**

Run: `grep -rn "ollama.chat" core/ server/ | grep -v "core/llm/backends.py"`
Expected: no matches (only `core/llm/backends.py` legitimately calls `ollama.chat`).

- [ ] **Step 3: Import-smoke the whole surface**

Run: `python -c "import core.email_assistant, core.agent, server.chat_pipeline, core.memory.fact_extractor, core.memory.journal, core.briefing, core.protocols.web"`
Expected: no error.

- [ ] **Step 4: Final commit if anything outstanding**

```bash
git add -A
git commit -m "chore: finalize hybrid LLM router local-only build" || echo "nothing to commit"
```

---

## Notes for the implementer

- **Line numbers drift** as you edit. Re-grep for `ollama.chat` in each target file before editing rather than trusting the numbers here.
- **`CONFIG` import**: every call site already imports `CONFIG` (it reads `CONFIG["model"][...]`), so no new config import is needed there — only the `from core.llm import chat as router_chat` line.
- **Do NOT mock Ollama in tests** (CLAUDE.md rule). The suite here never calls a real model: policy/config are pure, backends monkeypatch `ollama.chat`, router uses fake backends, call-site tests patch the router.
- **`asyncio.to_thread(router_chat, messages, sensitivity=..., ...)`** — `to_thread` forwards `*args, **kwargs` to the callable, so keyword args pass through fine.
```
