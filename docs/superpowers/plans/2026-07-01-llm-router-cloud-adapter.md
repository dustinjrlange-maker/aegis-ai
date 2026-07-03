# LLM Router Cloud Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the stubbed `CloudBackend` into a working Anthropic Claude API adapter so cloud escalation actually runs, with a transparent local fallback on any cloud failure.

**Architecture:** Fill in three existing `core/llm/` seams — `CloudBackend` (real Anthropic SDK call + ollama→Anthropic message translation + refusal detection), `router.chat` (try/except around the cloud call → local fallback), and `config.py` (API-key resolution + `cloud_model`/`cloud_max_tokens`). Policy and all 7 tagged call sites are untouched. Privacy posture is unchanged: toggle defaults OFF; `private` stays local by default.

**Tech Stack:** Python 3.12, `anthropic` SDK, `ollama` (local), `pytest`. Default cloud model `claude-opus-4-8`.

**Spec:** `docs/superpowers/specs/2026-07-01-llm-router-cloud-adapter-design.md`

---

## File Structure

- Modify: `requirements.txt` — add `anthropic`.
- Modify: `core/config/core_config.json` — add `cloud_model` / `cloud_max_tokens` to the `llm_router` block.
- Modify: `core/llm/config.py` — `RouterConfig` gains `cloud_model`/`cloud_max_tokens`; `load_config()` reads them; add `resolve_api_key()` + `_KEY_FILE`.
- Modify: `core/llm/backends.py` — `CloudBackend` becomes real; add `CloudRefusalError`, `CloudResponseError`, `_split_system()`, `_anthropic_installed()`.
- Modify: `core/llm/router.py` — cloud call wrapped in try/except with local fallback.
- Modify: `CLAUDE.md` — supersede the "no cloud APIs" rule.
- Test: `tests/llm/test_config.py`, `tests/llm/test_backends.py`, `tests/llm/test_router.py` (all exist; extend + fix the obsolete cloud stub test).

Run all router tests from the project root: `python -m pytest tests/llm -v`

---

## Task 1: Add the `anthropic` dependency + config defaults

**Files:**
- Modify: `requirements.txt`
- Modify: `core/config/core_config.json`

- [ ] **Step 1: Add `anthropic` to `requirements.txt`**

Add this line (keep the file's existing ordering/style; append if unsure):

```
anthropic
```

- [ ] **Step 2: Install it**

Run: `python -m pip install anthropic`
Expected: installs successfully; `python -c "import anthropic; print(anthropic.__version__)"` prints a version.

- [ ] **Step 3: Add cloud fields to `core/config/core_config.json`**

Find the existing `llm_router` block (added in the prior build):

```json
  "llm_router": {
    "cloud_enabled": false,
    "cloud_opt_in_features": []
  }
```

Replace it with (add the two new keys; keep JSON valid):

```json
  "llm_router": {
    "cloud_enabled": false,
    "cloud_opt_in_features": [],
    "cloud_model": "claude-opus-4-8",
    "cloud_max_tokens": 2048
  }
```

- [ ] **Step 4: Verify the JSON still parses**

Run: `python -c "import json; json.load(open(r'C:/Users/dusti/Projects/aegis-ai/core/config/core_config.json')); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Confirm `data/` is gitignored (key will live there)**

Run: `git check-ignore data/anthropic_key`
Expected: prints `data/anthropic_key` (meaning it's ignored). If it prints nothing, add `data/` to `.gitignore` in this step and re-run.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt core/config/core_config.json
git commit -m "feat: add anthropic dependency + cloud model/token config defaults"
```

---

## Task 2: `RouterConfig` gains `cloud_model` / `cloud_max_tokens`

**Files:**
- Modify: `core/llm/config.py`
- Test: `tests/llm/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/llm/test_config.py`:

```python
def test_cloud_model_and_tokens_load_from_defaults():
    cfg = load_config()
    assert cfg.cloud_model == "claude-opus-4-8"
    assert cfg.cloud_max_tokens == 2048


def test_cloud_model_and_tokens_override(tmp_path, monkeypatch):
    override = tmp_path / "llm_router.json"
    override.write_text(json.dumps(
        {"cloud_model": "claude-sonnet-4-6", "cloud_max_tokens": 512}
    ), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_OVERRIDE_PATH", override)
    cfg = load_config()
    assert cfg.cloud_model == "claude-sonnet-4-6"
    assert cfg.cloud_max_tokens == 512
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/llm/test_config.py::test_cloud_model_and_tokens_load_from_defaults -v`
Expected: FAIL — `AttributeError: 'RouterConfig' object has no attribute 'cloud_model'`

- [ ] **Step 3: Extend `RouterConfig` and `load_config`**

In `core/llm/config.py`, replace the `RouterConfig` dataclass:

```python
@dataclass
class RouterConfig:
    """Resolved router settings used by policy.decide() and router.chat()."""
    cloud_enabled: bool = False
    cloud_opt_in_features: tuple = field(default_factory=tuple)
    cloud_model: str = "claude-opus-4-8"
    cloud_max_tokens: int = 2048
```

Then in `load_config()`, after the existing `opt_in = list(...)` line, add default reads:

```python
    cloud_model = str(defaults.get("cloud_model", "claude-opus-4-8"))
    cloud_max_tokens = int(defaults.get("cloud_max_tokens", 2048))
```

Inside the `if _OVERRIDE_PATH.exists():` try block, after the existing `cloud_opt_in_features` handling, add:

```python
            if "cloud_model" in data:
                cloud_model = str(data["cloud_model"])
            if "cloud_max_tokens" in data:
                cloud_max_tokens = int(data["cloud_max_tokens"])
```

And update the return:

```python
    return RouterConfig(
        cloud_enabled=cloud_enabled,
        cloud_opt_in_features=tuple(opt_in),
        cloud_model=cloud_model,
        cloud_max_tokens=cloud_max_tokens,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/llm/test_config.py -v`
Expected: PASS (all config tests).

- [ ] **Step 5: Commit**

```bash
git add core/llm/config.py tests/llm/test_config.py
git commit -m "feat: load cloud_model and cloud_max_tokens into RouterConfig"
```

---

## Task 3: API-key resolution (`resolve_api_key`)

**Files:**
- Modify: `core/llm/config.py`
- Test: `tests/llm/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/llm/test_config.py`:

```python
def test_resolve_api_key_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "anthropic_key")
    assert cfgmod.resolve_api_key() == "sk-env"


def test_resolve_api_key_falls_back_to_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    key_file = tmp_path / "anthropic_key"
    key_file.write_text("  sk-file\n", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", key_file)
    assert cfgmod.resolve_api_key() == "sk-file"  # trimmed


def test_resolve_api_key_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nope")
    assert cfgmod.resolve_api_key() is None


def test_resolve_api_key_blank_env_falls_through(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    monkeypatch.setattr(cfgmod, "_KEY_FILE", tmp_path / "nope")
    assert cfgmod.resolve_api_key() is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/llm/test_config.py::test_resolve_api_key_prefers_env -v`
Expected: FAIL — `AttributeError: module 'core.llm.config' has no attribute 'resolve_api_key'`

- [ ] **Step 3: Add `os` import, `_KEY_FILE`, and `resolve_api_key`**

In `core/llm/config.py`, add `import os` with the stdlib imports (top of file, before `import json` or alongside it). After the `_OVERRIDE_PATH` line, add:

```python
_KEY_FILE = PROJECT_ROOT / "data" / "anthropic_key"
```

At the end of the file, add:

```python
def resolve_api_key():
    """Resolve the Anthropic API key: ANTHROPIC_API_KEY env var first, then the
    gitignored data/anthropic_key file. Returns None if neither is present.
    Never raises — a bad key file is logged and treated as absent."""
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()
    if _KEY_FILE.exists():
        try:
            text = _KEY_FILE.read_text(encoding="utf-8").strip()
            return text or None
        except Exception:
            logger.exception("Bad %s — treating as no key", _KEY_FILE)
    return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/llm/test_config.py -v`
Expected: PASS (all config tests, 4 new).

- [ ] **Step 5: Commit**

```bash
git add core/llm/config.py tests/llm/test_config.py
git commit -m "feat: resolve_api_key (env var then data/anthropic_key file)"
```

---

## Task 4: Message translation + cloud exceptions

**Files:**
- Modify: `core/llm/backends.py`
- Test: `tests/llm/test_backends.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/llm/test_backends.py`:

```python
from core.llm.backends import _split_system, CloudRefusalError, CloudResponseError


def test_split_system_extracts_system_and_keeps_convo():
    system, convo = _split_system([
        {"role": "system", "content": "You are Pike."},
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "again"},
    ])
    assert system == "You are Pike.\n\nBe concise."
    assert convo == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "again"},
    ]


def test_split_system_empty_system_when_none():
    system, convo = _split_system([{"role": "user", "content": "hi"}])
    assert system == ""
    assert convo == [{"role": "user", "content": "hi"}]


def test_split_system_raises_when_no_leading_user():
    import pytest
    with pytest.raises(CloudResponseError):
        _split_system([{"role": "system", "content": "sys only"}])
    with pytest.raises(CloudResponseError):
        _split_system([{"role": "assistant", "content": "a"}])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/llm/test_backends.py::test_split_system_extracts_system_and_keeps_convo -v`
Expected: FAIL — `ImportError: cannot import name '_split_system'`

- [ ] **Step 3: Add exceptions and `_split_system` to `core/llm/backends.py`**

At the top of `core/llm/backends.py`, after the imports, add the exceptions:

```python
class CloudRefusalError(RuntimeError):
    """Claude declined the request (stop_reason == 'refusal')."""


class CloudResponseError(RuntimeError):
    """Cloud response could not be used (no text block, or bad message shape)."""
```

Add the translation helper (near the bottom, module-level):

```python
def _split_system(messages):
    """Translate ollama-style messages to the Anthropic shape.

    Anthropic takes the system prompt as a top-level `system=` string, not a
    role. Collect all system messages into one string; keep only user/assistant
    messages. The first remaining message must be a user turn.
    """
    system_parts = []
    convo = []
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content", "")
            if content:
                system_parts.append(content)
        else:
            convo.append({"role": m["role"], "content": m["content"]})
    if not convo or convo[0]["role"] != "user":
        raise CloudResponseError("Anthropic requires a leading user message")
    return "\n\n".join(system_parts), convo
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/llm/test_backends.py -v`
Expected: PASS (existing backend tests + 3 new; the pre-existing cloud stub test still passes for now).

- [ ] **Step 5: Commit**

```bash
git add core/llm/backends.py tests/llm/test_backends.py
git commit -m "feat: ollama->anthropic message translation + cloud exceptions"
```

---

## Task 5: Real `CloudBackend` (available + chat)

**Files:**
- Modify: `core/llm/backends.py`
- Test: `tests/llm/test_backends.py`

- [ ] **Step 1: Write the failing test**

In `tests/llm/test_backends.py`, first REPLACE the obsolete stub test `test_cloud_backend_is_unavailable_and_refuses` (it asserts `available() is False` unconditionally and `chat` raises `NotImplementedError` — both change now). Delete that function and add these:

```python
import core.llm.backends as backends
from core.llm.config import RouterConfig


class _FakeBlock:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _FakeResp:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._resp


class _FakeClient:
    def __init__(self, resp):
        self.messages = _FakeMessages(resp)


def _cfg(**kw):
    return RouterConfig(**kw)


def test_cloud_available_false_without_key(monkeypatch):
    monkeypatch.setattr(backends, "resolve_api_key", lambda: None)
    assert CloudBackend().available() is False


def test_cloud_available_true_with_key_and_package(monkeypatch):
    monkeypatch.setattr(backends, "resolve_api_key", lambda: "sk-x")
    monkeypatch.setattr(backends, "_anthropic_installed", lambda: True)
    assert CloudBackend().available() is True


def test_cloud_available_false_when_package_missing(monkeypatch):
    monkeypatch.setattr(backends, "resolve_api_key", lambda: "sk-x")
    monkeypatch.setattr(backends, "_anthropic_installed", lambda: False)
    assert CloudBackend().available() is False


def test_cloud_chat_translates_uses_cloud_model_and_returns_text(monkeypatch):
    cb = CloudBackend()
    cb._client = _FakeClient(_FakeResp([_FakeBlock("text", "hello from cloud")]))
    monkeypatch.setattr(backends, "load_config",
                        lambda: _cfg(cloud_model="claude-opus-4-8", cloud_max_tokens=999))

    out = cb.chat(
        [{"role": "system", "content": "You are Pike."},
         {"role": "user", "content": "hi"}],
        model="qwen3:8b",  # local id — must be ignored
    )
    assert out == "hello from cloud"
    call = cb._client.messages.calls[0]
    assert call["model"] == "claude-opus-4-8"   # cloud model, not qwen3:8b
    assert call["max_tokens"] == 999
    assert call["system"] == "You are Pike."
    assert call["messages"] == [{"role": "user", "content": "hi"}]


def test_cloud_chat_raises_on_refusal(monkeypatch):
    import pytest
    cb = CloudBackend()
    cb._client = _FakeClient(_FakeResp([], stop_reason="refusal"))
    monkeypatch.setattr(backends, "load_config",
                        lambda: _cfg(cloud_model="claude-opus-4-8", cloud_max_tokens=100))
    with pytest.raises(CloudRefusalError):
        cb.chat([{"role": "user", "content": "..."}])


def test_cloud_chat_raises_when_no_text_block(monkeypatch):
    import pytest
    cb = CloudBackend()
    cb._client = _FakeClient(_FakeResp([_FakeBlock("thinking", "")]))
    monkeypatch.setattr(backends, "load_config",
                        lambda: _cfg(cloud_model="claude-opus-4-8", cloud_max_tokens=100))
    with pytest.raises(CloudResponseError):
        cb.chat([{"role": "user", "content": "..."}])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/llm/test_backends.py::test_cloud_available_true_with_key_and_package -v`
Expected: FAIL — `AttributeError: module 'core.llm.backends' has no attribute '_anthropic_installed'` (or `load_config`/`resolve_api_key` not found at module scope).

- [ ] **Step 3: Implement the real `CloudBackend`**

In `core/llm/backends.py`, add these imports near the top (with the other `core.*` imports):

```python
from core.llm.config import load_config, resolve_api_key
```

Add the package-check helper (module-level, near `_split_system`):

```python
def _anthropic_installed():
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False
```

Replace the entire `CloudBackend` class with:

```python
class CloudBackend:
    """Anthropic Claude API adapter.

    Enabled only when an API key resolves AND the `anthropic` package is
    importable; otherwise available() is False and the router falls back to
    local. Ignores the passed (local) model and uses the configured cloud model.
    """
    name = "cloud"

    def __init__(self):
        self._client = None  # lazily constructed anthropic.Anthropic

    def available(self):
        if resolve_api_key() is None:
            return False
        return _anthropic_installed()

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=resolve_api_key())
        return self._client

    def chat(self, messages, *, model=None, options=None, format=None):
        cfg = load_config()
        system, convo = _split_system(messages)
        kwargs = {
            "model": cfg.cloud_model,          # configured cloud model, not `model`
            "max_tokens": cfg.cloud_max_tokens,
            "messages": convo,
        }
        if system:
            kwargs["system"] = system
        response = self._get_client().messages.create(**kwargs)
        if getattr(response, "stop_reason", None) == "refusal":
            raise CloudRefusalError("Claude declined the request")
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        raise CloudResponseError("No text block in Claude response")
```

Also update the module docstring's stale first paragraph (line ~4) to reflect that CloudBackend is now real:

```python
"""LLM backends behind the router.

LocalBackend wraps ollama.chat. CloudBackend calls the Anthropic Claude API,
enabled only when a key resolves and the anthropic package is importable.
"""
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/llm/test_backends.py -v`
Expected: PASS (all backend tests; the obsolete stub test is gone).

- [ ] **Step 5: Run the whole router suite (nothing else broke)**

Run: `python -m pytest tests/llm -v`
Expected: PASS. Note: `tests/llm/test_router.py::test_cloud_routes_to_cloud_when_available` uses a FAKE backend, so it still passes.

- [ ] **Step 6: Commit**

```bash
git add core/llm/backends.py tests/llm/test_backends.py
git commit -m "feat: real CloudBackend (anthropic call, cloud-model, refusal detection)"
```

---

## Task 6: Router runtime fallback (cloud failure/refusal → local)

**Files:**
- Modify: `core/llm/router.py`
- Test: `tests/llm/test_router.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/llm/test_router.py` (reuses the existing `_FakeBackend`, `_patch_backends`, `_cfg` helpers in that file):

```python
class _RaisingBackend:
    def __init__(self, name, exc):
        self.name = name
        self._exc = exc
        self.calls = []

    def available(self):
        return True

    def chat(self, messages, *, model=None, options=None, format=None):
        self.calls.append({"messages": messages})
        raise self._exc


def test_cloud_runtime_error_falls_back_to_local(monkeypatch, caplog):
    local = _FakeBackend("local", True)
    cloud = _RaisingBackend("cloud", RuntimeError("boom"))
    _patch_backends(monkeypatch, local, cloud)
    _cfg(monkeypatch, cloud_enabled=True)

    with caplog.at_level("WARNING"):
        out = router.chat([{"role": "user", "content": "hi"}], sensitivity="public")
    assert out == "reply-from-local"          # local answer returned
    assert len(local.calls) == 1
    assert any("cloud call failed" in r.message for r in caplog.records)


def test_cloud_refusal_falls_back_to_local(monkeypatch):
    from core.llm.backends import CloudRefusalError
    local = _FakeBackend("local", True)
    cloud = _RaisingBackend("cloud", CloudRefusalError("declined"))
    _patch_backends(monkeypatch, local, cloud)
    _cfg(monkeypatch, cloud_enabled=True)

    out = router.chat([{"role": "user", "content": "guns"}], sensitivity="public")
    assert out == "reply-from-local"
    assert len(local.calls) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/llm/test_router.py::test_cloud_runtime_error_falls_back_to_local -v`
Expected: FAIL — the current router does not catch cloud exceptions, so `RuntimeError("boom")` propagates out of `router.chat`.

- [ ] **Step 3: Implement the runtime fallback**

In `core/llm/router.py`, replace the block from `backend = _BACKENDS[decision.backend]` through the final `return` (lines ~33-43) with:

```python
    backend = _BACKENDS[decision.backend]

    if decision.backend == "cloud":
        if backend.available():
            try:
                return backend.chat(messages, model=model, options=options, format=format)
            except Exception as e:
                logger.warning(
                    "[llm-router] cloud call failed (%s: %s) — falling back to local",
                    type(e).__name__, e,
                )
                backend = _BACKENDS["local"]
        else:
            logger.info(
                "[llm-router] cloud escalation preview: sensitivity=%s task=%s "
                "policy_reason=%s — cloud unavailable, executing locally",
                sensitivity, task, decision.reason,
            )
            backend = _BACKENDS["local"]

    return backend.chat(messages, model=model, options=options, format=format)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/llm/test_router.py -v`
Expected: PASS (all router tests — the new fallback tests plus the pre-existing local/preview/cloud-available/params/bad-sensitivity tests).

- [ ] **Step 5: Run the whole router suite**

Run: `python -m pytest tests/llm -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add core/llm/router.py tests/llm/test_router.py
git commit -m "feat: router falls back to local on cloud failure or refusal"
```

---

## Task 7: Update CLAUDE.md (supersede the no-cloud rule)

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Reword the LLM environment line**

In `CLAUDE.md`, find the line under **Environment**:

```
- **LLM**: Ollama (qwen3:8b) — do NOT add OpenAI/Anthropic API calls
```

Replace with:

```
- **LLM**: Ollama (qwen3:8b) local-first. Cloud (Anthropic Claude API) is opt-in and gated — every LLM call must go through the `core/llm` router seam, and the ONLY provider call lives in `core/llm/backends.py`. Do NOT add provider API calls anywhere else.
```

- [ ] **Step 2: Reword the "What NOT To Do" line**

In `CLAUDE.md`, find under **What NOT To Do**:

```
- Do NOT add cloud API calls (OpenAI, Anthropic, etc.) — this is local-only
```

Replace with:

```
- Do NOT call cloud provider APIs directly — route through `core/llm` (the only provider call is in `core/llm/backends.py`). Local stays the default; cloud is opt-in and gated.
```

- [ ] **Step 3: Sanity check the file still reads correctly**

Run: `git diff CLAUDE.md`
Expected: exactly the two line replacements above, nothing else.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: supersede no-cloud rule — cloud is opt-in via the router seam"
```

---

## Task 8: Final verification

- [ ] **Step 1: Run the full router suite**

Run: `python -m pytest tests/llm -v`
Expected: all pass.

- [ ] **Step 2: Run the whole project suite (no regressions)**

Run: `python -m pytest -q`
Expected: all pass (should be the prior green count plus the new cloud tests).

- [ ] **Step 3: Confirm the only provider call is in backends.py**

Run: `grep -rn "anthropic" core/ server/ | grep -v "core/llm/backends.py" | grep -v "core/llm/config.py"`
Expected: no matches (the only `anthropic` usage is the SDK import in `backends.py`; `config.py` references only the env var name `ANTHROPIC_API_KEY`, which this grep will show — that's fine, it's not a provider call).

- [ ] **Step 4: Import-smoke the surface**

Run: `python -c "import core.llm, core.llm.backends, core.llm.config, core.llm.router; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Commit anything outstanding**

```bash
git add -A
git commit -m "chore: finalize cloud adapter build" || echo "nothing to commit"
```

---

## Manual live-smoke test (operator, after merge — not automated)

This is a real paid API call; run it only with a key present. It is NOT part of the pytest suite (mirrors how the local seam was live-verified).

1. Set a key: `set ANTHROPIC_API_KEY=sk-...` (PowerShell: `$env:ANTHROPIC_API_KEY="sk-..."`) OR write it to `data/anthropic_key`.
2. Temporarily enable cloud: create `data/llm_router.json` containing `{"cloud_enabled": true}`.
3. Run:
   ```
   python -c "from core.llm import chat; print(chat([{'role':'user','content':'Reply with exactly: CLOUD OK'}], sensitivity='public', task='chat'))"
   ```
   Expected: `CLOUD OK` (or a close paraphrase) returned from Opus 4.8. Check the logs show it routed to cloud (no "executing locally" line).
4. **Delete `data/llm_router.json` afterward** so cloud returns to OFF.

## Notes for the implementer

- **Line numbers drift.** Re-read each target region before editing; match indentation exactly.
- **Do NOT mock Ollama** (CLAUDE.md). These tests mock the *Anthropic* client (a fake), which is fine — we're testing translation/plumbing, not model output. Local paths remain unmocked.
- **Circular imports:** `backends.py` importing `from core.llm.config import load_config, resolve_api_key` is safe — `config.py` imports only from `core.config`, never from `core.llm.*`.
- **`resolve_api_key` / `load_config` / `_anthropic_installed` are referenced as module attributes** in tests (`backends.resolve_api_key`, `backends.load_config`, `backends._anthropic_installed`) so monkeypatch works — keep them importable at `backends` module scope.
- The key file `data/anthropic_key` is never committed (data/ gitignored). Never write a real key into any test or the repo.
