"""Tests for extracted generator helper functions and _GenerationRun."""

# ruff: noqa: S101, PLC2701

from __future__ import annotations

from unittest.mock import patch

from messageviewer.models import Actor, ConversationNode, Message, SmsDataset
from source.generation_pipeline import _GenerationRun
from source.models import (
    ContactSlot,
    DeviceScenario,
    ResolutionItem,
    ScenarioConfig,
)
from source.quality_fix import (
    _apply_temporal_sort,
    _collect_standalone_character_names,
    _find_arc,
)

# ---------------------------------------------------------------------------
# _GenerationRun
# ---------------------------------------------------------------------------


def test_generation_run_init_captures_scenario_state() -> None:
    """_GenerationRun should store scenario, settings, and device count on init."""
    scenario = ScenarioConfig(
        devices=[
            DeviceScenario(id="d1", device_label="Device 1"),
            DeviceScenario(id="d2", device_label="Device 2"),
        ]
    )

    run = _GenerationRun(scenario, resume=False, override_checks=False)

    assert run.scenario is scenario
    assert run.total_devices == 2
    assert run.resume is False
    assert run.override_checks is False
    assert run.quota_exhausted is False
    assert run.validation_blocked is False
    assert run.run_log_entries == []


def test_generation_run_record_appends_log_entry() -> None:
    """record() should append a RunLogEntry with the given level, type, and payload."""
    scenario = ScenarioConfig()
    run = _GenerationRun(scenario, resume=False, override_checks=False)

    run.record("info", "test_event", {"key": "value"})

    assert len(run.run_log_entries) == 1
    entry = run.run_log_entries[0]
    assert entry.level == "info"
    assert entry.event_type == "test_event"
    assert entry.payload == {"key": "value"}
    assert entry.timestamp


def test_generation_run_record_defaults_empty_payload() -> None:
    """record() without a payload should default to an empty dict."""
    scenario = ScenarioConfig()
    run = _GenerationRun(scenario, resume=False, override_checks=False)

    run.record("warning", "quota_warning")

    assert run.run_log_entries[0].payload == {}


# ---------------------------------------------------------------------------
# _find_arc
# ---------------------------------------------------------------------------


def test_find_arc_exact_match() -> None:
    """Exact lowercase match should return the arc description."""
    arcs = {"alex rivera": "Grows more confident over time.", "sam chen": "Withdraws into secrecy."}

    result = _find_arc("Alex Rivera", arcs)

    assert result == "Grows more confident over time."


def test_find_arc_fuzzy_match_name_in_key() -> None:
    """Substring match (name in key) should return the arc."""
    arcs = {"alex rivera jr.": "Junior arc text."}

    result = _find_arc("Alex Rivera", arcs)

    assert result == "Junior arc text."


def test_find_arc_fuzzy_match_key_in_name() -> None:
    """Substring match (key in name) should return the arc."""
    arcs = {"alex": "Short name arc."}

    result = _find_arc("Alex Rivera", arcs)

    assert result == "Short name arc."


def test_find_arc_returns_none_when_no_match() -> None:
    """No matching arc should return None."""
    arcs = {"jordan lee": "Jordan's arc."}

    result = _find_arc("Alex Rivera", arcs)

    assert result is None


def test_find_arc_handles_empty_name_with_empty_dict() -> None:
    """Empty name with an empty arcs dict should return None gracefully."""
    result = _find_arc("", {})

    assert result is None


def test_find_arc_empty_name_matches_via_substring() -> None:
    """Empty string is a substring of any key, so _find_arc returns the first arc."""
    arcs = {"alex": "some arc"}

    result = _find_arc("", arcs)

    assert result == "some arc"


# ---------------------------------------------------------------------------
# _collect_standalone_character_names
# ---------------------------------------------------------------------------


def test_collect_standalone_names_from_standalone_devices() -> None:
    """Should collect owner and contact names from standalone-mode devices."""
    scenario = ScenarioConfig(
        devices=[
            DeviceScenario(
                id="d1",
                device_label="Device 1",
                owner_name="Alice",
                generation_mode="standalone",
                contacts=[
                    ContactSlot(id="c1", name="Bob"),
                    ContactSlot(id="c2", name="Carol"),
                ],
            ),
            DeviceScenario(
                id="d2",
                device_label="Device 2",
                owner_name="Dave",
                generation_mode="story",
                contacts=[ContactSlot(id="c3", name="Eve")],
            ),
        ]
    )

    names = _collect_standalone_character_names(scenario)

    assert "Alice" in names
    assert "Bob" in names
    assert "Carol" in names
    assert "Dave" not in names
    assert "Eve" not in names


def test_collect_standalone_names_deduplicates() -> None:
    """Duplicate names across standalone devices should be deduplicated."""
    scenario = ScenarioConfig(
        devices=[
            DeviceScenario(
                id="d1",
                owner_name="Alice",
                generation_mode="standalone",
                contacts=[ContactSlot(id="c1", name="Bob")],
            ),
            DeviceScenario(
                id="d2",
                owner_name="Alice",
                generation_mode="standalone",
                contacts=[ContactSlot(id="c2", name="Bob")],
            ),
        ]
    )

    names = _collect_standalone_character_names(scenario)

    assert names.count("Alice") == 1
    assert names.count("Bob") == 1


def test_collect_standalone_names_returns_empty_for_all_story_devices() -> None:
    """When all devices are story-mode, should return an empty list."""
    scenario = ScenarioConfig(
        devices=[
            DeviceScenario(id="d1", owner_name="Alice", generation_mode="story"),
        ]
    )

    names = _collect_standalone_character_names(scenario)

    assert names == []


# ---------------------------------------------------------------------------
# _apply_temporal_sort
# ---------------------------------------------------------------------------


def _make_message(time: str, sender: str = "A", direction: str = "outgoing") -> Message:
    """Build a Message instance with the specified timestamp.

    Args:
        time: ISO timestamp string.
        sender: Sender actor ID.
        direction: Message direction.

    Returns:
        A Message instance.

    """
    return Message(
        SenderActorId=sender,
        Content="test",
        TransferTime=time,
        Direction=direction,
        ServiceName="SMS",
    )


@patch("source.quality_fix.persistence")
def test_apply_temporal_sort_fixes_out_of_order_messages(mock_persistence: object) -> None:
    """Out-of-order messages should be sorted by TransferTime."""
    scenario = ScenarioConfig(devices=[DeviceScenario(id="d1", device_label="Device 1")])
    node = ConversationNode(
        source="owner",
        target=["contact"],
        type="SMS",
        message_content=[
            _make_message("2025-03-15T12:00:00"),
            _make_message("2025-03-15T08:00:00"),
            _make_message("2025-03-15T10:00:00"),
        ],
    )
    dataset = SmsDataset(nodes=[node], actors=[Actor(ActorId="owner", Name="Owner")])
    datasets = {"d1": dataset}
    device_numbers = {"d1": 1}
    adjustments: list[str] = []
    resolution_items: list[ResolutionItem] = []

    _apply_temporal_sort(scenario, datasets, device_numbers, adjustments, resolution_items)

    times = [m.TransferTime for m in node.message_content]
    assert times == ["2025-03-15T08:00:00", "2025-03-15T10:00:00", "2025-03-15T12:00:00"]
    assert len(adjustments) == 1
    assert "Sorted" in adjustments[0]


@patch("source.quality_fix.persistence")
def test_apply_temporal_sort_skips_already_sorted(mock_persistence: object) -> None:
    """Already-sorted threads should not trigger any adjustments."""
    scenario = ScenarioConfig(devices=[DeviceScenario(id="d1", device_label="Device 1")])
    node = ConversationNode(
        source="owner",
        target=["contact"],
        type="SMS",
        message_content=[
            _make_message("2025-03-15T08:00:00"),
            _make_message("2025-03-15T10:00:00"),
            _make_message("2025-03-15T12:00:00"),
        ],
    )
    dataset = SmsDataset(nodes=[node], actors=[Actor(ActorId="owner", Name="Owner")])
    datasets = {"d1": dataset}
    device_numbers = {"d1": 1}
    adjustments: list[str] = []
    resolution_items: list[ResolutionItem] = []

    _apply_temporal_sort(scenario, datasets, device_numbers, adjustments, resolution_items)

    assert adjustments == []
    assert resolution_items == []


@patch("source.quality_fix.persistence")
def test_apply_temporal_sort_skips_single_message_threads(mock_persistence: object) -> None:
    """Threads with fewer than _MIN_MESSAGES_FOR_SORT messages should be skipped."""
    scenario = ScenarioConfig(devices=[DeviceScenario(id="d1", device_label="Device 1")])
    node = ConversationNode(
        source="owner",
        target=["contact"],
        type="SMS",
        message_content=[_make_message("2025-03-15T12:00:00")],
    )
    dataset = SmsDataset(nodes=[node], actors=[Actor(ActorId="owner", Name="Owner")])
    datasets = {"d1": dataset}
    device_numbers = {"d1": 1}
    adjustments: list[str] = []
    resolution_items: list[ResolutionItem] = []

    _apply_temporal_sort(scenario, datasets, device_numbers, adjustments, resolution_items)

    assert adjustments == []
