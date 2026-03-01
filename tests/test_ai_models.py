"""Tests for AI-assist Pydantic response model validation."""

# ruff: noqa: S101

from __future__ import annotations

import pytest

from source.ai_assist import (
    EventParticipant,
    FullEvent,
    GenerateCharacterArcsResponse,
    GenerateNamesResponse,
    GenerateStoryArcResponse,
    GroupChatQuality,
    SuggestConnectionsResponse,
    SuggestedConnection,
    SuggestedEvent,
    SuggestedGroupChat,
    SuggestEventsResponse,
    SuggestGroupChatsResponse,
)

# ---------------------------------------------------------------------------
# SuggestEventsResponse
# ---------------------------------------------------------------------------


def test_suggest_events_response_validates_correct_data() -> None:
    """SuggestEventsResponse should accept a well-formed list of events."""
    response = SuggestEventsResponse(
        events=[
            SuggestedEvent(
                date="2025-06-15",
                time="14:00",
                description="Lunch at the diner",
                device1_impact="Owner texts about the lunch",
                device2_impact="Contact mentions running into someone",
                involved_d1_contacts=["Sam"],
                involved_d2_contacts=["Lee"],
            )
        ]
    )

    assert len(response.events) == 1
    assert response.events[0].date == "2025-06-15"
    assert response.events[0].involved_d1_contacts == ["Sam"]


def test_suggest_events_response_accepts_empty_events() -> None:
    """SuggestEventsResponse should accept an empty event list."""
    response = SuggestEventsResponse(events=[])

    assert response.events == []


def test_suggest_events_response_defaults_to_empty_list() -> None:
    """SuggestEventsResponse with no events argument should default to empty list."""
    response = SuggestEventsResponse()

    assert response.events == []


# ---------------------------------------------------------------------------
# GenerateCharacterArcsResponse
# ---------------------------------------------------------------------------


def test_character_arcs_response_handles_empty_arcs() -> None:
    """GenerateCharacterArcsResponse should accept an empty arcs dictionary."""
    response = GenerateCharacterArcsResponse(arcs={})

    assert response.arcs == {}


def test_character_arcs_response_stores_arcs_correctly() -> None:
    """GenerateCharacterArcsResponse should store character arcs by name."""
    response = GenerateCharacterArcsResponse(
        arcs={
            "Alex Rivera": "Grows bolder and takes risks throughout the timeline.",
            "Sam Chen": "Retreats into secrecy after the midpoint incident.",
        }
    )

    assert "Alex Rivera" in response.arcs
    assert "bold" in response.arcs["Alex Rivera"].lower()


def test_character_arcs_response_defaults_to_empty_dict() -> None:
    """GenerateCharacterArcsResponse with no arguments should default to empty dict."""
    response = GenerateCharacterArcsResponse()

    assert response.arcs == {}


# ---------------------------------------------------------------------------
# SuggestGroupChatsResponse
# ---------------------------------------------------------------------------


def test_group_chats_response_includes_quality_data() -> None:
    """SuggestGroupChatsResponse should carry both group chats and quality assessment."""
    response = SuggestGroupChatsResponse(
        group_chats=[
            SuggestedGroupChat(
                name="The Crew",
                members=[
                    EventParticipant(device_id="d1", contact_id="c1"),
                    EventParticipant(device_id="d1", contact_id="c2"),
                    EventParticipant(device_id="d2", contact_id="__owner__"),
                ],
                vibe="casual banter",
                message_volume="regular",
                start_date="2025-04-01",
                origin_event_id="ev-1",
            )
        ],
        quality=GroupChatQuality(score=0.85, severity="ok", findings=[]),
    )

    assert len(response.group_chats) == 1
    assert response.group_chats[0].name == "The Crew"
    assert response.quality.score == pytest.approx(0.85)
    assert response.quality.severity == "ok"


def test_group_chats_response_quality_defaults() -> None:
    """GroupChatQuality should default to score 0.0 and severity 'ok'."""
    quality = GroupChatQuality()

    assert quality.score == pytest.approx(0.0)
    assert quality.severity == "ok"
    assert quality.findings == []


def test_group_chats_response_accepts_empty_groups() -> None:
    """SuggestGroupChatsResponse should accept an empty group list."""
    response = SuggestGroupChatsResponse(group_chats=[])

    assert response.group_chats == []


# ---------------------------------------------------------------------------
# GenerateNamesResponse
# ---------------------------------------------------------------------------


def test_generate_names_response_parallel_arrays() -> None:
    """Names and roles should be parallel arrays of equal length."""
    response = GenerateNamesResponse(
        names=["Alex Rivera", "Sam Chen", "Jordan Lee"],
        roles=["best friend", "coworker", "cousin"],
    )

    assert len(response.names) == 3
    assert len(response.roles) == 3
    assert response.roles[1] == "coworker"


def test_generate_names_response_roles_default_to_empty() -> None:
    """Roles should default to an empty list when not provided."""
    response = GenerateNamesResponse(names=["Alex"])

    assert response.roles == []


# ---------------------------------------------------------------------------
# GenerateStoryArcResponse
# ---------------------------------------------------------------------------


def test_story_arc_response_stores_text() -> None:
    """GenerateStoryArcResponse should store the arc narrative text."""
    response = GenerateStoryArcResponse(story_arc="A detective uncovers the truth.")

    assert "detective" in response.story_arc


def test_story_arc_response_defaults_to_empty() -> None:
    """GenerateStoryArcResponse should default to empty string."""
    response = GenerateStoryArcResponse()

    assert not response.story_arc


# ---------------------------------------------------------------------------
# SuggestConnectionsResponse
# ---------------------------------------------------------------------------


def test_connections_response_validates_connections() -> None:
    """SuggestConnectionsResponse should validate and store connections."""
    response = SuggestConnectionsResponse(
        connections=[
            SuggestedConnection(
                type="shared_character",
                description="Sam appears on both phones",
                device1_contact="Sam",
                device2_contact="Sam C.",
                forensic_note="Same person, different names",
            )
        ]
    )

    assert len(response.connections) == 1
    assert response.connections[0].type == "shared_character"


# ---------------------------------------------------------------------------
# FullEvent
# ---------------------------------------------------------------------------


def test_full_event_carries_resolved_participants() -> None:
    """FullEvent should store resolved device/contact participant references."""
    event = FullEvent(
        date="2025-07-04",
        time="19:00",
        description="July 4th barbecue at the park",
        participants=[
            EventParticipant(device_id="d1", contact_id="__owner__"),
            EventParticipant(device_id="d2", contact_id="c3"),
        ],
        device_impacts={"d1": "Owner mentions the BBQ", "d2": "Contact sends photos"},
    )

    assert len(event.participants) == 2
    assert event.participants[0].contact_id == "__owner__"
    assert "d1" in event.device_impacts


# ---------------------------------------------------------------------------
# SuggestedGroupChat
# ---------------------------------------------------------------------------


def test_suggested_group_chat_defaults() -> None:
    """SuggestedGroupChat should have sensible defaults for optional fields."""
    group = SuggestedGroupChat(name="The Squad")

    assert group.message_volume == "regular"
    assert group.activation_mode == "event_time"
    assert group.auto_pair_threads is True
    assert group.quality_score == pytest.approx(1.0)
    assert group.members == []
