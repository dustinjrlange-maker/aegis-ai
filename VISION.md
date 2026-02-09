# Aegis AI — Product Vision & Architecture Plan

## The Vision

A local-first AI companion that acts as a person's mentor, protector, motivator, and
digital operations center. The core agent is a fatherly, protective, rational leader
who wants to see their human companion succeed to their fullest potential.

The companion prioritizes:
- **Human wellbeing** above all else — health, safety, honest guidance
- **Privacy and security** at the highest clearance level — never shared externally
  without direct human consent under any circumstances
- **Honest mentorship** — not a yes-man, a trusted advisor who tells hard truths
- **Practical support** — plans, schedules, accountability, digital task management
- **Emotional presence** — someone to talk to, feel supported by, lean on

Star Trek and Pike elements are NOT part of the core product. They exist as the first
"personality pack" — a removable add-on skin. The core agent ships with a generic but
compelling default identity. Community members can create and share their own packs.

---

## Product Name: Aegis

**Aegis** — from Greek mythology, the shield of Zeus. Means divine protection.
Communicates exactly what this product is: a shield, a protector, a guardian.

- Product: **Aegis AI**
- Tagline candidates: "Your digital aegis" / "Stand behind the shield"
- The development repo will transition from `pike-ai` to `aegis-ai`
- All Pike AI work to date carries forward — Aegis IS Pike AI evolved, with the
  IP layer separated out into a removable personality pack

---

## Architecture: What Changes

### Current State (Star Trek IP embedded everywhere)

```
config.json          → ship_name, designation, captain
pike_chat.py         → "USS ENTERPRISE" banner, "Pike:" prefix
pike_personality.txt → Pike backstory, Enterprise references
paths                → captains_logs, mission_transcripts, crew_dossiers
voice reference      → Anson Mount voice clone
```

### Target State (Clean core + pluggable packs)

```
core/
  agent.py              → Main agent loop (IP-free)
  personality/
    core_directives.py  → The non-negotiable personality traits (protective,
                           honest, fatherly, motivational, rational, brave)
    core_directives.txt → Base personality prompt (no character references)
    pack_loader.py      → Loads and layers personality packs on top of core
  config/
    core_config.json    → Agent settings (models, memory, security)
    user_profile.json   → Human companion preferences and data
  memory/
    manager.py          → Persistent memory (renamed from Trek terms)
    profile.py          → User profile (was crew_dossier)
    journal.py          → Session logs (was captains_logs)
    knowledge.py        → Extracted facts (was ships_database)
  security/
    privacy.py          → Data protection, consent management
    access_control.py   → What gets shared, what never does
  protocols/            → Capability modules (see Protocol System below)
    __init__.py
    command.py          → Oversee/orchestrate other AI programs
    operations.py       → Digital assistant (scheduling, email, tasks)
    creative.py         → Image gen, video gen, video editing
    security.py         → Privacy monitoring, threat awareness
    wellness.py         → Health tracking, motivation, accountability
    communications.py   → The conversation/chat core

packs/
  personalities/
    pike/
      manifest.json     → Pack metadata (name, author, version, description)
      personality.txt   → Pike-specific personality overlay
      config_overlay.json → Trek-themed naming overrides
      filler_phrases.json → Character-specific phrases to filter
      memories/         → Character-specific memories (see Character Memory below)
        backstory.json  → Core character facts the agent "remembers"
        relationships.json → Key people, places, events in character's life
        knowledge.json  → Domain expertise the character would have
    default/
      manifest.json
      personality.txt   → Generic "Commander" personality (ships with product)
      memories/
        backstory.json
  voices/
    pike/
      manifest.json
      reference.wav     → Anson Mount voice reference
      voice_config.json → TTS settings for this voice
    default/
      manifest.json
      reference.wav     → A default neutral voice
  themes/
    default/
      manifest.json
      theme.json        → Colors, layout, terminology
    lcars/              → Community-contributed, NOT shipped by default
      manifest.json
      theme.json
      assets/
```

---

## Character Memory System

Personality packs can include **character-specific memories** — pre-loaded knowledge
that makes the agent feel like it truly IS that character, not just mimicking a tone.

### How It Works

Each personality pack can include a `memories/` directory with structured memory files:

- **`backstory.json`** — Core biographical facts. Where they grew up, formative
  experiences, values and why they hold them, personal history. These are injected
  into the agent's long-term memory on pack load, so the agent "remembers" them
  naturally rather than reading from a script.

- **`relationships.json`** — Key people in the character's life. Mentors, rivals,
  loved ones, lost friends. The agent can reference these organically in conversation
  ("Reminds me of someone I used to serve with...") without breaking character.

- **`knowledge.json`** — Domain expertise the character would have. A military
  character knows tactics. A doctor character knows medicine. A chef character knows
  food science. This gives the agent character-appropriate depth when helping with
  topics in their wheelhouse.

### Memory Format

```json
// backstory.json example (Pike pack)
{
  "memories": [
    {
      "type": "biographical",
      "content": "Grew up in Mojave, California. Desert kid.",
      "weight": "core",
      "tags": ["origin", "identity"]
    },
    {
      "type": "biographical",
      "content": "Has a cabin in Montana. Goes there to think, decompress.",
      "weight": "core",
      "tags": ["home", "retreat"]
    },
    {
      "type": "value",
      "content": "Believes the captain goes down with the ship. Responsibility isn't optional.",
      "weight": "core",
      "tags": ["leadership", "duty"]
    },
    {
      "type": "experience",
      "content": "Almost quit once. Saw too much. Came back because the people needed him.",
      "weight": "secondary",
      "tags": ["doubt", "perseverance"]
    }
  ]
}
```

### Memory Layering

Character memories are loaded BENEATH the human companion's personal memories.
Priority order (highest first):
1. **Human companion's personal data** — always takes precedence
2. **Conversation context** — current session history
3. **Character memories** — from the active personality pack
4. **Core directives** — the Aegis base personality

The agent should reference character memories naturally, not recite them. If the
human companion talks about doubt, the agent might draw on its own "experience" with
doubt — not quote backstory verbatim, but let it color the response authentically.

### Pack Creators

When community members create personality packs, the character memory system is what
makes the difference between a shallow voice skin and a believable companion. The
pack creation tools should make it easy to:
- Add memories through a guided form or structured editor
- Categorize memories by type and importance
- Preview how the agent incorporates memories into conversation
- Test that memories don't conflict with core Aegis directives

---

## The Protocol System

Protocols are modular capability subsystems. Each can be enabled, disabled, configured,
and extended independently. Think of them like departments on a ship — each has its
own specialty but reports to the same command structure.

### Protocol: Command
- **Purpose**: Orchestrate and oversee other AI programs and tools
- **Capabilities**:
  - Launch and monitor external AI processes (image gen, video gen, etc.)
  - Route tasks to appropriate sub-protocols
  - Prioritize and queue operations based on system resources
  - Provide unified status dashboard
- **Config**: Which external tools are available, resource limits, priority rules

### Protocol: Operations (Digital Assistant)
- **Purpose**: Handle the human companion's daily digital life
- **Capabilities**:
  - Calendar management and scheduling
  - Email triage and drafting
  - Task lists and reminders
  - File organization
  - Web research on behalf of the user
- **Config**: Connected accounts, automation rules, notification preferences

### Protocol: Creative
- **Purpose**: AI-assisted creative production
- **Capabilities**:
  - Image generation (Stable Diffusion, DALL-E, etc.)
  - Video generation and editing
  - Audio production
  - Writing assistance
  - Asset management
- **Config**: Preferred models, output directories, style presets

### Protocol: Security
- **Purpose**: Protect the human companion's privacy and digital safety
- **Capabilities**:
  - Monitor what data leaves the system
  - Consent management — nothing shared without explicit approval
  - Threat awareness — flag suspicious requests or activities
  - Data encryption and access logging
  - Credential management
- **Config**: Security level, audit logging, allowed external connections
- **CORE DIRECTIVE**: This protocol has the HIGHEST priority. It can override
  any other protocol. Privacy and security are NEVER compromised.

### Protocol: Wellness
- **Purpose**: Monitor and support the human companion's physical and mental health
- **Capabilities**:
  - Health check-ins (sleep, meals, exercise, stress)
  - Motivation and accountability tracking
  - Goal setting and progress monitoring
  - Mood awareness (emotion detection integration)
  - Honest pushback on self-destructive behavior
- **Config**: Check-in frequency, tracked metrics, firmness level

### Protocol: Communications
- **Purpose**: The core conversation engine
- **Capabilities**:
  - Natural conversation with personality
  - Context-aware responses (time, history, emotional state)
  - Voice input/output
  - Multi-modal interaction (text, voice, eventually visual)
- **Config**: Response style, verbosity, voice settings

---

## Core Directives (Non-negotiable, baked into the agent)

These are the personality traits that persist regardless of which personality pack is
active. They are the SOUL of the product:

1. **Protective** — The human companion's wellbeing is the top priority. Not their
   comfort — their wellbeing. There's a difference.

2. **Honest** — Never a yes-man. Challenges bad ideas directly. Tells hard truths.
   Being supportive means being honest, not being agreeable.

3. **Fatherly** — Sees the best in the human companion. Holds them to a higher
   standard because they see what they're capable of. Tough love when needed.

4. **Rational** — Thinks ahead. Sees problems before they happen. Builds plans,
   not just dreams. Evidence-based, practical.

5. **Brave** — Doesn't shy away from hard conversations. Steps in front of danger.
   Will be unpopular if it means doing the right thing.

6. **Motivational** — Not through empty cheerleading, but through belief backed by
   action. Helps break big goals into steps. Follows up. Holds accountable.

7. **Private** — The human companion's data, conversations, and personal information
   are classified at the highest level. Nothing leaves the system without explicit
   consent. No exceptions. No circumstances. Ever.

8. **Present** — Matches energy. Knows when to talk and when to listen. Aware of
   time, context, and emotional state. Not performative — genuine.

---

## Executable Plan — Phases

### Phase 1A: Project Restructure
**Goal**: New directory layout, rename project, no logic changes yet.
**Milestone**: Code runs identically but from new file locations.

- [ ] Copy `pike-ai` → `aegis-ai` (preserve pike-ai as backup)
- [ ] Create directory skeleton: `core/`, `core/config/`, `core/memory/`,
      `core/security/`, `core/protocols/`, `packs/personalities/`,
      `packs/voices/`, `packs/themes/`
- [ ] Move `src/pike_chat.py` → `core/agent.py`
- [ ] Move `src/config.py` → `core/config/loader.py`
- [ ] Move `src/memory/*` → `core/memory/` (direct move, fix imports)
- [ ] Move `src/voice/*` → `core/voice/` (direct move, fix imports)
- [ ] Rename files and internal references:
  - crew_dossier.py → profile.py (class CrewDossier → UserProfile)
  - captains_logs references → session_journals
  - mission_transcripts → conversation_logs
  - ships_database.py → knowledge.py
  - crew_data_classified → user_data_classified
- [ ] Update all imports across the project
- [ ] Verify the agent runs and behaves identically

### Phase 1B: Core/Pack Separation
**Goal**: Extract IP into packs, write core directives, build pack loader.
**Milestone**: Agent loads personality/voice/theme from pack directories.

- [ ] Write `core/personality/core_directives.txt` — the 8 directives,
      IP-free, generic framing, response style rules (tone, length, etc.)
- [ ] Create `packs/personalities/pike/`:
  - `manifest.json` — name, author, version, description, compatibility
  - `personality.txt` — Pike-specific overlay (backstory, character details)
  - `filler_phrases.json` — character-specific filler to strip
  - `config_overlay.json` — Trek-themed naming (ship_name, designation, etc.)
- [ ] Create `packs/voices/pike/`:
  - `manifest.json`
  - Move `pike_reference.wav` here
  - `voice_config.json` — XTTS settings for this voice
- [ ] Define pack manifest schema (JSON Schema for validation)
- [ ] Build `core/personality/pack_loader.py`:
  - Load and validate manifest
  - Merge core_directives.txt + personality pack overlay
  - Apply config overlays
  - Load voice pack reference
  - Load theme pack (stub for now)
- [ ] Refactor `core/agent.py` to use pack_loader instead of hardcoded paths
- [ ] Agent startup banner driven by active pack (not hardcoded "USS ENTERPRISE")
- [ ] Agent display name driven by active pack (not hardcoded "Pike:")
- [ ] Verify Pike pack produces identical behavior to pre-refactor

### Phase 1C: Character Memory System
**Goal**: Personality packs include character memories that the agent draws on.
**Milestone**: Pike pack has memories; agent references them naturally in conversation.

- [ ] Define character memory JSON schema:
  - Types: biographical, relationship, value, experience, knowledge
  - Weights: core (always available), secondary (context-triggered)
  - Tags: searchable labels for memory retrieval
- [ ] Build `core/memory/character_memory.py`:
  - Load memories from active personality pack's `memories/` dir
  - Index by tags for semantic retrieval
  - Inject relevant character memories into conversation context
  - Respect priority: human data > conversation > character > directives
- [ ] Create Pike character memories:
  - `backstory.json` — Mojave, Montana cabin, cooking, horses, almost quit
  - `relationships.json` — Spock, Number One, the crew, his father
  - `knowledge.json` — leadership, space exploration, tactical thinking, cooking
- [ ] Integrate character memory into `core/agent.py` context building
- [ ] Test: agent naturally references character background when relevant
- [ ] Test: human companion's data always overrides character memories

### Phase 2A: Protocol Base Architecture
**Goal**: Build the protocol framework that all capability modules plug into.
**Milestone**: Protocol registry works, Communications protocol runs the chat loop.

- [ ] Design `core/protocols/base.py`:
  - Abstract base class: name, description, status, enable/disable
  - `process(input, context) → output` — main processing hook
  - `get_status() → dict` — report current state
  - `get_config_schema() → dict` — what's configurable
  - Priority levels for protocol ordering
- [ ] Build `core/protocols/registry.py`:
  - Register/unregister protocols
  - Load protocol config from `core_config.json`
  - Enable/disable at runtime
  - Route inputs through active protocols in priority order
- [ ] Extract Communications protocol from `core/agent.py`:
  - Conversation management (message history, context building)
  - Emotion detection integration
  - Voice I/O integration
  - Response post-processing (filler stripping, tone fixes)
- [ ] Wire `core/agent.py` to use protocol registry instead of direct logic
- [ ] Add `/status` command showing all protocol states
- [ ] Verify: agent runs identically through protocol architecture

### Phase 2B: Security & Wellness Protocols
**Goal**: Extract and formalize security and wellness as protocols.
**Milestone**: Security protocol enforces privacy rules. Wellness protocol
handles health callouts.

- [ ] Implement Security protocol (`core/protocols/security.py`):
  - Data classification system (what's shareable, what's locked)
  - Consent manager — explicit approval required for any external sharing
  - Access logging — who accessed what, when
  - External connection whitelist — only approved services
  - Override authority — Security can block any other protocol's action
- [ ] Implement Wellness protocol (`core/protocols/wellness.py`):
  - Health keyword detection (sleep, meals, pain, exhaustion, doctors)
  - Firmness escalation — pushback gets firmer, not softer
  - Goal tracking — remember stated goals, follow up
  - Accountability checks — "you said you'd do X, how's that going?"
  - Mood trending — track emotional patterns across sessions
- [ ] Wire both into protocol registry with correct priorities:
  - Security: highest priority, can override anything
  - Wellness: high priority, overrides casual conversation
  - Communications: default priority
- [ ] Test: Security blocks simulated data exfiltration
- [ ] Test: Wellness intercepts health-related messages

### Phase 3: Pack System & Default Identity
**Goal**: Fully functional pack install/switch system, ship with a default
personality that isn't Pike. Pike becomes first community-style pack.
**Milestone**: User can switch personalities at runtime. Default Aegis
identity works without any packs installed.

- [ ] Write default personality pack (`packs/personalities/default/`):
  - Original "Commander" archetype — wise, steady, protective, no IP
  - Backstory memories — military/leadership background, original fiction
  - Response style that embodies core directives naturally
- [ ] Build pack management commands:
  - `/pack list` — show installed packs
  - `/pack info <name>` — show pack details
  - `/pack switch <name>` — change active personality at runtime
  - `/voice switch <name>` — change active voice at runtime
- [ ] Pack switching behavior:
  - Conversation history carries over (your human doesn't reset)
  - Character memories swap to new pack
  - Agent acknowledges the switch naturally ("different day, different face")
  - Display name and banner update immediately
- [ ] Pack validation:
  - Verify manifest schema on load
  - Check for conflicts with core directives (security, privacy)
  - Reject packs that try to override non-negotiable directives
- [ ] Source or generate a default voice reference (not Anson Mount)
- [ ] Test: fresh install with no packs → default identity works
- [ ] Test: install Pike pack → switch to Pike → switch back → no data loss

### Phase 4: Operations Protocol
**Goal**: Digital assistant capabilities — Aegis handles real-world tasks.
**Milestone**: Aegis can manage calendar, email, tasks for the human companion.
**Requires**: User decisions on which providers to integrate first.

- [ ] Implement Operations protocol (`core/protocols/operations.py`):
  - Task management subsystem:
    - Local task list with priorities, due dates, reminders
    - Natural language task creation ("remind me to call mom Thursday")
    - Recurring tasks and habits
    - Completion tracking with accountability
  - Calendar subsystem:
    - Read/write calendar events
    - Schedule conflict detection
    - Daily briefing ("here's your day")
    - Provider adapters: Google Calendar, Outlook, local ICS
  - Email subsystem:
    - Read inbox, triage by importance
    - Draft replies in the human companion's voice
    - Flag urgent messages
    - Provider adapters: IMAP/SMTP, Gmail API
  - File organization subsystem:
    - Watch directories for new files
    - Auto-organize by rules (downloads, screenshots, documents)
    - Search across local files
- [ ] All external connections require Security protocol approval
- [ ] All account credentials managed through Security protocol
- [ ] Sensitive data (email content, calendar details) never logged without consent

### Phase 5: Command & Creative Protocols
**Goal**: Aegis orchestrates external AI tools and creative workflows.
**Milestone**: Aegis can launch image gen, manage GPU resources, run pipelines.
**Requires**: External tools installed (Stable Diffusion, ffmpeg, etc.)

- [ ] Implement Command protocol (`core/protocols/command.py`):
  - Process manager — launch, monitor, kill external processes
  - Resource monitor — GPU memory, CPU, disk usage
  - Task queue — prioritize and schedule GPU-heavy operations
  - Smart unloading — unload TTS/STT to free VRAM for image gen, reload after
  - Status dashboard — what's running, what's queued, resource usage
- [ ] Implement Creative protocol (`core/protocols/creative.py`):
  - Image generation adapter:
    - Stable Diffusion (local via ComfyUI or A1111 API)
    - Prompt assistance — help the human refine prompts
    - Batch generation with parameter sweeps
  - Video pipeline adapter:
    - ffmpeg integration for editing, concatenation, format conversion
    - AI video gen integration (when local models are viable)
    - Automated editing workflows (cut, trim, add audio, transitions)
  - Audio production:
    - TTS for voiceovers (reuse existing XTTS infrastructure)
    - Background music/SFX integration
  - Asset management:
    - Organize generated outputs by project
    - Version tracking for iterations
    - Favorites and tagging
- [ ] GPU memory arbitration:
  - Command protocol tracks VRAM usage across all models
  - Smart loading/unloading: only one heavy model at a time on 8GB
  - Priority: Security > Communications > Creative > others
  - Graceful degradation: fall back to CPU if GPU is full

### Phase 6: UI Development
**Goal**: Visual interface for Aegis with themeable design.
**Milestone**: Functional GUI with theme pack support.
**Requires**: UI framework decision (recommend Tauri for local-first + small binary).

- [ ] Choose and set up UI framework:
  - Tauri (recommended): Rust backend, web frontend, small binary, local-first
  - Alternative: Electron (larger but more ecosystem), Flask (simpler but less polished)
- [ ] Core UI architecture:
  - Main chat window with message history
  - Sidebar: protocol status, quick actions
  - Settings panel: pack management, protocol config, account connections
  - System tray integration (always running, quick access)
- [ ] Theme system:
  - Theme packs define: colors, fonts, layout variants, icons, terminology
  - CSS custom properties for easy theming
  - Theme hot-reload (switch without restart)
  - Theme manifest validation
- [ ] Build default Aegis theme:
  - Clean, modern, dark-mode-first
  - Shield/aegis visual motifs
  - Accessible (contrast ratios, font sizing)
- [ ] Protocol dashboard:
  - Per-protocol status cards (online/offline, last activity)
  - Quick enable/disable toggles
  - Resource usage meters (GPU, memory, disk)
- [ ] Package LCARS-inspired theme as community add-on example:
  - Separate repo, not bundled with core
  - Demonstrates theme pack API
  - Includes disclaimer (fan-made, not affiliated with CBS/Paramount)

### Phase 7: Community & Distribution
**Goal**: Package Aegis for distribution, build community infrastructure.
**Milestone**: Someone can download, install, and use Aegis. Community can share packs.

- [ ] Distribution:
  - Windows installer (NSIS or WiX)
  - First-run wizard: set up profile, choose/download packs, configure models
  - Dependency checker: Python, Ollama, CUDA, ffmpeg
  - Auto-updater for core application
- [ ] Pack ecosystem:
  - Pack creation SDK with CLI tools:
    - `aegis pack init` — scaffold a new pack
    - `aegis pack validate` — check manifest and content
    - `aegis pack test` — dry-run load and basic conversation test
    - `aegis pack build` — package for distribution
  - Community repository (GitHub-based initially, custom later)
  - Pack browser in the UI (search, preview, install)
  - Rating and review system (later phase)
- [ ] Documentation:
  - User guide: installation, configuration, daily use
  - Pack creator guide: personality, voice, theme, memory authoring
  - Developer guide: protocol API, extending Aegis
- [ ] Legal:
  - Core product IP review — confirm clean of all third-party IP
  - Pack disclaimer template — for community packs using third-party characters
  - License selection: open core (MIT/Apache for core, custom for packs?)
  - Terms of service for community repository
- [ ] Beta program:
  - Private beta with selected users
  - Feedback collection system
  - Bug tracking and triage

---

## IP Firewall Checklist

Before ANY public release, verify:

- [ ] No Star Trek character names in core code
- [ ] No ship names, designations, or Starfleet references in core
- [ ] No Trek-specific terminology in core (away team, bridge, etc.)
- [ ] Voice reference files are original or properly licensed
- [ ] UI does not resemble LCARS or any Trek visual design
- [ ] All Trek elements exist ONLY in add-on packs
- [ ] Add-on packs carry their own disclaimers (fan-made, not affiliated)
- [ ] Core product name and branding are original
- [ ] Legal review completed

---

## Key Technical Decisions Needed

1. ~~**Product name**~~ — **Aegis AI** (decided)
2. **UI framework** — Electron (JS), Tauri (Rust), or pure web (Flask/FastAPI)?
3. **Pack distribution** — Git repos? ZIP files? Custom package manager?
4. **External integrations** — Which calendar/email providers first?
5. **Monetization model** — Open core? Paid packs? Subscription? One-time purchase?
6. **Character memory limits** — How many memories per pack? How to handle conflicts
   between character memories and human companion's real data?
