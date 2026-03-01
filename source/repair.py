"""Reusable consistency-retry loop for conversation generation.

Consolidates the duplicated retry-with-validation pattern that appears in
multiple route handlers (SSE streaming, device-gate repair, pair-thread
generation, timeline repair).  Each call site previously maintained its
own ``while True`` loop that called ``generate_conversation``, validated
event/message consistency, reduced temperature, and built repair feedback.

This module extracts that pattern into a single async function
(:func:`generate_with_consistency_retries`) and a Pydantic result model
(:class:`GenerationRetryResult`) so callers get a clean, testable
interface with no code duplication.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from messageviewer.models import Message
from source.conversation import generate_conversation
from source.events import ConversationEvent
from source.models import (
    DeviceScenario,
    FlexTimelineEvent,
    GenerationSettings,
    ScenarioContext,
)
from source.quality_models import QualityFinding, QualitySeverity
from source.validation import build_repair_feedback, validate_event_message_consistency

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class GenerationRetryResult(BaseModel):
    """Result of a generation attempt with optional consistency retries.

    Bundles the generated messages, cost metrics, quality findings, and
    error state into a single return value so callers can inspect
    exactly what happened during the retry loop without positional-tuple
    gymnastics.

    Attributes:
        messages: Generated Message objects (may be empty on failure).
        llm_calls: Total LLM API calls consumed across all attempts.
        quota_hit: True if the API quota was exhausted during generation.
        consistency_findings: Quality findings from the last validation
            pass (empty when validation is disabled or not reached).
        retries_used: Number of consistency retries that were executed
            (0 means the first attempt passed or retries were disabled).
        error: Human-readable error description if generation failed
            with an unexpected exception, None on success.

    """

    messages: list[Message] = Field(default_factory=list)
    llm_calls: int = 0
    quota_hit: bool = False
    consistency_findings: list[QualityFinding] = Field(default_factory=list)
    retries_used: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Retry orchestrator
# ---------------------------------------------------------------------------


async def generate_with_consistency_retries(
    device: DeviceScenario,
    contact_index: int,
    settings: GenerationSettings,
    context: ScenarioContext,
    timeline_events: list[FlexTimelineEvent],
    include_story_context: bool,
    contact_events: list[ConversationEvent],
    max_retries: int = 3,
    enforce_consistency: bool = True,
    auto_repair: bool = True,
    initial_feedback: str = "",
) -> GenerationRetryResult:
    """Generate a conversation with automatic consistency-retry logic.

    Wraps :func:`generate_conversation` in a loop that validates the
    output against the expected timeline events and, when blocking
    findings are detected, rebuilds the prompt with targeted repair
    feedback and a reduced temperature.  This eliminates ~300 lines of
    duplicated retry logic previously scattered across four route
    handlers.

    Temperature is reduced by 0.2 on each retry (floored at 0.3) to
    encourage the LLM to follow repair instructions more faithfully.

    The loop exits early when:

    * The API quota is exhausted (``quota_hit`` is set).
    * No contact events exist to validate against.
    * The generated message list is empty.
    * Validation passes with no blocking findings.
    * ``auto_repair`` is False (single attempt only).
    * The maximum number of retries is reached.

    Args:
        device (DeviceScenario): The device scenario containing the
            owner profile and contact list.
        contact_index (int): Index of the target contact within
            ``device.contacts``.
        settings (GenerationSettings): Base generation settings.  A
            deep copy is made before each attempt so the caller's
            instance is never mutated.
        context (ScenarioContext): Bundled scenario-level parameters
            (theme, culture, story_arc, language) that travel together.
        timeline_events (list[FlexTimelineEvent]): Full scenario
            timeline events passed through to ``generate_conversation``
            for event injection.
        include_story_context (bool): Whether to include the global
            story bible and timeline event context in prompts.
        contact_events (list[ConversationEvent]): Pre-extracted events
            relevant to this specific owner-contact thread, used for
            post-generation consistency validation.
        max_retries (int): Maximum number of consistency retries
            (including the initial attempt).  Defaults to 3.
        enforce_consistency (bool): Whether to run event-message
            consistency validation at all.  When False the function
            returns after a single generation attempt.
        auto_repair (bool): Whether to automatically retry with repair
            feedback when blocking findings are detected.  When False
            findings are reported but no retry occurs.
        initial_feedback (str): Optional repair instructions to inject
            into the very first generation attempt (e.g. from a prior
            device-gate check).

    Returns:
        A :class:`GenerationRetryResult` containing the best messages
        produced, cumulative LLM call count, quota status, final
        consistency findings, retry count, and any error description.

    """
    contact = device.contacts[contact_index]
    entity_id = f"{device.owner_actor_id}->{contact.actor_id}"

    result = GenerationRetryResult()
    repair_feedback = initial_feedback

    for attempt in range(max_retries):
        attempt_settings = settings.model_copy(deep=True)
        if attempt > 0:
            attempt_settings.temperature = max(0.3, settings.temperature - (0.2 * attempt))

        try:
            attempt_messages, attempt_calls, quota_hit = await asyncio.to_thread(
                generate_conversation,
                device,
                contact_index,
                attempt_settings,
                context.theme,
                context.culture,
                timeline_events,
                context.story_arc,
                context.language,
                repair_feedback,
                include_story_context,
            )
        except Exception as exc:
            logger.exception("Unexpected error generating %s <-> %s", device.owner_name, contact.name)
            result.error = str(exc)
            result.retries_used = attempt
            return result

        result.messages = attempt_messages
        result.llm_calls += attempt_calls
        result.quota_hit = quota_hit

        if quota_hit or not enforce_consistency or not contact_events or not attempt_messages:
            result.retries_used = attempt
            return result

        findings = validate_event_message_consistency(
            messages=attempt_messages,
            events=contact_events,
            entity_id=entity_id,
            language=context.language,
        )
        result.consistency_findings = findings

        blocking = [f for f in findings if f.severity in {QualitySeverity.CRITICAL, QualitySeverity.WARNING}]
        if not blocking:
            result.retries_used = attempt
            return result

        if not auto_repair or attempt >= max_retries - 1:
            result.retries_used = attempt
            return result

        repair_feedback = build_repair_feedback(blocking)
        logger.info(
            "Consistency retry %d/%d for %s (blocking findings: %d)",
            attempt + 1,
            max_retries - 1,
            entity_id,
            len(blocking),
        )

    result.retries_used = max_retries - 1
    return result
