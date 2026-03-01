# Quality Gate Spec (Report-Only)

This document defines the quality-check contract implemented in the generation pipeline.

## Mode

- Current mode: `report_only`
- Behavior: generation never aborts because of quality checks.
- Output: warnings + scores + persisted report.

## Artifacts

- Run report: `data/generated/<scenario_id>_quality_report.json`
- Manifest summary: `quality_summary` embedded in `<scenario_id>_manifest.json`
- SSE warnings: `quality_warning` events during generation

## Check IDs

- `personality_coherence`
- `arc_event_consistency`
- `relationship_behavior_rules`
- `shared_identity_cross_device_lock`
- `conversation_memory_quality`
- `temporal_realism`
- `language_dialect_consistency`
- `group_event_coherence`
- `pairwise_pair_coverage`

## Severity Mapping

- `critical`: score `< 0.40`
- `warning`: `0.40 <= score < 0.70`
- `ok`: score `>= 0.70`

## Score Range

- Every check returns a normalized score `0.0 .. 1.0`
- Overall score is a weighted average of check scores

## Heuristic Coverage

### 1) Personality coherence

- Flags contradictory trait pairs in summaries
- Flags emoji-style mismatch vs sample phrases

### 2) Arc-event consistency

- Compares lexical overlap between arcs, events, and generated messages
- Warns on high narrative drift

### 3) Relationship behavior rules

- Applies role-based tone constraints on thread behavior
- Example: highly formal roles with excessive expressive punctuation

### 4) Shared identity cross-device lock

- Checks linked contacts for canonical identity consistency
- Warns on divergent names/personality cores across linked instances

### 5) Conversation memory quality

- Detects repeated-message loops at thread level

### 6) Temporal realism

- Detects non-monotonic timestamps
- Warns on extreme overnight messaging ratios

### 7) Language/dialect consistency

- Checks script consistency against scenario language setting
- Warns for substantial language drift

### 8) Group-event coherence

- Validates that suggested/defined groups are anchored to valid origin events
- Flags missing or stale `origin_event_id` references

### 9) Pairwise pair coverage

- Validates owner↔member direct-thread coverage for groups with `auto_pair_threads=true`
- Flags missing pair threads that should exist based on active group membership

## Report Shape (Summary)

- `summary.overall_score`
- `summary.overall_severity`
- `summary.check_scores`
- `summary.findings_total`, `critical_count`, `warning_count`, `ok_count`
- `checks[]` with per-check metrics + findings
- `top_findings[]` sorted actionable items

## Operational Guidance

- Treat `critical` findings as top triage candidates.
- If a scenario repeatedly produces low `arc_event_consistency`, regenerate arcs after event edits.
- If `shared_identity_cross_device_lock` is weak, consolidate linked-contact cores before reruns.
- Use warnings as iterative tuning feedback, not hard failure conditions.
