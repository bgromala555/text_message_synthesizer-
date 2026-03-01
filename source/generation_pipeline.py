"""Core generation orchestration for the Synthesized Chat Generator pipeline.

Contains the SSE transport helper, the ``_GenerationRun`` state class, and
all extracted sub-functions that drive the device-by-device,
contact-by-contact generation loop.  The public entry point is
:func:`run_pipeline`, which wraps the full event-stream generator for
consumption by the route handler in ``source.generator``.

Streaming progress is communicated via Server-Sent Events so the browser
can render real-time token output and per-contact completion signals.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from pathlib import Path

from messageviewer.models import Actor, ConversationNode, Message, SmsDataset
from source import (
    conversation,
    events,
    persistence,
    quality_checks,
    repair,
    spam,
    validation,
)
from source.llm_client import QuotaExhaustedError
from source.models import (
    DeviceScenario,
    FlexTimelineEvent,
    GroupChat,
    RepairDetail,
    RunLogEntry,
    ScenarioConfig,
    ScenarioContext,
)
from source.quality_models import QualitySeverity

logger = logging.getLogger(__name__)

# Number of token fragments to buffer before flushing an SSE "tokens" event.
STREAM_FLUSH_THRESHOLD: int = 20


# ---------------------------------------------------------------------------
# SSE transport helper
# ---------------------------------------------------------------------------


def _sse(event_type: str, **kwargs: object) -> str:
    """Format a Server-Sent Event payload as a ``data:`` line.

    Produces a single SSE frame with a JSON body containing a ``type``
    field and any additional keyword arguments.  Callers yield these
    strings into a :class:`StreamingResponse` to push real-time progress
    updates to the browser.

    Args:
        event_type (str): The event type label (e.g. ``"device_start"``).
        **kwargs: Arbitrary additional fields merged into the JSON body.

    Returns:
        A fully formatted SSE data line ending with a double newline.

    """
    return f"data: {json.dumps({'type': event_type, **kwargs})}\n\n"


# ---------------------------------------------------------------------------
# Private helpers — used exclusively by the generation pipeline
# ---------------------------------------------------------------------------


def _event_date_for_group(scenario: ScenarioConfig, group_chat: GroupChat) -> str:
    """Resolve the effective start date for a group chat.

    Prefers the explicit ``start_date`` on the group chat.  When absent
    and ``origin_event_id`` is set, the originating timeline event's date
    is used instead.  Falls back to an empty string when neither source
    is available.

    Args:
        scenario (ScenarioConfig): Full scenario config providing access
            to timeline events for origin-event lookup.
        group_chat (GroupChat): The group chat whose start date is needed.

    Returns:
        An ISO date string, or ``""`` when no date can be determined.

    """
    if group_chat.start_date:
        return group_chat.start_date
    if group_chat.origin_event_id:
        ev = next((e for e in scenario.timeline_events if e.id == group_chat.origin_event_id), None)
        if ev and ev.date:
            return ev.date
    return ""


def _thread_exists(nodes: list[ConversationNode], owner_actor_id: str, target_actor_id: str) -> bool:
    """Check whether a direct owner-to-target conversation thread already exists.

    Scans the node list for a single-target thread sourced from the owner
    that has at least one message.  Used to avoid creating duplicate pair
    threads during group-chat auto-pair generation.

    Args:
        nodes (list[ConversationNode]): Existing conversation nodes for a device.
        owner_actor_id (str): The device owner's actor ID.
        target_actor_id (str): The contact's actor ID.

    Returns:
        True if a matching non-empty thread exists, False otherwise.

    """
    return any(
        node.source == owner_actor_id and len(node.target) == 1 and node.target[0] == target_actor_id and node.message_content
        for node in nodes
    )


def count_existing_conversations(dataset: SmsDataset) -> set[str]:
    """Return the set of contact actor IDs that already have conversations.

    Scans every conversation node in the dataset and collects the target
    actor IDs of nodes that contain at least one message.  Used during
    resume to determine which contacts can be skipped.

    Args:
        dataset (SmsDataset): Previously loaded device dataset.

    Returns:
        Set of actor IDs whose threads have at least one message.

    """
    completed: set[str] = set()
    for node in dataset.nodes:
        if node.message_content:
            for t in node.target:
                completed.add(t)
    return completed


# ---------------------------------------------------------------------------
# Generation run state and extracted sub-functions
# ---------------------------------------------------------------------------


class _GenerationRun:
    """Shared mutable state for a single SSE generation pipeline run.

    Encapsulates the scenario, configuration flags, and runtime tracking
    (quota status, validation status, run log, generated datasets) so that
    extracted sub-functions can read and mutate a single context object
    instead of passing dozens of locals through closure.

    Attributes:
        scenario: Deep copy of the active scenario configuration.
        settings: Generation settings pulled from the scenario.
        total_devices: Number of devices in the scenario.
        resume: Whether the pipeline is resuming from a previous partial run.
        override_checks: Whether to skip the resume quality pre-check.
        max_consistency_retries: Max LLM retries for event-consistency repair.
        continue_on_error: Whether to continue generating after a contact error.
        enforce_event_consistency: Whether to validate event references.
        auto_repair_consistency: Whether to auto-repair inconsistent threads.
        strict_device_event_gate: Whether to run the post-device event gate.
        max_device_gate_repairs: Max threads repaired per device in the gate.
        run_log_entries: Structured log entries for the generation run.
        quota_exhausted: True when the LLM API quota is exceeded.
        validation_blocked: True when critical event issues block continuation.
        validation_block_reason: Human-readable reason for validation_blocked.
        generated_datasets: Map of device_id to SmsDataset for completed devices.
        gen_start: Monotonic timestamp marking the start of generation.
        last_saved_path: Path of the most recent device save for device_done.

    """

    def __init__(self, scenario: ScenarioConfig, resume: bool, override_checks: bool) -> None:
        """Initialize generation run state from a scenario.

        Args:
            scenario (ScenarioConfig): Deep copy of the active scenario.
            resume (bool): Whether to resume from a previous partial run.
            override_checks (bool): Whether to skip the resume quality
                pre-check.

        """
        self.scenario = scenario
        self.settings = scenario.generation_settings
        self.total_devices: int = len(scenario.devices)
        self.resume = resume
        self.override_checks = override_checks

        self.max_consistency_retries: int = 3
        self.continue_on_error: bool = True
        self.enforce_event_consistency: bool = True
        self.auto_repair_consistency: bool = True
        self.strict_device_event_gate: bool = True
        self.max_device_gate_repairs: int = 4

        self.run_log_entries: list[RunLogEntry] = []
        self.quota_exhausted: bool = False
        self.validation_blocked: bool = False
        self.validation_block_reason: str = ""
        self.generated_datasets: dict[str, SmsDataset] = {}
        self.gen_start: float = time.time()
        self.last_saved_path: Path | None = None

    def record(self, level: str, event_type: str, payload: dict[str, object] | None = None) -> None:
        """Append a structured entry to the generation run log.

        Args:
            level (str): Log severity (info, warning, error).
            event_type (str): Machine-readable event category.
            payload (dict[str, object] | None): Arbitrary data for the entry.

        """
        self.run_log_entries.append(
            RunLogEntry(
                timestamp=datetime.now(tz=UTC).isoformat(),
                level=level,
                event_type=event_type,
                payload=payload or {},
            )
        )


def _check_resume_preconditions(run: _GenerationRun) -> Generator[str, None, None]:
    """Validate existing generated data quality before allowing a resume.

    Loads every device's saved output, evaluates the full quality rubric,
    and yields a ``resume_blocked`` SSE event when blocking findings
    (CRITICAL or WARNING) are detected.  When the generator yields an
    event the caller should abort the event stream.

    Args:
        run (_GenerationRun): Active generation run state.

    Yields:
        At most one ``resume_blocked`` SSE event string.

    """
    existing_by_device: dict[str, SmsDataset] = {}
    for dev in run.scenario.devices:
        existing_dataset = persistence.load_existing_device_data(run.scenario.id, dev.id)
        if existing_dataset:
            existing_by_device[dev.id] = existing_dataset
    if not existing_by_device:
        return

    resume_precheck = quality_checks.evaluate_generation_quality(run.scenario, existing_by_device)
    resume_precheck_path = persistence.save_quality_report(f"{run.scenario.id}_resume_precheck", resume_precheck)
    blocking_findings = [f for f in resume_precheck.top_findings if f.severity in {QualitySeverity.CRITICAL, QualitySeverity.WARNING}]
    if not blocking_findings:
        return

    run.record(
        "warning",
        "resume_blocked",
        {
            "quality_report_path": str(resume_precheck_path),
            "findings_total": len(blocking_findings),
            "critical_count": resume_precheck.summary.critical_count,
            "warning_count": resume_precheck.summary.warning_count,
        },
    )
    yield _sse(
        "resume_blocked",
        message=(
            "Resume blocked: existing generated data does not fully match the current scenario. "
            "Review report, optionally run AI Quality Fix, then continue with override if approved."
        ),
        quality_report_path=str(resume_precheck_path),
        quality_summary=resume_precheck.summary.model_dump(),
        top_findings=[f.model_dump() for f in blocking_findings[:8]],
    )


async def _stream_single_contact(
    run: _GenerationRun,
    dev_idx: int,
    con_idx: int,
    device: DeviceScenario,
    device_ctx: ScenarioContext,
    device_timeline_events: list[FlexTimelineEvent],
    device_uses_story_context: bool,
    nodes: list[ConversationNode],
    actors: list[Actor],
) -> AsyncGenerator[str, None]:
    """Run streaming generation for one contact and yield SSE events.

    Uses :func:`conversation.generate_conversation_streaming` to obtain
    token-level deltas, buffers them, and periodically flushes ``tokens``
    SSE events.  On completion, appends the conversation to *nodes* and
    *actors*, saves the device dataset, and yields ``contact_done``.

    Args:
        run (_GenerationRun): Active generation run state.
        dev_idx (int): Zero-based device index.
        con_idx (int): Zero-based contact index.
        device (DeviceScenario): The device being generated.
        device_ctx (ScenarioContext): Bundled scenario context.
        device_timeline_events (list[FlexTimelineEvent]): Applicable events.
        device_uses_story_context (bool): Whether this device uses story mode.
        nodes (list[ConversationNode]): Mutable conversation node list.
        actors (list[Actor]): Mutable actor list.

    Yields:
        SSE event strings for streaming tokens, contact completion,
        quota exhaustion, and errors.

    """
    contact = device.contacts[con_idx]
    streaming_messages: list[Message] = []
    streaming_calls = 0
    streaming_quota_hit = False
    token_buffer: list[str] = []

    try:
        async for item in conversation.generate_conversation_streaming(
            device=device,
            contact_index=con_idx,
            settings=run.settings,
            theme=device_ctx.theme,
            culture=device_ctx.culture,
            timeline_events=device_timeline_events,
            story_arc=device_ctx.story_arc,
            language=device_ctx.language,
            include_story_context=device_uses_story_context,
        ):
            if isinstance(item, str):
                token_buffer.append(item)
                if len(token_buffer) >= STREAM_FLUSH_THRESHOLD:
                    yield _sse("tokens", content="".join(token_buffer), device=dev_idx + 1, contact=con_idx + 1)
                    token_buffer.clear()
            elif isinstance(item, tuple):
                streaming_messages, streaming_calls, streaming_quota_hit = item

        if token_buffer:
            yield _sse("tokens", content="".join(token_buffer), device=dev_idx + 1, contact=con_idx + 1)

    except QuotaExhaustedError:
        streaming_quota_hit = True
    except Exception as exc:
        run.record("error", "contact_error", {"device_label": device.device_label, "contact_name": contact.name, "error": str(exc)})
        yield _sse("contact_error", device=dev_idx + 1, contact=con_idx + 1, name=contact.name, error=str(exc))
        return

    if not any(a.ActorId == contact.actor_id for a in actors):
        actors.append(Actor(ActorId=contact.actor_id, Name=contact.name))
    if streaming_messages:
        nodes.append(
            ConversationNode(source=device.owner_actor_id, target=[contact.actor_id], type="SMS", message_content=streaming_messages)
        )

    yield _sse("contact_done", device=dev_idx + 1, contact=con_idx + 1, messages=len(streaming_messages), llm_calls=streaming_calls)

    dataset = SmsDataset(nodes=nodes, actors=actors)
    persistence.save_device_data(run.scenario.id, device.id, dataset, device.device_label, dev_idx + 1)

    if streaming_quota_hit:
        run.quota_exhausted = True
        yield _sse(
            "quota_exhausted",
            device=dev_idx + 1,
            contact=con_idx + 1,
            message="API quota exhausted. Progress saved — use Resume to continue later.",
        )


async def _generate_device_contacts(  # noqa: C901
    run: _GenerationRun,
    dev_idx: int,
    device: DeviceScenario,
    device_ctx: ScenarioContext,
    device_timeline_events: list[FlexTimelineEvent],
    device_uses_story_context: bool,
    nodes: list[ConversationNode],
    actors: list[Actor],
    completed_contacts: set[str],
) -> AsyncGenerator[str, None]:
    """Generate individual contact conversations for a single device.

    Iterates over every contact on the device, skipping already-completed
    contacts when resuming.  For each eligible contact, validates
    personality profiles, generates the conversation with consistency
    retries, appends results to *nodes* and *actors* (mutated in-place),
    and saves the device dataset incrementally after each contact.

    Sets ``run.quota_exhausted`` if the LLM API quota is hit.

    Args:
        run (_GenerationRun): Active generation run state.
        dev_idx (int): Zero-based device index in the scenario.
        device (DeviceScenario): The device being generated.
        device_ctx (ScenarioContext): Bundled scenario context for this device.
        device_timeline_events (list[FlexTimelineEvent]): Timeline events
            applicable to this device (empty for standalone mode).
        device_uses_story_context (bool): Whether this device uses story mode.
        nodes (list[ConversationNode]): Mutable list of conversation nodes
            for this device — appended to in-place.
        actors (list[Actor]): Mutable list of actors for this device —
            appended to in-place.
        completed_contacts (set[str]): Actor IDs already completed (resume).

    Yields:
        SSE event strings for contact progress, errors, warnings, and
        quality findings.

    """
    total_contacts = len(device.contacts)
    for con_idx, contact in enumerate(device.contacts):
        if run.quota_exhausted:
            break

        if run.resume and contact.actor_id in completed_contacts:
            run.record("info", "contact_skipped", {"device_label": device.device_label, "contact_name": contact.name})
            yield _sse("contact_skipped", device=dev_idx + 1, contact=con_idx + 1, name=contact.name)
            continue

        run.record("info", "contact_start", {"device_label": device.device_label, "contact_name": contact.name})
        yield _sse(
            "contact_start",
            device=dev_idx + 1,
            contact=con_idx + 1,
            total_contacts=total_contacts,
            name=contact.name,
        )

        if not conversation.profile_ready_for_generation(device.owner_personality):
            run.record("warning", "contact_skipped_no_owner_profile", {"device_label": device.device_label, "contact_name": contact.name})
            yield _sse(
                "contact_error",
                device=dev_idx + 1,
                contact=con_idx + 1,
                name=contact.name,
                error=(
                    "Device owner personality is missing or too thin for reliable generation. Run AI Quality Fix first, then regenerate."
                ),
            )
            continue
        if not conversation.profile_ready_for_generation(contact.personality):
            run.record("warning", "contact_skipped_no_profile", {"device_label": device.device_label, "contact_name": contact.name})
            yield _sse(
                "contact_error",
                device=dev_idx + 1,
                contact=con_idx + 1,
                name=contact.name,
                error=f"Contact {contact.name} personality is missing or too thin. Run AI Quality Fix first.",
            )
            continue

        contact_events = events.extract_conversation_events(device, contact.actor_id, contact.name, device_timeline_events)

        use_streaming = run.settings.streaming and not run.enforce_event_consistency

        if use_streaming:
            async for sse_event in _stream_single_contact(
                run, dev_idx, con_idx, device, device_ctx, device_timeline_events, device_uses_story_context, nodes, actors
            ):
                yield sse_event
            continue

        gen_result = await repair.generate_with_consistency_retries(
            device=device,
            contact_index=con_idx,
            settings=run.settings,
            context=device_ctx,
            timeline_events=device_timeline_events,
            include_story_context=device_uses_story_context,
            contact_events=contact_events,
            max_retries=run.max_consistency_retries,
            enforce_consistency=run.enforce_event_consistency,
            auto_repair=run.auto_repair_consistency,
        )

        if gen_result.error:
            run.record(
                "error",
                "contact_error",
                {"device_label": device.device_label, "contact_name": contact.name, "error": gen_result.error},
            )
            yield _sse("contact_error", device=dev_idx + 1, contact=con_idx + 1, name=contact.name, error=gen_result.error)
            if not run.continue_on_error:
                run.quota_exhausted = True
                break

        messages = gen_result.messages
        calls = gen_result.llm_calls
        quota_hit = gen_result.quota_hit

        if gen_result.retries_used > 0:
            run.record(
                "warning",
                "consistency_retry",
                {
                    "device_label": device.device_label,
                    "contact_name": contact.name,
                    "attempts": gen_result.retries_used + 1,
                    "findings": len(gen_result.consistency_findings),
                },
            )
            yield _sse(
                "quality_warning",
                check_id="arc_event_consistency",
                severity="warning",
                scope="thread",
                entity_id=f"{device.owner_actor_id}->{contact.actor_id}",
                message=f"Event/message consistency: {gen_result.retries_used} repair retries applied.",
            )

        for finding in gen_result.consistency_findings:
            if finding.severity in {QualitySeverity.CRITICAL, QualitySeverity.WARNING}:
                run.record(
                    "warning",
                    "consistency_finding",
                    {
                        "device_label": device.device_label,
                        "contact_name": contact.name,
                        "severity": finding.severity.value,
                        "message": finding.message,
                    },
                )
                yield persistence.finding_to_sse(finding)

        if not any(a.ActorId == contact.actor_id for a in actors):
            actors.append(Actor(ActorId=contact.actor_id, Name=contact.name))
        if messages:
            nodes.append(ConversationNode(source=device.owner_actor_id, target=[contact.actor_id], type="SMS", message_content=messages))
            for finding in quality_checks.quick_thread_findings(
                messages=messages,
                role=contact.role or "",
                language=run.settings.language or "en",
                entity_id=f"{device.owner_actor_id}->{contact.actor_id}",
            ):
                yield persistence.finding_to_sse(finding)

        yield _sse("contact_done", device=dev_idx + 1, contact=con_idx + 1, messages=len(messages), llm_calls=calls)

        dataset = SmsDataset(nodes=nodes, actors=actors)
        persistence.save_device_data(run.scenario.id, device.id, dataset, device.device_label, dev_idx + 1)

        if quota_hit:
            run.quota_exhausted = True
            yield _sse(
                "quota_exhausted",
                device=dev_idx + 1,
                contact=con_idx + 1,
                message="API quota exhausted. Progress saved — use Resume to continue later.",
            )
            break


async def _generate_device_group_chats(  # noqa: C901
    run: _GenerationRun,
    dev_idx: int,
    device: DeviceScenario,
    device_ctx: ScenarioContext,
    device_story_arc: str,
    device_timeline_events: list[FlexTimelineEvent],
    device_uses_story_context: bool,
    nodes: list[ConversationNode],
    actors: list[Actor],
) -> AsyncGenerator[str, None]:
    """Generate group conversations and auto pairwise threads for a device.

    Identifies group chats owned by this device, generates each group
    conversation via :func:`conversation.generate_group_conversation`,
    builds conversation nodes, and optionally creates direct pairwise
    threads between the device owner and each group member that does not
    already have one.

    Mutates *nodes* and *actors* in-place and saves incrementally.
    Sets ``run.quota_exhausted`` if the LLM API quota is hit.

    Args:
        run (_GenerationRun): Active generation run state.
        dev_idx (int): Zero-based device index in the scenario.
        device (DeviceScenario): The device being generated.
        device_ctx (ScenarioContext): Bundled scenario context.
        device_story_arc (str): Story arc applicable to this device.
        device_timeline_events (list[FlexTimelineEvent]): Timeline events
            applicable to this device (empty for standalone mode).
        device_uses_story_context (bool): Whether this device uses story mode.
        nodes (list[ConversationNode]): Mutable conversation node list.
        actors (list[Actor]): Mutable actor list.

    Yields:
        SSE event strings for group progress, errors, warnings, and
        quality findings.

    """
    if not device_uses_story_context:
        return

    owner_groups = [
        gc for gc in run.scenario.group_chats or [] if any(m.device_id == device.id and m.contact_id == "__owner__" for m in gc.members)
    ]
    for gc in owner_groups:  # noqa: PLR1702
        if run.quota_exhausted:
            break
        effective_start = _event_date_for_group(run.scenario, gc) or run.settings.date_start
        if gc.origin_event_id and not any(ev.id == gc.origin_event_id for ev in device_timeline_events):
            yield _sse(
                "quality_warning",
                check_id="group_event_coherence",
                severity="warning",
                score=0.55,
                scope="group",
                entity_id=gc.id,
                message=f"Group '{gc.name}' references missing origin event.",
                suggestion="Set a valid origin event or clear origin_event_id.",
            )
        yield _sse("group_start", device=dev_idx + 1, group_name=gc.name)

        gc_quota_hit = False
        try:
            gc.start_date = effective_start
            gc_messages, gc_calls, gc_quota_hit = await asyncio.to_thread(
                conversation.generate_group_conversation,
                device,
                gc,
                list(run.scenario.devices),
                run.settings,
                run.scenario.theme or "slice-of-life",
                run.scenario.culture or "american",
                device_story_arc,
                run.settings.language or "en",
            )
        except Exception as exc:
            logger.exception("Error generating group chat '%s'", gc.name)
            yield _sse("contact_error", device=dev_idx + 1, name=gc.name, error=str(exc))
            continue

        if gc_messages:
            gc_targets: list[str] = []
            for member in gc.members:
                m_dev = next((d for d in run.scenario.devices if d.id == member.device_id), None)
                if not m_dev:
                    continue
                if member.contact_id == "__owner__":
                    if m_dev.id != device.id:
                        gc_targets.append(m_dev.owner_actor_id)
                else:
                    mc = next((c for c in m_dev.contacts if c.id == member.contact_id), None)
                    if mc:
                        gc_targets.append(mc.actor_id)

            nodes.append(ConversationNode(source=device.owner_actor_id, target=gc_targets, type="SMS", message_content=gc_messages))
            for finding in quality_checks.quick_thread_findings(
                messages=gc_messages,
                role=gc.vibe or "group",
                language=run.settings.language or "en",
                entity_id=f"{device.owner_actor_id}->group:{gc.id}",
            ):
                yield persistence.finding_to_sse(finding)
            for aid in gc_targets:
                if not any(a.ActorId == aid for a in actors):
                    name = aid
                    for d in run.scenario.devices:
                        if d.owner_actor_id == aid:
                            name = d.owner_name
                            break
                        for c in d.contacts:
                            if c.actor_id == aid:
                                name = c.name
                                break
                    actors.append(Actor(ActorId=aid, Name=name))

        yield _sse("group_done", device=dev_idx + 1, group_name=gc.name, messages=len(gc_messages), llm_calls=gc_calls)

        dataset = SmsDataset(nodes=nodes, actors=actors)
        persistence.save_device_data(run.scenario.id, device.id, dataset, device.device_label, dev_idx + 1)

        if gc_quota_hit:
            run.quota_exhausted = True
            yield _sse("quota_exhausted", device=dev_idx + 1, message="API quota exhausted during group chat generation.")
            break

        if gc.auto_pair_threads and not run.quota_exhausted:
            async for pair_event in _generate_auto_pair_threads(
                run, dev_idx, device, device_ctx, device_timeline_events, device_uses_story_context, gc, nodes, actors
            ):
                yield pair_event


async def _generate_auto_pair_threads(
    run: _GenerationRun,
    dev_idx: int,
    device: DeviceScenario,
    device_ctx: ScenarioContext,
    device_timeline_events: list[FlexTimelineEvent],
    device_uses_story_context: bool,
    gc: GroupChat,
    nodes: list[ConversationNode],
    actors: list[Actor],
) -> AsyncGenerator[str, None]:
    """Create direct pairwise threads for group members on this device.

    For each group member belonging to the current device that does not
    already have a direct thread with the device owner, generates a
    one-on-one conversation and appends it to *nodes*.  Saves
    incrementally if any threads were created.

    Sets ``run.quota_exhausted`` if the LLM API quota is hit.

    Args:
        run (_GenerationRun): Active generation run state.
        dev_idx (int): Zero-based device index in the scenario.
        device (DeviceScenario): The device being generated.
        device_ctx (ScenarioContext): Bundled scenario context.
        device_timeline_events (list[FlexTimelineEvent]): Timeline events
            applicable to this device.
        device_uses_story_context (bool): Whether this device uses story mode.
        gc (GroupChat): The group chat whose members need pair threads.
        nodes (list[ConversationNode]): Mutable conversation node list.
        actors (list[Actor]): Mutable actor list.

    Yields:
        SSE event strings for pair thread errors, warnings, and completion.

    """
    pair_threads_created = 0
    for member in gc.members:
        if member.device_id != device.id:
            continue
        contact_id = member.contact_id
        if not contact_id or contact_id == "__owner__":
            continue
        c_idx = next((idx for idx, c in enumerate(device.contacts) if c.id == contact_id), -1)
        if c_idx < 0:
            continue
        target_actor_id = device.contacts[c_idx].actor_id
        if _thread_exists(nodes, device.owner_actor_id, target_actor_id):
            continue

        pair_contact = device.contacts[c_idx]
        pair_events = events.extract_conversation_events(device, pair_contact.actor_id, pair_contact.name, device_timeline_events)

        pair_result = await repair.generate_with_consistency_retries(
            device=device,
            contact_index=c_idx,
            settings=run.settings,
            context=device_ctx,
            timeline_events=device_timeline_events,
            include_story_context=device_uses_story_context,
            contact_events=pair_events,
            max_retries=run.max_consistency_retries,
            enforce_consistency=run.enforce_event_consistency,
            auto_repair=run.auto_repair_consistency,
        )

        if pair_result.error:
            logger.error("Error auto-generating pair thread for group '%s': %s", gc.name, pair_result.error)
            run.record("error", "pair_thread_error", {"group_name": gc.name, "error": pair_result.error})
            yield _sse("contact_error", device=dev_idx + 1, name=gc.name, error=pair_result.error)
        else:
            if pair_result.retries_used > 0:
                run.record(
                    "warning",
                    "pair_consistency_retry",
                    {
                        "group_name": gc.name,
                        "attempts": pair_result.retries_used + 1,
                        "findings": len(pair_result.consistency_findings),
                    },
                )
                yield _sse(
                    "quality_warning",
                    check_id="arc_event_consistency",
                    severity="warning",
                    scope="thread",
                    entity_id=f"{device.owner_actor_id}->{target_actor_id}",
                    message=f"Pairwise thread: {pair_result.retries_used} consistency repair retries applied.",
                )
            for finding in pair_result.consistency_findings:
                if finding.severity in {QualitySeverity.CRITICAL, QualitySeverity.WARNING}:
                    run.record(
                        "warning",
                        "pair_consistency_finding",
                        {
                            "group_name": gc.name,
                            "severity": finding.severity.value,
                            "message": finding.message,
                        },
                    )
                    yield persistence.finding_to_sse(finding)

            if pair_result.messages:
                nodes.append(
                    ConversationNode(
                        source=device.owner_actor_id,
                        target=[target_actor_id],
                        type="SMS",
                        message_content=pair_result.messages,
                    )
                )
                pair_threads_created += 1
                for finding in quality_checks.quick_thread_findings(
                    messages=pair_result.messages,
                    role=device.contacts[c_idx].role or "",
                    language=run.settings.language or "en",
                    entity_id=f"{device.owner_actor_id}->{target_actor_id}",
                ):
                    yield persistence.finding_to_sse(finding)

        if pair_result.quota_hit:
            run.quota_exhausted = True
            yield _sse(
                "quota_exhausted",
                device=dev_idx + 1,
                message="API quota exhausted while creating auto pair threads.",
            )
            break

    if pair_threads_created:
        dataset = SmsDataset(nodes=nodes, actors=actors)
        persistence.save_device_data(run.scenario.id, device.id, dataset, device.device_label, dev_idx + 1)
        yield _sse("pairwise_done", device=dev_idx + 1, group_name=gc.name, threads=pair_threads_created)


async def _run_device_event_gate(
    run: _GenerationRun,
    dev_idx: int,
    device: DeviceScenario,
    device_ctx: ScenarioContext,
    device_timeline_events: list[FlexTimelineEvent],
    device_uses_story_context: bool,
    nodes: list[ConversationNode],
    actors: list[Actor],
) -> AsyncGenerator[str, None]:
    """Verify event-linked behaviour and repair broken threads post-device.

    Runs the device event-alignment audit, emits findings for all CRITICAL
    and WARNING issues, and attempts targeted regeneration of the worst
    offending threads.  After repair, re-audits and emits a
    ``device_event_validation`` summary event.

    Updates ``run.last_saved_path``, ``run.generated_datasets``,
    ``run.validation_blocked``, and ``run.validation_block_reason`` as
    side effects when repairs are applied or blocking issues remain.

    Args:
        run (_GenerationRun): Active generation run state.
        dev_idx (int): Zero-based device index in the scenario.
        device (DeviceScenario): The device to validate.
        device_ctx (ScenarioContext): Bundled scenario context.
        device_timeline_events (list[FlexTimelineEvent]): Timeline events
            applicable to this device.
        device_uses_story_context (bool): Whether this device uses story.
        nodes (list[ConversationNode]): Mutable conversation node list.
        actors (list[Actor]): Mutable actor list (unused but kept for
            consistency with dataset rebuilds).

    Yields:
        SSE event strings for findings, repairs, and validation summary.

    """
    if not run.strict_device_event_gate or run.quota_exhausted:
        return

    gate_before = validation.audit_device_event_alignment(device, nodes, device_timeline_events, language=run.settings.language or "en")
    gate_all_issues = [f for f in gate_before if f.severity in {QualitySeverity.CRITICAL, QualitySeverity.WARNING}]
    gate_blocking_before = [f for f in gate_before if f.severity == QualitySeverity.CRITICAL]
    for finding in gate_all_issues:
        run.record(
            "warning",
            "device_gate_finding",
            {"device_label": device.device_label, "severity": finding.severity.value, "message": finding.message},
        )
        yield persistence.finding_to_sse(finding)

    repaired_threads = 0
    repair_detail_items: list[RepairDetail] = []
    if gate_blocking_before:
        targets: list[str] = []
        for finding in gate_blocking_before:
            if "->" in finding.entity_id:
                _, target_id = finding.entity_id.split("->", 1)
                if target_id and target_id not in targets:
                    targets.append(target_id)

        for target_actor_id in targets[: run.max_device_gate_repairs]:
            c_idx = next((idx for idx, c in enumerate(device.contacts) if c.actor_id == target_actor_id), -1)
            if c_idx < 0:
                continue
            contact_name = device.contacts[c_idx].name or target_actor_id
            target_findings = [f for f in gate_blocking_before if f.entity_id.endswith(f"->{target_actor_id}")]
            gate_feedback = validation.build_repair_feedback(target_findings) if target_findings else ""
            run.record(
                "info",
                "device_gate_repair_attempt",
                {
                    "device_label": device.device_label,
                    "target_actor_id": target_actor_id,
                    "contact_name": contact_name,
                    "triggering_findings": [
                        {"message": f.message, "suggestion": f.suggestion, "severity": f.severity.value} for f in target_findings
                    ],
                    "repair_feedback_prompt": gate_feedback,
                },
            )

            gate_result = await repair.generate_with_consistency_retries(
                device=device,
                contact_index=c_idx,
                settings=run.settings,
                context=device_ctx,
                timeline_events=device_timeline_events,
                include_story_context=device_uses_story_context,
                contact_events=[],
                max_retries=1,
                enforce_consistency=False,
                initial_feedback=gate_feedback,
            )

            if gate_result.error:
                run.record(
                    "error",
                    "device_gate_repair_error",
                    {
                        "device_label": device.device_label,
                        "target_actor_id": target_actor_id,
                        "error": gate_result.error,
                    },
                )
                repair_detail_items.append(
                    RepairDetail(
                        thread=f"{device.owner_name} <-> {contact_name}",
                        device=device.device_label,
                        issues=[f.message for f in target_findings],
                        outcome="error",
                        reason=gate_result.error,
                    )
                )
                continue

            if gate_result.quota_hit:
                run.quota_exhausted = True
                break

            if gate_result.messages:
                validation.replace_direct_thread(nodes, device.owner_actor_id, target_actor_id, gate_result.messages)
                repaired_threads += 1
                repair_detail_items.append(
                    RepairDetail(
                        thread=f"{device.owner_name} <-> {contact_name}",
                        device=device.device_label,
                        issues=[f.message for f in target_findings],
                        outcome="regenerated",
                        messages_produced=len(gate_result.messages),
                    )
                )
            else:
                repair_detail_items.append(
                    RepairDetail(
                        thread=f"{device.owner_name} <-> {contact_name}",
                        device=device.device_label,
                        issues=[f.message for f in target_findings],
                        outcome="empty",
                        reason="LLM returned no messages during repair.",
                    )
                )

        dataset = SmsDataset(nodes=nodes, actors=actors)
        run.last_saved_path = persistence.save_device_data(run.scenario.id, device.id, dataset, device.device_label, dev_idx + 1)
        run.generated_datasets[device.id] = dataset

    gate_after = validation.audit_device_event_alignment(device, nodes, device_timeline_events, language=run.settings.language or "en")
    gate_blocking_after = [f for f in gate_after if f.severity == QualitySeverity.CRITICAL]
    gate_warnings_after = [f for f in gate_after if f.severity == QualitySeverity.WARNING]

    for finding in gate_blocking_after + gate_warnings_after:
        run.record(
            "warning",
            "device_gate_post_repair_finding",
            {
                "device_label": device.device_label,
                "severity": finding.severity.value,
                "message": finding.message,
                "suggestion": finding.suggestion,
                "entity_id": finding.entity_id,
            },
        )
        yield persistence.finding_to_sse(finding)

    gate_data: dict[str, object] = {
        "device": dev_idx + 1,
        "label": device.device_label,
        "issues_before": len(gate_blocking_before),
        "issues_after": len(gate_blocking_after),
        "warnings_after": len(gate_warnings_after),
        "repaired_threads": repaired_threads,
        "passed": len(gate_blocking_after) == 0,
        "remaining_findings": [
            {"message": f.message, "suggestion": f.suggestion, "severity": f.severity.value, "entity_id": f.entity_id}
            for f in gate_blocking_after + gate_warnings_after
        ],
        "repair_details": [rd.model_dump() for rd in repair_detail_items],
    }
    yield _sse("device_event_validation", **gate_data)
    run.record("info", "device_event_validation", gate_data)

    if gate_blocking_after:
        run.validation_blocked = True
        remaining_detail = "; ".join(f.message for f in gate_blocking_after)
        run.validation_block_reason = (
            f"Stopped after {device.device_label}: {len(gate_blocking_after)} unresolved CRITICAL "
            f"event-consistency issue(s) remained after repair. Details: {remaining_detail}"
        )


def _finalize_generation(run: _GenerationRun) -> Generator[str, None, None]:
    """Emit post-generation quality report, manifest, and completion events.

    Evaluates the final quality rubric across all generated datasets,
    persists the quality report and scenario manifest, saves the run log,
    and yields the appropriate terminal SSE event (``complete``,
    ``stopped`` due to validation, or ``stopped`` due to quota).

    Args:
        run (_GenerationRun): Active generation run state.

    Yields:
        SSE event strings for quality findings and the terminal event.

    """
    quality_report = quality_checks.evaluate_generation_quality(run.scenario, run.generated_datasets)
    quality_report_path = persistence.save_quality_report(run.scenario.id, quality_report)
    for finding in quality_report.top_findings:
        if finding.severity in {QualitySeverity.CRITICAL, QualitySeverity.WARNING}:
            yield persistence.finding_to_sse(finding)

    persistence.save_scenario_manifest(
        run.scenario,
        persistence.OUTPUT_DIR,
        time.time() - run.gen_start,
        quality_summary=quality_report.summary.model_dump(),
    )
    run_log_path = persistence.save_run_log(run.scenario.id, [e.model_dump() for e in run.run_log_entries], run.gen_start)

    if run.validation_blocked:
        yield _sse(
            "stopped",
            message=run.validation_block_reason,
            run_log_path=str(run_log_path),
            quality_report_path=str(quality_report_path),
            quality_summary=quality_report.summary.model_dump(),
            blocking_findings=[
                {"message": f.message, "suggestion": f.suggestion, "severity": f.severity.value, "entity_id": f.entity_id}
                for f in quality_report.top_findings
                if f.severity == QualitySeverity.CRITICAL
            ][:10],
        )
    elif run.quota_exhausted:
        yield _sse(
            "stopped",
            message="Generation stopped — quota exhausted. All progress saved. Click Resume to continue.",
            run_log_path=str(run_log_path),
            quality_report_path=str(quality_report_path),
            quality_summary=quality_report.summary.model_dump(),
        )
    else:
        yield _sse(
            "complete",
            message="Generation complete",
            quality_report_path=str(quality_report_path),
            run_log_path=str(run_log_path),
            quality_summary=quality_report.summary.model_dump(),
        )


# ---------------------------------------------------------------------------
# Public pipeline entry point
# ---------------------------------------------------------------------------


async def run_pipeline(scenario: ScenarioConfig, resume: bool, override_checks: bool) -> AsyncGenerator[str, None]:
    """Run the full generation pipeline, yielding SSE events as it progresses.

    Creates a :class:`_GenerationRun` to hold shared mutable state,
    then delegates to extracted sub-functions for each pipeline stage:
    resume pre-checks, per-device contact generation, group chats,
    spam injection, event-gate validation, and finalization.

    This is the public entry point consumed by the route handler in
    ``source.generator``.

    Args:
        scenario (ScenarioConfig): Deep copy of the active scenario
            configuration.  The caller is responsible for copying.
        resume (bool): Whether to resume from a previous partial run.
        override_checks (bool): Whether to skip the resume quality
            pre-check.

    Yields:
        Formatted SSE data lines containing JSON with progress updates,
        errors, or completion signals.

    """
    run = _GenerationRun(scenario, resume, override_checks)

    if run.total_devices == 0:
        run.record("error", "no_devices")
        yield _sse("error", message="No devices configured")
        return

    persistence.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if run.resume and not run.override_checks:
        resume_blocked = False
        for event in _check_resume_preconditions(run):
            yield event
            resume_blocked = True
        if resume_blocked:
            return

    for dev_idx, device in enumerate(run.scenario.devices):
        if run.quota_exhausted or run.validation_blocked:
            break

        device_generation_mode = (device.generation_mode or "story").strip().lower()
        device_uses_story_context = device_generation_mode != "standalone"
        device_story_arc = (run.scenario.story_arc or "") if device_uses_story_context else ""
        device_timeline_events = run.scenario.timeline_events if device_uses_story_context else []

        device_ctx = ScenarioContext(
            theme=run.scenario.theme or "slice-of-life",
            culture=run.scenario.culture or "american",
            story_arc=device_story_arc,
            language=run.settings.language or "en",
        )

        existing_data: SmsDataset | None = None
        completed_contacts: set[str] = set()
        if run.resume:
            existing_data = persistence.load_existing_device_data(run.scenario.id, device.id)
            if existing_data:
                completed_contacts = count_existing_conversations(existing_data)
                all_contact_ids = {c.actor_id for c in device.contacts}
                if all_contact_ids <= completed_contacts:
                    run.record("info", "device_skipped", {"device_label": device.device_label, "reason": "already complete"})
                    yield _sse(
                        "device_skipped",
                        device=dev_idx + 1,
                        total=run.total_devices,
                        label=device.device_label,
                        reason="already complete",
                    )
                    continue

        run.record(
            "info",
            "device_start",
            {
                "device_label": device.device_label,
                "resuming": len(completed_contacts) > 0,
                "generation_mode": device_generation_mode,
            },
        )
        yield _sse(
            "device_start",
            device=dev_idx + 1,
            total=run.total_devices,
            label=device.device_label,
            resuming=len(completed_contacts) > 0,
            generation_mode=device_generation_mode,
        )

        actors = [Actor(ActorId=device.owner_actor_id, Name=device.owner_name)]
        nodes: list[ConversationNode] = []
        if existing_data:
            actors = list(existing_data.actors)
            nodes = list(existing_data.nodes)

        async for event in _generate_device_contacts(
            run, dev_idx, device, device_ctx, device_timeline_events, device_uses_story_context, nodes, actors, completed_contacts
        ):
            yield event

        async for event in _generate_device_group_chats(
            run, dev_idx, device, device_ctx, device_story_arc, device_timeline_events, device_uses_story_context, nodes, actors
        ):
            yield event

        if (device.spam_density or "medium") != "none" and not run.quota_exhausted:
            spam_nodes, spam_actors = spam.generate_spam_messages(device, run.settings)
            nodes.extend(spam_nodes)
            actors.extend(spam_actors)
            dataset = SmsDataset(nodes=nodes, actors=actors)
            persistence.save_device_data(run.scenario.id, device.id, dataset, device.device_label, dev_idx + 1)
            yield _sse("spam_done", device=dev_idx + 1, threads=len(spam_nodes))

        dataset = SmsDataset(nodes=nodes, actors=actors)
        run.last_saved_path = persistence.save_device_data(run.scenario.id, device.id, dataset, device.device_label, dev_idx + 1)
        run.generated_datasets[device.id] = dataset

        async for event in _run_device_event_gate(
            run, dev_idx, device, device_ctx, device_timeline_events, device_uses_story_context, nodes, actors
        ):
            yield event

        run.record(
            "info",
            "device_done",
            {"device_label": device.device_label, "path": str(run.last_saved_path), "partial": run.quota_exhausted},
        )
        yield _sse("device_done", device=dev_idx + 1, path=str(run.last_saved_path), partial=run.quota_exhausted)

    for event in _finalize_generation(run):
        yield event
