# Aegis AI — Claude Code Project Guide

## What This Is

Aegis AI is a local-first AI companion application. It runs entirely on the user's
machine (no cloud dependencies). The core is a personality-agnostic agent framework;
character, voice, and visual theming are loaded from pluggable "packs."

## Environment

- **Python**: 3.12.10 (do NOT use features beyond 3.12)
- **GPU**: NVIDIA RTX 2070 (8GB VRAM), CUDA 12.6
- **LLM**: Ollama (qwen3:8b) local-first. Cloud (Anthropic Claude API) is opt-in and gated — every LLM call must go through the `core/llm` router seam, and the ONLY provider call lives in `core/llm/backends.py`. Do NOT add provider API calls anywhere else.
- **TTS**: Coqui XTTS-v2 via `coqui-tts` (IDIAP fork, NOT the `TTS` package)
- **STT**: Faster-Whisper
- **Vector DB**: ChromaDB
- **OS**: Windows 11

### Dependency Notes

- PyTorch must be installed with `--index-url https://download.pytorch.org/whl/cu126`
- `coqui-tts` requires the `[codec]` extra on PyTorch 2.9+
- `torchaudio` must come from the same CUDA index as PyTorch

## Project Structure

```
aegis-ai/
  core/                    # Core engine — NO intellectual property here
    agent.py               # Main conversation loop
    config/                # Configuration loading
      core_config.json     # Master config (models, paths, features)
      loader.py            # Config loader, path resolution
    personality/           # Core identity system
      core_directives.txt  # 8 non-negotiable personality traits
      pack_loader.py       # Loads personality/voice/theme packs
    memory/                # Memory subsystems
      manager.py           # Orchestrator
      character_memory.py  # Pack-provided character memories
      fact_extractor.py    # Extract facts from conversations
      journal.py           # Session summaries
      knowledge.py         # ChromaDB vector storage
      profile.py           # User profile
      transcript.py        # Conversation logging
    protocols/             # Modular capability subsystems
      base.py              # Protocol ABC (the pattern to follow)
      registry.py          # Protocol routing by priority
      communications.py    # Core conversation (PRIORITY_NORMAL)
      security.py          # Privacy enforcement (PRIORITY_CRITICAL)
      wellness.py          # Health monitoring (PRIORITY_HIGH)
      operations.py        # Tasks, calendar, email (PRIORITY_NORMAL - 5)
      command.py           # Process orchestration (PRIORITY_NORMAL - 10)
      creative.py          # Creative tools (PRIORITY_LOW)
    security/
      privacy.py           # Access control, consent, logging
    voice/
      tts_engine.py        # Coqui XTTS-v2
      stt_engine.py        # Faster-Whisper
      input_router.py      # Text/voice input selection
      emotion.py           # Emotion detection
  packs/                   # Pluggable content packs
    personalities/         # Character packs
      default/             # Ships with product — generic "Commander"
      pike/                # Star Trek Pike — ALL Trek IP lives here
    voices/
      default/
      pike/
    themes/
      default/
  data/                    # User data (gitignored, never committed)
  server/                  # FastAPI web server
    app.py                 # API endpoints, protocol pipeline
  ui/                      # Web frontend (PWA)
    templates/index.html   # Mobile-first chat UI
    static/                # Icons, assets
  tools/                   # Pack SDK and dev tools
    pack_cli.py            # Pack CLI (init, validate, list, info)
    pack_validator.py      # Pack structure/schema validation
  start.py                 # Unified launcher (server or console)
  start.bat                # Windows launcher
```

## Architecture Rules

### The Protocol Pattern

Every capability module follows the Protocol ABC in `core/protocols/base.py`:

```python
class MyProtocol(Protocol):
    def __init__(self):
        super().__init__(
            name="my_protocol",
            description="What it does",
            priority=Protocol.PRIORITY_NORMAL,
        )

    def process_input(self, user_input, context):
        # Runs BEFORE the LLM call. Can modify input, inject context, or intercept.
        return {
            "input": user_input,        # Modified input (or original)
            "context_injection": "",     # Added to system prompt for this turn
            "intercept": False,          # If True, skip LLM — return response directly
            "response": "",              # Direct response (only if intercept=True)
        }

    def process_output(self, response, context):
        # Runs AFTER the LLM call. Can modify, suppress, or append to response.
        return {
            "response": response,   # Modified response (or original)
            "suppress": False,      # If True, don't show this response
            "append": "",           # Text appended after response
        }

    def get_commands(self):
        # Slash commands this protocol handles
        return [{"command": "foo", "description": "...", "handler": "cmd_foo"}]

    def get_status(self):
        status = super().get_status()
        status["custom_field"] = "value"
        return status
```

**Priority levels** (higher = processes first):
- `PRIORITY_CRITICAL = 100` — Security only
- `PRIORITY_HIGH = 80` — Wellness
- `PRIORITY_NORMAL = 50` — Communications
- `PRIORITY_LOW = 20` — Background tasks

When adding a new protocol:
1. Create the file in `core/protocols/`
2. Inherit from `Protocol`
3. Register it in `core/agent.py` where other protocols are registered
4. Follow the existing pattern exactly — same method signatures, same return dicts

### The Pack System

Packs live in `packs/{personalities,voices,themes}/<pack_name>/`. Each has:
- `manifest.json` — metadata (name, author, version, description)
- Content files specific to the pack type
- Optional `memories/` directory for character memories

**Critical rule**: ALL intellectual property (Star Trek, any licensed content) lives
ONLY in pack directories. The `core/` directory must be completely IP-free.

### Memory Layering (priority order)

1. **Human companion data** — always wins
2. **Conversation context** — current session
3. **Character memories** — from active personality pack
4. **Core directives** — base personality

Never invert this hierarchy.

### Config

`core/config/core_config.json` is the single source of truth for configuration.
Use `core.config.loader` to access it:

```python
from core.config import CONFIG, PROJECT_ROOT, get_path
```

All paths in config are relative strings — `loader.py` resolves them to absolute
paths at import time. Never hardcode absolute paths.

## Coding Standards

### Style

- Python 3.12, no type stubs or `.pyi` files needed
- Use `pathlib.Path` for all file paths (not `os.path`)
- Use `json` for data files (not YAML, not TOML)
- Imports: stdlib first, then third-party, then `core.*`
- No star imports (`from x import *`)
- Docstrings on classes and public methods (one-line summary is fine)
- f-strings over `.format()` or `%`

### Naming

- Files: `snake_case.py`
- Classes: `PascalCase`
- Functions/methods: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private methods: `_leading_underscore`
- Config keys: `snake_case` in JSON

### Error Handling

- Use try/except around external I/O (file, network, subprocess)
- Never silently swallow exceptions — at minimum log them
- Graceful degradation: if a feature fails, the agent keeps running
- Never `sys.exit()` from inside a protocol or subsystem

### What NOT To Do

- Do NOT call cloud provider APIs directly — route through `core/llm` (the only provider call is in `core/llm/backends.py`). Local stays the default; cloud is opt-in and gated.
- Do NOT modify `core/personality/core_directives.txt` — those are immutable
- Do NOT put character/IP content in `core/` — it goes in `packs/`
- Do NOT store user data in version-controlled directories
- Do NOT add new dependencies without noting them in `requirements.txt`
- Do NOT use `subprocess.run(..., shell=True)` except where absolutely necessary
- Do NOT modify the Security protocol's override authority
- Do NOT remove or weaken privacy protections

## Git Workflow

### Branch Naming

```
phase-4/operations-protocol
phase-5/command-creative
phase-6/tauri-ui
phase-7/distribution
fix/bug-description
feature/feature-name
```

### Commit Messages

```
phase 4: wire task NLP into operations protocol process_input
phase 5: add VRAM arbitration to command protocol
fix: handle empty task list in daily briefing
```

Keep commits focused — one logical change per commit. Commit after each
meaningful unit of work so progress isn't lost.

### Autonomous Session Rules

When running unattended (`--dangerously-skip-permissions`):

1. **Stay on your feature branch** — never checkout or push to main
2. **Commit frequently** — small, working increments
3. **Don't delete files** unless you created them in this session
4. **Don't modify unrelated code** — stay scoped to the task
5. **If something breaks, commit what works and stop** — don't spiral
6. **Don't run the full application** — focus on writing and testing code
7. **Don't install system-level packages** — only pip install if needed

## Testing

Tests don't exist yet. When adding them:

- Use `pytest` (add to requirements.txt if not present)
- Test directory: `tests/` at project root, mirroring `core/` structure
- Test files: `test_<module>.py`
- Each protocol should have basic tests:
  - `process_input` returns correct dict shape
  - `process_output` returns correct dict shape
  - Commands return strings
  - Edge cases (empty input, None values)
- Memory subsystems: test load/save roundtrip
- Don't mock Ollama — skip LLM-dependent tests with `@pytest.mark.skip`

## Current State (All Phases Complete)

What's built and working:
- Core agent loop with pack-driven personality
- Pack loader (personality, voice, theme)
- Full memory subsystem (transcript, journal, facts, knowledge, profile, character)
- Protocol framework (base, registry, priority routing)
- Communications protocol (conversation core)
- Security protocol (data classification, privacy enforcement)
- Wellness protocol (health monitoring, firmness escalation)
- Operations protocol (task CRUD, daily briefing, slash commands)
- Command protocol (process management, GPU monitoring)
- Creative protocol (ffmpeg integration, tool detection, asset management)
- Voice I/O (TTS via Coqui XTTS-v2, STT via Faster-Whisper)
- Emotion detection
- Two personality packs (default "Commander" + Pike)
- FastAPI server with full API (`server/app.py`)
- Mobile-first PWA web UI (`ui/templates/index.html`)
- Pack SDK CLI (`tools/pack_cli.py` — init, validate, list, info)
- Pack validator (`tools/pack_validator.py`)
- Launcher scripts (`start.py`, `start.bat`)

## Future Enhancements

### Operations Protocol
- Natural language task detection (regex patterns exist but not wired to process_input)
- Calendar integration (Google Calendar or local ICS)
- Email integration (IMAP/SMTP)
- Pending task injection into conversation context
- Recurring tasks and habits

### Command & Creative Protocols
- VRAM arbitration in Command (smart model loading/unloading for 8GB)
- Task queue with priority scheduling
- ComfyUI API integration in Creative
- Prompt assistance for image generation

### UI
- Theme pack CSS integration (load colors from active theme)
- System tray integration (Tauri or similar)
- Voice input button in web UI

### Community
- Windows installer (PyInstaller or similar)
- Community pack repository
- Documentation (user guide, pack creator guide)
