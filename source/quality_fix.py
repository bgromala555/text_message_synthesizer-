"""Quality-check and fix orchestration for scenario configurations.

Provides deterministic structural fixes (owner names, shared-identity
normalization, group metadata alignment), AI-assisted personality and
character-arc regeneration, timeline event-message consistency repair,
and temporal sorting of generated conversations.

The public entry point is :func:`execute_quality_check`, which wraps
all fix passes and returns a JSON-serializable result dict for the
route handler in ``source.generator``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from messageviewer.models import SmsDataset
from source import (
    ai_assist,
    conversation,
    persistence,
    quality_checks,
    validation,
)
from source.ai_assist import GenerateCharacterArcsRequest, GeneratePersonalityRequest
from source.llm_client import QuotaExhaustedError
from source.models import (
    ContactSlot,
    FlexPersonalityProfile,
    RepairDetail,
    ResolutionItem,
    ResolutionWriteup,
    ScenarioConfig,
)
from source.quality_models import QualityFinding, QualitySeverity

logger = logging.getLogger(__name__)

# Minimum number of contacts in a shared group before identity normalization applies.
_MIN_SHARED_GROUP_SIZE = 2

# Minimum personality summary length (chars) below which AI personality repair triggers.
_MIN_PERSONALITY_SUMMARY_LEN = 40

# Minimum messages in a thread before temporal-sort check is meaningful.
_MIN_MESSAGES_FOR_SORT = 2


# ---------------------------------------------------------------------------
# Deterministic helper functions
# ---------------------------------------------------------------------------


def _normalize_shared_actor_ids(scenario: ScenarioConfig) -> int:  # noqa: C901 - multi-field cross-device sync requires nested iteration
    """Normalize shared-contact actor IDs and core personality fields.

    For each shared-contact group, picks a canonical actor_id and
    personality core (summary, backstory, age, emotional_range) from the
    first contact that has them, then copies to all others in the group.
    This ensures the quality check sees consistent identity data across
    devices without requiring a regeneration pass.

    Args:
        scenario (ScenarioConfig): The scenario to normalize in-place.

    Returns:
        Number of individual contact records that were updated.

    """
    by_id: dict[str, ContactSlot] = {}
    for dev in scenario.devices:
        for c in dev.contacts:
            by_id[c.id] = c

    visited: set[str] = set()
    updates = 0
    for dev in scenario.devices:
        for contact in dev.contacts:
            if contact.id in visited or not contact.shared_with:
                continue
            group = [contact]
            visited.add(contact.id)
            for link in contact.shared_with:
                other = by_id.get(link.contact_id)
                if other and other.id not in visited:
                    group.append(other)
                    visited.add(other.id)

            if len(group) < _MIN_SHARED_GROUP_SIZE:
                continue

            canonical = ""
            for candidate in group:
                candidate_id = (candidate.actor_id or "").strip()
                if candidate_id:
                    canonical = candidate_id
                    break
            if canonical:
                for c in group:
                    if (c.actor_id or "").strip() != canonical:
                        c.actor_id = canonical
                        updates += 1

            canonical_profile = next(
                (c.personality for c in group if c.personality and (c.personality.personality_summary or "").strip()),
                None,
            )
            if canonical_profile is None:
                continue
            core_summary = canonical_profile.personality_summary
            core_backstory = canonical_profile.backstory_details
            core_age = canonical_profile.age
            core_emotional = canonical_profile.emotional_range
            for c in group:
                if c.personality is None:
                    continue
                changed = False
                if (c.personality.personality_summary or "").strip().lower() != (core_summary or "").strip().lower():
                    c.personality.personality_summary = core_summary
                    changed = True
                if (c.personality.backstory_details or "").strip() != (core_backstory or "").strip():
                    c.personality.backstory_details = core_backstory
                    changed = True
                if c.personality.age != core_age:
                    c.personality.age = core_age
                    changed = True
                if (c.personality.emotional_range or "").strip() != (core_emotional or "").strip():
                    c.personality.emotional_range = core_emotional
                    changed = True
                if changed:
                    updates += 1
    return updates


def _build_cast_summary_for_arcs(scenario: ScenarioConfig) -> str:
    """Build a cast summary string for character-arc generation.

    Enumerates every device and its contacts into a compact textual
    description that the LLM can use to generate character arcs which
    reference the correct names and relationships.

    Args:
        scenario (ScenarioConfig): Full scenario config.

    Returns:
        Multi-line cast summary with one line per device owner listing
        their contacts.

    """
    lines: list[str] = []
    for dev in scenario.devices:
        contacts = ", ".join(f"{c.name or 'unnamed'} ({c.role or 'contact'})" for c in dev.contacts)
        lines.append(f"{dev.owner_name or 'unnamed'}: contacts are {contacts}")
    return "\n".join(lines)


def _scenario_readiness_summary(scenario: ScenarioConfig) -> dict[str, str]:
    """Summarize scenario readiness across personalities, arcs, and events.

    Counts how many owners and contacts have personality profiles, how many
    have story arcs, and how many timeline events have participants assigned.
    Used by the quality-check endpoint to give the user a quick overview.

    Args:
        scenario (ScenarioConfig): The scenario to summarize.

    Returns:
        Dict with ``personality_complete``, ``arc_complete``, and
        ``events_with_participants`` keys as ``"N/M"`` fraction strings.

    """
    owner_total = len(scenario.devices)
    owner_with_personality = sum(1 for dev in scenario.devices if dev.owner_personality is not None)
    contacts_total = sum(len(dev.contacts) for dev in scenario.devices)
    contacts_with_personality = sum(1 for dev in scenario.devices for c in dev.contacts if c.personality is not None)

    owner_arcs = sum(1 for dev in scenario.devices if (dev.owner_story_arc or "").strip())
    contact_arcs = sum(1 for dev in scenario.devices for c in dev.contacts if (c.story_arc or "").strip())

    events_total = len(scenario.timeline_events)
    events_with_participants = sum(1 for ev in scenario.timeline_events if len(ev.participants or []) > 0)

    return {
        "personality_complete": f"{owner_with_personality + contacts_with_personality}/{owner_total + contacts_total}",
        "arc_complete": f"{owner_arcs + contact_arcs}/{owner_total + contacts_total}",
        "events_with_participants": f"{events_with_participants}/{events_total}",
    }


def _find_arc(name: str, arcs_lower: dict[str, str]) -> str | None:
    """Match a character name to an arc key, tolerating case and whitespace.

    Performs an exact lowercase match first, then falls back to substring
    matching in both directions (name-in-key and key-in-name).  This loose
    matching accommodates LLM-generated arc dictionaries whose keys may
    not exactly match the scenario character names.

    Args:
        name (str): Character name to look up.
        arcs_lower (dict[str, str]): Arc dictionary with lowercase-stripped
            keys mapping to arc description strings.

    Returns:
        The arc description string if a match is found, or ``None``
        if no arc key matches the given name.

    """
    key = (name or "").strip().lower()
    if key in arcs_lower:
        return arcs_lower[key]
    for arc_key, arc_val in arcs_lower.items():
        if key in arc_key or arc_key in key:
            return arc_val
    return None


# ---------------------------------------------------------------------------
# Structural, personality, and arc fix passes
# ---------------------------------------------------------------------------


def _apply_structural_fixes(
    scenario: ScenarioConfig,
    adjustments: list[str],
    resolution_items: list[ResolutionItem],
) -> None:
    """Apply deterministic structural fixes to the scenario configuration.

    Performs three categories of structural repair in sequence:

    1. **Owner name assignment** — fills blank device owner names from
       the owner personality profile or a fallback label.
    2. **Shared identity normalization** — synchronizes actor IDs and
       core personality fields across contacts linked by ``shared_with``.
    3. **Group metadata alignment** — backfills missing group start dates
       from origin events, sets ``activation_mode`` to ``"event_time"``,
       and enables ``auto_pair_threads``.

    All mutations are applied in-place.  Summary strings are appended to
    *adjustments* and structured :class:`ResolutionItem` entries to
    *resolution_items* for every category that produces changes.

    Args:
        scenario (ScenarioConfig): The scenario to repair in-place.
        adjustments (list[str]): Mutable list of human-readable adjustment
            summaries — appended to when fixes are applied.
        resolution_items (list[ResolutionItem]): Mutable list of structured
            resolution records — appended to when fixes are applied.

    """
    owner_name_fixes = 0
    for dev in scenario.devices:
        if not (dev.owner_name or "").strip():
            if dev.owner_personality and (dev.owner_personality.name or "").strip():
                dev.owner_name = dev.owner_personality.name
                owner_name_fixes += 1
            elif dev.owner_actor_id:
                dev.owner_name = f"Owner ({dev.device_label or dev.id})"
                owner_name_fixes += 1
    if owner_name_fixes > 0:
        adjustments.append(f"Assigned names to {owner_name_fixes} unnamed device owner(s).")
        resolution_items.append(
            ResolutionItem(
                issue="Device owner(s) had no name set.",
                action="Copied name from existing personality profile or assigned a fallback label.",
                result=f"Named {owner_name_fixes} owner(s).",
            )
        )

    shared_identity_updates = _normalize_shared_actor_ids(scenario)
    if shared_identity_updates > 0:
        adjustments.append(f"Synchronized {shared_identity_updates} shared-contact record(s) (actor IDs + personality cores).")
        resolution_items.append(
            ResolutionItem(
                issue="Shared identity drift across linked contacts.",
                action=(
                    "Synchronized actor IDs and core personality fields "
                    "(summary, backstory, age, emotional range) across linked contacts."
                ),
                result=f"Updated {shared_identity_updates} contact record(s).",
            )
        )

    group_updates = 0
    for gc in scenario.group_chats or []:
        if gc.origin_event_id and not gc.start_date:
            ev = next((e for e in scenario.timeline_events if e.id == gc.origin_event_id), None)
            if ev and ev.date:
                gc.start_date = ev.date
                group_updates += 1
        if gc.activation_mode != "event_time":
            gc.activation_mode = "event_time"
            group_updates += 1
        if not gc.auto_pair_threads:
            gc.auto_pair_threads = True
            group_updates += 1
    if group_updates > 0:
        adjustments.append(f"Applied {group_updates} group metadata alignment update(s).")
        resolution_items.append(
            ResolutionItem(
                issue="Group/event linkage metadata drift.",
                action="Aligned origin event dates, activation mode, and pair-thread defaults.",
                result=f"Applied {group_updates} group metadata update(s).",
            )
        )


async def _apply_personality_fixes(
    scenario: ScenarioConfig,
    adjustments: list[str],
    resolution_items: list[ResolutionItem],
) -> None:
    """Regenerate missing or low-quality personalities and fix fallback names.

    Scans every device owner and contact for personality profiles that are
    either absent or have a summary shorter than
    :data:`_MIN_PERSONALITY_SUMMARY_LEN`.  For each, calls
    :func:`ai_assist.generate_personality` to produce a full profile using
    scenario context (theme, culture, story arc, character arc).

    After the personality pass, replaces any remaining ``Owner (…)``
    fallback owner names with the name from the newly generated profile.
    Caps total regenerations at 10 to avoid runaway LLM usage.  All
    mutations are applied in-place.

    Args:
        scenario (ScenarioConfig): The scenario to repair in-place.
        adjustments (list[str]): Mutable adjustment summary list.
        resolution_items (list[ResolutionItem]): Mutable resolution log.

    """
    personality_updates = 0
    max_personality_repairs = 10
    for dev in scenario.devices:
        if personality_updates >= max_personality_repairs:
            break
        dev_uses_story = (dev.generation_mode or "story").strip().lower() != "standalone"
        owner_prof = dev.owner_personality
        owner_needs_repair = owner_prof is None or len((owner_prof.personality_summary or "").strip()) < _MIN_PERSONALITY_SUMMARY_LEN
        if owner_needs_repair and personality_updates < max_personality_repairs:
            owner_req = GeneratePersonalityRequest(
                name=dev.owner_name,
                role="owner",
                context=scenario.name,
                owner_name=dev.owner_name,
                theme=scenario.theme or "slice-of-life",
                culture=scenario.culture or "american",
                story_arc=(scenario.story_arc or "") if dev_uses_story else "",
                character_arc=dev.owner_story_arc or "",
            )
            owner_res = await ai_assist.generate_personality(owner_req)
            owner_payload = {k: v for k, v in owner_res.model_dump().items() if not str(k).startswith("_")}
            owner_payload["actor_id"] = dev.owner_actor_id
            owner_payload["name"] = dev.owner_name
            owner_payload["role"] = "owner"
            dev.owner_personality = FlexPersonalityProfile(**owner_payload)
            personality_updates += 1

        for c in dev.contacts:
            if personality_updates >= max_personality_repairs:
                break
            prof = c.personality
            needs_repair = prof is None or len((prof.personality_summary or "").strip()) < _MIN_PERSONALITY_SUMMARY_LEN
            if not needs_repair:
                continue

            p_req = GeneratePersonalityRequest(
                name=c.name,
                role=c.role or "",
                context=scenario.name,
                owner_name=dev.owner_name,
                theme=scenario.theme or "slice-of-life",
                culture=scenario.culture or "american",
                story_arc=(scenario.story_arc or "") if dev_uses_story else "",
                character_arc=c.story_arc or "",
            )
            p_res = await ai_assist.generate_personality(p_req)
            p_payload = {k: v for k, v in p_res.model_dump().items() if not str(k).startswith("_")}
            p_payload["actor_id"] = c.actor_id
            p_payload["name"] = c.name
            p_payload["role"] = c.role or ""
            c.personality = FlexPersonalityProfile(**p_payload)
            personality_updates += 1

    if personality_updates > 0:
        adjustments.append(f"AI refreshed {personality_updates} personality profile(s) using scenario + character context.")
        resolution_items.append(
            ResolutionItem(
                issue="Missing or low-quality personality profiles.",
                action="Regenerated personalities using scenario context (theme/culture/story arc/character arc/role).",
                result=f"Refreshed {personality_updates} profile(s).",
            )
        )

    for dev in scenario.devices:
        current = (dev.owner_name or "").strip()
        if current.startswith("Owner (") and dev.owner_personality:
            real_name = (dev.owner_personality.name or "").strip()
            if real_name and not real_name.startswith("Owner ("):
                dev.owner_name = real_name


def _collect_standalone_character_names(scenario: ScenarioConfig) -> list[str]:
    """Collect de-duplicated names of characters on standalone-mode devices.

    Used by :func:`_apply_arc_fixes` to tell the LLM which characters should
    receive independent arcs rather than arcs woven into the shared story.

    Args:
        scenario (ScenarioConfig): Full scenario configuration.

    Returns:
        Ordered, de-duplicated list of character names from all standalone
        devices (owners first, then their contacts).

    """
    names: list[str] = []
    for dev in scenario.devices:
        if (dev.generation_mode or "story").strip().lower() != "standalone":
            continue
        if (dev.owner_name or "").strip():
            names.append(dev.owner_name.strip())
        names.extend(c.name.strip() for c in dev.contacts if (c.name or "").strip())
    return list(dict.fromkeys(names))


async def _apply_arc_fixes(
    scenario: ScenarioConfig,
    adjustments: list[str],
    resolution_items: list[ResolutionItem],
) -> None:
    """Regenerate missing character arcs using the scenario story arc.

    Counts device owners and contacts missing arcs, then calls
    :func:`ai_assist.generate_character_arcs` once to produce all missing
    arcs in a single LLM call.  The returned arcs are matched to characters
    by name using :func:`_find_arc` (case-insensitive, substring-tolerant
    matching).

    Skips entirely if the scenario has no ``story_arc`` or all characters
    already have arcs assigned.  Standalone-mode device characters are
    passed to the LLM as ``standalone_character_names`` to receive
    independent arcs.  All mutations are applied in-place.

    Args:
        scenario (ScenarioConfig): The scenario to repair in-place.
        adjustments (list[str]): Mutable adjustment summary list.
        resolution_items (list[ResolutionItem]): Mutable resolution log.

    """
    if not scenario.story_arc:
        return

    missing_arc_targets = 0
    for dev in scenario.devices:
        if not dev.owner_story_arc:
            missing_arc_targets += 1
        for c in dev.contacts:
            if not c.story_arc:
                missing_arc_targets += 1
    if missing_arc_targets == 0:
        return

    cast_summary = _build_cast_summary_for_arcs(scenario)
    arc_req = GenerateCharacterArcsRequest(
        theme=scenario.theme or "slice-of-life",
        culture=scenario.culture or "american",
        story_arc=scenario.story_arc,
        cast_summary=cast_summary,
        standalone_character_names=_collect_standalone_character_names(scenario),
    )
    arc_res = await ai_assist.generate_character_arcs(arc_req)
    arcs = arc_res.arcs
    if not arcs:
        return

    arcs_lower: dict[str, str] = {k.strip().lower(): v for k, v in arcs.items()}
    ai_updates = 0
    for dev in scenario.devices:
        if not dev.owner_story_arc:
            arc_val = _find_arc(dev.owner_name, arcs_lower)
            if arc_val:
                dev.owner_story_arc = str(arc_val)
                ai_updates += 1
        for c in dev.contacts:
            if not c.story_arc:
                arc_val = _find_arc(c.name, arcs_lower)
                if arc_val:
                    c.story_arc = str(arc_val)
                    ai_updates += 1

    if ai_updates > 0:
        adjustments.append(f"AI regenerated {ai_updates} missing character arc(s).")
        resolution_items.append(
            ResolutionItem(
                issue="Missing character arc coverage.",
                action="Regenerated arcs from story arc + cast summary.",
                result=f"Generated {ai_updates} missing arc(s).",
            )
        )


def _repair_timeline_threads(
    scenario: ScenarioConfig,
    datasets_by_device: dict[str, SmsDataset],
    device_number_by_id: dict[str, int],
    adjustments: list[str],
    resolution_items: list[ResolutionItem],
) -> None:
    """Audit generated threads for timeline event violations and repair them.

    For each device with generated data, runs
    :func:`validation.audit_device_event_alignment` to identify threads
    whose messages contradict or omit referenced timeline events.  For
    actionable findings (CRITICAL or WARNING), regenerates the offending
    thread with a targeted repair prompt via
    :func:`conversation.generate_conversation`.

    Caps repairs at 6 threads per device and stops early on API quota
    exhaustion.  Updates *datasets_by_device* in-place with repaired
    datasets and persists them to disk.

    Args:
        scenario (ScenarioConfig): Active scenario configuration.
        datasets_by_device (dict[str, SmsDataset]): Mutable map of
            device ID to generated dataset — updated when threads are
            repaired.
        device_number_by_id (dict[str, int]): Map of device ID to
            1-based device number for file-naming.
        adjustments (list[str]): Mutable adjustment summary list.
        resolution_items (list[ResolutionItem]): Mutable resolution log.

    """
    lang = scenario.generation_settings.language or "en"
    max_timeline_repairs_per_device = 6
    timeline_threads_repaired = 0
    timeline_repair_items: list[RepairDetail] = []

    for device in scenario.devices:
        ds = datasets_by_device.get(device.id)
        if not ds or not ds.nodes:
            continue

        device_generation_mode = (device.generation_mode or "story").strip().lower()
        device_uses_story_context = device_generation_mode != "standalone"
        device_timeline_events = scenario.timeline_events if device_uses_story_context else []
        device_story_arc = (scenario.story_arc or "") if device_uses_story_context else ""

        event_findings = validation.audit_device_event_alignment(device, ds.nodes, device_timeline_events, language=lang)
        actionable = [f for f in event_findings if f.severity in {QualitySeverity.CRITICAL, QualitySeverity.WARNING}]
        if not actionable:
            continue

        targets_to_repair: dict[str, list[QualityFinding]] = {}
        for finding in actionable:
            if "->" in finding.entity_id:
                _, target_id = finding.entity_id.split("->", 1)
                targets_to_repair.setdefault(target_id, []).append(finding)

        device_repairs = 0
        for target_actor_id, target_findings in targets_to_repair.items():
            if device_repairs >= max_timeline_repairs_per_device:
                break
            c_idx = next((idx for idx, c in enumerate(device.contacts) if c.actor_id == target_actor_id), -1)
            if c_idx < 0:
                continue
            contact = device.contacts[c_idx]
            if contact.personality is None or device.owner_personality is None:
                timeline_repair_items.append(
                    RepairDetail(
                        device=device.device_label,
                        thread=f"{device.owner_name} <-> {contact.name}",
                        issues=[f.message for f in target_findings],
                        outcome="skipped",
                        reason="Missing personality profile — run personality fix first.",
                    )
                )
                continue

            repair_fb = validation.build_repair_feedback(target_findings)
            try:
                repaired_messages, _calls, repair_quota_hit = conversation.generate_conversation(
                    device,
                    c_idx,
                    scenario.generation_settings,
                    scenario.theme or "slice-of-life",
                    scenario.culture or "american",
                    device_timeline_events,
                    device_story_arc,
                    lang,
                    repair_fb,
                    device_uses_story_context,
                )
            except (QuotaExhaustedError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
                logger.warning("Timeline repair failed for %s <-> %s: %s", device.owner_name, contact.name, exc)
                timeline_repair_items.append(
                    RepairDetail(
                        device=device.device_label,
                        thread=f"{device.owner_name} <-> {contact.name}",
                        issues=[f.message for f in target_findings],
                        outcome="error",
                        reason=str(exc),
                    )
                )
                continue

            if repair_quota_hit:
                timeline_repair_items.append(
                    RepairDetail(
                        device=device.device_label,
                        thread=f"{device.owner_name} <-> {contact.name}",
                        issues=[f.message for f in target_findings],
                        outcome="quota_hit",
                        reason="API quota exhausted during repair.",
                    )
                )
                break

            if repaired_messages:
                nodes_list = list(ds.nodes)
                validation.replace_direct_thread(nodes_list, device.owner_actor_id, target_actor_id, repaired_messages)
                updated_ds = SmsDataset(nodes=nodes_list, actors=list(ds.actors))
                persistence.save_device_data(
                    scenario.id,
                    device.id,
                    updated_ds,
                    device.device_label,
                    device_number_by_id.get(device.id),
                )
                datasets_by_device[device.id] = updated_ds
                ds = updated_ds
                device_repairs += 1
                timeline_threads_repaired += 1
                timeline_repair_items.append(
                    RepairDetail(
                        device=device.device_label,
                        thread=f"{device.owner_name} <-> {contact.name}",
                        issues=[f.message for f in target_findings],
                        outcome="repaired",
                        messages_produced=len(repaired_messages),
                    )
                )
            else:
                timeline_repair_items.append(
                    RepairDetail(
                        device=device.device_label,
                        thread=f"{device.owner_name} <-> {contact.name}",
                        issues=[f.message for f in target_findings],
                        outcome="empty",
                        reason="LLM returned no messages during repair.",
                    )
                )

    if timeline_threads_repaired > 0:
        adjustments.append(f"Repaired {timeline_threads_repaired} thread(s) with timeline event-consistency issues.")
        resolution_items.append(
            ResolutionItem(
                issue="Timeline event-message consistency violations in generated data.",
                action=(
                    "Audited all devices for event alignment, identified mismatched threads, "
                    "and regenerated them with targeted repair prompts."
                ),
                result=f"Repaired {timeline_threads_repaired} thread(s). Details below.",
                repair_details=timeline_repair_items,
            )
        )
    elif timeline_repair_items:
        resolution_items.append(
            ResolutionItem(
                issue="Timeline event-message consistency violations detected but could not be fully resolved.",
                action="Attempted repair on mismatched threads.",
                result="Some repairs failed or were skipped. See details.",
                repair_details=timeline_repair_items,
            )
        )


def _apply_temporal_sort(
    scenario: ScenarioConfig,
    datasets_by_device: dict[str, SmsDataset],
    device_number_by_id: dict[str, int],
    adjustments: list[str],
    resolution_items: list[ResolutionItem],
) -> None:
    """Sort messages within each conversation thread by ascending timestamp.

    Iterates every node in every device dataset and checks whether the
    ``TransferTime`` fields are already in non-decreasing order.  Threads
    with fewer than :data:`_MIN_MESSAGES_FOR_SORT` messages are skipped.
    When out-of-order messages are detected the thread is re-sorted and
    the device dataset is persisted to disk.

    Args:
        scenario (ScenarioConfig): Active scenario configuration (used
            for the scenario ID during persistence).
        datasets_by_device (dict[str, SmsDataset]): Map of device ID to
            generated dataset — datasets are mutated in-place when
            threads are re-sorted.
        device_number_by_id (dict[str, int]): Map of device ID to
            1-based device number for file-naming.
        adjustments (list[str]): Mutable adjustment summary list.
        resolution_items (list[ResolutionItem]): Mutable resolution log.

    """
    threads_sorted = 0
    for device in scenario.devices:
        ds = datasets_by_device.get(device.id)
        if not ds or not ds.nodes:
            continue
        device_changed = False
        for node in ds.nodes:
            if len(node.message_content) < _MIN_MESSAGES_FOR_SORT:
                continue
            sorted_msgs = sorted(node.message_content, key=lambda m: m.TransferTime or "")
            if any(s.TransferTime != o.TransferTime for s, o in zip(sorted_msgs, node.message_content, strict=False)):
                node.message_content = sorted_msgs
                device_changed = True
                threads_sorted += 1
        if device_changed:
            persistence.save_device_data(
                scenario.id,
                device.id,
                ds,
                device.device_label,
                device_number_by_id.get(device.id),
            )
    if threads_sorted > 0:
        adjustments.append(f"Sorted messages in {threads_sorted} thread(s) to fix non-monotonic timestamps.")
        resolution_items.append(
            ResolutionItem(
                issue="Non-monotonic timestamps in generated threads.",
                action="Sorted messages within each thread by TransferTime.",
                result=f"Fixed ordering in {threads_sorted} thread(s).",
            )
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def execute_quality_check(
    scenario: ScenarioConfig,
    auto_adjust: bool,
    persist_scenario_fn: Callable[[ScenarioConfig], Any],
) -> dict[str, Any]:
    """Run an on-demand quality check on the scenario with optional auto-fix.

    Evaluates the scenario configuration and any existing generated data
    against the full quality rubric.  When *auto_adjust* is True, delegates
    to specialized fix functions for structural repairs, AI-assisted
    personality and arc regeneration, timeline event-message consistency
    repair, and temporal sorting before re-evaluating.

    The *persist_scenario_fn* callback is invoked after AI-assisted fixes
    to save the updated scenario back to the application state and disk.
    This avoids a direct import of ``source.app``.

    Args:
        scenario (ScenarioConfig): The active scenario (mutated in-place
            when auto_adjust is True).
        auto_adjust (bool): Whether to apply fixes before re-evaluation.
        persist_scenario_fn (Callable[[ScenarioConfig], Any]): Callback
            that persists the scenario to app state and disk after fixes.

    Returns:
        A JSON-serializable dict with before/after quality reports,
        adjustments summary, readiness, and resolution writeup.

    """
    device_number_by_id: dict[str, int] = {dev.id: idx + 1 for idx, dev in enumerate(scenario.devices)}
    datasets_by_device: dict[str, SmsDataset] = {}
    for device in scenario.devices:
        existing = persistence.load_existing_device_data(scenario.id, device.id)
        if existing:
            datasets_by_device[device.id] = existing

    report_before = quality_checks.evaluate_generation_quality(scenario, datasets_by_device)
    adjustments: list[str] = []
    resolution_items: list[ResolutionItem] = []

    if auto_adjust:
        _apply_structural_fixes(scenario, adjustments, resolution_items)
        await _apply_personality_fixes(scenario, adjustments, resolution_items)
        await _apply_arc_fixes(scenario, adjustments, resolution_items)
        persist_scenario_fn(scenario)
        _repair_timeline_threads(scenario, datasets_by_device, device_number_by_id, adjustments, resolution_items)
        _apply_temporal_sort(scenario, datasets_by_device, device_number_by_id, adjustments, resolution_items)

    report_after = quality_checks.evaluate_generation_quality(scenario, datasets_by_device)
    quality_report_path = persistence.save_quality_report(f"{scenario.id}_quality_check", report_after)
    readiness = _scenario_readiness_summary(scenario)
    before_problem_count = report_before.summary.critical_count + report_before.summary.warning_count
    after_problem_count = report_after.summary.critical_count + report_after.summary.warning_count
    resolved_estimate = max(0, before_problem_count - after_problem_count)
    if auto_adjust and not resolution_items:
        resolution_items.append(
            ResolutionItem(
                issue="No blocking issue required an automatic edit.",
                action="Ran structural, AI quality, and timeline-repair passes against scenario and generated data.",
                result="No changes were necessary.",
            )
        )
    writeup = ResolutionWriteup(
        before_problem_count=before_problem_count,
        after_problem_count=after_problem_count,
        resolved_estimate=resolved_estimate,
        items=resolution_items,
    )

    return {
        "mode": "report_only",
        "auto_adjust_applied": auto_adjust,
        "adjustments": adjustments,
        "before": report_before.model_dump(),
        "after": report_after.model_dump(),
        "quality_report_path": str(quality_report_path),
        "readiness": readiness,
        "resolution_writeup": writeup.model_dump(),
    }
