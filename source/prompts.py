"""LLM prompt construction for the Synthesized Chat Generator.

Builds the system and user prompts sent to the LLM for both 1-to-1 and group
chat conversations.  The module is split from the monolithic ``generator.py``
so that prompt logic can be tested and iterated on independently of skeleton
generation, persistence, and the FastAPI routing layer.

All ``build_*`` functions delegate to a module-level ``PromptRenderer``
instance that loads Jinja2 templates from ``source/prompt_templates/``.  The
public API is unchanged for backward compatibility.

Key entry points:

* ``build_system_prompt``      — full system prompt for a direct conversation.
* ``build_group_system_prompt`` — full system prompt for a group chat.
* ``build_batch_prompt``       — per-batch user prompt (shared by both modes).

Helper functions:

* ``format_profile_for_prompt``  — serialise a personality profile to text.
* ``build_personality_arc_hint`` — time-aware personality evolution hints.
* ``short_items``                — truncate / cap a list for prompt injection.
"""

from __future__ import annotations

import logging

from source.llm_client import StoryState
from source.models import FlexPersonalityProfile
from source.prompt_renderer import PromptRenderer, format_profile
from source.prompt_renderer import short_items as renderer_short_items
from source.skeleton import SkeletonMessage

logger = logging.getLogger(__name__)

_renderer: PromptRenderer | None = None


def get_renderer() -> PromptRenderer:
    """Return the module-level PromptRenderer, creating it on first call.

    Implements a lazy singleton so the Jinja2 environment is only
    initialised when prompt rendering is actually needed.

    Returns:
        The shared ``PromptRenderer`` instance.

    """
    global _renderer
    if _renderer is None:
        _renderer = PromptRenderer()
    return _renderer


def format_profile_for_prompt(profile: FlexPersonalityProfile, owner_name: str) -> str:
    """Format a FlexPersonalityProfile into prompt-ready text.

    Converts all profile fields into a structured text block identical to
    the pattern used in the existing rewriter.  Each section is separated by
    blank lines for readability when the LLM parses the system prompt.

    Delegates to the canonical implementation in ``prompt_renderer``.

    Args:
        profile (FlexPersonalityProfile): The personality profile to format.
        owner_name (str): The device owner's name for relationship context.

    Returns:
        Multi-line formatted string describing this character.

    """
    return format_profile(profile, owner_name)


def build_system_prompt(
    owner_profile: FlexPersonalityProfile,
    contact_profile: FlexPersonalityProfile,
    owner_name: str,
    theme: str = "slice-of-life",
    culture: str = "american",
    story_arc: str = "",
    owner_arc: str = "",
    contact_arc: str = "",
    language: str = "en",
    consistency_feedback: str = "",
) -> str:
    """Build the full LLM system prompt for a conversation pair.

    Delegates to ``PromptRenderer.render_direct_system`` which loads the
    ``direct_system.j2`` template and renders it with the supplied context.

    Args:
        owner_profile (FlexPersonalityProfile): The device owner's personality
            profile.
        contact_profile (FlexPersonalityProfile): The contact's personality
            profile.
        owner_name (str): Display name of the device owner.
        theme (str): Scenario genre preset that flavors the conversation tone.
        culture (str): Cultural/geographic context for locations, food, norms.
        story_arc (str): Global scenario narrative (the "answer key").
        owner_arc (str): The phone owner's individual narrative trajectory.
        contact_arc (str): The contact's individual narrative trajectory.
        language (str): ISO language code for all generated message content.
        consistency_feedback (str): Validation feedback the LLM must correct.

    Returns:
        Complete system prompt string.

    """
    return get_renderer().render_direct_system(
        owner_profile=owner_profile,
        contact_profile=contact_profile,
        owner_name=owner_name,
        theme=theme,
        culture=culture,
        story_arc=story_arc,
        owner_arc=owner_arc,
        contact_arc=contact_arc,
        language=language,
        consistency_feedback=consistency_feedback,
    )


def build_group_system_prompt(
    owner_profile: FlexPersonalityProfile,
    member_profiles: list[FlexPersonalityProfile],
    owner_name: str,
    group_name: str,
    group_vibe: str,
    theme: str = "slice-of-life",
    culture: str = "american",
    story_arc: str = "",
    language: str = "en",
) -> str:
    """Build the LLM system prompt for a group chat conversation.

    Delegates to ``PromptRenderer.render_group_system`` which loads the
    ``group_system.j2`` template and renders it with the supplied context.

    Args:
        owner_profile (FlexPersonalityProfile): The device owner's personality
            profile.
        member_profiles (list[FlexPersonalityProfile]): Profiles of all other
            group members.
        owner_name (str): Display name of the device owner.
        group_name (str): The group chat's display name.
        group_vibe (str): Short description of the group dynamic.
        theme (str): Scenario genre preset.
        culture (str): Cultural/geographic context.
        story_arc (str): Global scenario narrative.
        language (str): ISO language code for message content.

    Returns:
        Complete system prompt string.

    """
    return get_renderer().render_group_system(
        owner_profile=owner_profile,
        member_profiles=member_profiles,
        owner_name=owner_name,
        group_name=group_name,
        group_vibe=group_vibe,
        theme=theme,
        culture=culture,
        story_arc=story_arc,
        language=language,
    )


def build_personality_arc_hint(
    owner_profile: FlexPersonalityProfile,
    contact_profile: FlexPersonalityProfile,
    batch_num: int,
    total_batches: int,
) -> str:
    """Generate a hint for how personalities should evolve within a batch.

    Delegates to ``PromptRenderer.render_personality_arc`` which loads
    the ``personality_arc.j2`` template.

    Args:
        owner_profile (FlexPersonalityProfile): The device owner's personality.
        contact_profile (FlexPersonalityProfile): The contact's personality.
        batch_num (int): Current batch number (1-indexed).
        total_batches (int): Total batches in the conversation.

    Returns:
        Prompt hint string about personality evolution, or empty string if
        no life-situation data is available.

    """
    return get_renderer().render_personality_arc(
        owner_profile=owner_profile,
        contact_profile=contact_profile,
        batch_num=batch_num,
        total_batches=total_batches,
    )


def short_items(items: list[str], take: int, max_len: int) -> str:
    """Truncate and join the most recent items from a list for prompt injection.

    Selects up to ``take`` items from the end of the list, clipping each to
    ``max_len`` characters, and joins them with commas.  Empty or whitespace-
    only items are silently skipped.

    Delegates to the canonical implementation in ``prompt_renderer``.

    Args:
        items (list[str]): Source list of strings (e.g. topics, events).
        take (int): Maximum number of items to include (from the tail).
        max_len (int): Maximum character length per item before truncation.

    Returns:
        Comma-separated string of the selected, truncated items.

    """
    return renderer_short_items(items, take, max_len)


def build_batch_prompt(
    skeleton_batch: list[SkeletonMessage],
    actor_lookup: dict[str, str],
    batch_num: int,
    total_batches: int,
    story_state: StoryState,
    event_block: str = "",
    arc_block: str = "",
) -> str:
    """Build the user prompt for a single batch of messages.

    Delegates to ``PromptRenderer.render_batch_prompt`` which loads the
    ``batch_user.j2`` template and renders it with the supplied context.

    Args:
        skeleton_batch (list[SkeletonMessage]): The batch of skeleton messages
            to fill.
        actor_lookup (dict[str, str]): Mapping of actor ID to display name.
        batch_num (int): Current batch number (1-indexed).
        total_batches (int): Total number of batches.
        story_state (StoryState): Accumulated story state from previous batches.
        event_block (str): Pre-formatted event directive string (or empty).
        arc_block (str): Pre-formatted personality arc hint string (or empty).

    Returns:
        Formatted user prompt string.

    """
    return get_renderer().render_batch_prompt(
        skeleton_batch=skeleton_batch,
        actor_lookup=actor_lookup,
        batch_num=batch_num,
        total_batches=total_batches,
        story_state=story_state,
        event_block=event_block,
        arc_block=arc_block,
    )
