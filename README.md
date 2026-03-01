# Synthesized Chat Generator (MessageViewerWWE)

Build realistic multi-phone SMS datasets with AI, then inspect the output in a browser UI.

This project is designed for **shared use**:
- no one needs to edit anything inside `.venv`
- each person uses their own API key in `.env` or in the UI
- secrets stay local and out of shared files

---

## Quick Start (Windows / PowerShell)

1) Create and activate a virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

2) Install dependencies with `uv`:

```powershell
uv sync
```

3) Add your own API key:

```powershell
copy .env.example .env
```

Open `.env` and set:

```text
OPENAI_API_KEY=sk-proj-your-key-here
```

4) Run the app:

```powershell
uv run messageviewer
```

5) Open:

`http://127.0.0.1:8080/`

---

## API Key Rules (for team sharing)

- **Do not** hardcode a key in library files or anything under `.venv`.
- Keys should come from:
  - `OPENAI_API_KEY` in `.env`, or
  - the UI key box (top-right badge), which writes to local `.env`.
- `.env` is local-only and should not be shared.
- `.env.example` is the safe template that can be shared.

---

## What This App Does (5th-grade version)

Think of this app like a **movie script machine for text messages**:

1. You create fake phones and fake contacts.
2. You describe each person (personality, habits, style).
3. You add story events (who met where, what happened, when).
4. The app asks the AI to write the texts in small chunks.
5. The app keeps memory so later messages still match earlier messages.
6. You get JSON files that look like real message exports.

---

## Main Pieces

- `source/app.py`: FastAPI server and API routes.
- `source/static/app.js`: browser logic, tabs, API calls, progress updates.
- `source/ai_assist.py`: AI helper endpoints (names, personalities, story arcs, events, groups).
- `source/generator.py`: final generation pipeline, batching, memory, progress streaming.
- `source/models.py`: scenario builder data models.
- `messageviewer/models.py`: output dataset models (`actors`, `messages`).

---

## Runtime Flow (simple but detailed)

### 1) App startup

- Server starts in `source/app.py`.
- It loads `.env` into environment variables.
- It mounts:
  - `/api/ai/*` routes (assist tools),
  - `/api/generate/*` routes (final generation).

### 2) Frontend boot

- Browser loads `/`.
- Frontend fetches scenario state from `GET /api/scenario`.
- UI tabs let you edit devices, contacts, arcs, events, links, and generation settings.
- Edits are synced back with `PUT /api/scenario` (debounced autosync).

### 3) AI assist stage (optional but recommended)

Frontend can call:
- `POST /api/ai/generate-names`
- `POST /api/ai/generate-personality`
- `POST /api/ai/generate-story-arc`
- `POST /api/ai/generate-character-arcs`
- event/link/group suggestion endpoints

These calls fill in drafts you can edit before final generation.

### 4) Final generation stage

`POST /api/generate/run` starts the main pipeline:

1. Build message skeletons (timestamps + direction).
2. Build prompts from:
   - personalities,
   - story arc + character arcs,
   - timeline events + cross-device links.
3. Generate in batches (not one giant call).
4. Save per-device outputs incrementally (resume-safe).
5. Save scenario manifest and quality report.

---

## How Conversation Memory Is Maintained

During generation, each batch can return a `StoryState` memory object:
- topics already covered
- key events already mentioned
- unresolved threads
- relationship vibe
- owner/contact emotional state

That memory is fed into the next batch prompt, so the AI does not "forget" the conversation.

In kid terms: the AI carries a little notebook from one chapter to the next.

---

## How Events Trigger Behavior

- Timeline events are injected into prompts as "must-mention when relevant" context.
- Group chats can be linked to `origin_event_id` + `start_date`.
- Event-driven groups can activate around event time.
- If `auto_pair_threads` is enabled, direct owner↔member threads are also created when needed.

In kid terms: an event is like a domino. When it falls, related chats wake up.

---

## How Outputs Are Saved

Generated files go to `data/generated/`:
- per-device dataset files
- `<scenario_id>_manifest.json` (run summary)
- `<scenario_id>_quality_report.json` (quality findings)

Scenarios you save from the UI are stored in:
- `data/scenarios/<scenario_id>.json`

---

## Quality Checks (Report-only)

Quality checks do **not** block generation. They report warnings so you can improve the scenario:
- personality coherence
- arc/event consistency
- shared identity consistency across linked contacts
- temporal realism
- language consistency
- group event coherence

Use the report to tune and rerun.

---

## Share This Project Safely

When giving this project to another person:

1. Include source files + docs.
2. Include `.env.example`.
3. Exclude:
   - `.env`
   - `.venv`
   - `.mypy_cache`
   - `.ruff_cache`
   - `.cursor`
4. Tell them to run:
   - `uv sync`
   - `copy .env.example .env`
   - add their own key
   - `uv run messageviewer`

---

## Troubleshooting

- **"No API Key" badge**: set key in `.env` or UI badge input.
- **Generation returns empty messages**: key missing/invalid, or quota issue.
- **Resume button appears**: partial files exist; pipeline is resume-aware.
- **Unexpected old data**: start a new scenario and save before running generation.

---

## Developer Checks

Before sharing code changes, run:

```powershell
uv run black --preview --line-length 140 source tests
uv run ruff check --preview --fix source tests
uv run mypy source tests
uv run pytest
```

---

## Important Security Note

Never commit or share real API keys.  
If a real key was ever exposed, rotate it in your OpenAI account immediately.
