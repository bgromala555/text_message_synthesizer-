"""Pydantic models for the Synthesized Chat Generator scenario configuration.

Defines the data structures used to capture a user's multi-device SMS scenario:
devices, contacts, personality assignments, cross-device connections, timeline
events, and generation settings.

Uses **flexible** versions of PersonalityProfile and TextingStyle where every
field has a default value.  This is essential because:
  - The LLM may return partial data, wrong types (``"age": "28"``), or extra keys.
  - The user may partially fill out a profile in the UI and sync before finishing.
  - Pydantic strict validation on the ``PUT /api/scenario`` endpoint would otherwise
    reject the entire payload with a 422.

The strict originals in ``messageviewer/`` are left untouched; at generation time
the flexible profiles are converted to the strict format.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


def _new_id() -> str:
    """Generate a short unique identifier for scenario objects.

    Returns:
        An 8-character hex string derived from a UUID4.

    """
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Flexible personality models (tolerant of LLM output + partial edits)
# ---------------------------------------------------------------------------


class FlexTextingStyle(BaseModel):
    """Flexible version of TextingStyle where every field has a default.

    Accepts whatever the LLM gives us without blowing up validation.

    Attributes:
        punctuation: Description of punctuation habits.
        capitalization: Description of capitalization habits.
        emoji_use: How often and which emojis this person uses.
        abbreviations: Whether they use abbreviations.
        avg_message_length: Rough descriptor like "short", "medium", "long".
        quirks: Any unique texting habits.

    """

    model_config = {"extra": "allow"}

    punctuation: str = ""
    capitalization: str = ""
    emoji_use: str = ""
    abbreviations: str = ""
    avg_message_length: str = ""
    quirks: str = ""


class FlexPersonalityProfile(BaseModel):
    """Flexible version of PersonalityProfile where every field has a default.

    Accepts partial data, wrong types (coerced where possible), and extra
    keys from LLM responses without failing validation.  At generation time
    the pipeline converts this to the strict ``PersonalityProfile`` from
    ``messageviewer.personalities``.

    Attributes:
        actor_id: Unique actor identifier.
        name: Character display name.
        age: How old they are (coerced from string if needed).
        cultural_background: Ethnic/cultural heritage independent of scenario
            locale (e.g. "Nigerian-American", "French expat", "local Chinese").
            Empty means infer from the scenario culture setting.
        neighborhood: Where they live.
        role: Relationship role.
        job_details: Work life details.
        personality_summary: Core personality description.
        emotional_range: Typical emotions and expression style.
        backstory_details: Relevant personal history.
        hobbies_and_interests: Specific hobbies.
        favorite_media: Shows, music, books they consume.
        food_and_drink: Dietary preferences and food spots.
        favorite_local_spots: Real places in the setting they frequent.
        current_life_situations: What is going on in their life.
        topics_they_bring_up: Subjects they naturally introduce.
        topics_they_avoid: Subjects they steer away from.
        pet_peeves: Things that annoy them.
        humor_style: How they are funny in texts.
        daily_routine_notes: Typical day/week description.
        texting_style: Texting mechanics.
        how_owner_talks_to_them: How the device owner adapts voice for them.
        relationship_arc: How the relationship evolves.
        sample_phrases: Example phrases capturing their voice.

    """

    model_config = {"extra": "allow"}

    actor_id: str = ""
    name: str = ""
    age: int = 30
    cultural_background: str = ""
    neighborhood: str = ""
    role: str = ""
    job_details: str = ""
    personality_summary: str = ""
    emotional_range: str = ""
    backstory_details: str = ""
    hobbies_and_interests: list[str] = Field(default_factory=list)
    favorite_media: list[str] = Field(default_factory=list)
    food_and_drink: str = ""
    favorite_local_spots: list[str] = Field(default_factory=list)
    current_life_situations: list[str] = Field(default_factory=list)
    topics_they_bring_up: list[str] = Field(default_factory=list)
    topics_they_avoid: list[str] = Field(default_factory=list)
    pet_peeves: list[str] = Field(default_factory=list)
    humor_style: str = ""
    daily_routine_notes: str = ""
    texting_style: FlexTextingStyle = Field(default_factory=FlexTextingStyle)
    how_owner_talks_to_them: str = ""
    relationship_arc: str = ""
    sample_phrases: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_and_migrate(cls, data: Any) -> Any:
        """Coerce age from string to int and migrate legacy field names.

        Handles two backward-compatibility renames so that old saved
        scenarios load cleanly:
        - ``specific_nyc_haunts`` -> ``favorite_local_spots``
        - ``how_alex_talks_to_them`` -> ``how_owner_talks_to_them``

        Returns:
            The (possibly mutated) input data dict with legacy fields renamed.

        """
        if isinstance(data, dict):
            if "age" in data:
                raw = data["age"]
                if isinstance(raw, str):
                    try:
                        data["age"] = int(raw)
                    except ValueError:
                        data["age"] = 30
            if "specific_nyc_haunts" in data and "favorite_local_spots" not in data:
                data["favorite_local_spots"] = data.pop("specific_nyc_haunts")
            if "how_alex_talks_to_them" in data and "how_owner_talks_to_them" not in data:
                data["how_owner_talks_to_them"] = data.pop("how_alex_talks_to_them")
        return data


# ---------------------------------------------------------------------------
# Connection types
# ---------------------------------------------------------------------------


class ConnectionType(StrEnum):
    """The kind of cross-device link between two contacts.

    Attributes:
        SHARED_CHARACTER: The same person appears on multiple devices.
        LOCATION_LINK: A physical location referenced on multiple devices.
        NEAR_MISS: A timestamp-matchable event where characters unknowingly cross paths.

    """

    SHARED_CHARACTER = "shared_character"
    LOCATION_LINK = "location_link"
    NEAR_MISS = "near_miss"


# ---------------------------------------------------------------------------
# Cross-device contact reference
# ---------------------------------------------------------------------------


class DeviceContactRef(BaseModel):
    """A reference to a contact (or owner via ``__owner__``) on a specific device.

    Used in cross-device links (``ContactSlot.shared_with``), timeline event
    participants (``FlexTimelineEvent.participants``), and group chat members
    (``GroupChat.members``).

    Attributes:
        device_id: Unique ID of the device.
        contact_id: Contact slot ID, or ``__owner__`` for the device owner.

    """

    device_id: str = ""
    contact_id: str = ""


# ---------------------------------------------------------------------------
# Contact and Device models
# ---------------------------------------------------------------------------


class ContactSlot(BaseModel):
    """A single contact on a device, with optional personality data.

    Represents one entry in a device's contact list.  The ``shared_with``
    field links this contact to a contact on another device (same person,
    different phone).

    The ``message_volume`` field controls how many messages are generated
    for this contact.  A "best friend" texts every day; a "barber" texts
    once a month.

    Volume levels and their approximate effect on messages_per_day:
      - ``heavy``   — 100% density, texts every day (partner, best friend)
      - ``regular`` — 50% density, texts most days (friend, coworker)
      - ``light``   — 15% density, texts a few times a week (acquaintance)
      - ``minimal`` — 5% density, texts once or twice a month (barber, ex)

    Attributes:
        id: Unique identifier for this contact slot.
        actor_id: The actor ID used in the generated dataset.
        name: Display name of the contact.
        role: Short relationship descriptor (e.g. "best friend").
        message_volume: How much this person texts — "heavy", "regular",
            "light", or "minimal".  Defaults to "regular".
        story_arc: This character's individual narrative trajectory — their
            role in the plot, what they know, and how they change.
        personality: Flexible personality profile, or None if not assigned.
        shared_with: List of ``DeviceContactRef`` instances identifying
            the same person on other devices.  Empty means not shared.

    """

    model_config = {"extra": "allow"}

    id: str = Field(default_factory=_new_id)
    actor_id: str = ""
    name: str = ""
    role: str = ""
    cultural_background: str = ""
    message_volume: str = "regular"
    story_arc: str = ""
    personality: FlexPersonalityProfile | None = None
    shared_with: list[DeviceContactRef] = Field(default_factory=list)


class DeviceScenario(BaseModel):
    """Configuration for a single phone/device in the scenario.

    The ``spam_density`` field controls how much junk mail this particular
    phone receives.  Set to ``"none"`` to disable spam entirely for this
    device, or ``"low"``/``"medium"``/``"high"`` to vary the noise floor.
    A phone with very few real contacts but high spam density simulates a
    burner or secondary phone.

    Attributes:
        id: Unique identifier for this device.
        device_label: Human-readable label.
        owner_name: Display name of the phone owner.
        owner_actor_id: Actor ID for the owner.
        owner_story_arc: The phone owner's narrative trajectory.
        generation_mode: Per-device generation scope. ``"story"`` means
            this device uses global story/events/group context. ``"standalone"``
            means generate normal conversations without shared story/event links.
        role_style: Per-device contact-role distribution preset used by AI
            name generation. ``"normal"`` favors everyday contacts,
            ``"mixed"`` allows a small amount of plot roles, and
            ``"story_heavy"`` allows a larger but still bounded plot share.
        spam_density: Per-device spam level — "none", "low", "medium", or "high".
        owner_personality: Flexible personality profile for the owner.
        contacts: Ordered list of contacts on this device.

    """

    model_config = {"extra": "allow"}

    id: str = Field(default_factory=_new_id)
    device_label: str = ""
    owner_name: str = ""
    owner_actor_id: str = ""
    owner_story_arc: str = ""
    generation_mode: str = "story"
    role_style: str = "normal"
    spam_density: str = "medium"
    owner_personality: FlexPersonalityProfile | None = None
    contacts: list[ContactSlot] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Cross-device connection models
# ---------------------------------------------------------------------------


class CharacterOverlapConfig(BaseModel):
    """User-editable configuration for a shared character across devices.

    Attributes:
        character_name: The shared character's full name.
        device1_behavior: How they present on the first device.
        device2_behavior: How they present on the second device.
        contrast_note: What a forensic examiner would discover.

    """

    character_name: str = ""
    device1_behavior: str = ""
    device2_behavior: str = ""
    contrast_note: str = ""


class FlexLocationLink(BaseModel):
    """Flexible version of LocationLink with defaults on all fields.

    Attributes:
        place_name: Name of the location.
        address_hint: Street or neighborhood hint.
        device1_who_mentions: Names mentioning on device 1.
        device2_who_mentions: Names mentioning on device 2.
        discovery_note: What a forensic examiner would notice.

    """

    place_name: str = ""
    address_hint: str = ""
    device1_who_mentions: list[str] = Field(default_factory=list)
    device2_who_mentions: list[str] = Field(default_factory=list)
    discovery_note: str = ""


class FlexNearMissEvent(BaseModel):
    """Flexible version of NearMissEvent with defaults on all fields.

    Attributes:
        date: ISO date string.
        time_window: Approximate time window.
        location: Where it happens.
        device1_character: Device 1 character present.
        device1_text_hint: What might appear in Device 1 texts.
        device2_character: Device 2 character present.
        device2_text_hint: What might appear in Device 2 texts.
        description: Narrative description.

    """

    date: str = ""
    time_window: str = ""
    location: str = ""
    device1_character: str = ""
    device1_text_hint: str = ""
    device2_character: str = ""
    device2_text_hint: str = ""
    description: str = ""


class ConnectionLink(BaseModel):
    """A cross-device connection between contacts on different phones.

    Attributes:
        id: Unique identifier for this connection.
        connection_type: The kind of link.
        source_device_id: ID of the first device involved.
        source_contact_id: ID of the contact slot on the first device.
        target_device_id: ID of the second device involved.
        target_contact_id: ID of the contact slot on the second device.
        label: Short description shown on the link chart edge.
        character_overlap: Detail config for shared-character links.
        location_link: Detail config for location links.
        near_miss: Detail config for near-miss links.

    """

    model_config = {"extra": "allow"}

    id: str = Field(default_factory=_new_id)
    connection_type: ConnectionType = ConnectionType.SHARED_CHARACTER
    source_device_id: str = ""
    source_contact_id: str = ""
    target_device_id: str = ""
    target_contact_id: str = ""
    label: str = ""
    character_overlap: CharacterOverlapConfig | None = None
    location_link: FlexLocationLink | None = None
    near_miss: FlexNearMissEvent | None = None


# ---------------------------------------------------------------------------
# Timeline events (flexible)
# ---------------------------------------------------------------------------


class FlexTimelineEvent(BaseModel):
    """Flexible shared timeline event with defaults on all fields.

    Replaces the strict ``SharedTimelineEvent`` from messageviewer in
    the scenario config so partial edits and LLM output are accepted.

    The ``encounter_type`` controls how the event manifests in generated
    messages:

    - ``planned`` — participants coordinate beforehand and discuss it after.
    - ``chance_encounter`` — participants bump into each other unexpectedly;
      messages reflect surprise ("crazy running into you!").
    - ``near_miss`` — participants were at the same place but didn't notice
      each other; only discovered later through other people or social media.

    Attributes:
        id: Unique identifier for this event.
        date: ISO date string.
        time: Optional time string or None.
        description: What happened.
        encounter_type: How participants meet — "planned", "chance_encounter",
            or "near_miss".  Defaults to "planned".
        device_impacts: Per-device impact descriptions keyed by device ID.
        involved_contacts: Contact IDs grouped by device ID.
        participants: Flat list of ``DeviceContactRef`` instances for
            the people selected by the user as involved in this event.

    """

    model_config = {"extra": "allow"}

    id: str = Field(default_factory=_new_id)
    date: str = ""
    time: str | None = None
    description: str = ""
    encounter_type: str = "planned"
    device_impacts: dict[str, str] = Field(default_factory=dict)
    involved_contacts: dict[str, list[str]] = Field(default_factory=dict)
    participants: list[DeviceContactRef] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Generation settings and top-level config
# ---------------------------------------------------------------------------


class GenerationSettings(BaseModel):
    """Controls for the message generation pipeline.

    Spam density is now controlled per-device via ``DeviceScenario.spam_density``
    rather than at this global level.

    Attributes:
        date_start: ISO date string for the first day of messages.
        date_end: ISO date string for the last day of messages.
        messages_per_day_min: Minimum average messages per contact per day.
        messages_per_day_max: Maximum average messages per contact per day.
        batch_size: Number of messages sent to the LLM per request.
        llm_provider: Which LLM API to call.
        llm_model: Model name override, or empty for provider default.
        temperature: Sampling temperature for the LLM.
        language: ISO language code for all generated message content.
        streaming: Whether to use token-level streaming from the LLM API.
            When ``True``, the generator yields partial token deltas as
            SSE events for real-time progress feedback in the browser.

    """

    date_start: str = "2025-01-01"
    date_end: str = "2025-12-31"
    messages_per_day_min: int = 2
    messages_per_day_max: int = 8
    batch_size: int = 25
    llm_provider: str = "openai"
    llm_model: str = ""
    temperature: float = 0.9
    language: str = "en"
    streaming: bool = True


class ScenarioContext(BaseModel):
    """Bundled scenario-level parameters that travel together across functions.

    Groups theme, culture, story_arc, and language into a single object
    to eliminate long parameter lists where these four values always appear
    as a unit.

    Attributes:
        theme: Genre preset that flavors conversation tone.
        culture: Cultural/geographic context for locations, food, norms.
        story_arc: The scenario's narrative bible / answer key.
        language: ISO language code for all generated message content.

    """

    theme: str = "slice-of-life"
    culture: str = "american"
    story_arc: str = ""
    language: str = "en"


class RunLogEntry(BaseModel):
    """Structured log entry for generation run auditing.

    Replaces bare ``dict[str, object]`` with a typed model so run logs
    are consistent and introspectable.

    Attributes:
        timestamp: ISO 8601 timestamp of when the entry was recorded.
        level: Log severity level (info, warning, error).
        event_type: Machine-readable event category.
        payload: Arbitrary structured data for this event.

    """

    timestamp: str = ""
    level: str = "info"
    event_type: str = ""
    payload: dict[str, object] = Field(default_factory=dict)


class RepairDetail(BaseModel):
    """Result record for a single thread repair attempt.

    Captures what was wrong, what action was taken, and the outcome
    so the quality-check endpoint can report a detailed resolution writeup.

    Attributes:
        thread: Human-readable thread label (e.g. "Marcus <-> Elena").
        device: Device label where the thread lives.
        issues: List of issue descriptions that triggered the repair.
        outcome: Result status: "regenerated", "error", "empty", "skipped", "quota_hit".
        messages_produced: Number of messages in the repaired thread (0 if failed).
        reason: Explanation when outcome is not "regenerated".

    """

    thread: str = ""
    device: str = ""
    issues: list[str] = Field(default_factory=list)
    outcome: str = ""
    messages_produced: int = 0
    reason: str = ""


class ResolutionItem(BaseModel):
    """A single action item describing one auto-fix step during quality repair.

    Captures the type of fix applied, a description of the issue found,
    the corrective action taken, and the result.  For timeline repairs an
    optional ``repair_details`` list carries per-thread repair outcomes.

    Attributes:
        issue: Description of the problem that triggered this fix.
        action: Explanation of the corrective action taken.
        result: Human-readable outcome summary.
        repair_details: Per-thread repair outcome records for timeline fixes.
            Each entry is a serialised ``RepairDetail``.  Empty for non-timeline
            resolution items.

    """

    issue: str = ""
    action: str = ""
    result: str = ""
    repair_details: list[RepairDetail] = Field(default_factory=list)


class ResolutionWriteup(BaseModel):
    """Summary of all auto-fix actions taken during a quality check pass.

    Provides before/after problem counts and itemized details of each
    resolution step so the user can audit what was changed and why.

    Attributes:
        before_problem_count: Total warnings + criticals before fixes.
        after_problem_count: Total warnings + criticals after fixes.
        resolved_estimate: How many issues were resolved.
        items: Ordered list of typed resolution action items.

    """

    before_problem_count: int = 0
    after_problem_count: int = 0
    resolved_estimate: int = 0
    items: list[ResolutionItem] = Field(default_factory=list)


class GroupChat(BaseModel):
    """A multi-person group chat that emerges from events or the story arc.

    Group chats appear as a single ``ConversationNode`` with multiple
    targets in the generated dataset.  The ``members`` list references
    contacts (and optionally owners via ``__owner__``) from specific
    devices.

    Attributes:
        id: Unique identifier for this group chat.
        name: Display name for the group (e.g., "The Crew").
        members: List of ``DeviceContactRef`` instances.
        origin_event_id: Timeline event that spawned this group, if any.
        start_date: ISO date when the group chat begins.
        end_date: ISO date when the group chat dies (empty = ongoing).
        message_volume: Density of messages — heavy, regular, light, minimal.
        vibe: Short description of the group dynamic (e.g., "casual banter").
        activation_mode: How the group becomes active. ``event_time`` means
            group starts when the origin event occurs.
        auto_pair_threads: If true, generator attempts to ensure direct 1:1
            owner↔member threads exist when this group is active.
        quality_score: Optional coherence score from group/event validation.

    """

    model_config = {"extra": "allow"}

    id: str = Field(default_factory=_new_id)
    name: str = ""
    members: list[DeviceContactRef] = Field(default_factory=list)
    origin_event_id: str = ""
    start_date: str = ""
    end_date: str = ""
    message_volume: str = "regular"
    vibe: str = ""
    activation_mode: str = "event_time"
    auto_pair_threads: bool = True
    quality_score: float = 1.0


class ScenarioConfig(BaseModel):
    """Top-level configuration for an entire synthesized chat scenario.

    The ``theme`` field flavors every AI-generated element — names,
    personalities, events, and message content all shift to fit the
    chosen genre.

    The ``culture`` field anchors the scenario in a specific cultural
    and geographic context.  Names, neighborhoods, food spots, slang,
    daily routines, and social norms all shift accordingly.  When
    combined with the ``language`` setting in GenerationSettings, this
    lets users create scenarios like "Arabic language + Gulf Arab
    culture" or "French language + West African culture".

    The ``story_arc`` field is the scenario's narrative bible — the
    overarching plot, key reveals, resolution, and "answer key" that
    keeps all generated content coherent.  Each character also has
    their own ``story_arc`` describing their individual trajectory.

    Attributes:
        id: Unique identifier for this scenario.
        name: User-supplied name for the scenario.
        theme: Genre preset that influences AI generation across the board.
        culture: Cultural and geographic context for name styles, locations,
            social norms, food, and daily life details.
        story_arc: Global narrative premise, key plot beats, and resolution.
        devices: Ordered list of device configurations.
        connections: Cross-device links between contacts.
        timeline_events: Shared events pinned to specific dates.
        generation_settings: Parameters controlling the generation pipeline.

    """

    model_config = {"extra": "allow"}

    id: str = Field(default_factory=_new_id)
    name: str = "Untitled Scenario"
    theme: str = "slice-of-life"
    culture: str = "american"
    story_arc: str = ""
    devices: list[DeviceScenario] = Field(default_factory=list)
    connections: list[ConnectionLink] = Field(default_factory=list)
    timeline_events: list[FlexTimelineEvent] = Field(default_factory=list)
    group_chats: list[GroupChat] = Field(default_factory=list)
    generation_settings: GenerationSettings = Field(default_factory=GenerationSettings)


__all__ = [
    "CharacterOverlapConfig",
    "ConnectionLink",
    "ConnectionType",
    "ContactSlot",
    "DeviceContactRef",
    "DeviceScenario",
    "FlexLocationLink",
    "FlexNearMissEvent",
    "FlexPersonalityProfile",
    "FlexTextingStyle",
    "FlexTimelineEvent",
    "GenerationSettings",
    "GroupChat",
    "RepairDetail",
    "ResolutionItem",
    "ResolutionWriteup",
    "RunLogEntry",
    "ScenarioConfig",
    "ScenarioContext",
]
