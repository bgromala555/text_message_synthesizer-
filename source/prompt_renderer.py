"""Jinja2-based prompt rendering for the Synthesized Chat Generator.

Loads prompt templates from ``source/prompt_templates/`` and renders them
with conversation-specific variables.  Each render method mirrors one of the
``build_*`` functions in ``source/prompts.py`` and produces functionally
identical output, but sources its text from Jinja2 templates instead of
inline f-strings.

Few-shot examples are stored as separate include files under
``prompt_templates/examples/``.  On every render call the renderer randomly
selects one example variation so the LLM sees stylistic variety across
batches.

Typical usage::

    renderer = PromptRenderer()
    system = renderer.render_direct_system(owner_profile, contact_profile, ...)
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from source.llm_client import StoryState
from source.models import FlexPersonalityProfile
from source.prompt_constants import CULTURE_GENERATION_HINTS, THEME_GENERATION_HINTS
from source.skeleton import SkeletonMessage

logger = logging.getLogger(__name__)

_DEFAULT_TEMPLATE_DIR: Path = Path(__file__).resolve().parent / "prompt_templates"

_ARC_EARLY_THRESHOLD: float = 0.2
_ARC_MID_THRESHOLD: float = 0.5
_ARC_LATE_THRESHOLD: float = 0.8

LANGUAGE_LABELS: dict[str, str] = {
    "en": "English",
    "es": "Spanish",
    "ar": "Arabic",
    "zh": "Mandarin Chinese",
    "fr": "French",
}

_DIRECT_EXAMPLE_FILES: list[str] = [
    "examples/direct_example_1.j2",
    "examples/direct_example_2.j2",
]

_GROUP_EXAMPLE_FILES: list[str] = [
    "examples/group_example_1.j2",
    "examples/group_example_2.j2",
]


def format_profile(profile: FlexPersonalityProfile, owner_name: str) -> str:
    """Format a FlexPersonalityProfile into prompt-ready text.

    Converts all profile fields into a structured text block identical to
    the pattern used in the existing rewriter.  Each section is separated by
    blank lines for readability when the LLM parses the system prompt.

    Args:
        profile (FlexPersonalityProfile): The personality profile to format.
        owner_name (str): The device owner's name for relationship context.

    Returns:
        Multi-line formatted string describing this character.

    """
    hobbies = "\n".join(f"  - {h}" for h in profile.hobbies_and_interests)
    media = "\n".join(f"  - {m}" for m in profile.favorite_media)
    local_spots = ", ".join(profile.favorite_local_spots)
    life_now = "\n".join(f"  - {s}" for s in profile.current_life_situations)
    peeves = ", ".join(profile.pet_peeves)
    phrases = "\n".join(f'  - "{p}"' for p in profile.sample_phrases)
    topics_up = ", ".join(profile.topics_they_bring_up)
    topics_avoid = ", ".join(profile.topics_they_avoid)

    background_line = ""
    if profile.cultural_background:
        background_line = f"Cultural background: {profile.cultural_background}\n"

    return (
        f"**{profile.name}** (age {profile.age}, {profile.neighborhood})\n"
        f"{background_line}"
        f"Role: {profile.role}\n"
        f"Job: {profile.job_details}\n"
        f"Personality: {profile.personality_summary}\n"
        f"Backstory: {profile.backstory_details}\n"
        f"Emotional range: {profile.emotional_range}\n"
        f"Humor style: {profile.humor_style}\n"
        f"Daily routine: {profile.daily_routine_notes}\n\n"
        f"Hobbies & interests:\n{hobbies}\n\n"
        f"Media they consume:\n{media}\n\n"
        f"Food & drink: {profile.food_and_drink}\n"
        f"Favorite local spots: {local_spots}\n\n"
        f"What's going on in their life right now:\n{life_now}\n\n"
        f"Topics they naturally bring up: {topics_up}\n"
        f"Topics they avoid: {topics_avoid}\n"
        f"Pet peeves: {peeves}\n\n"
        f"Texting style:\n"
        f"  - Punctuation: {profile.texting_style.punctuation}\n"
        f"  - Capitalization: {profile.texting_style.capitalization}\n"
        f"  - Emoji: {profile.texting_style.emoji_use}\n"
        f"  - Abbreviations: {profile.texting_style.abbreviations}\n"
        f"  - Message length: {profile.texting_style.avg_message_length}\n"
        f"  - Quirks: {profile.texting_style.quirks}\n\n"
        f"How {owner_name} talks to them: {profile.how_owner_talks_to_them}\n"
        f"Relationship arc over the year: {profile.relationship_arc}\n\n"
        f"Sample phrases (for voice reference, DON'T copy verbatim):\n{phrases}"
    )


def short_items(items: list[str], take: int, max_len: int) -> str:
    """Truncate and join the most recent items from a list for prompt injection.

    Selects up to ``take`` items from the end of the list, clipping each to
    ``max_len`` characters, and joins them with commas.  Empty or whitespace-
    only items are silently skipped.

    Args:
        items (list[str]): Source list of strings.
        take (int): Maximum number of items to include from the tail.
        max_len (int): Maximum character length per item before truncation.

    Returns:
        Comma-separated string of the selected, truncated items.

    """
    return ", ".join([x[:max_len] for x in items[-take:] if x.strip()])


def _pick_example(candidates: list[str]) -> str:
    """Randomly select one example include path from the given candidates.

    Args:
        candidates (list[str]): Template-relative paths to example files.

    Returns:
        A single example file path chosen at random.

    """
    return secrets.choice(candidates)


def _compute_phase_hint(progress: float) -> str:
    """Return the personality-arc phase description for the given progress.

    Args:
        progress (float): Batch progress ratio in ``[0, 1]``.

    Returns:
        A descriptive string for the LLM about character evolution phase.

    """
    if progress < _ARC_EARLY_THRESHOLD:
        return (
            "This is EARLY in the timeline. Establish baseline routines, habits, and life situations. "
            "Characters mention their daily patterns naturally."
        )
    if progress < _ARC_MID_THRESHOLD:
        return (
            "Timeline is progressing. Small changes should start appearing: new interests, "
            "shifting moods, hints that life situations are evolving. "
            "Characters may mention trying something new or feeling differently about things."
        )
    if progress < _ARC_LATE_THRESHOLD:
        return (
            "Past the midpoint. Life situations should have noticeably shifted. "
            "Some problems may have resolved, new ones emerged. Routines may have changed. "
            "Characters reflect on how things are different from before."
        )
    return (
        "LATE in the timeline. Characters have grown. Some backstory threads should reach "
        "resolution or new equilibrium. The relationship between them may have deepened or shifted."
    )


class PromptRenderer:
    """Renders LLM prompts from Jinja2 templates with optional example variation.

    Loads templates from a directory on disk and exposes one ``render_*``
    method per prompt type.  Each method accepts the same raw inputs as
    the corresponding ``build_*`` function in ``source/prompts.py`` and
    returns a fully rendered prompt string.

    Few-shot examples are randomly varied per call to prevent the LLM from
    over-fitting to a single example shape.

    Attributes:
        env: The Jinja2 rendering environment bound to the template directory.

    """

    def __init__(self, template_dir: Path | None = None) -> None:
        """Initialise the renderer with a template directory.

        Args:
            template_dir (Path | None): Path to the directory containing
                ``.j2`` template files.  Defaults to
                ``source/prompt_templates/`` relative to this module.

        """
        resolved = template_dir or _DEFAULT_TEMPLATE_DIR
        self.env: Environment = Environment(
            loader=FileSystemLoader(str(resolved)),
            autoescape=select_autoescape(),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )

    def render_direct_system(
        self,
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
        """Render the full LLM system prompt for a direct conversation pair.

        Combines generation rules, personality profiles, genre context,
        cultural context, story arc context, language directive, and
        formatting instructions into a single system message via the
        ``direct_system.j2`` template.

        Args:
            owner_profile (FlexPersonalityProfile): The device owner's
                personality profile.
            contact_profile (FlexPersonalityProfile): The contact's
                personality profile.
            owner_name (str): Display name of the device owner.
            theme (str): Scenario genre preset for conversation tone.
            culture (str): Cultural/geographic context.
            story_arc (str): Global scenario narrative.
            owner_arc (str): The phone owner's individual narrative trajectory.
            contact_arc (str): The contact's individual narrative trajectory.
            language (str): ISO language code for generated message content.
            consistency_feedback (str): Validation feedback the LLM must fix.

        Returns:
            Complete system prompt string.

        """
        template = self.env.get_template("direct_system.j2")

        lang_label = ""
        if language and language != "en":
            lang_label = LANGUAGE_LABELS.get(language, language)

        contact_name = contact_profile.name if contact_profile.name else "Contact"

        return template.render(
            theme_hint=THEME_GENERATION_HINTS.get(theme, THEME_GENERATION_HINTS["slice-of-life"]),
            culture_hint=CULTURE_GENERATION_HINTS.get(culture, CULTURE_GENERATION_HINTS["american"]),
            story_arc=story_arc,
            owner_name=owner_name,
            owner_arc=owner_arc,
            contact_name=contact_name,
            contact_arc=contact_arc,
            lang_label=lang_label,
            consistency_feedback=consistency_feedback.strip(),
            owner_block=format_profile(owner_profile, owner_name),
            contact_block=format_profile(contact_profile, owner_name),
            example_file=_pick_example(_DIRECT_EXAMPLE_FILES),
        )

    def render_group_system(
        self,
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
        """Render the LLM system prompt for a group chat conversation.

        Describes all participants' personalities and the group dynamic,
        then asks the LLM to generate realistic group chat banter via
        the ``group_system.j2`` template.

        Args:
            owner_profile (FlexPersonalityProfile): The device owner's
                personality profile.
            member_profiles (list[FlexPersonalityProfile]): Profiles of all
                other group members.
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
        template = self.env.get_template("group_system.j2")

        lang_label = ""
        if language and language != "en":
            lang_label = LANGUAGE_LABELS.get(language, language)

        member_blocks = ""
        for i, mp in enumerate(member_profiles, 2):
            member_blocks += f"\nPARTICIPANT {i}:\n{format_profile(mp, owner_name)}\n"

        return template.render(
            group_name=group_name,
            group_vibe=group_vibe or "casual friend group banter",
            theme_hint=THEME_GENERATION_HINTS.get(theme, THEME_GENERATION_HINTS["slice-of-life"]),
            culture_hint=CULTURE_GENERATION_HINTS.get(culture, CULTURE_GENERATION_HINTS["american"]),
            story_arc=story_arc,
            lang_label=lang_label,
            owner_block=format_profile(owner_profile, owner_name),
            member_blocks=member_blocks,
            example_file=_pick_example(_GROUP_EXAMPLE_FILES),
        )

    def render_batch_prompt(
        self,
        skeleton_batch: list[SkeletonMessage],
        actor_lookup: dict[str, str],
        batch_num: int,
        total_batches: int,
        story_state: StoryState,
        event_block: str = "",
        arc_block: str = "",
    ) -> str:
        """Render the user prompt for a single batch of messages.

        Provides the LLM with the structural skeleton, accumulated story
        state, relevant timeline events, and personality evolution hints
        via the ``batch_user.j2`` template.

        Args:
            skeleton_batch (list[SkeletonMessage]): The batch of skeleton
                messages to fill.
            actor_lookup (dict[str, str]): Mapping of actor ID to display name.
            batch_num (int): Current batch number (1-indexed).
            total_batches (int): Total number of batches.
            story_state (StoryState): Accumulated story state from previous
                batches.
            event_block (str): Pre-formatted event directive string or empty.
            arc_block (str): Pre-formatted personality arc hint string or empty.

        Returns:
            Formatted user prompt string.

        """
        template = self.env.get_template("batch_user.j2")

        context = f"BATCH {batch_num} of {total_batches}"
        if batch_num == 1:
            context += " (conversation start)"
        elif batch_num == total_batches:
            context += " (conversation end)"

        time_start = skeleton_batch[0].transfer_time[:10]
        time_end = skeleton_batch[-1].transfer_time[:10]
        context += f"\nTIME RANGE: {time_start} to {time_end}"

        if story_state.topics_covered:
            topics_list = short_items(story_state.topics_covered, take=12, max_len=80)
            key_events = short_items(story_state.key_events, take=8, max_len=100)
            unresolved = short_items(story_state.unresolved_threads, take=6, max_len=90)
            context += (
                f"\n\n=== CONVERSATION MEMORY (DO NOT REPEAT) ===\n"
                f"Topics covered: {topics_list}\n"
                f"Key events: {key_events}\n"
                f"Unresolved threads: {unresolved}\n"
                f"Relationship vibe: {(story_state.relationship_vibe or '')[:140]}\n"
                f"Owner state: {(story_state.owner_state or '')[:140]}\n"
                f"Contact state: {(story_state.contact_state or '')[:140]}\n"
                f"=== END MEMORY ==="
            )

        if event_block:
            context += event_block
        if arc_block:
            context += arc_block

        skeleton_entries: list[str] = []
        for i, msg in enumerate(skeleton_batch):
            sender_name = actor_lookup.get(msg.sender_actor_id, msg.sender_actor_id)
            skeleton_entries.append(f"{i + 1}. [{msg.direction.upper()}] {sender_name} @ {msg.transfer_time}")

        return template.render(
            context=context,
            skeleton_entries=skeleton_entries,
            skeleton_count=len(skeleton_batch),
        )

    def render_personality_arc(
        self,
        owner_profile: FlexPersonalityProfile,
        contact_profile: FlexPersonalityProfile,
        batch_num: int,
        total_batches: int,
    ) -> str:
        """Render personality arc evolution hints for a batch.

        Early batches establish baseline routines.  As the conversation
        progresses, life situations shift.  This gives the LLM permission
        to make the conversation feel alive rather than static.

        Args:
            owner_profile (FlexPersonalityProfile): The device owner's
                personality.
            contact_profile (FlexPersonalityProfile): The contact's
                personality.
            batch_num (int): Current batch number (1-indexed).
            total_batches (int): Total batches in the conversation.

        Returns:
            Prompt hint string about personality evolution, or empty string
            if no life-situation data is available.

        """
        owner_situations = owner_profile.current_life_situations or []
        contact_situations = contact_profile.current_life_situations or []
        owner_routine = owner_profile.daily_routine_notes or ""
        contact_routine = contact_profile.daily_routine_notes or ""

        if not owner_situations and not contact_situations and not owner_routine and not contact_routine:
            return ""

        template = self.env.get_template("personality_arc.j2")
        progress = batch_num / max(total_batches, 1)

        return template.render(
            phase_hint=_compute_phase_hint(progress),
            owner_situations=", ".join(owner_situations[:5]) if owner_situations else "",
            contact_situations=", ".join(contact_situations[:5]) if contact_situations else "",
            owner_routine=owner_routine[:200] if owner_routine else "",
            contact_routine=contact_routine[:200] if contact_routine else "",
        )
