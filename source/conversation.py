"""Conversation generation functions for direct and group chat threads.

Extracts the core generation logic from the monolithic generator module
into focused, testable functions.  Direct conversations use a skeleton-to-LLM
batch-to-message assembly pipeline with event injection and personality arc
hints.  Group conversations follow the same pattern with multi-sender
skeletons and group-aware system prompts.

Both generators include retry logic for transient LLM / JSON failures and
quota-exhaustion handling so callers can save partial progress.

The :func:`generate_conversation_streaming` variant uses the async
:class:`StreamingOpenAIProvider` to yield partial token deltas as they
arrive, then parses the complete response at the end.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator

from messageviewer.models import Actor, ConversationNode, Message, SmsDataset
from source.events import (
    ConversationEvent,
    augment_skeleton_for_events,
    events_for_batch,
    extract_conversation_events,
    force_planned_event_coordination,
    format_event_directives,
)
from source.llm_client import (
    DEFAULT_MODEL,
    MAX_LLM_TOKENS,
    QuotaExhaustedError,
    StoryState,
    budget_prompt,
    call_llm,
    merge_story_states,
    parse_llm_response,
)
from source.models import (
    DeviceScenario,
    FlexPersonalityProfile,
    FlexTimelineEvent,
    GenerationSettings,
    GroupChat,
)
from source.prompts import (
    build_batch_prompt,
    build_group_system_prompt,
    build_personality_arc_hint,
    build_system_prompt,
)
from source.skeleton import SkeletonMessage, build_group_skeleton, generate_skeleton

logger = logging.getLogger(__name__)

# Minimum character lengths for personality profiles to be considered usable
# by the generation pipeline.  Prevents sending near-empty context to the LLM.
_MIN_SUMMARY_LENGTH = 20
_MIN_ROUTINE_LENGTH = 10


# ---------------------------------------------------------------------------
# Profile readiness gate
# ---------------------------------------------------------------------------


def profile_ready_for_generation(profile: FlexPersonalityProfile | None) -> bool:
    """Return whether a profile is present and minimally usable for generation.

    A profile is considered ready when it has a personality summary of at
    least ``_MIN_SUMMARY_LENGTH`` characters and either a daily routine note
    of at least ``_MIN_ROUTINE_LENGTH`` characters or at least one current
    life situation entry.  This guards against sending near-empty personality
    context to the LLM.

    Args:
        profile (FlexPersonalityProfile | None): Owner or contact personality
            profile to evaluate.

    Returns:
        True when the profile has basic identity and behavioral signal,
        False otherwise.

    """
    if profile is None:
        return False
    summary = (profile.personality_summary or "").strip()
    routine = (profile.daily_routine_notes or "").strip()
    return len(summary) >= _MIN_SUMMARY_LENGTH and (len(routine) >= _MIN_ROUTINE_LENGTH or bool(profile.current_life_situations))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _assemble_direct_messages(skeleton: list[SkeletonMessage], all_content: list[str]) -> list[Message]:
    """Build Message objects from a skeleton and generated content strings.

    Pairs each skeleton entry with the corresponding generated text.
    Only the portion of the skeleton for which content was produced is
    included, so partial-generation results are handled gracefully.

    Args:
        skeleton (list[SkeletonMessage]): Chronological skeleton from
            ``generate_skeleton``.
        all_content (list[str]): Generated text strings, one per skeleton
            entry (may be shorter than the skeleton on quota exhaustion).

    Returns:
        List of Message objects aligned with the completed portion of
        the skeleton.

    """
    completed = skeleton[: len(all_content)]
    return [
        Message(
            SenderActorId=skel.sender_actor_id,
            Content=all_content[i],
            TransferTime=skel.transfer_time,
            Direction=skel.direction,
            ServiceName=skel.service_name,
        )
        for i, skel in enumerate(completed)
    ]


def _resolve_group_members(
    device: DeviceScenario,
    group_chat: GroupChat,
    all_devices: list[DeviceScenario],
) -> tuple[list[str], list[FlexPersonalityProfile], dict[str, str]]:
    """Resolve group chat member profiles and actor IDs from cross-device references.

    Iterates through the group chat member list, looks up each member's
    device and contact, and collects their personality profiles.  The
    device owner is skipped (they are always participant 1).

    Args:
        device (DeviceScenario): The phone owner's device, used to
            identify and skip the owner from the member list.
        group_chat (GroupChat): The group chat configuration containing
            member references with device_id and contact_id pairs.
        all_devices (list[DeviceScenario]): All devices in the scenario
            for cross-referencing member profiles.

    Returns:
        Tuple of (member_actor_ids, member_profiles, actor_lookup) where
        actor_lookup maps actor ID to display name for all participants
        including the device owner.

    """
    member_actor_ids: list[str] = []
    member_profiles: list[FlexPersonalityProfile] = []
    actor_lookup: dict[str, str] = {device.owner_actor_id: device.owner_name}

    for member in group_chat.members:
        dev_id = member.device_id
        con_id = member.contact_id
        if con_id == "__owner__" and dev_id == device.id:
            continue
        member_dev = next((d for d in all_devices if d.id == dev_id), None)
        if not member_dev:
            continue
        if con_id == "__owner__":
            if member_dev.owner_personality:
                member_profiles.append(member_dev.owner_personality)
                member_actor_ids.append(member_dev.owner_actor_id)
                actor_lookup[member_dev.owner_actor_id] = member_dev.owner_name
        else:
            contact = next((c for c in member_dev.contacts if c.id == con_id), None)
            if contact and contact.personality:
                member_profiles.append(contact.personality)
                member_actor_ids.append(contact.actor_id)
                actor_lookup[contact.actor_id] = contact.name

    return member_actor_ids, member_profiles, actor_lookup


def _log_conversation_events(
    conv_events: list[ConversationEvent],
    owner_name: str,
    contact_name: str,
) -> None:
    """Log summary of extracted conversation events for a direct thread.

    Args:
        conv_events (list[ConversationEvent]): List of ConversationEvent
            objects extracted for this owner-contact pair.
        owner_name (str): Display name of the device owner.
        contact_name (str): Display name of the contact.

    """
    if not conv_events:
        return
    primary_count = sum(1 for e in conv_events if not e.is_secondary)
    secondary_count = sum(1 for e in conv_events if e.is_secondary)
    logger.info(
        "Found %d events for %s <-> %s (%d primary, %d secondary): %s",
        len(conv_events),
        owner_name,
        contact_name,
        primary_count,
        secondary_count,
        ", ".join(f"{e.date}{'*' if e.is_secondary else ''}" for e in conv_events),
    )


# ---------------------------------------------------------------------------
# Direct (1-to-1) conversation generation
# ---------------------------------------------------------------------------


def generate_conversation(
    device: DeviceScenario,
    contact_index: int,
    settings: GenerationSettings,
    theme: str = "slice-of-life",
    culture: str = "american",
    timeline_events: list[FlexTimelineEvent] | None = None,
    story_arc: str = "",
    language: str = "en",
    consistency_feedback: str = "",
    include_story_context: bool = True,
) -> tuple[list[Message], int, bool]:
    """Generate all messages for one direct conversation thread.

    Creates the skeleton, batches it, sends each batch to the LLM, and
    assembles the final Message list.  Timeline events involving both
    the owner and this contact are injected into the relevant batch
    prompts so the LLM produces messages that reference them.

    The global ``story_arc`` and per-character arcs are embedded in
    the system prompt so every conversation stays consistent with the
    overarching narrative.

    If the API quota is exhausted mid-generation, returns whatever
    messages were completed so far along with a ``quota_hit`` flag so
    the caller can save partial progress.

    Args:
        device (DeviceScenario): The device scenario containing owner and
            contact profiles.
        contact_index (int): Index of the contact in the device's contact
            list.
        settings (GenerationSettings): Generation settings controlling
            batch size, date range, temperature, and model selection.
        theme (str): Scenario genre preset that flavors the conversation
            tone (e.g. ``"slice-of-life"``, ``"thriller"``).
        culture (str): Cultural/geographic context for locations, food,
            and social norms.
        timeline_events (list[FlexTimelineEvent] | None): Full scenario
            timeline events for event injection into batch prompts.
        story_arc (str): The scenario-level narrative bible / answer key
            that anchors all conversations.
        language (str): ISO 639-1 language code for all generated message
            content.
        consistency_feedback (str): Repair instructions from a previous
            consistency check, injected into the system prompt so the LLM
            corrects specific issues.
        include_story_context (bool): Whether to include the global story
            bible and timeline event context in prompt construction.
            Character-level arcs are still included when present.

    Returns:
        Tuple of (list of Message objects, total LLM calls made,
        quota_exhausted flag).

    """
    contact = device.contacts[contact_index]
    owner_profile = device.owner_personality
    contact_profile = contact.personality

    if owner_profile is None or contact_profile is None:
        logger.warning("Missing personality for %s or contact %s — skipping", device.owner_name, contact.name)
        return [], 0, False
    if not profile_ready_for_generation(owner_profile) or not profile_ready_for_generation(contact_profile):
        logger.warning("Missing personality for %s or contact %s — skipping", device.owner_name, contact.name)
        return [], 0, False

    skeleton = generate_skeleton(device.owner_actor_id, contact.actor_id, settings, contact.message_volume or "regular")
    if not skeleton:
        return [], 0, False

    effective_events = (timeline_events or []) if include_story_context else []

    conv_events = extract_conversation_events(device, contact.actor_id, contact.name, effective_events)
    if conv_events:
        skeleton = augment_skeleton_for_events(skeleton, conv_events, device.owner_actor_id, contact.actor_id)
    _log_conversation_events(conv_events, device.owner_name, contact.name)

    actor_lookup = {device.owner_actor_id: device.owner_name, contact.actor_id: contact.name}
    system_prompt = build_system_prompt(
        owner_profile,
        contact_profile,
        device.owner_name,
        theme,
        culture=culture,
        story_arc=story_arc if include_story_context else "",
        owner_arc=device.owner_story_arc or "",
        contact_arc=contact.story_arc or "",
        language=language,
        consistency_feedback=consistency_feedback,
    )

    batch_size = settings.batch_size
    batches = [skeleton[i : i + batch_size] for i in range(0, len(skeleton), batch_size)]
    total_batches = len(batches)

    all_content: list[str] = []
    story_state = StoryState()
    llm_calls = 0
    injected_event_dates: set[str] = set()

    quota_hit = False
    for batch_idx, batch in enumerate(batches):
        if quota_hit:
            break

        batch_start = batch[0].transfer_time[:10]
        batch_end = batch[-1].transfer_time[:10]

        buildup, active, aftermath = events_for_batch(batch_start, batch_end, conv_events, injected_event_dates)
        event_block = format_event_directives(buildup, active, aftermath)

        for ev in active:
            injected_event_dates.add(ev.date)

        arc_block = build_personality_arc_hint(owner_profile, contact_profile, batch_idx + 1, total_batches)

        user_prompt = build_batch_prompt(batch, actor_lookup, batch_idx + 1, total_batches, story_state, event_block, arc_block)

        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                result = call_llm(system_prompt, user_prompt, settings)
                llm_calls += 1
                parsed_messages, new_state = parse_llm_response(result.content, len(batch))
                all_content.extend(parsed_messages)
                story_state = merge_story_states(story_state, new_state)
                break
            except QuotaExhaustedError:
                logger.error("Quota exhausted at batch %d/%d — returning partial results", batch_idx + 1, total_batches)
                quota_hit = True
                break
            except (json.JSONDecodeError, ValueError) as e:
                retries += 1
                logger.warning("Batch %d/%d attempt %d failed: %s", batch_idx + 1, total_batches, retries, e)
                if retries >= max_retries:
                    all_content.extend(["..." for _ in batch])
                time.sleep(1)

        time.sleep(0.1)

    final_messages = _assemble_direct_messages(skeleton, all_content)

    # Deterministic safeguard: planned events must have at least one
    # pre-event coordination message.
    final_messages, forced_coordination_count = force_planned_event_coordination(
        final_messages,
        conv_events,
        device.owner_actor_id,
        language=language,
    )
    if forced_coordination_count:
        logger.info(
            "Injected %d planned-event coordination messages for %s <-> %s",
            forced_coordination_count,
            device.owner_name,
            contact.name,
        )

    return final_messages, llm_calls, quota_hit


# ---------------------------------------------------------------------------
# Legacy convenience wrapper
# ---------------------------------------------------------------------------


def generate_device_dataset(device: DeviceScenario, settings: GenerationSettings) -> SmsDataset:
    """Generate the full SMS dataset for one device.

    Iterates over all contacts on the device, generates conversation
    threads via :func:`generate_conversation`, and assembles them into
    an ``SmsDataset``.  This is a legacy convenience wrapper — the SSE
    streaming endpoint uses the lower-level functions directly.

    Args:
        device (DeviceScenario): The device scenario to generate for,
            including owner profile and all contacts.
        settings (GenerationSettings): Generation settings controlling
            batch size, date range, temperature, and model selection.

    Returns:
        Complete SmsDataset for this device containing all conversation
        threads and actor metadata.

    """
    actors = [Actor(ActorId=device.owner_actor_id, Name=device.owner_name)]
    nodes: list[ConversationNode] = []

    for i, contact in enumerate(device.contacts):
        actors.append(Actor(ActorId=contact.actor_id, Name=contact.name))

        messages, _calls, _quota_hit = generate_conversation(device, i, settings, timeline_events=[])
        if messages:
            nodes.append(
                ConversationNode(
                    source=device.owner_actor_id,
                    target=[contact.actor_id],
                    type="SMS",
                    message_content=messages,
                )
            )
            logger.info("Generated %d messages for %s <-> %s", len(messages), device.owner_name, contact.name)

    return SmsDataset(nodes=nodes, actors=actors)


# ---------------------------------------------------------------------------
# Group chat generation
# ---------------------------------------------------------------------------


def generate_group_conversation(
    device: DeviceScenario,
    group_chat: GroupChat,
    all_devices: list[DeviceScenario],
    settings: GenerationSettings,
    theme: str = "slice-of-life",
    culture: str = "american",
    story_arc: str = "",
    language: str = "en",
) -> tuple[list[Message], int, bool]:
    """Generate all messages for one group chat conversation.

    Builds a multi-sender skeleton, constructs a group-aware system
    prompt, and sends batched requests to the LLM.  Uses the shared
    ``build_batch_prompt`` function for consistent prompt construction
    including conversation memory, event injection, and personality arc
    hints.

    Includes retry logic with up to 3 attempts per batch for transient
    JSON parsing or API errors, and early exit on quota exhaustion so
    callers can save partial results.

    Args:
        device (DeviceScenario): The device this group chat appears on
            (the phone owner's device).
        group_chat (GroupChat): The group chat configuration including
            member references, name, vibe, and volume settings.
        all_devices (list[DeviceScenario]): All devices in the scenario,
            used for resolving member profiles by cross-referencing
            device and contact IDs.
        settings (GenerationSettings): Generation settings controlling
            batch size, date range, temperature, and model selection.
        theme (str): Scenario genre preset that flavors the conversation
            tone.
        culture (str): Cultural/geographic context for the conversation.
        story_arc (str): Global narrative bible that anchors all
            conversations in the scenario.
        language (str): ISO 639-1 language code for message content.

    Returns:
        Tuple of (list of Message objects, total LLM calls made,
        quota_exhausted flag).

    """
    owner_profile = device.owner_personality
    if not owner_profile:
        return [], 0, False

    member_actor_ids, member_profiles, actor_lookup = _resolve_group_members(device, group_chat, all_devices)

    if not member_actor_ids:
        logger.warning("No resolvable members for group '%s' — skipping", group_chat.name)
        return [], 0, False

    skeleton = build_group_skeleton(
        device.owner_actor_id,
        member_actor_ids,
        settings,
        group_chat.start_date or settings.date_start,
        group_chat.end_date or settings.date_end,
        group_chat.message_volume or "regular",
    )
    if not skeleton:
        return [], 0, False

    system_prompt = build_group_system_prompt(
        owner_profile,
        member_profiles,
        device.owner_name,
        group_chat.name,
        group_chat.vibe,
        theme,
        culture,
        story_arc,
        language,
    )

    batch_size = settings.batch_size
    batches = [skeleton[i : i + batch_size] for i in range(0, len(skeleton), batch_size)]
    total_batches = len(batches)

    all_content: list[str] = []
    story_state = StoryState()
    llm_calls = 0
    quota_hit = False

    for batch_idx, batch in enumerate(batches):
        if quota_hit:
            break

        user_prompt = build_batch_prompt(batch, actor_lookup, batch_idx + 1, total_batches, story_state)

        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                result = call_llm(system_prompt, user_prompt, settings)
                llm_calls += 1
                messages_text, new_state = parse_llm_response(result.content, len(batch))
                all_content.extend(messages_text)
                if new_state:
                    story_state = merge_story_states(story_state, new_state)
                break
            except QuotaExhaustedError:
                logger.error("Quota exhausted at group batch %d/%d", batch_idx + 1, total_batches)
                quota_hit = True
                break
            except (json.JSONDecodeError, ValueError) as e:
                retries += 1
                logger.warning("Group batch %d/%d attempt %d failed: %s", batch_idx + 1, total_batches, retries, e)
                if retries >= max_retries:
                    all_content.extend(["..." for _ in batch])
                time.sleep(1)
        if quota_hit:
            break
        time.sleep(0.1)

    final_messages = [
        Message(
            SenderActorId=skel.sender_actor_id,
            Content=all_content[i] if i < len(all_content) else "...",
            TransferTime=skel.transfer_time,
            Direction=skel.direction,
            ServiceName="SMS",
        )
        for i, skel in enumerate(skeleton)
    ]

    return final_messages, llm_calls, quota_hit


# ---------------------------------------------------------------------------
# Streaming conversation generation
# ---------------------------------------------------------------------------


async def generate_conversation_streaming(
    device: DeviceScenario,
    contact_index: int,
    settings: GenerationSettings,
    theme: str = "slice-of-life",
    culture: str = "american",
    timeline_events: list[FlexTimelineEvent] | None = None,
    story_arc: str = "",
    language: str = "en",
    include_story_context: bool = True,
) -> AsyncGenerator[str | tuple[list[Message], int, bool], None]:
    """Generate a direct conversation thread with token-level streaming.

    Works identically to :func:`generate_conversation` but uses the
    :class:`StreamingOpenAIProvider` to yield partial token deltas as
    they arrive from the LLM.  Yields ``str`` tokens during generation
    and a final ``tuple[list[Message], int, bool]`` with the assembled
    result when all batches are complete.

    Callers should inspect each yielded value's type:

    - ``str`` → partial token delta (for SSE streaming to the browser)
    - ``tuple`` → final result ``(messages, llm_calls, quota_hit)``

    Args:
        device (DeviceScenario): The device scenario with owner and
            contact profiles.
        contact_index (int): Index of the contact in the device's
            contact list.
        settings (GenerationSettings): Generation settings controlling
            batch size, date range, temperature, and model selection.
        theme (str): Scenario genre preset.
        culture (str): Cultural/geographic context.
        timeline_events (list[FlexTimelineEvent] | None): Timeline events
            for event injection.
        story_arc (str): Scenario-level narrative bible.
        language (str): ISO 639-1 language code.
        include_story_context (bool): Whether to include the global story
            bible and timeline event context in prompt construction.

    Yields:
        ``str`` token deltas during streaming, then a final
        ``tuple[list[Message], int, bool]`` with the assembled result.

    """
    from source.llm_provider import get_streaming_provider  # noqa: PLC0415  # circular import

    contact = device.contacts[contact_index]
    owner_profile = device.owner_personality
    contact_profile = contact.personality

    if owner_profile is None or contact_profile is None:
        logger.warning("Missing personality for %s or contact %s — skipping", device.owner_name, contact.name)
        yield ([], 0, False)
        return
    if not profile_ready_for_generation(owner_profile) or not profile_ready_for_generation(contact_profile):
        logger.warning("Missing personality for %s or contact %s — skipping", device.owner_name, contact.name)
        yield ([], 0, False)
        return

    skeleton = generate_skeleton(device.owner_actor_id, contact.actor_id, settings, contact.message_volume or "regular")
    if not skeleton:
        yield ([], 0, False)
        return

    effective_events = (timeline_events or []) if include_story_context else []
    conv_events = extract_conversation_events(device, contact.actor_id, contact.name, effective_events)
    if conv_events:
        skeleton = augment_skeleton_for_events(skeleton, conv_events, device.owner_actor_id, contact.actor_id)

    actor_lookup = {device.owner_actor_id: device.owner_name, contact.actor_id: contact.name}
    system_prompt = build_system_prompt(
        owner_profile,
        contact_profile,
        device.owner_name,
        theme,
        culture=culture,
        story_arc=story_arc if include_story_context else "",
        owner_arc=device.owner_story_arc or "",
        contact_arc=contact.story_arc or "",
        language=language,
    )

    model = settings.llm_model or DEFAULT_MODEL
    streaming_provider = get_streaming_provider(settings.llm_provider)

    batch_size = settings.batch_size
    batches = [skeleton[i : i + batch_size] for i in range(0, len(skeleton), batch_size)]
    total_batches = len(batches)

    all_content: list[str] = []
    story_state = StoryState()
    llm_calls = 0
    quota_hit = False
    injected_event_dates: set[str] = set()

    for batch_idx, batch in enumerate(batches):
        if quota_hit:
            break

        batch_start = batch[0].transfer_time[:10]
        batch_end = batch[-1].transfer_time[:10]

        buildup, active, aftermath = events_for_batch(batch_start, batch_end, conv_events, injected_event_dates)
        event_block = format_event_directives(buildup, active, aftermath)

        for ev in active:
            injected_event_dates.add(ev.date)

        arc_block = build_personality_arc_hint(owner_profile, contact_profile, batch_idx + 1, total_batches)
        user_prompt = build_batch_prompt(batch, actor_lookup, batch_idx + 1, total_batches, story_state, event_block, arc_block)

        sys_budgeted, usr_budgeted, _ = budget_prompt(system_prompt, user_prompt, model, max_output_tokens=MAX_LLM_TOKENS)

        try:
            collected: list[str] = []
            async for token in streaming_provider.generate_stream(
                system_prompt=sys_budgeted,
                user_prompt=usr_budgeted,
                model=model,
                temperature=settings.temperature,
                max_tokens=MAX_LLM_TOKENS,
            ):
                collected.append(token)
                yield token

            llm_calls += 1
            full_response = "".join(collected)
            parsed_messages, new_state = parse_llm_response(full_response, len(batch))
            all_content.extend(parsed_messages)
            story_state = merge_story_states(story_state, new_state)

        except QuotaExhaustedError:
            logger.error("Quota exhausted at streaming batch %d/%d", batch_idx + 1, total_batches)
            quota_hit = True
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Streaming batch %d/%d parse failed: %s", batch_idx + 1, total_batches, exc)
            all_content.extend(["..." for _ in batch])

    final_messages = _assemble_direct_messages(skeleton, all_content)
    final_messages, _ = force_planned_event_coordination(final_messages, conv_events, device.owner_actor_id, language=language)

    yield (final_messages, llm_calls, quota_hit)
