# Cloud Brain Settings UI — Design

**Date:** 2026-07-02
**Status:** Approved, ready for planning
**Builds on:** the hybrid LLM router (PR #3 local-only + PR #4 cloud adapter, both merged to `main`).
**Scope:** Piece #1 of 3 remaining hybrid-brain steps — a Settings-panel **Cloud Brain** section: an on/off cloud toggle, a masked API-key field with save/remove, a "Test" button that validates the key with one real call, and a read-only model display. Writes the exact files the router already reads.

## Background

The router works and cloud is live-verified, but cloud can currently only be toggled by hand-editing `data/llm_router.json` and the key placed via `data/anthropic_key` by hand. This makes cloud usable from the UI without a terminal — the natural home for a non-technical daily driver.

The router reads two files, unchanged by this build:
- `data/llm_router.json` → `load_config()` overlays `cloud_enabled` (bool), plus `cloud_opt_in_features`, `cloud_model`, `cloud_max_tokens`.
- `data/anthropic_key` → `resolve_api_key()` returns `ANTHROPIC_API_KEY` env var first, else this file's stripped contents.

## Non-goals (deferred)

- **Piece #2** — the live, per-turn consent/announcement flow. This build ships only a **static caption** explaining what turning cloud on does; the interactive "this turn would go to cloud, here's what it'd send" prompt is #2.
- **Piece #3** — task-tier escalation.
- Editable model / max_tokens in the UI (model shown read-only). Per-feature `cloud_opt_in_features` editing (private opt-in) stays file-only for now.

## Architecture

No new files. Two layers extend existing patterns:

- **Frontend** (`ui/templates/index.html`): a new **Cloud Brain** section rendered inside `renderSettings()`, using the existing `settingToggle`-style row for the toggle and the vault-account `type="password"` row pattern (index.html ~11912–11928) for the masked key field. Status/result text uses the existing `.setting-saved` / `#...Msg` span pattern. All requests go through `authFetch` (injects `Authorization: Bearer` + `X-Vault-Token`).
- **Backend** (`server/app.py`): four endpoints following the existing `POST /api/settings` handler style (Pydantic request model, `Depends(require_user)`, read-modify-write a JSON file under a `PROJECT_ROOT`-relative path). Endpoints delegate file work to small, unit-testable helpers.

### Testable helper seam

To keep the endpoints thin and the logic unit-testable without a running server, put the file logic in helpers. Location: a new module `core/llm/cloud_settings.py` (co-located with the router config it manipulates), so `config.py` stays focused on reads and this module owns the writes.

```python
# core/llm/cloud_settings.py
def get_cloud_status() -> dict:
    """Return {'cloud_enabled': bool, 'key_set': bool, 'cloud_model': str}. Never the key value."""

def set_cloud_enabled(enabled: bool) -> None:
    """Read-modify-write data/llm_router.json, updating only cloud_enabled; preserve all other keys."""

def set_api_key(key: str) -> None:
    """Write data/anthropic_key with the stripped key; an empty/blank key DELETES the file."""

def test_cloud_key() -> dict:
    """Make one tiny real Claude call via CloudBackend. Return {'ok': True} or {'ok': False, 'error': <friendly str>}."""
```

- `set_cloud_enabled` merges: load existing `data/llm_router.json` (or `{}`), set `cloud_enabled`, write back with `indent=2`. Never clobbers `cloud_opt_in_features` / `cloud_model` / `cloud_max_tokens`.
- `get_cloud_status` reads `load_config()` for `cloud_enabled`/`cloud_model` and `resolve_api_key() is not None` for `key_set`.
- `set_api_key` writes to `_KEY_FILE` (`data/anthropic_key`); blank ⇒ `unlink(missing_ok=True)`. `data/` is gitignored.
- `test_cloud_key` constructs a `CloudBackend`, calls `.chat([{"role":"user","content":"ping"}])` (bounded by `cloud_max_tokens`), returns `{"ok": True}` on a string, else `{"ok": False, "error": <mapped message>}`. It resolves the key fresh (new `CloudBackend()` instance) so a just-saved key is used. If no key resolves, returns `{"ok": False, "error": "No API key set"}` without a network call.

### Endpoints (`server/app.py`)

All `async def ...(..., user_id: str = Depends(require_user))`; new Pydantic models alongside the existing ones.

| Method / path | Body | Returns | Action |
|---|---|---|---|
| `GET /api/cloud` | — | `{cloud_enabled, key_set, cloud_model}` | `get_cloud_status()` — never the key value |
| `POST /api/cloud/enabled` | `{enabled: bool}` | `{success, cloud_enabled}` | `set_cloud_enabled(enabled)` |
| `POST /api/cloud/key` | `{key: str}` | `{success, key_set}` | `set_api_key(key)` (blank ⇒ removed) |
| `POST /api/cloud/test` | — | `{ok}` or `{ok: false, error}` | `test_cloud_key()` |

### Frontend — the Cloud Brain section

Rendered in `renderSettings()` after the existing sections. On panel open, a `loadCloudSettings()` (called from the existing `loadSettings()` flow, or on section render) does `authFetch(API + '/cloud')` and fills the controls. Contents, top to bottom:

1. **Cloud escalation** toggle (`.proto-toggle`). `onchange` → `POST /api/cloud/enabled {enabled: this.checked}`, shows the `SAVED` pip on success.
2. **Caption** (`.setting-desc`): *"When on, general chat with Pike uses Anthropic's Opus 4.8 for stronger reasoning. Your private data — email, journals, memory — always stays local."*
3. **API key** — `type="password"` input + **Save** button → `POST /api/cloud/key {key}`. Status span shows **"✓ key set"** or **"no key"** (from `key_set`, never the value). Field is cleared after a successful save (the value is never echoed back).
4. **Test** button → `POST /api/cloud/test`; shows **"✓ works"** or **"✗ rejected: <error>"**.
5. **Remove key** link → `POST /api/cloud/key {key: ""}`, updates status to "no key".
6. **Model** row (read-only): shows `cloud_model` (`claude-opus-4-8`) as static text for transparency.

## Key security

- The key value is **write-only end to end**: no GET returns it, it is never rendered, never logged. `get_cloud_status` exposes only `key_set: bool`.
- Masked (`type="password"`) input; cleared after save.
- Travels over localhost only — same trust model as the existing vault-PIN field.
- Reuses `resolve_api_key()` and the existing `CloudBackend`; no new secret handling.

## Error handling

- `test_cloud_key` maps common failures to friendly strings: authentication error → "Key rejected"; connection error → "Couldn't reach Anthropic (network)"; any other → the exception message. Never leaks the key in the error.
- `set_cloud_enabled` / `set_api_key`: a corrupt existing `data/llm_router.json` is treated as `{}` (start fresh) rather than crashing; file-write errors surface as a 500 with a generic message (no key content).
- Endpoints validate types via Pydantic; `enabled` must be bool, `key` a string.

## Testing

- **Unit (`tests/llm/test_cloud_settings.py`)** — monkeypatch `_OVERRIDE_PATH` / `_KEY_FILE` to `tmp_path`:
  - `set_cloud_enabled(True)` then `False` writes valid JSON and **preserves** a pre-existing `cloud_opt_in_features` / `cloud_model` in the file (merge, not clobber).
  - `set_api_key("sk-x")` writes the file; `get_cloud_status()` reports `key_set: True` **and never returns the key string**; `set_api_key("")` deletes the file → `key_set: False`.
  - `get_cloud_status()` shape + values from a known config.
  - `test_cloud_key()`: with `CloudBackend` monkeypatched to return a string → `{ok: True}`; monkeypatched to raise an auth-type error → `{ok: False, error: "Key rejected"}`; with no key → `{ok: False, error: "No API key set"}` and no backend call.
- **Endpoint smoke (if a FastAPI `TestClient` harness is reasonable):** each route returns the right shape with a stubbed `require_user`; otherwise rely on the helper unit tests + manual verification.
- **Manual (operator):** launch the app → Settings → Cloud Brain: toggle on (SAVED pip; confirm `data/llm_router.json` shows `cloud_enabled: true`), paste key + Save ("✓ key set"), click Test ("✓ works"), Remove key ("no key"), toggle back off. Confirm the key value never appears in the page, network responses, or logs.

## Open items for later builds (not this spec)

- Piece #2: live per-turn consent/announcement.
- Piece #3: task-tier escalation.
- UI editing of `cloud_model` / `cloud_max_tokens` / per-feature `cloud_opt_in_features`.
