"""Tests for source.repair — consistency-retry loop orchestrator.

Covers the retry/repair flow in ``generate_with_consistency_retries``,
including temperature reduction, early-exit conditions, error handling,
and mock-verified LLM delegation.  All LLM calls are mocked so no
real API traffic is generated.
"""

# ruff: noqa: S101

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from messageviewer.models import Message
from source.events import ConversationEvent
from source.models import (
    ContactSlot,
    DeviceScenario,
    GenerationSettings,
    ScenarioContext,
)
from source.quality_models import QualityCheckId, QualityFinding, QualitySeverity
from source.repair import GenerationRetryResult, generate_with_consistency_retries

# Patch targets — patched where they are imported in source.repair
_PATCH_GENERATE = "source.repair.generate_conversation"
_PATCH_VALIDATE = "source.repair.validate_event_message_consistency"
_PATCH_FEEDBACK = "source.repair.build_repair_feedback"


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _make_device(owner_name: str = "Alice", contact_name: str = "Bob") -> DeviceScenario:
    """Build a minimal DeviceScenario with one contact for testing.

    Returns:
        A DeviceScenario with one contact slot.

    """
    return DeviceScenario(
        id="dev1",
        owner_name=owner_name,
        owner_actor_id="PA001",
        contacts=[
            ContactSlot(id="c1", actor_id="C01", name=contact_name, role="friend"),
        ],
    )


def _make_settings(**overrides: Any) -> GenerationSettings:
    """Build default generation settings with optional overrides.

    Returns:
        A GenerationSettings instance with sensible defaults.

    """
    defaults: dict[str, Any] = {"temperature": 0.9, "llm_provider": "openai", "batch_size": 25}
    defaults.update(overrides)
    return GenerationSettings(**defaults)


def _make_context(**overrides: Any) -> ScenarioContext:
    """Build a minimal ScenarioContext with optional overrides.

    Returns:
        A ScenarioContext with default theme/culture/language.

    """
    defaults: dict[str, Any] = {"theme": "slice-of-life", "culture": "american", "story_arc": "", "language": "en"}
    defaults.update(overrides)
    return ScenarioContext(**defaults)


def _make_message(content: str = "hey", date: str = "2025-03-01T10:00:00") -> Message:
    """Build a single Message with minimal required fields.

    Returns:
        A Message with the given content and transfer time.

    """
    return Message(
        SenderActorId="PA001",
        Content=content,
        TransferTime=date,
        Direction="outgoing",
        ServiceName="SMS",
    )


def _make_event(
    date: str = "2025-03-05",
    encounter_type: str = "planned",
    is_secondary: bool = False,
) -> ConversationEvent:
    """Build a minimal ConversationEvent for testing.

    Returns:
        A ConversationEvent with the given parameters.

    """
    return ConversationEvent(
        date=date,
        description="test event",
        encounter_type=encounter_type,
        owner_name="Alice",
        contact_name="Bob",
        is_secondary=is_secondary,
    )


def _make_blocking_finding(message: str = "Something is wrong") -> QualityFinding:
    """Build a CRITICAL quality finding that triggers a retry.

    Returns:
        A QualityFinding with CRITICAL severity.

    """
    return QualityFinding(
        check_id=QualityCheckId.ARC_EVENT_CONSISTENCY,
        severity=QualitySeverity.CRITICAL,
        score=0.25,
        scope="thread",
        entity_id="PA001->C01",
        message=message,
        suggestion="Fix it.",
    )


def _make_ok_finding() -> QualityFinding:
    """Build an OK-severity finding that does NOT trigger a retry.

    Returns:
        A QualityFinding with OK severity.

    """
    return QualityFinding(
        check_id=QualityCheckId.ARC_EVENT_CONSISTENCY,
        severity=QualitySeverity.OK,
        score=0.90,
        scope="thread",
        entity_id="PA001->C01",
        message="Looks fine",
    )


# ---------------------------------------------------------------------------
# GenerationRetryResult model
# ---------------------------------------------------------------------------


class TestGenerationRetryResult:
    """Tests for the GenerationRetryResult Pydantic model."""

    def test_defaults_produce_empty_successful_result(self) -> None:
        """Default construction yields an empty success result with zero counters."""
        result = GenerationRetryResult()

        assert result.messages == []
        assert result.llm_calls == 0
        assert result.quota_hit is False
        assert result.consistency_findings == []
        assert result.retries_used == 0
        assert result.error is None

    def test_fields_round_trip_through_serialization(self) -> None:
        """All fields survive a dump/load cycle with correct values."""
        msg = _make_message("hello")
        finding = _make_ok_finding()
        result = GenerationRetryResult(
            messages=[msg],
            llm_calls=5,
            quota_hit=True,
            consistency_findings=[finding],
            retries_used=2,
            error="boom",
        )
        data = result.model_dump()

        assert data["llm_calls"] == 5
        assert data["quota_hit"] is True
        assert data["retries_used"] == 2
        assert data["error"] == "boom"
        assert len(data["messages"]) == 1
        assert data["messages"][0]["Content"] == "hello"
        assert len(data["consistency_findings"]) == 1
        assert data["consistency_findings"][0]["severity"] == "ok"


# ---------------------------------------------------------------------------
# generate_with_consistency_retries — success paths
# ---------------------------------------------------------------------------


class TestRetryLoopSuccess:
    """Tests where generation succeeds without or with minimal retries."""

    @pytest.mark.asyncio
    async def test_first_attempt_passes_returns_messages_no_retry(self) -> None:
        """When validation finds no blocking issues, the function returns after one attempt."""
        msgs = [_make_message("hi"), _make_message("hey")]
        with (
            patch(_PATCH_GENERATE, return_value=(msgs, 1, False)) as mock_gen,
            patch(_PATCH_VALIDATE, return_value=[]),
        ):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
                max_retries=3,
            )

        assert result.messages == msgs
        assert result.llm_calls == 1
        assert result.retries_used == 0
        assert result.error is None
        assert result.quota_hit is False
        mock_gen.assert_called_once()

    @pytest.mark.asyncio
    async def test_ok_severity_findings_do_not_trigger_retry(self) -> None:
        """OK-severity findings are not blocking and should not cause a retry."""
        msgs = [_make_message("all good")]
        with (
            patch(_PATCH_GENERATE, return_value=(msgs, 1, False)),
            patch(_PATCH_VALIDATE, return_value=[_make_ok_finding()]),
        ):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
            )

        assert result.retries_used == 0
        assert result.llm_calls == 1
        assert len(result.consistency_findings) == 1
        assert result.consistency_findings[0].severity == QualitySeverity.OK


# ---------------------------------------------------------------------------
# generate_with_consistency_retries — retry mechanics
# ---------------------------------------------------------------------------


class TestRetryLoopMechanics:
    """Tests verifying temperature reduction, feedback injection, and retry counting."""

    @pytest.mark.asyncio
    async def test_blocking_findings_trigger_retry_with_reduced_temperature(self) -> None:
        """When blocking findings appear, the loop retries with lower temperature."""
        msgs = [_make_message("attempt")]
        blocking = [_make_blocking_finding()]
        captured_temperatures: list[float] = []

        def fake_generate(device: Any, ci: Any, settings: Any, *args: Any, **kwargs: Any) -> tuple[list[Message], int, bool]:
            """Capture the temperature used for each generation attempt.

            Returns:
                Tuple of (messages, call_count, quota_hit).

            """
            captured_temperatures.append(settings.temperature)
            return (msgs, 1, False)

        with (
            patch(_PATCH_GENERATE, side_effect=fake_generate),
            patch(_PATCH_VALIDATE, side_effect=[blocking, []]),
            patch(_PATCH_FEEDBACK, return_value="fix stuff"),
        ):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(temperature=0.9),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
                max_retries=3,
            )

        assert result.retries_used == 1
        assert result.llm_calls == 2
        assert captured_temperatures[0] == pytest.approx(0.9)
        assert captured_temperatures[1] == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_temperature_floors_at_0_3(self) -> None:
        """Temperature should never drop below 0.3 regardless of retry count."""
        msgs = [_make_message("low temp")]
        blocking = [_make_blocking_finding()]
        captured_temperatures: list[float] = []

        def fake_generate(device: Any, ci: Any, settings: Any, *args: Any, **kwargs: Any) -> tuple[list[Message], int, bool]:
            """Capture the temperature used for each generation attempt.

            Returns:
                Tuple of (messages, call_count, quota_hit).

            """
            captured_temperatures.append(settings.temperature)
            return (msgs, 1, False)

        with (
            patch(_PATCH_GENERATE, side_effect=fake_generate),
            patch(_PATCH_VALIDATE, return_value=blocking),
            patch(_PATCH_FEEDBACK, return_value="fix"),
        ):
            await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(temperature=0.5),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
                max_retries=5,
            )

        # attempt 0: 0.5, attempt 1: max(0.3, 0.5-0.2)=0.3,
        # attempt 2: max(0.3, 0.5-0.4)=0.3, etc.
        for temp in captured_temperatures[1:]:
            assert temp >= 0.3

    @pytest.mark.asyncio
    async def test_max_retries_exhausted_returns_last_attempt(self) -> None:
        """When all retries are used, the result from the last attempt is returned."""
        msgs = [_make_message("final")]
        blocking = [_make_blocking_finding("still broken")]

        with (
            patch(_PATCH_GENERATE, return_value=(msgs, 1, False)),
            patch(_PATCH_VALIDATE, return_value=blocking),
            patch(_PATCH_FEEDBACK, return_value="fix it"),
        ):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
                max_retries=3,
            )

        assert result.retries_used == 2
        assert result.llm_calls == 3
        assert len(result.consistency_findings) > 0
        assert result.consistency_findings[0].message == "still broken"

    @pytest.mark.asyncio
    async def test_initial_feedback_passed_to_first_attempt(self) -> None:
        """The initial_feedback string is forwarded to the first generate_conversation call."""
        msgs = [_make_message("ok")]
        captured_feedback: list[str] = []

        def fake_generate(
            device: Any, ci: Any, settings: Any, theme: Any, culture: Any, events: Any, arc: Any, lang: Any, feedback: Any, story_ctx: Any
        ) -> tuple[list[Message], int, bool]:
            """Capture the feedback argument passed to generate_conversation.

            Returns:
                Tuple of (messages, call_count, quota_hit).

            """
            captured_feedback.append(feedback)
            return (msgs, 1, False)

        with (
            patch(_PATCH_GENERATE, side_effect=fake_generate),
            patch(_PATCH_VALIDATE, return_value=[]),
        ):
            await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
                initial_feedback="prior device-gate issue",
            )

        assert captured_feedback[0] == "prior device-gate issue"

    @pytest.mark.asyncio
    async def test_llm_calls_accumulate_across_retries(self) -> None:
        """LLM call counts from every attempt are summed in the result."""
        blocking = [_make_blocking_finding()]
        call_idx = 0

        def fake_generate(device: Any, ci: Any, settings: Any, *args: Any, **kwargs: Any) -> tuple[list[Message], int, bool]:
            """Return increasing call counts per attempt.

            Returns:
                Tuple of (messages, call_count, quota_hit).

            """
            nonlocal call_idx
            call_idx += 1
            return ([_make_message(f"try {call_idx}")], call_idx * 2, False)

        with (
            patch(_PATCH_GENERATE, side_effect=fake_generate),
            patch(_PATCH_VALIDATE, side_effect=[blocking, []]),
            patch(_PATCH_FEEDBACK, return_value="fix"),
        ):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
            )

        # attempt 1 returns 2 calls, attempt 2 returns 4 calls → total 6
        assert result.llm_calls == 6


# ---------------------------------------------------------------------------
# generate_with_consistency_retries — early exit conditions
# ---------------------------------------------------------------------------


class TestRetryLoopEarlyExit:
    """Tests for conditions that bypass the retry loop after a single attempt."""

    @pytest.mark.asyncio
    async def test_quota_hit_exits_immediately(self) -> None:
        """When the LLM reports quota exhaustion, the loop returns without validation."""
        msgs = [_make_message("partial")]
        with (
            patch(_PATCH_GENERATE, return_value=(msgs, 1, True)),
            patch(_PATCH_VALIDATE) as mock_validate,
        ):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
            )

        assert result.quota_hit is True
        assert result.retries_used == 0
        mock_validate.assert_not_called()

    @pytest.mark.asyncio
    async def test_enforce_consistency_false_skips_validation(self) -> None:
        """When enforce_consistency=False, the function skips validation entirely."""
        msgs = [_make_message("no validate")]
        with (
            patch(_PATCH_GENERATE, return_value=(msgs, 1, False)),
            patch(_PATCH_VALIDATE) as mock_validate,
        ):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
                enforce_consistency=False,
            )

        assert result.messages == msgs
        assert result.retries_used == 0
        mock_validate.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_contact_events_skips_validation(self) -> None:
        """When contact_events is empty, no validation is performed."""
        msgs = [_make_message("no events")]
        with (
            patch(_PATCH_GENERATE, return_value=(msgs, 1, False)),
            patch(_PATCH_VALIDATE) as mock_validate,
        ):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[],
            )

        assert result.messages == msgs
        mock_validate.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_messages_from_llm_skips_validation(self) -> None:
        """When the LLM returns no messages, validation is skipped."""
        with (
            patch(_PATCH_GENERATE, return_value=([], 1, False)),
            patch(_PATCH_VALIDATE) as mock_validate,
        ):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
            )

        assert result.messages == []
        assert result.llm_calls == 1
        mock_validate.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_repair_false_reports_findings_without_retry(self) -> None:
        """When auto_repair=False, blocking findings are returned but no retry happens."""
        msgs = [_make_message("one shot")]
        blocking = [_make_blocking_finding()]

        with (
            patch(_PATCH_GENERATE, return_value=(msgs, 1, False)) as mock_gen,
            patch(_PATCH_VALIDATE, return_value=blocking),
        ):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
                auto_repair=False,
            )

        assert result.retries_used == 0
        assert result.llm_calls == 1
        assert len(result.consistency_findings) == 1
        assert result.consistency_findings[0].severity == QualitySeverity.CRITICAL
        mock_gen.assert_called_once()


# ---------------------------------------------------------------------------
# generate_with_consistency_retries — error handling
# ---------------------------------------------------------------------------


class TestRetryLoopErrorHandling:
    """Tests for exception paths inside the retry loop."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_error_string(self) -> None:
        """When generate_conversation raises, the error is captured in result.error."""
        with patch(_PATCH_GENERATE, side_effect=RuntimeError("LLM timeout")):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
            )

        assert result.error == "LLM timeout"
        assert result.messages == []
        assert result.retries_used == 0

    @pytest.mark.asyncio
    async def test_exception_on_second_attempt_captures_partial_progress(self) -> None:
        """If the first attempt succeeds but the retry raises, partial progress is preserved."""
        msgs_first = [_make_message("first try")]
        blocking = [_make_blocking_finding()]
        call_count = 0

        def fake_generate(device: Any, ci: Any, settings: Any, *args: Any, **kwargs: Any) -> tuple[list[Message], int, bool]:
            """Succeed on first call then raise TimeoutError on retry.

            Returns:
                Tuple of (messages, call_count, quota_hit) on first call.

            Raises:
                TimeoutError: On subsequent calls.

            """
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (msgs_first, 2, False)
            raise TimeoutError("network timeout")

        with (
            patch(_PATCH_GENERATE, side_effect=fake_generate),
            patch(_PATCH_VALIDATE, return_value=blocking),
            patch(_PATCH_FEEDBACK, return_value="fix it"),
        ):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
                max_retries=3,
            )

        assert result.error == "network timeout"
        assert result.retries_used == 1
        assert result.llm_calls == 2

    @pytest.mark.asyncio
    async def test_value_error_from_parse_captured_cleanly(self) -> None:
        """A ValueError (e.g. JSON parse failure) is captured as result.error."""
        with patch(_PATCH_GENERATE, side_effect=ValueError("invalid JSON response")):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
            )

        assert result.error is not None
        assert "invalid JSON response" in result.error
        assert result.messages == []


# ---------------------------------------------------------------------------
# generate_with_consistency_retries — WARNING severity
# ---------------------------------------------------------------------------


class TestRetryLoopWarningSeverity:
    """Tests verifying WARNING-severity findings also count as blocking."""

    @pytest.mark.asyncio
    async def test_warning_findings_trigger_retry(self) -> None:
        """WARNING-severity findings are blocking and should trigger a retry."""
        msgs = [_make_message("warn")]
        warning_finding = QualityFinding(
            check_id=QualityCheckId.ARC_EVENT_CONSISTENCY,
            severity=QualitySeverity.WARNING,
            score=0.50,
            scope="thread",
            entity_id="PA001->C01",
            message="Chance encounter has pre-planning",
            suggestion="Remove coordination.",
        )
        call_count = 0

        def fake_generate(device: Any, ci: Any, settings: Any, *args: Any, **kwargs: Any) -> tuple[list[Message], int, bool]:
            """Count generation calls.

            Returns:
                Tuple of (messages, call_count, quota_hit).

            """
            nonlocal call_count
            call_count += 1
            return (msgs, 1, False)

        with (
            patch(_PATCH_GENERATE, side_effect=fake_generate),
            patch(_PATCH_VALIDATE, side_effect=[[warning_finding], []]),
            patch(_PATCH_FEEDBACK, return_value="fix warning"),
        ):
            result = await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=_make_settings(),
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
            )

        assert call_count == 2
        assert result.retries_used == 1


# ---------------------------------------------------------------------------
# generate_with_consistency_retries — settings isolation
# ---------------------------------------------------------------------------


class TestSettingsIsolation:
    """Tests that the caller's GenerationSettings instance is never mutated."""

    @pytest.mark.asyncio
    async def test_original_settings_temperature_unchanged_after_retries(self) -> None:
        """The caller's settings object retains its original temperature after retries."""
        original_settings = _make_settings(temperature=0.9)
        blocking = [_make_blocking_finding()]

        with (
            patch(_PATCH_GENERATE, return_value=([_make_message("x")], 1, False)),
            patch(_PATCH_VALIDATE, return_value=blocking),
            patch(_PATCH_FEEDBACK, return_value="fix"),
        ):
            await generate_with_consistency_retries(
                device=_make_device(),
                contact_index=0,
                settings=original_settings,
                context=_make_context(),
                timeline_events=[],
                include_story_context=True,
                contact_events=[_make_event()],
                max_retries=3,
            )

        assert original_settings.temperature == pytest.approx(0.9)
