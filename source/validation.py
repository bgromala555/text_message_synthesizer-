"""Event-message consistency validation and thread repair helpers.

Validates that generated messages around timeline events use the correct
encounter semantics (planned, chance_encounter, near_miss) and provides
repair utilities for fixing misaligned threads.

The module contains four public functions:

1. ``validate_event_message_consistency`` — checks keyword presence around
   event dates against the expected encounter type.
2. ``build_repair_feedback`` — converts consistency findings into
   prompt-level repair guidance for the LLM.
3. ``replace_direct_thread`` — replaces or creates a direct owner-to-target
   thread in a list of conversation nodes.
4. ``audit_device_event_alignment`` — audits all event-linked contacts on
   one device and returns quality findings for any mismatches.
"""

from __future__ import annotations

import logging

from messageviewer.models import ConversationNode, Message
from source.events import (
    ConversationEvent,
    event_window_text,
    extract_conversation_events,
    get_encounter_terms,
)
from source.models import DeviceScenario, FlexTimelineEvent
from source.quality_models import QualityCheckId, QualityFinding, QualitySeverity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event-message consistency validation
# ---------------------------------------------------------------------------


def validate_event_message_consistency(
    messages: list[Message],
    events: list[ConversationEvent],
    entity_id: str,
    language: str = "en",
) -> list[QualityFinding]:
    """Validate message behavior against event encounter semantics.

    Checks that messages around each event date match the expected
    encounter type (near-miss, chance, planned).  Keyword matching is
    language-aware — Arabic, French, and English terms are all checked
    when the scenario language is non-English.

    For each primary (non-secondary) event the function examines:

    - **near_miss** — should NOT contain direct-encounter language but
      SHOULD contain delayed-discovery language after the event.
    - **chance_encounter** — should NOT show pre-planning language before
      the event date.
    - **planned** — SHOULD show scheduling/coordination language before
      the event date.

    Args:
        messages: The conversation messages for this thread.
        events: Timeline events relevant to this thread.
        entity_id: Identifier for the thread (e.g. 'owner->contact').
        language: ISO 639-1 language code for keyword matching.

    Returns:
        List of quality findings for any semantic mismatches.

    """
    findings: list[QualityFinding] = []
    if not messages or not events:
        return findings

    terms = get_encounter_terms(language)
    planning_terms = terms.get("planning", ())
    direct_encounter_terms = terms.get("direct_encounter", ())
    discovery_terms = terms.get("discovery", ())

    for ev in events:
        # Encounter-type semantics don't apply to secondary/ripple events
        # because the contact in this thread wasn't at the event itself.
        if ev.is_secondary:
            continue
        before_text, after_text = event_window_text(messages, ev.date)
        if ev.encounter_type == "near_miss":
            has_direct_meet = any(term in after_text for term in direct_encounter_terms)
            has_discovery = any(term in after_text for term in discovery_terms)
            if has_direct_meet:
                findings.append(
                    QualityFinding(
                        check_id=QualityCheckId.ARC_EVENT_CONSISTENCY,
                        severity=QualitySeverity.CRITICAL,
                        score=0.25,
                        scope="thread",
                        entity_id=entity_id,
                        message=f"Near-miss event on {ev.date} is written like a direct meetup.",
                        suggestion="Rewrite event-adjacent messages so they discover overlap later instead of meeting directly.",
                    )
                )
            elif not has_discovery:
                findings.append(
                    QualityFinding(
                        check_id=QualityCheckId.ARC_EVENT_CONSISTENCY,
                        severity=QualitySeverity.WARNING,
                        score=0.55,
                        scope="thread",
                        entity_id=entity_id,
                        message=f"Near-miss event on {ev.date} lacks later discovery messaging.",
                        suggestion="Add post-event discovery lines (e.g., 'wait you were there too?').",
                    )
                )
        elif ev.encounter_type == "chance_encounter":
            planned_before = any(term in before_text for term in planning_terms)
            if planned_before:
                findings.append(
                    QualityFinding(
                        check_id=QualityCheckId.ARC_EVENT_CONSISTENCY,
                        severity=QualitySeverity.WARNING,
                        score=0.50,
                        scope="thread",
                        entity_id=entity_id,
                        message=f"Chance-encounter event on {ev.date} shows pre-planning text.",
                        suggestion="Remove prior coordination and frame post-event messages as surprise.",
                    )
                )
        elif ev.encounter_type == "planned":
            planned_before = any(term in before_text for term in planning_terms)
            if not planned_before:
                findings.append(
                    QualityFinding(
                        check_id=QualityCheckId.ARC_EVENT_CONSISTENCY,
                        severity=QualitySeverity.WARNING,
                        score=0.60,
                        scope="thread",
                        entity_id=entity_id,
                        message=f"Planned event on {ev.date} lacks any scheduling/coordination beforehand.",
                        suggestion="Add brief pre-event logistics so the planned encounter feels causal.",
                    )
                )

    return findings


# ---------------------------------------------------------------------------
# Repair helpers
# ---------------------------------------------------------------------------


def build_repair_feedback(findings: list[QualityFinding]) -> str:
    """Convert consistency findings into prompt-level repair guidance.

    Takes the first six findings and formats them as actionable instructions
    suitable for inclusion in an LLM repair prompt.  Each finding's message
    and suggestion are presented as a bullet list.

    Args:
        findings: Quality findings from ``validate_event_message_consistency``
            or ``audit_device_event_alignment``.

    Returns:
        A newline-joined string of repair instructions, starting with a
        header line and followed by up to six bulleted items.

    """
    lines = ["Fix all event/message consistency issues listed below:"]
    for finding in findings[:6]:
        lines.append(f"- {finding.message}")
        if finding.suggestion:
            lines.append(f"  Required fix: {finding.suggestion}")
    return "\n".join(lines)


def replace_direct_thread(
    nodes: list[ConversationNode],
    owner_actor_id: str,
    target_actor_id: str,
    messages: list[Message],
) -> None:
    """Replace (or create) a direct owner-to-target thread in-place.

    Searches the conversation node list for a direct (single-target) thread
    from the owner to the specified target.  If found, its message content
    is replaced.  If no matching node exists, a new one is appended.

    Args:
        nodes: Mutable list of conversation nodes for one device.
        owner_actor_id: Actor ID of the device owner (source of the thread).
        target_actor_id: Actor ID of the contact (single target of the thread).
        messages: Replacement message list for this thread.

    """
    for node in nodes:
        if node.source == owner_actor_id and len(node.target) == 1 and node.target[0] == target_actor_id:
            node.message_content = messages
            return
    nodes.append(ConversationNode(source=owner_actor_id, target=[target_actor_id], type="SMS", message_content=messages))


# ---------------------------------------------------------------------------
# Device-level event alignment audit
# ---------------------------------------------------------------------------


def audit_device_event_alignment(
    device: DeviceScenario,
    nodes: list[ConversationNode],
    timeline_events: list[FlexTimelineEvent],
    language: str = "en",
) -> list[QualityFinding]:
    """Audit one device to ensure event-driven threads reflect intended encounters.

    Checks every contact that participates in at least one timeline event
    and verifies the thread messages use the correct encounter semantics
    (near-miss, planned, chance).  Language-aware keyword matching ensures
    non-English scenarios are evaluated correctly.

    For contacts with relevant events but no corresponding thread, a
    critical finding is generated (or warning if only secondary events
    involve the contact).

    Args:
        device: The device scenario to audit.
        nodes: Generated conversation nodes for this device.
        timeline_events: Full scenario timeline events.
        language: ISO 639-1 code for keyword matching (e.g. 'ar').

    Returns:
        List of quality findings for any misaligned threads.

    """
    findings: list[QualityFinding] = []
    by_target: dict[str, ConversationNode] = {}
    for node in nodes:
        if node.source == device.owner_actor_id and len(node.target) == 1 and node.message_content:
            by_target[node.target[0]] = node

    for contact in device.contacts:
        conv_events = extract_conversation_events(device, contact.actor_id, contact.name, timeline_events)
        if not conv_events:
            continue
        thread = by_target.get(contact.actor_id)
        if not thread:
            has_primary = any(not e.is_secondary for e in conv_events)
            findings.append(
                QualityFinding(
                    check_id=QualityCheckId.ARC_EVENT_CONSISTENCY,
                    severity=QualitySeverity.CRITICAL if has_primary else QualitySeverity.WARNING,
                    score=0.30 if has_primary else 0.55,
                    scope="thread",
                    entity_id=f"{device.owner_actor_id}->{contact.actor_id}",
                    message=f"Expected event-linked thread missing for contact '{contact.name or contact.actor_id}'.",
                    suggestion="Generate or repair this direct thread before continuing to next device.",
                )
            )
            continue
        findings.extend(
            validate_event_message_consistency(
                messages=thread.message_content,
                events=conv_events,
                entity_id=f"{device.owner_actor_id}->{contact.actor_id}",
                language=language,
            )
        )
    return findings
