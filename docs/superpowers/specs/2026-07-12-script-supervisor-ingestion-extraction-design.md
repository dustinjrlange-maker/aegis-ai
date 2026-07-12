# Script Supervisor — Sub-project 1: Ingestion & Extraction Core

**Date:** 2026-07-12
**Status:** Approved design → writing implementation plan
**Parent:** Aegis Wave 5 (documents + script breakdown). This is the first of six sub-projects for the in-Aegis Script Supervisor tool.

---

## Context

Switch works on actively-shooting productions under NDA. Feeding raw script/schedule prose to cloud Claude (Anthropic) could constitute a breach. The Script Supervisor tool lets Aegis help with production digital tasks (breakdown, scheduling, prop tracking, sheet population, Q&A) **without cloud Claude ever reading the prose**. The local layer extracts structured tables; only those derived tables leave the box.

This overlaps Aegis Wave 5's locked rule: *split pipeline, prose never leaves the box, cloud sees only derived tables.*

### Full tool decomposition (build order — for context, not all in this spec)

1. **Ingestion + Extraction core** ← *this spec* (the privacy boundary; everything depends on it)
2. **Prop-possession timeline** — interval model: prop → character → start scene → end (script-loss-point or manual production cutoff); all projections reflow on edit
3. **Breakdown sheets** — per-scene/page breakdown (parallel to #2)
4. **Scheduling / stripboard** — scenes → shoot days, cross-referenced against timeline, conflict flags
5. **Sheet rendering** — populate Google Sheets/Excel, color-coded, clean (reuses existing Aegis Sheets wiring)
6. **Image recognition / auto-filing + Q&A over tables**

Each sub-project gets its own spec → plan → implementation cycle. This spec covers **#1 only**.

---

## 1. Purpose & privacy boundary

Get a script into Aegis as **structured derived tables**, entirely on-box, so downstream sub-projects and cloud Claude can operate without ever touching prose.

**Hard invariants:**
- Raw script text is stored on-box only, marked `never-egress`.
- Extraction runs on the **local LLM only** — never cloud.
- **Local OCR only** — no cloud OCR service.
- Cloud-eligible surface = derived tables. Prose is never in a cloud-bound payload.
- Wires into Aegis's existing privacy gate / full-payload scan.

---

## 2. Components

Each unit is isolated with a well-defined interface, independently testable.

### Source adapters
All adapters produce a `RawScript` (on-box). Pluggable behind one interface.

- **`FileSource`** — accepts PDF, FDX (Final Draft XML), Fountain, plain text.
  - FDX / Fountain are already structured → direct parse.
  - PDF → screenplay-format layout parsing (scene headings, centered character cues, dialogue, action).
- **`PortalSource`** — for secure password-protected portals (Scenechronize, Croogloo, DA, etc.) where scripts can't be downloaded.
  - **Path A (this spec): user-driven capture.** User opens the portal, logs in themselves, pages through. Aegis never sees or stores credentials. Aegis captures each rendered page locally.
  - Capture assumes portals render as **images / locked canvas with text-selection disabled** and **watermark each page with reader identity** → capture is **screenshot each page → local OCR → reconstructed text**. (Watermarking is low-risk here: only derived tables egress, prose stays on-box.)
  - Browser control via Playwright and/or `iris`; OCR local.
  - **Path B (deferred): automated login + navigation** behind the same adapter interface. User would store portal creds in Aegis. Not built in v1.

### Normalizer
Raw text → normalized screenplay structure: scene splits, INT/EXT, location, D/N, page/eighths, character cues, action blocks. Screenplay-format-aware. Same output shape regardless of source adapter.

### Extractor
Normalized structure → derived tables via **local LLM**:
- Scenes (number, heading, INT/EXT, location, D/N, page/eighths)
- Characters (+ aliases)
- Locations
- Scene metadata (characters present, short action summary)
- **Prop candidate list** (surfaced, not trusted — see scope cuts)

### Store
SQLite. Persists two clearly separated tiers:
- `RawScript` — on-box, `never-egress` flag, per script version.
- `DerivedTables` — queryable, cloud-eligible.

### Privacy guard
Asserts no raw prose can enter a cloud-bound payload. Reuses the full-payload scan pattern already built for escalate-on-trouble. Extraction path is pinned to the local backend.

---

## 3. Data flow

```
Source (File | Portal-A)
  → RawScript (on-box, never-egress)
  → Normalizer
  → Extractor (local LLM)
  → DerivedTables (SQLite)
  → available to cloud + downstream sub-projects
```

---

## 4. Data model — revisions

Productions issue colored revision pages (white/blue/pink…) constantly, often renumbering scenes. The prop-timeline (sub-project 2) breaks if the schema can't hold versions. So the schema is **designed for multiple script versions per project now**:

```
Project 1 ── * ScriptVersion 1 ── 1 RawScript
                              └── * Scene / Character / Location / PropCandidate (DerivedTables)
```

Actual **revision-remap logic** (mapping scene renumbering across versions) is **deferred** to a later sub-project. The schema just has to hold versions without collapsing.

---

## 5. Error handling & human-in-loop

NDA stakes → extraction is **reviewed, not trusted blindly**.

- OCR low-confidence pages → flagged for user review.
- PDF / extraction ambiguity → entities marked `needs-confirm`.
- Portal capture → detect incompletely-rendered pages before capturing (retry/flag).
- User confirms the extracted scene/character list before it is marked `trusted` and consumed downstream.

---

## 6. Testing

- Public-domain screenplay fixtures in each format (FDX / Fountain / PDF).
- Rendered-image fixtures for the OCR path.
- Golden derived-table outputs per fixture.
- **Privacy invariant test:** assert raw prose never appears in any cloud-bound payload (mirrors the escalate-on-trouble payload-scan test).
- Adapter interface conformance tests (all sources → same `RawScript` shape; all normalized structures → same extractor input).

---

## 7. Scope cuts for v1 (deliberately thin)

- Auto-detect scenes / characters / locations / metadata; **props hand-added** (candidates surfaced, not trusted) — full auto-prop-detection deferred.
- Portal **path A only** (user-driven capture); path B (automated login) deferred.
- Schema supports versions; **revision-remap deferred**.
- No breakdown/schedule/sheet output in this sub-project — that's #2–#5.

---

## Open items carried forward

- Exact local OCR engine choice (iris vision/OCR vs bundled Tesseract/PaddleOCR) — decided during implementation planning; must stay local.
- Portal path B (automated login) — future adapter.
- Revision-remap logic — sub-project after the timeline.
