"""Tests for source.spam — spam generation, category detection, timestamps, and exchange threads."""

# ruff: noqa: S101, PLC2701

from __future__ import annotations

import random
from datetime import date, datetime

import pytest

from source.models import DeviceScenario, FlexPersonalityProfile, GenerationSettings
from source.spam import (
    SPAM_DENSITY_RANGE,
    _build_exchange_thread,
    _detect_spam_categories,
    _random_timestamp,
    generate_spam_messages,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_settings() -> GenerationSettings:
    """Provide generation settings with a narrow date range for deterministic tests.

    Returns:
        A GenerationSettings covering March 2025.

    """
    return GenerationSettings(date_start="2025-03-01", date_end="2025-03-31")


def _make_device(
    *,
    spam_density: str = "medium",
    hobbies: list[str] | None = None,
    food_and_drink: str = "",
) -> DeviceScenario:
    """Build a DeviceScenario with optional personality traits for spam detection tests.

    Args:
        spam_density: Per-device spam level.
        hobbies: Owner's hobbies to populate personality profile.
        food_and_drink: Owner's food preference text.

    Returns:
        A DeviceScenario configured with the given personality traits.

    """
    personality = FlexPersonalityProfile(
        hobbies_and_interests=hobbies or [],
        food_and_drink=food_and_drink,
    )
    return DeviceScenario(
        id="dev-test",
        device_label="Test Phone",
        owner_name="Tester",
        owner_actor_id="owner-1",
        spam_density=spam_density,
        owner_personality=personality,
    )


# ---------------------------------------------------------------------------
# generate_spam_messages
# ---------------------------------------------------------------------------


def test_generate_spam_messages_none_density(minimal_settings: GenerationSettings) -> None:
    """spam_density='none' produces zero spam nodes and zero actors."""
    device = _make_device(spam_density="none")

    nodes, actors = generate_spam_messages(device, minimal_settings)

    assert nodes == []
    assert actors == []


def test_generate_spam_messages_low_density(minimal_settings: GenerationSettings) -> None:
    """spam_density='low' produces a thread count within the configured low range."""
    random.seed(42)
    device = _make_device(spam_density="low")

    nodes, actors = generate_spam_messages(device, minimal_settings)

    lo, hi = SPAM_DENSITY_RANGE["low"]
    assert lo <= len(nodes) <= hi
    assert len(actors) == len(nodes)


# ---------------------------------------------------------------------------
# _detect_spam_categories
# ---------------------------------------------------------------------------


def test_detect_spam_categories_includes_general() -> None:
    """'general' is always present regardless of personality content."""
    device = _make_device(hobbies=[])

    categories = _detect_spam_categories(device)

    assert "general" in categories


@pytest.mark.parametrize(
    ("hobbies", "food", "expected_category"),
    [
        (["gaming", "streaming"], "", "tech"),
        ([], "loves cooking Italian food", "food"),
        (["yoga", "running"], "", "health"),
    ],
    ids=["gaming-tech", "cooking-food", "fitness-health"],
)
def test_detect_spam_categories_matches_interests(
    hobbies: list[str],
    food: str,
    expected_category: str,
) -> None:
    """Interest keywords in hobbies and food_and_drink map to expected spam categories."""
    device = _make_device(hobbies=hobbies, food_and_drink=food)

    categories = _detect_spam_categories(device)

    assert expected_category in categories
    assert "general" in categories


# ---------------------------------------------------------------------------
# _random_timestamp
# ---------------------------------------------------------------------------


def test_random_timestamp_in_range() -> None:
    """Generated timestamp falls within the specified date range (inclusive)."""
    random.seed(99)
    ts = _random_timestamp("2025-06-01", "2025-06-30")
    dt = datetime.fromisoformat(ts)

    assert dt.date() >= date(2025, 6, 1)
    assert dt.date() <= date(2025, 6, 30)


def test_random_timestamp_business_hours() -> None:
    """With business_hours=True, the hour is between 9 and 17 inclusive."""
    random.seed(7)
    for _ in range(50):
        ts = _random_timestamp("2025-01-01", "2025-12-31", business_hours=True)
        dt = datetime.fromisoformat(ts)
        assert 9 <= dt.hour <= 17, f"Hour {dt.hour} outside business range"


# ---------------------------------------------------------------------------
# _build_exchange_thread
# ---------------------------------------------------------------------------


def test_build_exchange_thread_message_count() -> None:
    """The thread contains exactly one Message per turn in the exchange template."""
    random.seed(0)
    exchange = [
        {"dir": "incoming", "text": "Hey!"},
        {"dir": "outgoing", "text": "Wrong number"},
        {"dir": "incoming", "text": "Sorry!"},
    ]

    msgs = _build_exchange_thread(exchange, "owner-1", "stranger-1", "2025-05-10T14:00:00-05:00")

    assert len(msgs) == 3
    assert msgs[0].Content == "Hey!"
    assert msgs[1].Content == "Wrong number"
    assert msgs[2].Content == "Sorry!"


def test_build_exchange_thread_alternating_senders() -> None:
    """Incoming turns use sender_id, outgoing turns use owner_id."""
    random.seed(0)
    exchange = [
        {"dir": "incoming", "text": "Hello"},
        {"dir": "outgoing", "text": "Who?"},
        {"dir": "incoming", "text": "My bad"},
    ]

    msgs = _build_exchange_thread(exchange, "owner-1", "stranger-1", "2025-05-10T14:00:00-05:00")

    assert msgs[0].SenderActorId == "stranger-1"
    assert msgs[0].Direction == "incoming"
    assert msgs[1].SenderActorId == "owner-1"
    assert msgs[1].Direction == "outgoing"
    assert msgs[2].SenderActorId == "stranger-1"
    assert msgs[2].Direction == "incoming"
