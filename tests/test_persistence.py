"""Tests for source.persistence — path sanitization, dataset I/O, and SSE formatting."""

# ruff: noqa: S101, PLC2701

from __future__ import annotations

import json
from pathlib import Path

import pytest

import source.persistence as persistence_mod
from messageviewer.models import Actor, ConversationNode, Message, SmsDataset
from source.persistence import (
    _safe_label_slug,
    finding_to_sse,
    load_existing_device_data,
    parse_messages_schema_dataset,
    sanitize_path_component,
    save_device_data,
    to_messages_schema_payload,
)
from source.quality_models import QualityCheckId, QualityFinding, QualitySeverity

# ---------------------------------------------------------------------------
# sanitize_path_component
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "etc_passwd"),
        ("..\\..\\windows\\system32", "windows_system32"),
    ],
    ids=["unix-traversal", "windows-traversal"],
)
def test_sanitize_path_component_strips_traversal(raw: str, expected: str) -> None:
    """Directory-traversal sequences are collapsed into safe underscored fragments."""
    assert sanitize_path_component(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("path/to/file", "path_to_file"),
        ("back\\slash", "back_slash"),
    ],
    ids=["forward-slash", "backslash"],
)
def test_sanitize_path_component_strips_slashes(raw: str, expected: str) -> None:
    """Forward and back slashes are replaced with underscores."""
    assert sanitize_path_component(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", None],
    ids=["empty", "whitespace", "none"],
)
def test_sanitize_path_component_handles_empty(raw: str | None) -> None:
    """Empty, whitespace-only, or None inputs return the sentinel 'unknown'."""
    assert sanitize_path_component(raw) == "unknown"  # type: ignore[arg-type]


def test_sanitize_path_component_preserves_safe_chars() -> None:
    """Alphanumeric characters, hyphens, and underscores pass through unchanged."""
    safe_input = "my-scenario_2025"
    assert sanitize_path_component(safe_input) == "my-scenario_2025"


# ---------------------------------------------------------------------------
# _safe_label_slug
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Alex's Phone", "alex_s_phone"),
        ("Device #2!", "device_2"),
        ("UPPER Case", "upper_case"),
        ("", "device"),
    ],
    ids=["apostrophe", "special-chars", "mixed-case", "empty"],
)
def test_safe_label_slug(label: str, expected: str) -> None:
    """Device labels are slugified to lowercase with special chars replaced."""
    assert _safe_label_slug(label) == expected


# ---------------------------------------------------------------------------
# save / load device data roundtrip
# ---------------------------------------------------------------------------


def _make_minimal_dataset() -> SmsDataset:
    """Build a small SmsDataset for roundtrip testing.

    Returns:
        An SmsDataset with two actors and one conversation node containing two messages.

    """
    return SmsDataset(
        actors=[
            Actor(ActorId="owner-1", Name="Alice"),
            Actor(ActorId="contact-1", Name="Bob"),
        ],
        nodes=[
            ConversationNode(
                source="owner-1",
                target=["contact-1"],
                type="SMS",
                message_content=[
                    Message(
                        SenderActorId="owner-1",
                        Content="Hey Bob!",
                        TransferTime="2025-06-15T10:30:00-05:00",
                        Direction="outgoing",
                        ServiceName="SMS",
                    ),
                    Message(
                        SenderActorId="contact-1",
                        Content="Hi Alice!",
                        TransferTime="2025-06-15T10:31:00-05:00",
                        Direction="incoming",
                        ServiceName="SMS",
                    ),
                ],
            )
        ],
    )


def test_save_and_load_device_data_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A saved dataset can be loaded back and retains actors and message content."""
    monkeypatch.setattr(persistence_mod, "OUTPUT_DIR", tmp_path)

    dataset = _make_minimal_dataset()
    save_device_data("scen-1", "dev-1", dataset)

    loaded = load_existing_device_data("scen-1", "dev-1")
    assert loaded is not None
    assert len(loaded.actors) == 2
    assert loaded.actors[0].Name == "Alice"
    assert len(loaded.nodes) == 1
    assert len(loaded.nodes[0].message_content) == 2
    assert loaded.nodes[0].message_content[0].Content == "Hey Bob!"
    assert loaded.nodes[0].message_content[1].Content == "Hi Alice!"


def test_load_existing_device_data_returns_none_for_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading a nonexistent device dataset returns None rather than raising."""
    monkeypatch.setattr(persistence_mod, "OUTPUT_DIR", tmp_path)

    result = load_existing_device_data("nonexistent-scenario", "nonexistent-device")
    assert result is None


# ---------------------------------------------------------------------------
# to_messages_schema_payload / parse_messages_schema_dataset
# ---------------------------------------------------------------------------


def test_to_messages_schema_payload_structure() -> None:
    """The messages-schema payload has top-level 'actors' and 'messages' keys with correct shapes."""
    dataset = _make_minimal_dataset()

    payload = to_messages_schema_payload(dataset)

    assert "actors" in payload
    assert "messages" in payload

    actors = payload["actors"]
    assert isinstance(actors, list)
    assert len(actors) == 2
    assert actors[0]["ActorId"] == "owner-1"
    assert actors[0]["Name"] == "Alice"

    messages = payload["messages"]
    assert isinstance(messages, list)
    assert len(messages) == 1
    conv = messages[0]
    assert conv["source"] == "owner-1"
    assert conv["target"] == "contact-1"
    assert conv["message_count"] == 2


def test_parse_messages_schema_dataset_roundtrip() -> None:
    """Converting to messages schema and parsing back preserves actors and message content."""
    original = _make_minimal_dataset()

    payload = to_messages_schema_payload(original)
    restored = parse_messages_schema_dataset(payload)

    assert len(restored.actors) == 2
    actor_names = {a.Name for a in restored.actors}
    assert "Alice" in actor_names
    assert "Bob" in actor_names

    assert len(restored.nodes) == 1
    assert len(restored.nodes[0].message_content) == 2
    assert restored.nodes[0].message_content[0].Content == "Hey Bob!"
    assert restored.nodes[0].message_content[1].Direction == "incoming"


# ---------------------------------------------------------------------------
# finding_to_sse
# ---------------------------------------------------------------------------


def test_finding_to_sse_format() -> None:
    """SSE output starts with 'data: ', ends with double newline, and contains valid JSON."""
    finding = QualityFinding(
        check_id=QualityCheckId.TEMPORAL_REALISM,
        severity=QualitySeverity.WARNING,
        score=0.72,
        scope="device",
        entity_id="dev-1",
        message="Timestamps overlap",
        suggestion="Space messages further apart",
    )

    sse = finding_to_sse(finding)

    assert sse.startswith("data: ")
    assert sse.endswith("\n\n")

    json_body = json.loads(sse.removeprefix("data: ").strip())
    assert json_body["type"] == "quality_warning"
    assert json_body["check_id"] == "temporal_realism"
    assert json_body["severity"] == "warning"
    assert json_body["score"] == pytest.approx(0.72)
    assert json_body["scope"] == "device"
    assert json_body["entity_id"] == "dev-1"
    assert json_body["message"] == "Timestamps overlap"
    assert json_body["suggestion"] == "Space messages further apart"
