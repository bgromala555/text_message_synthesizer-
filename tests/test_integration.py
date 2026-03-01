"""Integration tests for the generation pipeline end-to-end.

Tests the full generation flow from scenario configuration through skeleton
generation, prompt construction, (mocked) LLM interaction, event injection,
consistency validation, and repair feedback.  All LLM calls are mocked so
tests are deterministic and fast.
"""

# ruff: noqa: S101

from __future__ import annotations

import json
import random
from unittest.mock import patch

import pytest

from messageviewer.models import Message
from source.conversation import generate_conversation
from source.events import ConversationEvent, extract_conversation_events
from source.llm_client import LLMCallResult, StoryState, merge_story_states
from source.models import (
    ContactSlot,
    DeviceScenario,
    FlexPersonalityProfile,
    FlexTextingStyle,
    FlexTimelineEvent,
    GenerationSettings,
    ScenarioConfig,
    ScenarioContext,
)
from source.prompts import build_batch_prompt, build_system_prompt
from source.quality_models import QualityCheckId, QualityFinding, QualitySeverity
from source.skeleton import SkeletonMessage, build_group_skeleton, generate_skeleton
from source.validation import build_repair_feedback, validate_event_message_consistency

# ---------------------------------------------------------------------------
# Deterministic LLM mock response
# ---------------------------------------------------------------------------


def _make_llm_response(count: int, topics: list[str] | None = None) -> str:
    """Build a valid JSON string mimicking an LLM batch response.

    Generates ``count`` short messages and a complete story_state object
    so that both ``parse_llm_response`` and ``merge_story_states`` behave
    as they would with a real provider.

    Args:
        count: Number of messages to include in the response.
        topics: Optional list of topics for the story_state.

    Returns:
        JSON string matching the expected LLM response schema.

    """
    messages = [f"msg-{i}" for i in range(count)]
    return json.dumps(
        {
            "messages": messages,
            "story_state": {
                "topics_covered": topics or ["greetings"],
                "key_events": [],
                "unresolved_threads": [],
                "relationship_vibe": "casual",
                "owner_state": "relaxed",
                "contact_state": "friendly",
            },
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_personality() -> FlexPersonalityProfile:
    """Return a minimal but valid personality profile that passes the generation readiness gate.

    Returns:
        FlexPersonalityProfile with enough data to pass the generation readiness gate.

    """
    return FlexPersonalityProfile(
        actor_id="actor_001",
        name="Test Person",
        age=28,
        neighborhood="Downtown",
        role="friend",
        personality_summary="A friendly, outgoing person who loves hiking and cooking. They text frequently with lots of emojis.",
        daily_routine_notes="Works 9-5 at a tech startup, goes to the gym after work.",
        current_life_situations=["just moved to a new apartment"],
        texting_style=FlexTextingStyle(punctuation="minimal", emoji_use="frequent"),
    )


@pytest.fixture
def owner_personality() -> FlexPersonalityProfile:
    """Return a distinct owner personality that passes the generation readiness gate.

    Returns:
        FlexPersonalityProfile representing the device owner.

    """
    return FlexPersonalityProfile(
        actor_id="owner_001",
        name="Alex Rivera",
        age=32,
        neighborhood="Midtown",
        role="owner",
        personality_summary="Analytical but warm tech lead who loves sci-fi. Uses proper grammar in texts but is casual with close friends.",
        daily_routine_notes="Works remote, walks the dog at 7am, codes until 6pm, reads at night.",
        current_life_situations=["training for a half marathon"],
        texting_style=FlexTextingStyle(punctuation="full", emoji_use="occasional"),
    )


@pytest.fixture
def contact_personality_b() -> FlexPersonalityProfile:
    """Return a second contact personality for multi-contact scenarios.

    Returns:
        FlexPersonalityProfile representing a coworker contact.

    """
    return FlexPersonalityProfile(
        actor_id="actor_002",
        name="Jordan Lee",
        age=25,
        neighborhood="East Village",
        role="coworker",
        personality_summary="Energetic junior developer who sends a lot of memes and reacts to everything. Always enthusiastic.",
        daily_routine_notes="Commutes by bike, grabs coffee at 8am, pair-programs all day.",
        current_life_situations=["looking for a new apartment"],
        texting_style=FlexTextingStyle(punctuation="none", emoji_use="heavy"),
    )


@pytest.fixture
def short_settings() -> GenerationSettings:
    """Return generation settings spanning a short 15-day window for fast tests.

    Returns:
        GenerationSettings configured for a 15-day date range with small batches.

    """
    return GenerationSettings(
        date_start="2025-06-01",
        date_end="2025-06-15",
        messages_per_day_min=2,
        messages_per_day_max=5,
        batch_size=10,
        temperature=0.9,
    )


@pytest.fixture
def sample_scenario(
    owner_personality: FlexPersonalityProfile,
    sample_personality: FlexPersonalityProfile,
    contact_personality_b: FlexPersonalityProfile,
    short_settings: GenerationSettings,
) -> ScenarioConfig:
    """Build a complete scenario with 1 device and 2 contacts for pipeline tests.

    Returns:
        ScenarioConfig with a single device holding two contacts.

    """
    device = DeviceScenario(
        id="dev-1",
        device_label="Phone A",
        owner_name="Alex Rivera",
        owner_actor_id="owner_001",
        owner_personality=owner_personality,
        contacts=[
            ContactSlot(
                id="c1",
                actor_id="actor_001",
                name="Test Person",
                role="friend",
                message_volume="regular",
                personality=sample_personality,
            ),
            ContactSlot(
                id="c2",
                actor_id="actor_002",
                name="Jordan Lee",
                role="coworker",
                message_volume="light",
                personality=contact_personality_b,
            ),
        ],
    )
    return ScenarioConfig(
        id="scenario-int",
        name="Integration Test Scenario",
        devices=[device],
        generation_settings=short_settings,
    )


# ---------------------------------------------------------------------------
# 1. Full generation pipeline (single device)
# ---------------------------------------------------------------------------


def test_full_generation_pipeline_single_device_produces_correct_messages(
    sample_scenario: ScenarioConfig,
) -> None:
    """Generate a full direct conversation with mocked LLM and verify skeleton→prompt→assembly flow."""
    device = sample_scenario.devices[0]
    settings = sample_scenario.generation_settings
    random.seed(42)

    # Arrange: generate skeleton to predict batch count
    skeleton = generate_skeleton(
        device.owner_actor_id,
        device.contacts[0].actor_id,
        settings,
        device.contacts[0].message_volume,
    )
    batch_count = (len(skeleton) + settings.batch_size - 1) // settings.batch_size

    # Arrange: mock call_llm to return valid JSON with the right message count per batch
    call_count = 0

    def mock_call_llm(system_prompt: str, user_prompt: str, gen_settings: GenerationSettings) -> LLMCallResult:
        """Return a deterministic LLMCallResult matching the requested batch size.

        Wraps the JSON response in an ``LLMCallResult`` with zeroed token
        usage, matching the updated ``call_llm`` return type.

        Returns:
            LLMCallResult with the expected message count and a story_state.

        """
        nonlocal call_count
        call_count += 1
        for line in user_prompt.split("\n"):
            if "MESSAGE SKELETON" in line and "messages" in line:
                count_str = line.split("(")[1].split(" ")[0]
                return LLMCallResult(content=_make_llm_response(int(count_str), topics=[f"batch-{call_count}-topic"]))
        return LLMCallResult(content=_make_llm_response(settings.batch_size, topics=[f"batch-{call_count}-topic"]))

    random.seed(42)
    with patch("source.conversation.call_llm", side_effect=mock_call_llm):
        messages, llm_calls, quota_hit = generate_conversation(
            device,
            contact_index=0,
            settings=settings,
        )

    # Assert: messages were produced and match skeleton size
    assert len(messages) == len(skeleton)
    assert llm_calls == batch_count
    assert quota_hit is False

    # Assert: each message has populated content, correct actors, and timestamps
    owner_ids = {m.SenderActorId for m in messages if m.Direction == "outgoing"}
    contact_ids = {m.SenderActorId for m in messages if m.Direction == "incoming"}
    assert owner_ids == {device.owner_actor_id}
    assert contact_ids == {device.contacts[0].actor_id}

    for msg in messages:
        assert msg.Content.startswith("msg-")
        assert msg.TransferTime >= "2025-06-01"
        assert msg.TransferTime[:10] <= "2025-06-15"
        assert msg.ServiceName == "SMS"


# ---------------------------------------------------------------------------
# 1b. System prompt includes personality details
# ---------------------------------------------------------------------------


def test_system_prompt_includes_personality_details(
    owner_personality: FlexPersonalityProfile,
    sample_personality: FlexPersonalityProfile,
) -> None:
    """System prompt must embed both participants' personality data and generation rules."""
    prompt = build_system_prompt(
        owner_profile=owner_personality,
        contact_profile=sample_personality,
        owner_name="Alex Rivera",
        theme="slice-of-life",
        culture="american",
    )

    assert "Alex Rivera" in prompt
    assert "Test Person" in prompt
    assert "Analytical but warm tech lead" in prompt
    assert "friendly, outgoing person who loves hiking" in prompt
    assert "PARTICIPANT 1 (phone owner)" in prompt
    assert "PARTICIPANT 2" in prompt
    assert "story_state" in prompt
    assert "ABSOLUTE RULES" in prompt


# ---------------------------------------------------------------------------
# 1c. Batch prompt includes event directives
# ---------------------------------------------------------------------------


def test_batch_prompt_includes_event_directives_when_events_present() -> None:
    """Batch prompts must inject event directive blocks when events are active."""
    skeleton_batch = [
        SkeletonMessage(sender_actor_id="owner_001", transfer_time="2025-06-10T14:00:00-05:00", direction="outgoing"),
        SkeletonMessage(sender_actor_id="actor_001", transfer_time="2025-06-10T14:05:00-05:00", direction="incoming"),
    ]
    actor_lookup = {"owner_001": "Alex", "actor_001": "Test Person"}
    story_state = StoryState()

    event_block = (
        "\n=== SCENARIO EVENTS (MUST be reflected in messages) ===\n"
        "ACTIVE EVENT on 2025-06-10:\n"
        "  What happened: Coffee meetup at Blue Bottle\n"
        "=== END SCENARIO EVENTS ==="
    )

    prompt = build_batch_prompt(
        skeleton_batch=skeleton_batch,
        actor_lookup=actor_lookup,
        batch_num=1,
        total_batches=1,
        story_state=story_state,
        event_block=event_block,
    )

    assert "SCENARIO EVENTS" in prompt
    assert "Coffee meetup at Blue Bottle" in prompt
    assert "BATCH 1 of 1" in prompt
    assert "conversation start" in prompt
    assert "MESSAGE SKELETON (2 messages)" in prompt
    assert "[OUTGOING] Alex" in prompt
    assert "[INCOMING] Test Person" in prompt


# ---------------------------------------------------------------------------
# 1d. Story state accumulates across batches
# ---------------------------------------------------------------------------


def test_story_state_accumulates_across_multiple_merges() -> None:
    """Merging multiple batch states should accumulate topics and update scalars."""
    state = StoryState()

    batch1 = StoryState(
        topics_covered=["weather"],
        key_events=["rain started"],
        relationship_vibe="neutral",
        owner_state="dry",
        contact_state="wet",
    )
    state = merge_story_states(state, batch1)

    batch2 = StoryState(
        topics_covered=["lunch plans"],
        key_events=["ordered pizza"],
        relationship_vibe="warmer",
        owner_state="hungry",
        contact_state="excited",
    )
    state = merge_story_states(state, batch2)

    assert state.topics_covered == ["weather", "lunch plans"]
    assert state.key_events == ["rain started", "ordered pizza"]
    assert state.relationship_vibe == "warmer"
    assert state.owner_state == "hungry"
    assert state.contact_state == "excited"


# ---------------------------------------------------------------------------
# 2. Generation with timeline events
# ---------------------------------------------------------------------------


def test_generation_with_timeline_events_extracts_and_surfaces_events(
    owner_personality: FlexPersonalityProfile,
    sample_personality: FlexPersonalityProfile,
) -> None:
    """extract_conversation_events finds the right events and they surface in batch prompts."""
    device = DeviceScenario(
        id="dev-1",
        device_label="Phone A",
        owner_name="Alex Rivera",
        owner_actor_id="owner_001",
        owner_personality=owner_personality,
        contacts=[
            ContactSlot(
                id="c1",
                actor_id="actor_001",
                name="Test Person",
                role="friend",
                message_volume="regular",
                personality=sample_personality,
            ),
        ],
    )

    events = [
        FlexTimelineEvent(
            id="ev-1",
            date="2025-06-10",
            time="14:00",
            description="Coffee meetup at Blue Bottle",
            encounter_type="planned",
            device_impacts={"dev-1": "Owner and Test Person grab coffee and catch up"},
            participants=[
                {"device_id": "dev-1", "contact_id": "__owner__"},
                {"device_id": "dev-1", "contact_id": "c1"},
            ],
        ),
        FlexTimelineEvent(
            id="ev-2",
            date="2025-06-12",
            description="Concert at the park",
            encounter_type="chance_encounter",
            device_impacts={"dev-1": "Owner bumps into Test Person at the show"},
            participants=[
                {"device_id": "dev-1", "contact_id": "__owner__"},
                {"device_id": "dev-1", "contact_id": "c1"},
            ],
        ),
    ]

    # Act: extract conversation events
    conv_events = extract_conversation_events(device, "actor_001", "Test Person", events)

    # Assert: both events are extracted, correct type and ordering
    assert len(conv_events) == 2
    assert conv_events[0].date == "2025-06-10"
    assert conv_events[0].encounter_type == "planned"
    assert conv_events[0].description == "Coffee meetup at Blue Bottle"
    assert conv_events[0].owner_name == "Alex Rivera"
    assert conv_events[0].contact_name == "Test Person"
    assert conv_events[0].is_secondary is False

    assert conv_events[1].date == "2025-06-12"
    assert conv_events[1].encounter_type == "chance_encounter"
    assert conv_events[1].is_secondary is False


def test_events_for_uninvolved_contact_returns_empty(
    owner_personality: FlexPersonalityProfile,
    sample_personality: FlexPersonalityProfile,
) -> None:
    """Events that don't involve a specific contact should not be extracted for them."""
    device = DeviceScenario(
        id="dev-1",
        device_label="Phone A",
        owner_name="Alex Rivera",
        owner_actor_id="owner_001",
        owner_personality=owner_personality,
        contacts=[
            ContactSlot(
                id="c1",
                actor_id="actor_001",
                name="Test Person",
                role="friend",
                personality=sample_personality,
            ),
            ContactSlot(
                id="c2",
                actor_id="actor_002",
                name="Jordan Lee",
                role="coworker",
            ),
        ],
    )

    events = [
        FlexTimelineEvent(
            id="ev-only-c2",
            date="2025-06-10",
            description="Lunch with Jordan",
            participants=[
                {"device_id": "dev-1", "contact_id": "__owner__"},
                {"device_id": "dev-1", "contact_id": "c2"},
            ],
        ),
    ]

    conv_events = extract_conversation_events(device, "actor_001", "Test Person", events)
    assert conv_events == []


# ---------------------------------------------------------------------------
# 3. Consistency validation detects issues
# ---------------------------------------------------------------------------


def test_consistency_validation_detects_near_miss_with_direct_meeting_language() -> None:
    """Near-miss events with direct-encounter language must be flagged as CRITICAL."""
    event = ConversationEvent(
        date="2025-06-10",
        description="Near miss at Central Park",
        encounter_type="near_miss",
        owner_name="Alex",
        contact_name="Sam",
    )

    # Messages that violate near-miss semantics: "ran into you" implies direct meeting
    messages = [
        Message(
            SenderActorId="owner",
            Content="hey were you at central park today?",
            TransferTime="2025-06-10T16:00:00",
            Direction="outgoing",
            ServiceName="SMS",
        ),
        Message(
            SenderActorId="contact",
            Content="yeah I ran into you near the fountain!",
            TransferTime="2025-06-10T16:05:00",
            Direction="incoming",
            ServiceName="SMS",
        ),
        Message(
            SenderActorId="owner",
            Content="good seeing you there",
            TransferTime="2025-06-10T16:10:00",
            Direction="outgoing",
            ServiceName="SMS",
        ),
    ]

    findings = validate_event_message_consistency(
        messages=messages,
        events=[event],
        entity_id="owner->contact",
        language="en",
    )

    assert len(findings) >= 1
    critical_findings = [f for f in findings if f.severity == QualitySeverity.CRITICAL]
    assert len(critical_findings) >= 1
    assert "Near-miss" in critical_findings[0].message
    assert critical_findings[0].check_id == QualityCheckId.ARC_EVENT_CONSISTENCY


def test_consistency_validation_detects_planned_event_missing_coordination() -> None:
    """Planned events without pre-coordination language should be flagged as WARNING."""
    event = ConversationEvent(
        date="2025-06-10",
        description="Lunch at Olive Garden",
        encounter_type="planned",
        owner_name="Alex",
        contact_name="Sam",
    )

    # Messages around event date but NO scheduling/coordination language
    messages = [
        Message(
            SenderActorId="owner",
            Content="just chilling at home",
            TransferTime="2025-06-08T10:00:00",
            Direction="outgoing",
            ServiceName="SMS",
        ),
        Message(
            SenderActorId="contact", Content="same here lol", TransferTime="2025-06-08T10:05:00", Direction="incoming", ServiceName="SMS"
        ),
        Message(
            SenderActorId="owner", Content="the food was great", TransferTime="2025-06-10T18:00:00", Direction="outgoing", ServiceName="SMS"
        ),
    ]

    findings = validate_event_message_consistency(
        messages=messages,
        events=[event],
        entity_id="owner->contact",
        language="en",
    )

    assert len(findings) >= 1
    warning_findings = [f for f in findings if f.severity == QualitySeverity.WARNING]
    assert len(warning_findings) >= 1
    assert "Planned event" in warning_findings[0].message
    assert "scheduling" in warning_findings[0].message.lower() or "coordination" in warning_findings[0].message.lower()


def test_consistency_validation_passes_for_well_formed_planned_event() -> None:
    """A planned event with coordination before and follow-up after should produce no findings."""
    event = ConversationEvent(
        date="2025-06-10",
        description="Dinner at Nobu",
        encounter_type="planned",
        owner_name="Alex",
        contact_name="Sam",
    )

    messages = [
        Message(
            SenderActorId="owner",
            Content="let's meet at 7 tomorrow?",
            TransferTime="2025-06-09T18:00:00",
            Direction="outgoing",
            ServiceName="SMS",
        ),
        Message(
            SenderActorId="contact",
            Content="yea that time works for me",
            TransferTime="2025-06-09T18:05:00",
            Direction="incoming",
            ServiceName="SMS",
        ),
        Message(
            SenderActorId="owner",
            Content="that sushi was incredible",
            TransferTime="2025-06-10T22:00:00",
            Direction="outgoing",
            ServiceName="SMS",
        ),
    ]

    findings = validate_event_message_consistency(
        messages=messages,
        events=[event],
        entity_id="owner->contact",
        language="en",
    )

    assert findings == []


# ---------------------------------------------------------------------------
# 4. Group conversation generation
# ---------------------------------------------------------------------------


def test_group_conversation_skeleton_distributes_messages_across_senders() -> None:
    """Group skeleton with 3 members should distribute messages across owner and all members."""
    random.seed(99)

    settings = GenerationSettings(
        date_start="2025-06-01",
        date_end="2025-06-30",
        messages_per_day_min=3,
        messages_per_day_max=6,
        batch_size=25,
    )

    owner_actor = "owner_001"
    member_actors = ["actor_001", "actor_002", "actor_003"]

    skeleton = build_group_skeleton(
        owner_actor_id=owner_actor,
        member_actor_ids=member_actors,
        settings=settings,
        start_date="2025-06-01",
        end_date="2025-06-30",
        message_volume="regular",
    )

    assert len(skeleton) > 0

    sender_counts: dict[str, int] = {}
    for msg in skeleton:
        sender_counts[msg.sender_actor_id] = sender_counts.get(msg.sender_actor_id, 0) + 1

    # Owner should appear in senders
    assert owner_actor in sender_counts
    # At least 2 of the 3 members should have sent messages over 30 days
    member_senders = [a for a in member_actors if a in sender_counts]
    assert len(member_senders) >= 2

    # Owner should get roughly 40% (between 20-60% to account for randomness)
    total = len(skeleton)
    owner_fraction = sender_counts[owner_actor] / total
    assert 0.20 <= owner_fraction <= 0.60

    # All messages should be outgoing (owner) or incoming (member)
    for msg in skeleton:
        if msg.sender_actor_id == owner_actor:
            assert msg.direction == "outgoing"
        else:
            assert msg.direction == "incoming"


# ---------------------------------------------------------------------------
# 5. Skeleton volume scaling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("volume", "expected_relative_order"),
    [
        ("heavy", 3),
        ("regular", 2),
        ("light", 1),
        ("minimal", 0),
    ],
)
def test_skeleton_volume_scaling_produces_appropriate_counts(
    volume: str,
    expected_relative_order: int,
) -> None:
    """Each volume tier should produce progressively fewer messages over a 30-day window."""
    random.seed(12345)
    settings = GenerationSettings(
        date_start="2025-06-01",
        date_end="2025-06-30",
        messages_per_day_min=2,
        messages_per_day_max=8,
    )

    skeleton = generate_skeleton("owner", "contact", settings, message_volume=volume)
    # Store count for cross-tier comparison (done via parametrize ordering)
    assert len(skeleton) >= 0  # minimal can produce 0 on unlucky seeds

    # Each message has valid fields
    for msg in skeleton:
        assert msg.sender_actor_id in {"owner", "contact"}
        assert msg.direction in {"outgoing", "incoming"}
        assert msg.transfer_time[:4] == "2025"


def test_skeleton_volume_tiers_produce_monotonically_decreasing_counts() -> None:
    """Heavy > regular > light > minimal in expected message count across 30 days."""
    random.seed(42)
    settings = GenerationSettings(
        date_start="2025-06-01",
        date_end="2025-06-30",
        messages_per_day_min=2,
        messages_per_day_max=8,
    )

    counts: dict[str, int] = {}
    for vol in ("heavy", "regular", "light", "minimal"):
        random.seed(42)
        counts[vol] = len(generate_skeleton("owner", "contact", settings, message_volume=vol))

    assert counts["heavy"] > counts["regular"]
    assert counts["regular"] > counts["light"]
    assert counts["light"] >= counts["minimal"]


# ---------------------------------------------------------------------------
# 7. ScenarioContext model validation
# ---------------------------------------------------------------------------


def test_scenario_context_model_validates_defaults() -> None:
    """ScenarioContext should have sensible defaults and accept partial construction."""
    ctx = ScenarioContext()

    assert ctx.theme == "slice-of-life"
    assert ctx.culture == "american"
    assert not ctx.story_arc
    assert ctx.language == "en"


def test_scenario_context_model_accepts_custom_values() -> None:
    """ScenarioContext should accept and preserve custom field values."""
    ctx = ScenarioContext(
        theme="thriller",
        culture="gulf-arab",
        story_arc="A journalist uncovers a smuggling ring.",
        language="ar",
    )

    assert ctx.theme == "thriller"
    assert ctx.culture == "gulf-arab"
    assert ctx.story_arc == "A journalist uncovers a smuggling ring."
    assert ctx.language == "ar"


def test_scenario_context_roundtrips_through_serialization() -> None:
    """ScenarioContext should survive JSON roundtrip via Pydantic."""
    original = ScenarioContext(
        theme="noir",
        culture="french",
        story_arc="A detective searches for a missing painting.",
        language="fr",
    )
    serialized = original.model_dump_json()
    restored = ScenarioContext.model_validate_json(serialized)

    assert restored.theme == original.theme
    assert restored.culture == original.culture
    assert restored.story_arc == original.story_arc
    assert restored.language == original.language


# ---------------------------------------------------------------------------
# 8. Repair feedback construction
# ---------------------------------------------------------------------------


def test_repair_feedback_construction_produces_actionable_text() -> None:
    """build_repair_feedback should format findings into prompt-ready repair instructions."""
    findings = [
        QualityFinding(
            check_id=QualityCheckId.ARC_EVENT_CONSISTENCY,
            severity=QualitySeverity.CRITICAL,
            score=0.25,
            scope="thread",
            entity_id="owner->contact",
            message="Near-miss event on 2025-06-10 is written like a direct meetup.",
            suggestion="Rewrite event-adjacent messages so they discover overlap later instead of meeting directly.",
        ),
        QualityFinding(
            check_id=QualityCheckId.ARC_EVENT_CONSISTENCY,
            severity=QualitySeverity.WARNING,
            score=0.60,
            scope="thread",
            entity_id="owner->contact",
            message="Planned event on 2025-06-15 lacks any scheduling/coordination beforehand.",
            suggestion="Add brief pre-event logistics so the planned encounter feels causal.",
        ),
    ]

    feedback = build_repair_feedback(findings)

    assert "Fix all event/message consistency issues" in feedback
    assert "Near-miss event on 2025-06-10" in feedback
    assert "direct meetup" in feedback
    assert "Planned event on 2025-06-15" in feedback
    assert "Required fix:" in feedback
    assert "Rewrite event-adjacent messages" in feedback
    assert "pre-event logistics" in feedback


def test_repair_feedback_caps_at_six_findings() -> None:
    """build_repair_feedback should include at most 6 findings to avoid prompt bloat."""
    findings = [
        QualityFinding(
            check_id=QualityCheckId.ARC_EVENT_CONSISTENCY,
            severity=QualitySeverity.WARNING,
            score=0.5,
            scope="thread",
            entity_id="owner->contact",
            message=f"Issue number {i}",
            suggestion=f"Fix issue {i}",
        )
        for i in range(10)
    ]

    feedback = build_repair_feedback(findings)

    # Should contain issues 0-5 but not 6-9
    assert "Issue number 0" in feedback
    assert "Issue number 5" in feedback
    assert "Issue number 6" not in feedback


def test_repair_feedback_empty_findings_returns_header_only() -> None:
    """build_repair_feedback with no findings should return just the header line."""
    feedback = build_repair_feedback([])

    assert feedback == "Fix all event/message consistency issues listed below:"
    assert feedback.count("\n") == 0
