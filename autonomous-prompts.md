# Autonomous Run Prompts for Aegis AI

Copy-paste these into Claude Code with `--dangerously-skip-permissions`.
Run ONE prompt per session. Each is scoped to a single unit of work.

---

## Phase 4A: Wire Task NLP into Operations Protocol

```
You are working on the Aegis AI project at C:\Users\dusti\Projects\aegis-ai.
Read CLAUDE.md first for project rules.

TASK: Wire the natural language task detection into the Operations protocol.

The file core/protocols/operations.py already has TASK_PATTERNS regex list and full
task CRUD (add_task, complete_task, remove_task, get_pending_tasks). But process_input()
is a no-op — it doesn't use the patterns.

Do this:
1. In process_input(), match user_input against TASK_PATTERNS. If a match is found,
   call self.add_task() with the captured text and set intercept=True with a
   confirmation response (e.g. "Got it. Added task: <text>").
2. Also in process_input(), if there are pending tasks, inject a brief summary into
   context_injection so the LLM is aware of the user's task list. Keep it short —
   just count and any overdue items.
3. Add a "morning briefing" trigger: if it's the first message of a session and there
   are pending tasks, inject the daily briefing into context_injection.
4. Write tests in tests/test_operations.py:
   - Test each TASK_PATTERN matches expected inputs
   - Test process_input intercepts when a task pattern matches
   - Test process_input injects context when tasks are pending
   - Test process_input passes through normal messages unchanged
5. Commit each logical piece separately on the current branch.

Do NOT modify any other protocol files. Do NOT modify agent.py.
Stay on the current git branch. Do not push.
```

---

## Phase 4B: Calendar Integration (Local ICS)

```
You are working on the Aegis AI project at C:\Users\dusti\Projects\aegis-ai.
Read CLAUDE.md first for project rules.

TASK: Add local ICS calendar support to the Operations protocol.

Do this:
1. Add `icalendar` to requirements.txt (it's a pure Python library, no binary deps).
2. In core/protocols/operations.py, add calendar methods:
   - _load_calendar(self) — load from data/calendar.ics (create if missing)
   - _save_calendar(self)
   - add_event(self, summary, start, end=None, description="") — add VEVENT
   - get_today_events(self) — events for today
   - get_upcoming_events(self, days=7) — next N days
   - format_schedule(self, events) — readable text output
3. Add calendar slash commands:
   - /calendar — show today's events
   - /calendar add <summary> <date> [time] — add event
   - /calendar week — show next 7 days
4. Update get_daily_briefing() to include today's calendar events.
5. Add calendar patterns to process_input() for natural language:
   - "I have a meeting on Thursday at 2pm"
   - "Schedule X for next Monday"
   - "What's on my calendar today"
6. Write tests in tests/test_calendar.py:
   - Test add/retrieve events roundtrip
   - Test format_schedule output
   - Test natural language pattern matching
7. Commit after each logical piece.

Do NOT add Google Calendar or Outlook integration — just local ICS for now.
Do NOT modify agent.py. Stay on current branch. Do not push.
```

---

## Phase 4C: Operations Context Integration

```
You are working on the Aegis AI project at C:\Users\dusti\Projects\aegis-ai.
Read CLAUDE.md first for project rules.

TASK: Make the Operations protocol context-aware in conversations.

Right now, even though operations has tasks and (after 4B) calendar, the LLM doesn't
know about them during normal conversation. Fix that.

Do this:
1. In operations.py process_input(), build a context injection that includes:
   - Count of pending tasks (if any)
   - Any overdue tasks (with text)
   - Today's calendar events (if calendar integration exists)
   - Any high-priority items
   Keep the injection concise — 3-5 lines max, not a wall of text.
2. Only inject if there's something relevant. Don't inject empty context.
3. Add a "what should I focus on today" intent detector in process_input():
   - Patterns like "what's today look like", "what should I do", "daily briefing"
   - Set intercept=True and return the full daily briefing as response
4. Write tests in tests/test_operations_context.py:
   - Test context injection with pending tasks
   - Test context injection with no tasks (should be empty)
   - Test briefing intent detection
5. Commit after each logical piece.

Do NOT modify agent.py. Stay on current branch. Do not push.
```

---

## Phase 5A: VRAM Arbitration in Command Protocol

```
You are working on the Aegis AI project at C:\Users\dusti\Projects\aegis-ai.
Read CLAUDE.md first for project rules.

TASK: Add VRAM arbitration to the Command protocol.

The system has 8GB VRAM (RTX 2070). Multiple models compete for it:
- Ollama (chat model) — always loaded
- Coqui XTTS-v2 (TTS) — loaded when voice is active
- Faster-Whisper (STT) — loaded when voice is active
- Stable Diffusion (if creative protocol needs it) — huge VRAM consumer

Do this:
1. In core/protocols/command.py, add a VRAMManager class (or methods on CommandProtocol):
   - get_vram_status() — query nvidia-smi for current usage (already have get_gpu_info)
   - register_model(name, estimated_vram_mb, priority) — track known models
   - can_load(name) — check if there's room
   - request_vram(name, required_mb) — returns True if available, suggests evictions if not
   - suggest_evictions(required_mb) — which lower-priority models to unload
2. Define default model registry:
   - "ollama" — ~4000MB, priority=CRITICAL (never evict)
   - "xtts" — ~2000MB, priority=HIGH
   - "whisper" — ~500MB, priority=HIGH
   - "stable_diffusion" — ~6000MB, priority=NORMAL
3. Add /vram command showing current model allocations vs available VRAM.
4. Write tests in tests/test_vram.py:
   - Test model registration
   - Test can_load with various scenarios
   - Test eviction suggestions (should never suggest evicting critical models)
5. Commit after each logical piece.

This is advisory only — it doesn't actually load/unload models yet. That comes later
when the protocols are wired to their model managers.

Do NOT modify other protocol files. Stay on current branch. Do not push.
```

---

## Phase 5B: ComfyUI API Integration in Creative Protocol

```
You are working on the Aegis AI project at C:\Users\dusti\Projects\aegis-ai.
Read CLAUDE.md first for project rules.

TASK: Add ComfyUI API integration to the Creative protocol.

ComfyUI runs a local API server (default http://127.0.0.1:8188). The Creative protocol
already detects if ComfyUI is installed. Now add the ability to actually use it.

Do this:
1. In core/protocols/creative.py, add ComfyUI adapter methods:
   - _comfyui_available(self) — check if the API server is running (GET /system_stats)
   - _comfyui_generate(self, prompt_workflow, output_dir=None) — POST /prompt, poll for completion
   - _comfyui_get_models(self) — GET /object_info to list available checkpoints
   - _comfyui_default_workflow(self, positive_prompt, negative_prompt="", steps=20, cfg=7.0)
     — build a basic txt2img workflow JSON
2. Add slash commands:
   - /generate <prompt> — generate an image with default settings
   - /models — list available SD models in ComfyUI
3. Add `requests` to requirements.txt if not already present.
4. Handle the case where ComfyUI is installed but not running — return a helpful
   message telling the user to start it.
5. Write tests in tests/test_creative_comfyui.py:
   - Test workflow JSON generation (no API call needed)
   - Test _comfyui_available returns False when server is down
   - Mock test for generate flow
6. Commit after each logical piece.

Do NOT launch ComfyUI or download models. Do NOT modify other protocols.
Stay on current branch. Do not push.
```

---

## Phase 5C: Process Queue in Command Protocol

```
You are working on the Aegis AI project at C:\Users\dusti\Projects\aegis-ai.
Read CLAUDE.md first for project rules.

TASK: Add a priority task queue to the Command protocol.

The Command protocol can launch processes, but it doesn't queue them. On 8GB VRAM,
only one heavy task should run at a time.

Do this:
1. In core/protocols/command.py, add a task queue:
   - queue_task(name, command, priority="normal", requires_gpu=False)
   - _process_queue() — background thread that runs queued tasks one at a time
   - get_queue_status() — what's running, what's waiting
   - cancel_queued(name) — remove from queue before it runs
2. GPU-requiring tasks should check VRAM availability before starting (use the
   VRAM methods from Phase 5A if they exist, otherwise just check get_gpu_info).
3. Priority order: high > normal > low. Within same priority: FIFO.
4. Add /queue command showing queue status.
5. Write tests in tests/test_command_queue.py:
   - Test queue ordering by priority
   - Test cancellation
   - Test queue status formatting
6. Commit after each logical piece.

Do NOT modify other protocol files. Stay on current branch. Do not push.
```

---

## Testing Foundation

```
You are working on the Aegis AI project at C:\Users\dusti\Projects\aegis-ai.
Read CLAUDE.md first for project rules.

TASK: Set up the test infrastructure and write baseline tests for all existing modules.

There are currently no tests. Fix that.

Do this:
1. Add pytest to requirements.txt.
2. Create tests/ directory at project root.
3. Create tests/conftest.py with common fixtures:
   - tmp_data_dir — temporary directory for test data files
   - mock_config — a test config dict that doesn't touch real data/
   - sample_task — a sample task dict
4. Write tests for the Protocol base class (tests/test_protocol_base.py):
   - Test that abstract methods are enforced
   - Test enable/disable
   - Test priority ordering
5. Write tests for the Protocol registry (tests/test_registry.py):
   - Test registration and priority ordering
   - Test process_input pipeline (multiple protocols)
   - Test process_output pipeline
   - Test command routing
6. Write tests for Operations protocol (tests/test_operations.py):
   - Test task CRUD (add, complete, remove, get_pending)
   - Test overdue detection
   - Test daily briefing format
   - Test command handlers (/task add, /task done, /task list)
7. Write tests for Command protocol (tests/test_command.py):
   - Test get_gpu_info handles missing nvidia-smi gracefully
   - Test process management data structures
8. Write tests for Creative protocol (tests/test_creative.py):
   - Test tool detection
   - Test output listing
9. Run pytest and fix any failures.
10. Commit the full test suite.

Do NOT test LLM-dependent code (agent.py conversation loop, memory summarization).
Do NOT test voice (requires audio hardware). Stay on current branch. Do not push.
```

---

## Initial Git Commit

```
You are working on the Aegis AI project at C:\Users\dusti\Projects\aegis-ai.

TASK: Create the initial git commit for the project.

Do this:
1. Run git status to see what's there.
2. Make sure .gitignore is properly excluding data/ user content, voice reference wavs,
   venv, __pycache__, .env files.
3. Stage everything that should be tracked:
   - All core/ Python files
   - All packs/ files EXCEPT voice reference.wav files (they're in .gitignore)
   - CLAUDE.md, VISION.md, requirements.txt, .gitignore
   - server/ and ui/ stubs
4. Do NOT stage: data/ contents, .env, venv/, __pycache__/
5. Create the initial commit with message: "initial commit: aegis-ai phases 1-3"
6. Run git log to confirm.

Do not push. Do not create branches yet.
```

---

## Usage

Run any of these with:

```powershell
cd C:\Users\dusti\Projects\aegis-ai
git checkout -b <branch-name>
claude --dangerously-skip-permissions -p "<paste prompt here>"
```

Or use the launch script (run-autonomous.ps1) for logging.
