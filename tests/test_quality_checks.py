"""Comprehensive unit tests for source/quality_checks.py.

Tests all exported quality-check functions, helper utilities, and the
top-level evaluate_generation_quality orchestrator.  Uses lightweight
factory functions to build ScenarioConfig / SmsDataset fixtures without
touching external services.
"""

# ruff: noqa: S101, PLC2701

from __future__ import annotations

import pytest

from messageviewer.models import ConversationNode, Message, SmsDataset
from source.models import (
    ContactSlot,
    DeviceContactRef,
    DeviceScenario,
    FlexPersonalityProfile,
    FlexTextingStyle,
    FlexTimelineEvent,
    GenerationSettings,
    GroupChat,
    ScenarioConfig,
)
from source.quality_checks import (
    _build_shared_contact_groups,
    _check_arc_event_consistency,
    _check_conversation_memory_quality,
    _check_group_event_coherence,
    _check_language_consistency,
    _check_pairwise_coverage,
    _check_personality_coherence,
    _check_relationship_behavior,
    _check_shared_identity_lock,
    _check_temporal_realism,
    _contains_emoji,
    _lang_script_ratio,
    _normalize_name,
    _severity_for_score,
    _tokenize,
    evaluate_generation_quality,
    quick_thread_findings,
)
from source.quality_models import (
    QualityCheckId,
    QualitySeverity,
)

# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _make_message(
    content: str = "hello",
    sender: str = "PA001",
    time: str = "2025-06-15T10:00:00",
    direction: str = "outgoing",
) -> Message:
    """Build a minimal Message with sensible defaults.

    Returns:
        A Message instance with the specified or default field values.

    """
    return Message(
        SenderActorId=sender,
        Content=content,
        TransferTime=time,
        Direction=direction,
        ServiceName="SMS",
    )


def _make_node(
    source: str = "PA001",
    targets: list[str] | None = None,
    messages: list[Message] | None = None,
) -> ConversationNode:
    """Build a ConversationNode with defaults.

    Returns:
        A ConversationNode with the specified or default field values.

    """
    return ConversationNode(
        source=source,
        target=targets or ["C01"],
        type="SMS",
        message_content=messages or [_make_message()],
    )


def _make_dataset(nodes: list[ConversationNode] | None = None) -> SmsDataset:
    """Build a minimal SmsDataset.

    Returns:
        An SmsDataset with the given nodes (or empty) and no actors.

    """
    return SmsDataset(nodes=nodes or [], actors=[])


def _make_personality(
    name: str = "TestPerson",
    actor_id: str = "C01",
    summary: str = "friendly and outgoing",
    emoji_use: str = "moderate",
    sample_phrases: list[str] | None = None,
    role: str = "",
) -> FlexPersonalityProfile:
    """Build a FlexPersonalityProfile with sensible defaults.

    Returns:
        A FlexPersonalityProfile populated with the specified or default values.

    """
    return FlexPersonalityProfile(
        actor_id=actor_id,
        name=name,
        personality_summary=summary,
        texting_style=FlexTextingStyle(emoji_use=emoji_use),
        sample_phrases=sample_phrases or ["hey!", "what's up?"],
        role=role,
    )


def _make_contact(
    contact_id: str = "c1",
    actor_id: str = "C01",
    name: str = "Contact1",
    role: str = "friend",
    personality: FlexPersonalityProfile | None = None,
    shared_with: list[DeviceContactRef] | None = None,
    story_arc: str = "",
) -> ContactSlot:
    """Build a ContactSlot with sensible defaults.

    Returns:
        A ContactSlot populated with the specified or default values.

    """
    return ContactSlot(
        id=contact_id,
        actor_id=actor_id,
        name=name,
        role=role,
        personality=personality,
        shared_with=shared_with or [],
        story_arc=story_arc,
    )


def _make_device(
    device_id: str = "dev1",
    owner_actor_id: str = "PA001",
    owner_name: str = "Owner",
    contacts: list[ContactSlot] | None = None,
    owner_personality: FlexPersonalityProfile | None = None,
    owner_story_arc: str = "",
) -> DeviceScenario:
    """Build a DeviceScenario with sensible defaults.

    Returns:
        A DeviceScenario populated with the specified or default values.

    """
    return DeviceScenario(
        id=device_id,
        owner_actor_id=owner_actor_id,
        owner_name=owner_name,
        contacts=contacts or [],
        owner_personality=owner_personality,
        owner_story_arc=owner_story_arc,
    )


def _make_scenario(
    devices: list[DeviceScenario] | None = None,
    story_arc: str = "A simple story about friends.",
    timeline_events: list[FlexTimelineEvent] | None = None,
    group_chats: list[GroupChat] | None = None,
    language: str = "en",
) -> ScenarioConfig:
    """Build a ScenarioConfig with sensible defaults.

    Returns:
        A ScenarioConfig with the specified or default values.

    """
    return ScenarioConfig(
        id="scenario-1",
        devices=devices or [],
        story_arc=story_arc,
        timeline_events=timeline_events or [],
        group_chats=group_chats or [],
        generation_settings=GenerationSettings(language=language),
    )


# ===================================================================
# _severity_for_score
# ===================================================================


class TestSeverityForScore:
    """Tests for the _severity_for_score threshold mapper."""

    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0.0, QualitySeverity.CRITICAL),
            (0.10, QualitySeverity.CRITICAL),
            (0.39, QualitySeverity.CRITICAL),
            (0.40, QualitySeverity.WARNING),
            (0.55, QualitySeverity.WARNING),
            (0.69, QualitySeverity.WARNING),
            (0.70, QualitySeverity.OK),
            (0.85, QualitySeverity.OK),
            (1.0, QualitySeverity.OK),
        ],
    )
    def test_severity_thresholds_return_correct_bucket(self, score: float, expected: QualitySeverity) -> None:
        """Scores below 0.40 are CRITICAL, below 0.70 WARNING, otherwise OK."""
        assert _severity_for_score(score) == expected


# ===================================================================
# _normalize_name
# ===================================================================


class TestNormalizeName:
    """Tests for the _normalize_name helper."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("John Doe", "johndoe"),
            ("  ALICE--Bob  ", "alicebob"),
            ("", ""),
            ("a!@#b$c", "abc"),
            ("Test123", "test123"),
        ],
    )
    def test_normalize_name_strips_non_alnum_and_lowercases(self, raw: str, expected: str) -> None:
        """Non-alphanumeric chars are removed and text is lowercased."""
        assert _normalize_name(raw) == expected


# ===================================================================
# _contains_emoji
# ===================================================================


class TestContainsEmoji:
    """Tests for the _contains_emoji helper."""

    def test_returns_true_for_text_with_emoji(self) -> None:
        """Text containing a Unicode emoji should be detected."""
        assert _contains_emoji("hello 😀") is True

    def test_returns_false_for_plain_text(self) -> None:
        """Plain ASCII text should not trigger emoji detection."""
        assert _contains_emoji("hello world") is False

    def test_returns_false_for_empty_string(self) -> None:
        """Empty string has no emojis."""
        assert _contains_emoji("") is False


# ===================================================================
# _tokenize
# ===================================================================


class TestTokenize:
    """Tests for the _tokenize text tokenizer."""

    def test_extracts_latin_tokens_above_min_length(self) -> None:
        """Latin tokens shorter than 3 chars should be excluded."""
        tokens = _tokenize("I am at the big park")
        assert "park" in tokens
        assert "big" in tokens
        assert "am" not in tokens
        assert "at" not in tokens

    def test_removes_english_stop_words(self) -> None:
        """Common English stop words should be filtered out."""
        tokens = _tokenize("the quick brown fox and this rabbit")
        assert "the" not in tokens
        assert "and" not in tokens
        assert "this" not in tokens
        assert "quick" in tokens
        assert "brown" in tokens
        assert "rabbit" in tokens

    def test_extracts_arabic_tokens(self) -> None:
        """Arabic character runs of 2+ should be captured."""
        tokens = _tokenize("مرحبا بالعالم")
        assert "مرحبا" in tokens
        assert "بالعالم" in tokens

    def test_removes_arabic_stop_words(self) -> None:
        """Common Arabic stop words should be filtered out."""
        tokens = _tokenize("هذا في المنزل")
        assert "هذا" not in tokens
        assert "في" not in tokens
        assert "المنزل" in tokens

    def test_empty_string_returns_empty_set(self) -> None:
        """Empty input should return an empty set."""
        assert _tokenize("") == set()


# ===================================================================
# _lang_script_ratio
# ===================================================================


class TestLangScriptRatio:
    """Tests for the _lang_script_ratio helper."""

    def test_english_text_with_en_language_returns_high_ratio(self) -> None:
        """Pure English text scored against 'en' should be near 1.0."""
        assert _lang_script_ratio("Hello world", "en") == pytest.approx(1.0)

    def test_arabic_text_with_ar_language_returns_high_ratio(self) -> None:
        """Pure Arabic text scored against 'ar' should be near 1.0."""
        ratio = _lang_script_ratio("مرحبا بالعالم", "ar")
        assert ratio > 0.9

    def test_mixed_script_with_en_language_returns_lower_ratio(self) -> None:
        """Half-English, half-Arabic text against 'en' should be roughly 0.5."""
        ratio = _lang_script_ratio("Hello مرحبا", "en")
        assert 0.3 < ratio < 0.8

    def test_empty_string_returns_one(self) -> None:
        """Whitespace-only input should default to 1.0."""
        assert _lang_script_ratio("", "en") == pytest.approx(1.0)
        assert _lang_script_ratio("   ", "ar") == pytest.approx(1.0)

    def test_unknown_language_returns_one(self) -> None:
        """Unknown language codes default to 1.0."""
        assert _lang_script_ratio("anything", "zz") == pytest.approx(1.0)

    def test_numbers_only_text_returns_one(self) -> None:
        """Text with only digits and no alpha chars returns 1.0."""
        assert _lang_script_ratio("12345", "en") == pytest.approx(1.0)


# ===================================================================
# _build_shared_contact_groups
# ===================================================================


class TestBuildSharedContactGroups:
    """Tests for the _build_shared_contact_groups utility."""

    def test_no_shared_contacts_returns_empty(self) -> None:
        """Devices with no shared_with links produce no groups."""
        scenario = _make_scenario(
            devices=[
                _make_device(contacts=[_make_contact(contact_id="c1")]),
                _make_device(device_id="dev2", contacts=[_make_contact(contact_id="c2")]),
            ]
        )
        groups = _build_shared_contact_groups(scenario)
        assert groups == []

    def test_shared_contacts_are_grouped(self) -> None:
        """Two contacts linked via shared_with should form one group."""
        c1 = _make_contact(
            contact_id="c1",
            actor_id="+1111",
            shared_with=[DeviceContactRef(device_id="dev2", contact_id="c2")],
        )
        c2 = _make_contact(contact_id="c2", actor_id="+1111")
        scenario = _make_scenario(
            devices=[
                _make_device(contacts=[c1]),
                _make_device(device_id="dev2", contacts=[c2]),
            ]
        )
        groups = _build_shared_contact_groups(scenario)
        assert len(groups) == 1
        ids_in_group = {c.id for c in groups[0]}
        assert ids_in_group == {"c1", "c2"}

    def test_single_device_no_groups(self) -> None:
        """A single device with no cross-device links yields no groups."""
        scenario = _make_scenario(devices=[_make_device(contacts=[_make_contact()])])
        groups = _build_shared_contact_groups(scenario)
        assert groups == []


# ===================================================================
# _check_personality_coherence
# ===================================================================


class TestCheckPersonalityCoherence:
    """Tests for the _check_personality_coherence check."""

    def test_no_personalities_returns_perfect_score(self) -> None:
        """Scenario without personality profiles should score 1.0."""
        scenario = _make_scenario(devices=[_make_device()])
        result = _check_personality_coherence(scenario)
        assert result.score == pytest.approx(1.0)
        assert result.severity == QualitySeverity.OK
        assert result.check_id == QualityCheckId.PERSONALITY_COHERENCE
        assert result.findings == []

    def test_coherent_personalities_return_perfect_score(self) -> None:
        """Personalities with no contradictions should score 1.0."""
        p = _make_personality(summary="friendly and calm person", emoji_use="moderate")
        scenario = _make_scenario(devices=[_make_device(contacts=[_make_contact(personality=p)])])
        result = _check_personality_coherence(scenario)
        assert result.score == pytest.approx(1.0)
        assert result.findings == []
        assert result.metrics["contradictions"] == pytest.approx(0.0)

    def test_contradictory_traits_produce_findings(self) -> None:
        """A personality with 'introvert' and 'extrovert' should flag a contradiction."""
        p = _make_personality(summary="An introvert who is also an extrovert")
        scenario = _make_scenario(devices=[_make_device(contacts=[_make_contact(personality=p)])])
        result = _check_personality_coherence(scenario)
        assert result.score < 1.0
        assert result.metrics["contradictions"] >= 1.0
        assert len(result.findings) >= 1
        assert "introvert" in result.findings[0].message
        assert "extrovert" in result.findings[0].message

    def test_emoji_policy_conflict_with_sample_phrases(self) -> None:
        """Emoji 'never' policy with emoji in sample phrases should flag."""
        p = _make_personality(
            summary="plain speaker",
            emoji_use="never uses emojis",
            sample_phrases=["hey! 😀", "what's up 🎉"],
        )
        scenario = _make_scenario(devices=[_make_device(contacts=[_make_contact(personality=p)])])
        result = _check_personality_coherence(scenario)
        assert result.metrics["contradictions"] >= 1.0
        assert any("emoji" in f.message.lower() for f in result.findings)

    def test_owner_personality_also_checked(self) -> None:
        """The device owner's personality should also be included in the check."""
        owner_p = _make_personality(summary="calm and also chaotic person", actor_id="PA001")
        scenario = _make_scenario(devices=[_make_device(owner_personality=owner_p)])
        result = _check_personality_coherence(scenario)
        assert result.metrics["contradictions"] >= 1.0
        assert result.metrics["profiles_checked"] >= 1.0

    def test_multiple_contradictions_lower_score_further(self) -> None:
        """Multiple profiles with contradictions should reduce the score more."""
        p1 = _make_personality(name="A", summary="introvert and extrovert")
        p2 = _make_personality(name="B", summary="calm yet chaotic", actor_id="C02")
        scenario = _make_scenario(
            devices=[
                _make_device(
                    contacts=[
                        _make_contact(contact_id="c1", personality=p1),
                        _make_contact(contact_id="c2", actor_id="C02", personality=p2),
                    ]
                )
            ]
        )
        result = _check_personality_coherence(scenario)
        assert result.metrics["contradictions"] >= 2.0
        assert result.score < 1.0


# ===================================================================
# _check_arc_event_consistency
# ===================================================================


class TestCheckArcEventConsistency:
    """Tests for the _check_arc_event_consistency check."""

    def test_empty_datasets_with_matching_arcs_returns_ok(self) -> None:
        """No generated messages and matching event text should pass."""
        scenario = _make_scenario(
            story_arc="robbery at the downtown bank",
            timeline_events=[FlexTimelineEvent(description="robbery at the downtown bank")],
        )
        result = _check_arc_event_consistency(scenario, {})
        assert result.score >= 0.70
        assert result.check_id == QualityCheckId.ARC_EVENT_CONSISTENCY
        assert result.severity == QualitySeverity.OK

    def test_high_overlap_messages_return_high_score(self) -> None:
        """Messages echoing the arc text should produce a high score."""
        scenario = _make_scenario(
            story_arc="robbery at the downtown bank",
            timeline_events=[FlexTimelineEvent(description="downtown bank robbery occurred")],
        )
        ds = _make_dataset(
            [
                _make_node(
                    messages=[
                        _make_message("The downtown bank was robbed"),
                        _make_message("robbery happened yesterday"),
                    ]
                )
            ]
        )
        result = _check_arc_event_consistency(scenario, {"dev1": ds})
        assert result.score > 0.5
        assert result.metrics["has_messages"] == pytest.approx(1.0)

    def test_completely_unrelated_messages_lower_score(self) -> None:
        """Messages about unrelated topics should lower the overlap score."""
        scenario = _make_scenario(
            story_arc="bank robbery downtown police chase",
            timeline_events=[],
        )
        ds = _make_dataset(
            [
                _make_node(
                    messages=[
                        _make_message("nice weather today"),
                        _make_message("going grocery shopping later"),
                    ]
                )
            ]
        )
        result = _check_arc_event_consistency(scenario, {"dev1": ds})
        assert result.score < 0.70

    def test_cross_language_scenario_applies_floor(self) -> None:
        """Arabic messages with English arcs should get a cross-language floor."""
        scenario = _make_scenario(
            story_arc="robbery at the downtown bank",
            timeline_events=[FlexTimelineEvent(description="downtown bank heist")],
            language="ar",
        )
        ds = _make_dataset([_make_node(messages=[_make_message("مرحبا بالعالم")])])
        result = _check_arc_event_consistency(scenario, {"dev1": ds})
        assert result.metrics["cross_language"] == pytest.approx(1.0)
        assert result.score >= 0.65

    def test_empty_story_arc_returns_perfect_score(self) -> None:
        """An empty story arc means no arc tokens — should default to 1.0."""
        scenario = _make_scenario(story_arc="", devices=[_make_device(owner_story_arc="")])
        result = _check_arc_event_consistency(scenario, {})
        assert result.score == pytest.approx(1.0)


# ===================================================================
# _check_relationship_behavior
# ===================================================================


class TestCheckRelationshipBehavior:
    """Tests for the _check_relationship_behavior check."""

    def test_no_datasets_returns_perfect_score(self) -> None:
        """Empty datasets should produce a perfect 1.0 score."""
        scenario = _make_scenario()
        result = _check_relationship_behavior(scenario, {})
        assert result.score == pytest.approx(1.0)
        assert result.severity == QualitySeverity.OK

    def test_boss_thread_with_low_exclaim_passes(self) -> None:
        """A boss-role thread with few exclamation marks should pass."""
        contact = _make_contact(actor_id="BOSS1", role="boss")
        device = _make_device(contacts=[contact])
        scenario = _make_scenario(devices=[device])

        node = _make_node(
            source="PA001",
            targets=["BOSS1"],
            messages=[
                _make_message("Can we reschedule the meeting?"),
                _make_message("Sure, let me check my calendar."),
                _make_message("Wednesday works for me."),
                _make_message("Sounds good, confirmed."),
                _make_message("Thanks for the update."),
            ],
        )
        ds = _make_dataset([node])
        result = _check_relationship_behavior(scenario, {"dev1": ds})
        assert result.score == pytest.approx(1.0)
        assert result.findings == []

    def test_boss_thread_with_high_exclaim_produces_violation(self) -> None:
        """A boss-role thread with many exclamations should flag a violation."""
        contact = _make_contact(actor_id="BOSS1", role="boss")
        device = _make_device(contacts=[contact])
        scenario = _make_scenario(devices=[device])

        node = _make_node(
            source="PA001",
            targets=["BOSS1"],
            messages=[
                _make_message("OMG that's amazing!"),
                _make_message("No way!!!"),
                _make_message("I can't believe it!"),
                _make_message("This is incredible!"),
            ],
        )
        ds = _make_dataset([node])
        result = _check_relationship_behavior(scenario, {"dev1": ds})
        assert result.score < 1.0
        assert result.metrics["violations"] >= 1.0
        assert len(result.findings) >= 1
        assert "expressive" in result.findings[0].message.lower()

    def test_non_role_thread_is_not_checked(self) -> None:
        """Threads with contacts having no special role should be skipped."""
        contact = _make_contact(actor_id="FRIEND1", role="best friend")
        device = _make_device(contacts=[contact])
        scenario = _make_scenario(devices=[device])

        node = _make_node(
            source="PA001",
            targets=["FRIEND1"],
            messages=[_make_message("OMG! YES! AMAZING! WOW!")],
        )
        ds = _make_dataset([node])
        result = _check_relationship_behavior(scenario, {"dev1": ds})
        assert result.metrics["threads_checked"] == pytest.approx(0.0)
        assert result.score == pytest.approx(1.0)

    def test_doctor_role_checks_exclaim_ratio(self) -> None:
        """Doctor-role contacts should also be checked for exclamation ratio."""
        contact = _make_contact(actor_id="DOC1", role="family doctor")
        device = _make_device(contacts=[contact])
        scenario = _make_scenario(devices=[device])

        node = _make_node(
            source="PA001",
            targets=["DOC1"],
            messages=[
                _make_message("I feel great!"),
                _make_message("The results are amazing!"),
                _make_message("Wow, incredible news!"),
                _make_message("Thank you so much!"),
            ],
        )
        ds = _make_dataset([node])
        result = _check_relationship_behavior(scenario, {"dev1": ds})
        assert result.metrics["threads_checked"] >= 1.0
        assert result.metrics["violations"] >= 1.0


# ===================================================================
# _check_shared_identity_lock
# ===================================================================


class TestCheckSharedIdentityLock:
    """Tests for the _check_shared_identity_lock check."""

    def test_no_shared_contacts_returns_perfect_score(self) -> None:
        """No shared contacts means nothing to validate — score 1.0."""
        scenario = _make_scenario(devices=[_make_device(contacts=[_make_contact()])])
        result = _check_shared_identity_lock(scenario)
        assert result.score == pytest.approx(1.0)
        assert result.findings == []

    def test_consistent_shared_identity_returns_perfect_score(self) -> None:
        """Shared contacts with matching actor_id and personality should pass."""
        same_personality = _make_personality(summary="kind and thoughtful")
        c1 = _make_contact(
            contact_id="c1",
            actor_id="+1111",
            personality=same_personality,
            shared_with=[DeviceContactRef(device_id="dev2", contact_id="c2")],
        )
        c2 = _make_contact(
            contact_id="c2",
            actor_id="+1111",
            personality=same_personality,
        )
        scenario = _make_scenario(
            devices=[
                _make_device(contacts=[c1]),
                _make_device(device_id="dev2", contacts=[c2]),
            ]
        )
        result = _check_shared_identity_lock(scenario)
        assert result.score == pytest.approx(1.0)
        assert result.findings == []

    def test_different_actor_ids_produce_finding(self) -> None:
        """Shared contacts with different actor_ids should trigger a finding."""
        c1 = _make_contact(
            contact_id="c1",
            actor_id="+1111",
            shared_with=[DeviceContactRef(device_id="dev2", contact_id="c2")],
        )
        c2 = _make_contact(contact_id="c2", actor_id="+2222")
        scenario = _make_scenario(
            devices=[
                _make_device(contacts=[c1]),
                _make_device(device_id="dev2", contacts=[c2]),
            ]
        )
        result = _check_shared_identity_lock(scenario)
        assert result.score < 1.0
        assert result.metrics["inconsistencies"] >= 1.0
        assert any("actor ID" in f.message for f in result.findings)

    def test_different_personality_summaries_produce_finding(self) -> None:
        """Same actor_id but different personality summaries should flag."""
        p1 = _make_personality(summary="very kind and thoughtful")
        p2 = _make_personality(summary="grumpy and antagonistic")
        c1 = _make_contact(
            contact_id="c1",
            actor_id="+1111",
            personality=p1,
            shared_with=[DeviceContactRef(device_id="dev2", contact_id="c2")],
        )
        c2 = _make_contact(contact_id="c2", actor_id="+1111", personality=p2)
        scenario = _make_scenario(
            devices=[
                _make_device(contacts=[c1]),
                _make_device(device_id="dev2", contacts=[c2]),
            ]
        )
        result = _check_shared_identity_lock(scenario)
        assert result.metrics["inconsistencies"] >= 1.0
        assert any("personality" in f.message.lower() for f in result.findings)


# ===================================================================
# _check_conversation_memory_quality
# ===================================================================


class TestCheckConversationMemoryQuality:
    """Tests for the _check_conversation_memory_quality check."""

    def test_empty_datasets_returns_perfect_score(self) -> None:
        """No datasets means no threads to check — score 1.0."""
        result = _check_conversation_memory_quality({})
        assert result.score == pytest.approx(1.0)
        assert result.check_id == QualityCheckId.CONVERSATION_MEMORY

    def test_unique_messages_return_perfect_score(self) -> None:
        """All unique messages in a thread should pass."""
        node = _make_node(
            messages=[
                _make_message("hey there"),
                _make_message("how are you"),
                _make_message("fine thanks"),
            ]
        )
        result = _check_conversation_memory_quality({"dev1": _make_dataset([node])})
        assert result.score == pytest.approx(1.0)
        assert result.findings == []

    def test_high_repetition_flags_thread(self) -> None:
        """A thread with many duplicated messages should be flagged."""
        repeated = _make_message("ok sounds good")
        node = _make_node(
            messages=[
                repeated,
                _make_message("ok sounds good"),
                _make_message("ok sounds good"),
                _make_message("ok sounds good"),
                _make_message("ok sounds good"),
                _make_message("ok sounds good"),
                _make_message("ok sounds good"),
                _make_message("something different"),
            ]
        )
        result = _check_conversation_memory_quality({"dev1": _make_dataset([node])})
        assert result.score < 1.0
        assert result.metrics["repetition_flags"] >= 1.0
        assert any("repeated" in f.message.lower() or "looping" in f.message.lower() for f in result.findings)

    def test_thread_with_empty_content_is_counted_but_not_flagged(self) -> None:
        """Threads with messages that have no content should not crash."""
        node = _make_node(messages=[_make_message("")])
        result = _check_conversation_memory_quality({"dev1": _make_dataset([node])})
        assert result.metrics["threads"] == pytest.approx(1.0)
        assert result.findings == []


# ===================================================================
# _check_group_event_coherence
# ===================================================================


class TestCheckGroupEventCoherence:
    """Tests for the _check_group_event_coherence check."""

    def test_no_group_chats_returns_perfect_score(self) -> None:
        """A scenario with no group chats should score 1.0."""
        scenario = _make_scenario(group_chats=[])
        result = _check_group_event_coherence(scenario)
        assert result.score == pytest.approx(1.0)
        assert result.check_id == QualityCheckId.GROUP_EVENT_COHERENCE

    def test_group_with_valid_event_and_start_date_passes(self) -> None:
        """Group correctly linked to an existing event with start_date passes."""
        event = FlexTimelineEvent(id="ev1", date="2025-03-01", description="Party")
        gc = GroupChat(id="gc1", name="Party Group", origin_event_id="ev1", start_date="2025-03-01")
        scenario = _make_scenario(timeline_events=[event], group_chats=[gc])
        result = _check_group_event_coherence(scenario)
        assert result.score == pytest.approx(1.0)
        assert result.findings == []

    def test_group_referencing_missing_event_produces_finding(self) -> None:
        """Group referencing a nonexistent event should flag."""
        gc = GroupChat(id="gc1", name="Ghost Group", origin_event_id="nonexistent")
        scenario = _make_scenario(group_chats=[gc])
        result = _check_group_event_coherence(scenario)
        assert result.score < 1.0
        assert any("missing origin event" in f.message.lower() for f in result.findings)

    def test_group_with_event_but_no_start_date_produces_finding(self) -> None:
        """Group linked to an event that has a date, but group has no start_date."""
        event = FlexTimelineEvent(id="ev1", date="2025-06-01", description="Concert")
        gc = GroupChat(id="gc1", name="Concert Crew", origin_event_id="ev1", start_date="")
        scenario = _make_scenario(timeline_events=[event], group_chats=[gc])
        result = _check_group_event_coherence(scenario)
        assert result.score < 1.0
        assert any("start date" in f.message.lower() or "start_date" in f.message.lower() for f in result.findings)

    def test_group_with_no_origin_event_produces_finding(self) -> None:
        """Group with empty origin_event_id should flag lack of narrative grounding."""
        gc = GroupChat(id="gc1", name="Random Group", origin_event_id="")
        scenario = _make_scenario(group_chats=[gc])
        result = _check_group_event_coherence(scenario)
        assert result.score < 1.0
        assert any("no origin event" in f.message.lower() for f in result.findings)

    def test_multiple_groups_with_issues_lower_score_further(self) -> None:
        """Multiple group issues should compound the score reduction."""
        gc1 = GroupChat(id="gc1", name="Group A", origin_event_id="")
        gc2 = GroupChat(id="gc2", name="Group B", origin_event_id="missing-id")
        scenario = _make_scenario(group_chats=[gc1, gc2])
        result = _check_group_event_coherence(scenario)
        assert result.metrics["coherence_issues"] == pytest.approx(2.0)
        assert result.score == pytest.approx(0.0)


# ===================================================================
# _check_pairwise_coverage
# ===================================================================


class TestCheckPairwiseCoverage:
    """Tests for the _check_pairwise_coverage check."""

    def test_no_generated_data_returns_clean_score(self) -> None:
        """When no data has been generated yet, score should be 1.0 and skip."""
        scenario = _make_scenario()
        result = _check_pairwise_coverage(scenario, {})
        assert result.score == pytest.approx(1.0)
        assert result.metrics["has_generated_data"] == pytest.approx(0.0)

    def test_all_pairs_present_returns_perfect_score(self) -> None:
        """All expected pair threads existing should yield a 1.0 score."""
        contact = _make_contact(contact_id="c1", actor_id="C01")
        device = _make_device(device_id="dev1", owner_actor_id="PA001", contacts=[contact])
        gc = GroupChat(
            id="gc1",
            name="The Crew",
            auto_pair_threads=True,
            members=[
                DeviceContactRef(device_id="dev1", contact_id="__owner__"),
                DeviceContactRef(device_id="dev1", contact_id="c1"),
            ],
        )
        scenario = _make_scenario(devices=[device], group_chats=[gc])
        node = _make_node(source="PA001", targets=["C01"], messages=[_make_message("hi")])
        ds = _make_dataset([node])
        result = _check_pairwise_coverage(scenario, {"dev1": ds})
        assert result.score == pytest.approx(1.0)
        assert result.metrics["missing_pairs"] == pytest.approx(0.0)

    def test_missing_pair_thread_produces_finding(self) -> None:
        """A missing pair thread should reduce score and produce a finding."""
        contact = _make_contact(contact_id="c1", actor_id="C01")
        device = _make_device(device_id="dev1", owner_actor_id="PA001", contacts=[contact])
        gc = GroupChat(
            id="gc1",
            name="The Crew",
            auto_pair_threads=True,
            members=[
                DeviceContactRef(device_id="dev1", contact_id="__owner__"),
                DeviceContactRef(device_id="dev1", contact_id="c1"),
            ],
        )
        scenario = _make_scenario(devices=[device], group_chats=[gc])
        # Dataset has a node but not matching the expected pair
        node = _make_node(source="PA001", targets=["SOMEONE_ELSE"], messages=[_make_message("hi")])
        ds = _make_dataset([node])
        result = _check_pairwise_coverage(scenario, {"dev1": ds})
        assert result.score < 1.0
        assert result.metrics["missing_pairs"] >= 1.0
        assert len(result.findings) >= 1

    def test_auto_pair_threads_false_skips_group(self) -> None:
        """Groups with auto_pair_threads=False should not be checked for coverage."""
        contact = _make_contact(contact_id="c1", actor_id="C01")
        device = _make_device(device_id="dev1", owner_actor_id="PA001", contacts=[contact])
        gc = GroupChat(
            id="gc1",
            name="Silent Group",
            auto_pair_threads=False,
            members=[
                DeviceContactRef(device_id="dev1", contact_id="__owner__"),
                DeviceContactRef(device_id="dev1", contact_id="c1"),
            ],
        )
        scenario = _make_scenario(devices=[device], group_chats=[gc])
        node = _make_node(source="PA001", targets=["SOMEONE_ELSE"], messages=[_make_message("hi")])
        ds = _make_dataset([node])
        result = _check_pairwise_coverage(scenario, {"dev1": ds})
        assert result.metrics["expected_pairs"] == pytest.approx(0.0)
        assert result.score == pytest.approx(1.0)


# ===================================================================
# _check_temporal_realism
# ===================================================================


class TestCheckTemporalRealism:
    """Tests for the _check_temporal_realism check."""

    def test_empty_datasets_returns_perfect_score(self) -> None:
        """No datasets should yield 1.0."""
        result = _check_temporal_realism({})
        assert result.score == pytest.approx(1.0)

    def test_daytime_messages_return_perfect_score(self) -> None:
        """Messages all during daytime hours should pass."""
        node = _make_node(
            messages=[
                _make_message(time="2025-06-15T10:00:00"),
                _make_message(time="2025-06-15T11:00:00"),
                _make_message(time="2025-06-15T14:00:00"),
            ]
        )
        result = _check_temporal_realism({"dev1": _make_dataset([node])})
        assert result.score == pytest.approx(1.0)
        assert result.metrics["overnight_ratio"] == pytest.approx(0.0)

    def test_all_overnight_messages_reduce_score(self) -> None:
        """Messages all before 5 AM should produce a high overnight ratio and lower score."""
        node = _make_node(
            messages=[
                _make_message(time="2025-06-15T01:00:00"),
                _make_message(time="2025-06-15T02:00:00"),
                _make_message(time="2025-06-15T03:00:00"),
                _make_message(time="2025-06-15T04:00:00"),
            ]
        )
        result = _check_temporal_realism({"dev1": _make_dataset([node])})
        assert result.score < 1.0
        assert result.metrics["overnight_ratio"] == pytest.approx(1.0)
        assert any("overnight" in f.message.lower() for f in result.findings)

    def test_non_monotonic_timestamps_produce_finding(self) -> None:
        """Out-of-order timestamps should be detected and flagged."""
        node = _make_node(
            messages=[
                _make_message(time="2025-06-15T14:00:00"),
                _make_message(time="2025-06-15T10:00:00"),
                _make_message(time="2025-06-15T16:00:00"),
            ]
        )
        result = _check_temporal_realism({"dev1": _make_dataset([node])})
        assert result.metrics["non_monotonic_threads"] == pytest.approx(1.0)
        assert any("non-monotonic" in f.message.lower() for f in result.findings)

    def test_both_overnight_and_non_monotonic_compound(self) -> None:
        """Both overnight and non-monotonic issues should reduce score together."""
        overnight_node = _make_node(
            source="PA001",
            targets=["C01"],
            messages=[
                _make_message(time="2025-06-15T01:00:00"),
                _make_message(time="2025-06-15T02:00:00"),
            ],
        )
        reversed_node = _make_node(
            source="PA001",
            targets=["C02"],
            messages=[
                _make_message(time="2025-06-15T15:00:00"),
                _make_message(time="2025-06-15T12:00:00"),
            ],
        )
        result = _check_temporal_realism({"dev1": _make_dataset([overnight_node, reversed_node])})
        assert result.score < 1.0
        assert len(result.findings) >= 1


# ===================================================================
# _check_language_consistency
# ===================================================================


class TestCheckLanguageConsistency:
    """Tests for the _check_language_consistency check."""

    def test_no_messages_returns_perfect_score(self) -> None:
        """No messages in datasets should yield 1.0 with no findings."""
        scenario = _make_scenario(language="en")
        result = _check_language_consistency(scenario, {})
        assert result.score == pytest.approx(1.0)
        assert result.metrics["messages_checked"] == pytest.approx(0.0)

    def test_english_messages_with_en_target_return_high_score(self) -> None:
        """Pure English messages under 'en' language should score near 1.0."""
        scenario = _make_scenario(language="en")
        node = _make_node(
            messages=[
                _make_message("Hello, how are you?"),
                _make_message("I am doing great, thanks!"),
            ]
        )
        result = _check_language_consistency(scenario, {"dev1": _make_dataset([node])})
        assert result.score == pytest.approx(1.0)
        assert result.metrics["messages_checked"] == pytest.approx(2.0)

    def test_arabic_messages_with_en_target_produce_low_score(self) -> None:
        """Arabic messages under 'en' target should lower the score."""
        scenario = _make_scenario(language="en")
        node = _make_node(
            messages=[
                _make_message("مرحبا بالعالم"),
                _make_message("كيف حالك اليوم"),
            ]
        )
        result = _check_language_consistency(scenario, {"dev1": _make_dataset([node])})
        assert result.score < 0.70
        assert len(result.findings) >= 1

    def test_unknown_language_target_always_passes(self) -> None:
        """A language code that isn't 'en' or 'ar' won't trigger warnings."""
        scenario = _make_scenario(language="fr")
        node = _make_node(messages=[_make_message("مرحبا")])
        result = _check_language_consistency(scenario, {"dev1": _make_dataset([node])})
        # _lang_script_ratio returns 1.0 for unknown language — no warning
        assert result.findings == []


# ===================================================================
# _check_temporal_realism — boundary
# ===================================================================


class TestTemporalRealismBoundary:
    """Edge-case boundary tests for temporal realism scoring."""

    def test_hour_four_is_overnight(self) -> None:
        """4 AM is below the 5 AM cutoff and should count as overnight."""
        node = _make_node(messages=[_make_message(time="2025-06-15T04:00:00")])
        result = _check_temporal_realism({"dev1": _make_dataset([node])})
        assert result.metrics["overnight_ratio"] == pytest.approx(1.0)

    def test_hour_five_is_not_overnight(self) -> None:
        """5 AM is at the cutoff boundary and should not count as overnight."""
        node = _make_node(messages=[_make_message(time="2025-06-15T05:00:00")])
        result = _check_temporal_realism({"dev1": _make_dataset([node])})
        assert result.metrics["overnight_ratio"] == pytest.approx(0.0)


# ===================================================================
# evaluate_generation_quality (orchestrator)
# ===================================================================


class TestEvaluateGenerationQuality:
    """Tests for the top-level evaluate_generation_quality orchestrator."""

    def test_empty_scenario_returns_all_checks_and_high_score(self) -> None:
        """An empty scenario with no data should produce all 9 check results and high overall."""
        scenario = _make_scenario()
        report = evaluate_generation_quality(scenario, {})
        assert report.scenario_id == "scenario-1"
        assert len(report.checks) == 9
        assert report.summary.overall_score > 0.9
        assert report.summary.overall_severity == QualitySeverity.OK

    def test_check_ids_cover_all_expected_checks(self) -> None:
        """All 9 QualityCheckId values should appear in the report."""
        scenario = _make_scenario()
        report = evaluate_generation_quality(scenario, {})
        check_ids = {c.check_id for c in report.checks}
        expected = {
            QualityCheckId.PERSONALITY_COHERENCE,
            QualityCheckId.ARC_EVENT_CONSISTENCY,
            QualityCheckId.RELATIONSHIP_BEHAVIOR,
            QualityCheckId.SHARED_IDENTITY_LOCK,
            QualityCheckId.GROUP_EVENT_COHERENCE,
            QualityCheckId.PAIRWISE_COVERAGE,
            QualityCheckId.CONVERSATION_MEMORY,
            QualityCheckId.TEMPORAL_REALISM,
            QualityCheckId.LANGUAGE_CONSISTENCY,
        }
        assert check_ids == expected

    def test_findings_are_aggregated_and_sorted(self) -> None:
        """Top findings should be sorted by severity (CRITICAL first) then score ascending."""
        p = _make_personality(summary="introvert and extrovert")
        gc = GroupChat(id="gc1", name="Bad Group", origin_event_id="missing")
        scenario = _make_scenario(
            devices=[_make_device(contacts=[_make_contact(personality=p)])],
            group_chats=[gc],
        )
        report = evaluate_generation_quality(scenario, {})
        assert report.summary.findings_total >= 2
        assert len(report.top_findings) <= 15
        # CRITICAL findings should appear before WARNING ones
        severities = [f.severity for f in report.top_findings]
        critical_indices = [i for i, s in enumerate(severities) if s == QualitySeverity.CRITICAL]
        warning_indices = [i for i, s in enumerate(severities) if s == QualitySeverity.WARNING]
        if critical_indices and warning_indices:
            assert max(critical_indices) < min(warning_indices)

    def test_summary_counts_match_findings(self) -> None:
        """Summary critical/warning/ok counts should match actual findings."""
        scenario = _make_scenario()
        report = evaluate_generation_quality(scenario, {})
        total_from_counts = report.summary.critical_count + report.summary.warning_count + report.summary.ok_count
        assert total_from_counts == report.summary.findings_total

    def test_weighted_score_is_within_valid_range(self) -> None:
        """Overall score should be between 0 and 1."""
        scenario = _make_scenario()
        report = evaluate_generation_quality(scenario, {})
        assert 0.0 <= report.summary.overall_score <= 1.0

    def test_check_scores_dict_uses_string_keys(self) -> None:
        """The check_scores dict in summary should use string enum values as keys."""
        scenario = _make_scenario()
        report = evaluate_generation_quality(scenario, {})
        for key in report.summary.check_scores:
            assert isinstance(key, str)
        assert "personality_coherence" in report.summary.check_scores


# ===================================================================
# quick_thread_findings
# ===================================================================


class TestQuickThreadFindings:
    """Tests for the quick_thread_findings SSE helper."""

    def test_no_messages_returns_empty(self) -> None:
        """Empty message list should produce no findings."""
        findings = quick_thread_findings([], role="boss", language="en", entity_id="thread-1")
        assert findings == []

    def test_high_repeat_ratio_produces_finding(self) -> None:
        """Many duplicated messages should trigger a memory finding."""
        messages = [_make_message("ok sure")] * 8 + [_make_message("something else")]
        findings = quick_thread_findings(messages, role="friend", language="en", entity_id="t1")
        memory_findings = [f for f in findings if f.check_id == QualityCheckId.CONVERSATION_MEMORY]
        assert len(memory_findings) >= 1
        assert "duplicate" in memory_findings[0].message.lower() or "repeated" in memory_findings[0].message.lower()

    def test_low_repeat_ratio_produces_no_memory_finding(self) -> None:
        """Unique messages should not trigger a memory finding."""
        messages = [_make_message(f"unique message {i}") for i in range(10)]
        findings = quick_thread_findings(messages, role="friend", language="en", entity_id="t1")
        memory_findings = [f for f in findings if f.check_id == QualityCheckId.CONVERSATION_MEMORY]
        assert memory_findings == []

    def test_boss_role_high_exclaim_produces_finding(self) -> None:
        """Boss role thread with many exclamation marks should flag."""
        messages = [
            _make_message("Great job!"),
            _make_message("Amazing work!"),
            _make_message("Incredible!"),
            _make_message("Wonderful!"),
        ]
        findings = quick_thread_findings(messages, role="boss", language="en", entity_id="t1")
        behavior_findings = [f for f in findings if f.check_id == QualityCheckId.RELATIONSHIP_BEHAVIOR]
        assert len(behavior_findings) >= 1

    def test_non_formal_role_skips_exclaim_check(self) -> None:
        """Non-boss/manager/doctor role should not trigger exclaim check."""
        messages = [_make_message("WOW!"), _make_message("AMAZING!"), _make_message("YES!")]
        findings = quick_thread_findings(messages, role="best friend", language="en", entity_id="t1")
        behavior_findings = [f for f in findings if f.check_id == QualityCheckId.RELATIONSHIP_BEHAVIOR]
        assert behavior_findings == []

    def test_language_drift_produces_finding(self) -> None:
        """Arabic text with 'en' target should trigger language drift."""
        messages = [_make_message("مرحبا بالعالم"), _make_message("كيف حالك")]
        findings = quick_thread_findings(messages, role="friend", language="en", entity_id="t1")
        lang_findings = [f for f in findings if f.check_id == QualityCheckId.LANGUAGE_CONSISTENCY]
        assert len(lang_findings) >= 1
        assert "language" in lang_findings[0].message.lower()

    def test_correct_language_no_drift_finding(self) -> None:
        """English text with 'en' target should produce no language findings."""
        messages = [_make_message("Hello there"), _make_message("How is your day")]
        findings = quick_thread_findings(messages, role="friend", language="en", entity_id="t1")
        lang_findings = [f for f in findings if f.check_id == QualityCheckId.LANGUAGE_CONSISTENCY]
        assert lang_findings == []

    def test_manager_role_high_exclaim_produces_finding(self) -> None:
        """Manager role thread with high exclaim ratio should flag."""
        messages = [_make_message("Wow!"), _make_message("Great!"), _make_message("Yes!")]
        findings = quick_thread_findings(messages, role="manager", language="en", entity_id="t1")
        behavior_findings = [f for f in findings if f.check_id == QualityCheckId.RELATIONSHIP_BEHAVIOR]
        assert len(behavior_findings) >= 1

    def test_empty_content_messages_return_empty_findings(self) -> None:
        """Messages with empty Content should produce no findings."""
        messages = [_make_message(""), _make_message("")]
        findings = quick_thread_findings(messages, role="boss", language="en", entity_id="t1")
        assert findings == []

    @pytest.mark.parametrize("language", ["fr", "de", "es", "zz"])
    def test_non_en_ar_language_skips_drift_check(self, language: str) -> None:
        """Language drift check only fires for 'en' and 'ar'."""
        messages = [_make_message("مرحبا"), _make_message("你好")]
        findings = quick_thread_findings(messages, role="friend", language=language, entity_id="t1")
        lang_findings = [f for f in findings if f.check_id == QualityCheckId.LANGUAGE_CONSISTENCY]
        assert lang_findings == []
