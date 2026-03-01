"""Unit tests for generation core utility functions."""

# ruff: noqa: S101

from __future__ import annotations

import pytest

from source.llm_client import StoryState, merge_story_states, parse_llm_response


def test_parse_llm_response_handles_code_fence_and_story_state() -> None:
    """Parse fenced JSON and extract story state."""
    raw = """```json
{"messages":["m1","m2"],"story_state":{"topics_covered":["rent"],"key_events":[],"unresolved_threads":[],"relationship_vibe":"tense","owner_state":"stressed","contact_state":"calm"}}
```"""
    messages, state = parse_llm_response(raw, expected_count=2)

    assert messages == ["m1", "m2"]
    assert state is not None
    assert state.relationship_vibe == "tense"
    assert state.owner_state == "stressed"


def test_parse_llm_response_pads_small_shortfall() -> None:
    """Pad with placeholders when the model is close to expected count."""
    raw = '{"messages": ["only-one"]}'
    messages, state = parse_llm_response(raw, expected_count=3)

    assert messages == ["only-one", "...", "..."]
    assert state is None


def test_parse_llm_response_raises_on_large_shortfall() -> None:
    """Fail fast when too many messages are missing."""
    raw = '{"messages": ["one"]}'
    with pytest.raises(ValueError, match="expected 5"):
        parse_llm_response(raw, expected_count=5)


def test_merge_story_states_prefers_latest_non_empty_fields() -> None:
    """Merge running memory while capping list growth."""
    accumulated = StoryState(
        topics_covered=["topic-a"],
        key_events=["event-a"],
        unresolved_threads=["thread-a"],
        relationship_vibe="neutral",
        owner_state="ok",
        contact_state="ok",
    )
    new_batch = StoryState(
        topics_covered=["topic-b"],
        key_events=["event-b"],
        unresolved_threads=["thread-b"],
        relationship_vibe="warmer",
        owner_state="better",
        contact_state="engaged",
    )

    merged = merge_story_states(accumulated, new_batch)
    assert merged.topics_covered == ["topic-a", "topic-b"]
    assert merged.key_events == ["event-a", "event-b"]
    assert merged.unresolved_threads == ["thread-b"]
    assert merged.relationship_vibe == "warmer"
