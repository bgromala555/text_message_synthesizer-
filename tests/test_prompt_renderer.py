"""Tests for source.prompt_renderer — Jinja2-based prompt rendering."""

# ruff: noqa: S101, PLC2701

from __future__ import annotations

from unittest.mock import patch

from source.llm_client import StoryState
from source.models import FlexPersonalityProfile, FlexTextingStyle
from source.prompt_renderer import PromptRenderer, _compute_phase_hint, format_profile, short_items
from source.skeleton import SkeletonMessage


def _make_profile(name: str = "Alex Rivera", role: str = "best friend") -> FlexPersonalityProfile:
    """Build a minimal FlexPersonalityProfile for testing.

    Args:
        name: Character display name.
        role: Relationship role.

    Returns:
        A FlexPersonalityProfile with populated fields.

    """
    return FlexPersonalityProfile(
        name=name,
        age=28,
        role=role,
        neighborhood="Midtown",
        job_details="Software developer",
        personality_summary="Laid back and witty",
        emotional_range="Calm to sarcastic",
        backstory_details="Grew up in the suburbs",
        hobbies_and_interests=["hiking", "cooking"],
        favorite_media=["The Office", "podcasts"],
        food_and_drink="Coffee and tacos",
        favorite_local_spots=["Central Park", "Joe's Pizza"],
        current_life_situations=["just got promoted", "training for a marathon"],
        topics_they_bring_up=["tech news", "weekend plans"],
        topics_they_avoid=["politics"],
        pet_peeves=["slow walkers"],
        humor_style="dry sarcasm",
        daily_routine_notes="Morning runs, late work sessions",
        texting_style=FlexTextingStyle(
            punctuation="minimal",
            capitalization="lowercase mostly",
            emoji_use="occasional",
            abbreviations="some",
            avg_message_length="short",
            quirks="double-texts a lot",
        ),
        how_owner_talks_to_them="casual and jokey",
        relationship_arc="Getting closer over time",
        sample_phrases=["yo what's good", "lmk"],
    )


def _make_skeleton(count: int = 3) -> list[SkeletonMessage]:
    """Build a list of skeleton messages for batch prompt testing.

    Args:
        count: Number of skeleton messages to generate.

    Returns:
        A list of SkeletonMessage stubs with ascending timestamps.

    """
    return [
        SkeletonMessage(
            sender_actor_id=f"actor_{i % 2}",
            direction="outgoing" if i % 2 == 0 else "incoming",
            transfer_time=f"2025-03-{15 + i:02d}T10:{i:02d}:00",
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# format_profile
# ---------------------------------------------------------------------------


def testformat_profile_contains_name_and_age() -> None:
    """Formatted profile text should include the character name and age."""
    profile = _make_profile()
    text = format_profile(profile, "Jordan")

    assert "**Alex Rivera**" in text
    assert "age 28" in text


def testformat_profile_contains_hobbies() -> None:
    """Formatted profile text should list hobbies."""
    profile = _make_profile()
    text = format_profile(profile, "Jordan")

    assert "hiking" in text
    assert "cooking" in text


def testformat_profile_contains_texting_style() -> None:
    """Formatted profile text should include texting style details."""
    profile = _make_profile()
    text = format_profile(profile, "Jordan")

    assert "Punctuation: minimal" in text
    assert "double-texts a lot" in text


def testformat_profile_includes_cultural_background_when_set() -> None:
    """Cultural background should appear in the profile block when populated."""
    profile = _make_profile()
    profile.cultural_background = "Korean-American"
    text = format_profile(profile, "Jordan")

    assert "Cultural background: Korean-American" in text


def testformat_profile_omits_cultural_background_when_empty() -> None:
    """Cultural background line should be absent when the field is empty."""
    profile = _make_profile()
    profile.cultural_background = ""
    text = format_profile(profile, "Jordan")

    assert "Cultural background:" not in text


# ---------------------------------------------------------------------------
# short_items
# ---------------------------------------------------------------------------


def testshort_items_truncates_and_joins() -> None:
    """Should take the last N items, truncate each, and join with commas."""
    items = ["alpha", "bravo", "charlie", "delta"]
    result = short_items(items, take=2, max_len=3)

    assert result == "cha, del"


def testshort_items_skips_whitespace_only() -> None:
    """Whitespace-only items should be silently excluded."""
    items = ["real", "   ", "also real"]
    result = short_items(items, take=3, max_len=100)

    assert "real" in result
    assert "also real" in result


# ---------------------------------------------------------------------------
# _compute_phase_hint
# ---------------------------------------------------------------------------


def test_compute_phase_hint_early() -> None:
    """Progress < 0.2 should describe the EARLY phase."""
    hint = _compute_phase_hint(0.05)

    assert "EARLY" in hint
    assert "baseline" in hint.lower()


def test_compute_phase_hint_mid() -> None:
    """Progress 0.2-0.5 should describe small changes appearing."""
    hint = _compute_phase_hint(0.35)

    assert "progressing" in hint.lower()


def test_compute_phase_hint_late_mid() -> None:
    """Progress 0.5-0.8 should describe noticeable shifts."""
    hint = _compute_phase_hint(0.65)

    assert "midpoint" in hint.lower()


def test_compute_phase_hint_late() -> None:
    """Progress >= 0.8 should describe the LATE phase."""
    hint = _compute_phase_hint(0.9)

    assert "LATE" in hint


# ---------------------------------------------------------------------------
# PromptRenderer.render_direct_system
# ---------------------------------------------------------------------------


@patch("source.prompt_renderer._pick_example", return_value="examples/direct_example_1.j2")
def test_render_direct_system_contains_expected_sections(mock_pick: object) -> None:
    """Direct system prompt should contain generation rules, profiles, and genre context."""
    renderer = PromptRenderer()
    owner = _make_profile("Jordan Lee", "self")
    contact = _make_profile("Sam Chen", "coworker")

    output = renderer.render_direct_system(owner, contact, owner_name="Jordan Lee")

    assert "ABSOLUTE RULES" in output
    assert "Jordan Lee" in output
    assert "Sam Chen" in output
    assert "PARTICIPANT 1" in output
    assert "PARTICIPANT 2" in output


@patch("source.prompt_renderer._pick_example", return_value="examples/direct_example_1.j2")
def test_render_direct_system_includes_language_label(mock_pick: object) -> None:
    """Non-English language should produce a LANGUAGE section in the prompt."""
    renderer = PromptRenderer()
    owner = _make_profile("Jordan", "self")
    contact = _make_profile("Sam", "friend")

    output = renderer.render_direct_system(owner, contact, owner_name="Jordan", language="es")

    assert "Spanish" in output
    assert "LANGUAGE" in output


@patch("source.prompt_renderer._pick_example", return_value="examples/direct_example_1.j2")
def test_render_direct_system_includes_story_arc(mock_pick: object) -> None:
    """When a story arc is provided, it should appear in the prompt."""
    renderer = PromptRenderer()
    owner = _make_profile("Jordan", "self")
    contact = _make_profile("Sam", "friend")

    output = renderer.render_direct_system(owner, contact, owner_name="Jordan", story_arc="A heist goes wrong downtown.")

    assert "heist goes wrong" in output
    assert "STORY BIBLE" in output


# ---------------------------------------------------------------------------
# PromptRenderer.render_group_system
# ---------------------------------------------------------------------------


@patch("source.prompt_renderer._pick_example", return_value="examples/group_example_1.j2")
def test_render_group_system_includes_all_member_profiles(mock_pick: object) -> None:
    """Group system prompt should include all member profile blocks."""
    renderer = PromptRenderer()
    owner = _make_profile("Jordan", "self")
    members = [_make_profile("Sam", "coworker"), _make_profile("Lee", "cousin")]

    output = renderer.render_group_system(owner, members, owner_name="Jordan", group_name="The Crew", group_vibe="chill banter")

    assert "GROUP NAME: The Crew" in output
    assert "Sam" in output
    assert "Lee" in output
    assert "PARTICIPANT 1" in output
    assert "PARTICIPANT 2" in output
    assert "PARTICIPANT 3" in output


# ---------------------------------------------------------------------------
# PromptRenderer.render_batch_prompt
# ---------------------------------------------------------------------------


def test_render_batch_prompt_includes_skeleton_and_story_state() -> None:
    """Batch prompt should contain the skeleton entries and story state context."""
    renderer = PromptRenderer()
    skeleton = _make_skeleton(3)
    actor_lookup = {"actor_0": "Jordan", "actor_1": "Sam"}
    story_state = StoryState(
        topics_covered=["weekend plans"],
        key_events=["met at party"],
        unresolved_threads=["who took the keys"],
        relationship_vibe="warm",
        owner_state="relaxed",
        contact_state="excited",
    )

    output = renderer.render_batch_prompt(skeleton, actor_lookup, batch_num=1, total_batches=5, story_state=story_state)

    assert "BATCH 1 of 5" in output
    assert "conversation start" in output
    assert "Jordan" in output
    assert "Sam" in output
    assert "3 messages" in output


def test_render_batch_prompt_includes_event_and_arc_blocks() -> None:
    """Event and arc blocks should be included when provided."""
    renderer = PromptRenderer()
    skeleton = _make_skeleton(2)
    actor_lookup = {"actor_0": "Jordan", "actor_1": "Sam"}
    story_state = StoryState()

    output = renderer.render_batch_prompt(
        skeleton,
        actor_lookup,
        batch_num=2,
        total_batches=4,
        story_state=story_state,
        event_block="\n=== SCENARIO EVENT: Party at 8pm ===",
        arc_block="\n=== ARC: Sam starts to distance ===",
    )

    assert "Party at 8pm" in output
    assert "Sam starts to distance" in output


# ---------------------------------------------------------------------------
# PromptRenderer.render_personality_arc
# ---------------------------------------------------------------------------


def test_render_personality_arc_returns_phase_hint() -> None:
    """Personality arc should contain the appropriate phase hint for the batch progress."""
    renderer = PromptRenderer()
    owner = _make_profile("Jordan", "self")
    contact = _make_profile("Sam", "friend")

    output = renderer.render_personality_arc(owner, contact, batch_num=1, total_batches=10)

    assert "EARLY" in output
    assert "LIFE EVOLUTION" in output


def test_render_personality_arc_late_phase() -> None:
    """Late-phase arcs should describe character growth and resolution."""
    renderer = PromptRenderer()
    owner = _make_profile("Jordan", "self")
    contact = _make_profile("Sam", "friend")

    output = renderer.render_personality_arc(owner, contact, batch_num=9, total_batches=10)

    assert "LATE" in output


def test_render_personality_arc_empty_when_no_situations() -> None:
    """Should return empty string when no life situations or routines exist."""
    renderer = PromptRenderer()
    owner = FlexPersonalityProfile(name="Jordan")
    contact = FlexPersonalityProfile(name="Sam")

    output = renderer.render_personality_arc(owner, contact, batch_num=1, total_batches=5)

    assert not output
