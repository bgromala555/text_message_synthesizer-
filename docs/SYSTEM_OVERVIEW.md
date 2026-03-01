# MessageViewerWWE System Overview

This document explains the current architecture in plain language and maps the main runtime flows.
For the unified deep-dive reference that includes architecture, quality gate contract,
cleanup status, and personality/arc mechanics with diagrams, see
`docs/MASTER_SYSTEM_GUIDE.md`.

## 1) Main Runtime Surfaces

- `source/app.py`: main FastAPI app for the scenario builder UI.
- `source/templates/index.html`: single-page UI shell (tabs, modals, controls).
- `source/static/app.js`: frontend state + API calls + generation progress handling.
- `source/ai_assist.py`: AI helper endpoints (names, personalities, events, story arcs, group suggestions).
- `source/generator.py`: final message generation pipeline + SSE progress stream.
- `source/models.py`: scenario/editor models used by the UI/backend.
- `messageviewer/models.py`: final dataset output schema models.

## 2) High-Level Request Flow

1. Browser loads `/` from `source/app.py` and receives `index.html`.
2. Frontend (`app.js`) loads scenario state from `GET /api/scenario`.
3. User edits scenario across tabs. Frontend debounces and syncs via `PUT /api/scenario`.
4. Optional AI assists call `/api/ai/*` endpoints and merge results back into scenario state.
   - Recommended narrative flow: suggest events, then suggest groups from those events, then review quality warnings.
5. User runs generation via `POST /api/generate/run`.
6. Backend streams progress events over SSE while writing per-device JSON outputs.
7. Manifest is written to `data/generated/<scenario_id>_manifest.json`.

## 3) Frontend Tab Responsibilities

- Devices: owner/device records and owner-level settings.
- Contacts: relationship graph and shared contact links.
- Personalities: AI generate/fill personality profiles.
- Story Arc: global story bible + per-character arcs.
- Events & Links: timeline events, cross-device links, group chat planning.
- Generate: launch generation, resume, monitor SSE progress.

## 4) Backend Route Groups

### Core scenario app (`source/app.py`)

- Scenario lifecycle: `/api/scenario`, `/api/scenario/new`, `/api/scenario/save`, `/api/scenario/load/{id}`
- API key: `/api/apikey/status`, `/api/apikey/set`
- CRUD endpoints for devices, contacts, connections, events, settings
- Router mounts:
  - `/api/ai/*` from `source/ai_assist.py`
  - `/api/generate/*` from `source/generator.py`

### AI assist routes (`source/ai_assist.py`)

- Names: `POST /api/ai/generate-names`
- Personalities: `POST /api/ai/generate-personality`
- Events/links/group suggestions
- Story arcs:
  - `POST /api/ai/generate-story-arc`
  - `POST /api/ai/generate-character-arcs`

### Generation routes (`source/generator.py`)

- `POST /api/generate/run`: full generation with SSE progress
- `GET /api/generate/status`: API availability
- `GET /api/generate/progress`: detect partial completion for resume UX

## 5) Generation Pipeline (Order of Operations)

Per device:
1. Generate direct owner-contact conversations.
2. Generate group conversations for groups the owner belongs to.
   - Event-driven groups can activate using `origin_event_id` + `start_date`.
   - When enabled, owner↔member pair threads are auto-generated if missing.
3. Add spam/noise threads (based on per-device spam density).
4. Save incrementally after each contact/group step (resume-safe).

After all devices:
1. Save per-device output JSON files.
2. Save scenario manifest with summary + settings + arc/event metadata.

## 6) Data Shapes in Use

- Scenario editing shape: `source/models.py` (`ScenarioConfig`, `DeviceScenario`, `ContactSlot`, `GroupChat`, `FlexTimelineEvent`, `GenerationSettings`).
- Output dataset shape: generated via `source/generator.py`, stored as:
  - top-level `actors`
  - top-level `messages`
  - each message record contains `source`, `target`, `type`, `message_content`, and summary metadata.

## 7) Notes on Legacy Components

There is an older `messageviewer/` app/tooling path still present. The active scenario builder flow runs through `source/*`. Legacy files may still be useful for historical workflows or one-off scripts, so treat removals as deliberate cleanups, not automatic deletions.
