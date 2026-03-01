# Personality and Story Arc System

This document explains how personalities, story arcs, events, and conversation generation interact.

## 1) Core Concept

The system has two narrative layers:

- **Global arc**: scenario-level "story bible" (`scenario.story_arc`)
- **Character arcs**: per-owner and per-contact trajectories (`owner_story_arc`, `contact.story_arc`)

Personalities and generated messages are expected to remain consistent with these layers.

## 2) Where Data Lives

- Scenario + character fields are defined in `source/models.py`.
- AI arc/personality endpoints are in `source/ai_assist.py`.
- Prompt assembly and final conversation generation are in `source/generator.py`.
- Frontend editing and "AI Fill" actions are in `source/static/app.js`.

## 3) Personality System

### Inputs

When generating a personality (`/api/ai/generate-personality`), request context can include:

- `name`, `role`, `owner_name`
- `theme`, `culture`
- `story_arc` (global)
- `character_arc` (specific person)

### Behavior

`source/ai_assist.py` appends story context to the personality prompt:

- global arc as narrative ground truth
- character arc for what this person knows/hides/how they evolve

This is how personality output gets tied to plot direction.

## 4) Story Arc System

### Global arc generation

`POST /api/ai/generate-story-arc` builds a long-form narrative with:

- premise
- key cast roles
- inciting incident
- escalation beats
- climax
- resolution
- secrets/knowledge asymmetry

Inputs: `theme`, `culture`, `cast_summary`, `existing_events`, `date range`, `num_events`.

### Character arc generation

`POST /api/ai/generate-character-arcs` uses the global arc plus cast summary to produce arc text per character.

## 5) How Arcs Affect Message Generation

In `source/generator.py`, prompt builders inject:

- **STORY BIBLE** block (global arc)
- **CHARACTER ARCS** block (owner/contact arcs)

The generation rules explicitly instruct the model to stay consistent with this story context.

## 6) How Events Interact with Arcs

Timeline events are injected into prompts during conversation generation.
This means:

- arcs define intended narrative direction
- events can force local deviations in what characters text about
- messages usually reflect both arc pressure and event reality

Important: the system does **not** auto-rewrite the stored arc text when events change later. If major events shift the narrative, regenerate or manually update arcs.

## 7) Group Chats and Arcs

Group chat generation runs after direct owner-contact threads for each device.

- It includes global story context
- It only runs for groups where that device owner is a member
- It does not invent undefined memberships

So group chat unpredictability is bounded by defined group membership + timeline events + arc constraints.

Event-driven groups can now be anchored with `origin_event_id` and activated by event/start date.
When `auto_pair_threads` is enabled, generation also attempts to ensure direct owner↔member
threads exist for active groups so conversational evidence can appear in both group and 1:1 channels.

## 8) Practical Workflow (Recommended)

1. Build devices/contacts and shared links.
2. Generate names and personalities.
3. Generate global story arc.
4. Generate character arcs.
5. Generate events/links and review.
6. Regenerate character arcs if events significantly change direction.
7. Run final generation.

This sequence gives the most coherent outputs.

## 9) Quality Scoring and Interpretation

Generation now includes a report-only quality layer. It does not block output,
but it reports where coherence may be weak.

- Report file: `data/generated/<scenario_id>_quality_report.json`
- Manifest summary: `quality_summary` in `<scenario_id>_manifest.json`
- Live progress warnings: `quality_warning` SSE events shown in Generate logs

### Severity thresholds

- `critical`: score `< 0.40`
- `warning`: `0.40 <= score < 0.70`
- `ok`: score `>= 0.70`

### Checks included

- personality coherence
- arc-event consistency
- relationship behavior rules
- shared identity cross-device lock
- conversation memory quality
- temporal realism
- language/dialect consistency

Use these warnings as iterative tuning feedback. If arc/event consistency is
low, regenerate character arcs after major event edits. If shared identity is
low, align linked contacts to one canonical persona core and keep variation in
relationship-specific texting tone only.
