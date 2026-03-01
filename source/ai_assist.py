"""AI-assist endpoints for the Synthesized Chat Generator.

Provides LLM-powered helpers for generating contact names, personality
profiles, timeline events, and cross-device connection suggestions. Each
endpoint calls the OpenAI API with a focused prompt and returns structured
JSON that the frontend renders into editable form fields.

All endpoints are mounted under /api/ai/ by the main app.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from source.llm_client import DEFAULT_MODEL, get_openai_client
from source.models import FlexPersonalityProfile
from source.prompt_constants import CULTURE_SCENARIO_PROMPTS, THEME_SCENARIO_PROMPTS
from source.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter()
OPENAI_JSON_RETRY_ATTEMPTS = 3
MIN_GROUP_MEMBERS = 2
GROUP_OK_THRESHOLD = 0.70
GROUP_WARNING_THRESHOLD = 0.40


def _coerce_str_list(value: object) -> list[str]:
    """Convert unknown payload values to a list of strings.

    Args:
        value: Unknown JSON-compatible value.

    Returns:
        A list containing only string items.

    """
    if isinstance(value, list):
        coerced: list[str] = []
        for item in value:
            if isinstance(item, str):
                coerced.append(item)
            elif isinstance(item, int):
                coerced.append(str(item))
        return coerced
    return []


def _coerce_object_list(value: object) -> list[dict[str, object]]:
    """Convert unknown payload values to a list of object dictionaries.

    Args:
        value: Unknown JSON-compatible value.

    Returns:
        A list containing only ``dict[str, object]`` entries.

    """
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_mapping(value: object) -> dict[str, object]:
    """Convert unknown payload values to a string-keyed object mapping.

    Args:
        value: Unknown JSON-compatible value.

    Returns:
        A string-keyed dictionary when possible, otherwise an empty mapping.

    """
    if isinstance(value, dict):
        return {str(key): val for key, val in value.items()}
    return {}


THEME_PROMPTS = THEME_SCENARIO_PROMPTS


CULTURE_PROMPTS = CULTURE_SCENARIO_PROMPTS


async def _call_openai_json(system_prompt: str, user_prompt: str, model: str = "") -> dict[str, object]:
    """Send a prompt to OpenAI and parse the JSON response.

    Uses JSON response format for reliable structured output.  The model
    defaults to ``DEFAULT_MODEL`` when *model* is empty, allowing callers
    to override it from request payloads without changing any prompt logic.

    The synchronous OpenAI client call is offloaded to a thread via
    ``asyncio.to_thread`` so it does not block the event loop.

    Args:
        system_prompt: Instructions for the assistant role.
        user_prompt: The user's request.
        model: OpenAI model identifier.  Falls back to
            ``DEFAULT_MODEL`` (``"gpt-4o"``) when empty.

    Returns:
        Parsed JSON dictionary from the model's response.

    Raises:
        HTTPException: If the API key is missing or the API call fails.

    """
    effective_model = model or DEFAULT_MODEL
    client = get_openai_client(raise_on_missing=True)
    if client is None:
        raise HTTPException(status_code=401, detail="OpenAI API key not configured.")
    last_exc: Exception | None = None

    for attempt in range(OPENAI_JSON_RETRY_ATTEMPTS):
        try:
            response = await asyncio.to_thread(
                lambda: client.chat.completions.create(
                    model=effective_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.9,
                    max_tokens=4096,
                    response_format={"type": "json_object"},
                )
            )
            content = (response.choices[0].message.content or "{}").strip()
            if not content:
                return {}
            return json.loads(content)
        except json.JSONDecodeError as exc:
            last_exc = exc
            logger.warning("OpenAI JSON decode failed on attempt %s", attempt + 1)
        except Exception as exc:
            last_exc = exc
            logger.warning("OpenAI API call failed on attempt %s", attempt + 1)

        if attempt < OPENAI_JSON_RETRY_ATTEMPTS - 1:
            await asyncio.sleep(1.5 * (attempt + 1))

    logger.exception("OpenAI API call failed after retries")
    raise HTTPException(status_code=502, detail="OpenAI API error: unable to complete request after retries") from last_exc


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class GenerateNamesRequest(BaseModel):
    """Request body for the name generation endpoint.

    Attributes:
        count: Number of names to generate.
        context: Optional context like city, era, or demographic notes.
        owner_name: The device owner's name, so roles are relative to them.
        theme: Scenario genre preset (e.g. "espionage", "romance").
        culture: Cultural/geographic context (sets the world, not character identity).
        generation_mode: Per-device mode. ``"standalone"`` should produce
            normal-life contacts only; ``"story"`` may include a small number
            of plot-relevant roles.
        role_style: Per-device role distribution preset. One of
            ``"normal"``, ``"mixed"``, or ``"story_heavy"``.
        cast_diversity: Controls how ethnically/culturally diverse the generated
            names are.  ``"mixed"`` (default) produces a realistic multicultural
            cast.  ``"homogeneous"`` forces all names to match the scenario locale.
            ``"highly_diverse"`` maximizes ethnic and cultural variety.
        model: Optional LLM model override.  Empty uses the default model.

    """

    count: int = 5
    context: str = ""
    owner_name: str = ""
    theme: str = "slice-of-life"
    culture: str = "american"
    generation_mode: str = "story"
    role_style: str = "normal"
    cast_diversity: str = "mixed"
    model: str = ""


class GenerateNamesResponse(BaseModel):
    """Response body containing generated names and relationship roles.

    Names and roles are parallel arrays — ``roles[i]`` is the relationship
    role for ``names[i]`` relative to the phone owner.

    Attributes:
        names: List of generated full names.
        roles: List of relationship roles (e.g. "best friend", "coworker").

    """

    names: list[str]
    roles: list[str] = Field(default_factory=list)


class SuggestedEvent(BaseModel):
    """A single suggested timeline event from the LLM.

    Captures a plausible event that creates forensic links between devices,
    including impact descriptions and the contacts involved on each device.

    Attributes:
        date: ISO date string for the event (YYYY-MM-DD).
        time: Optional time string (HH:MM) or ``None`` if unspecified.
        description: Narrative description of the event.
        device1_impact: How the event manifests on Device 1's text messages.
        device2_impact: How the event manifests on Device 2's text messages.
        involved_d1_contacts: Contact names involved from Device 1.
        involved_d2_contacts: Contact names involved from Device 2.

    """

    date: str = ""
    time: str | None = None
    description: str = ""
    device1_impact: str = ""
    device2_impact: str = ""
    involved_d1_contacts: list[str] = Field(default_factory=list)
    involved_d2_contacts: list[str] = Field(default_factory=list)


class SuggestEventsResponse(BaseModel):
    """Response from the event suggestion endpoint.

    Contains a list of suggested timeline events that create cross-device
    forensic links through timestamp matches and location overlaps.

    Attributes:
        events: List of suggested timeline events.

    """

    events: list[SuggestedEvent] = Field(default_factory=list)


class SuggestedConnection(BaseModel):
    """A single suggested cross-device connection from the LLM.

    Represents a forensic link between contacts on different devices such
    as shared characters, location links, or near-miss encounters.

    Attributes:
        type: Connection kind (shared_character, location_link, near_miss).
        description: Human-readable explanation of the connection.
        device1_contact: Contact name on Device 1.
        device2_contact: Contact name on Device 2.
        forensic_note: Investigative note about the forensic significance.

    """

    type: str = ""
    description: str = ""
    device1_contact: str = ""
    device2_contact: str = ""
    forensic_note: str = ""


class SuggestConnectionsResponse(BaseModel):
    """Response from the connection suggestion endpoint.

    Contains a list of plausible cross-device connections identified by
    the LLM based on contact rosters across devices.

    Attributes:
        connections: List of suggested cross-device connections.

    """

    connections: list[SuggestedConnection] = Field(default_factory=list)


class EventParticipant(BaseModel):
    """A participant reference linking a person to their device and contact slot.

    Used in fully-populated events and group chat member lists to provide
    concrete ``device_id`` / ``contact_id`` pairs that the frontend can
    consume without additional mapping.

    Attributes:
        device_id: The device this participant belongs to.
        contact_id: The contact slot ID (or ``__owner__`` for the device owner).

    """

    device_id: str = ""
    contact_id: str = ""


class FullEvent(BaseModel):
    """A fully-populated timeline event with participants and per-device impacts.

    Unlike :class:`SuggestedEvent`, which uses free-text contact names,
    this model carries resolved ``device_id`` / ``contact_id`` pairs and
    per-device impact descriptions keyed by device ID.

    Attributes:
        date: ISO date string for the event (YYYY-MM-DD).
        time: Optional time string (HH:MM) or ``None`` if unspecified.
        description: Narrative description of the event.
        participants: Resolved participant references with device/contact IDs.
        device_impacts: Per-device impact descriptions keyed by device ID.

    """

    date: str = ""
    time: str | None = None
    description: str = ""
    participants: list[EventParticipant] = Field(default_factory=list)
    device_impacts: dict[str, str] = Field(default_factory=dict)


class SuggestFullEventsResponse(BaseModel):
    """Response from the fully-populated event suggestion endpoint.

    Each event carries resolved participant IDs and per-device impact
    descriptions ready for direct insertion into the scenario timeline.

    Attributes:
        events: List of fully-populated timeline events.

    """

    events: list[FullEvent] = Field(default_factory=list)


class GenerateStoryArcResponse(BaseModel):
    """Response from the story arc generation endpoint.

    Contains the omniscient narrative bible for the scenario, including
    premise, plot beats, climax, and definitive resolution.

    Attributes:
        story_arc: The full story arc text (300-500 words).

    """

    story_arc: str = ""


class GenerateCharacterArcsResponse(BaseModel):
    """Response from the character arcs generation endpoint.

    Maps each character's full name to their individual narrative arc
    describing motivations, knowledge, actions, and trajectory.

    Attributes:
        arcs: Mapping of character name to arc description text.

    """

    arcs: dict[str, str] = Field(default_factory=dict)


class GroupChatQuality(BaseModel):
    """Quality assessment for the group chat suggestion batch.

    Evaluates how well the suggested group chats link back to known
    timeline events, surfacing warnings when event references are
    missing or invalid.

    Attributes:
        score: Ratio of valid event links to total groups (0.0-1.0).
        severity: Quality tier -- ``"ok"``, ``"warning"``, or ``"critical"``.
        findings: Human-readable issues found during quality evaluation.

    """

    score: float = 0.0
    severity: str = "ok"
    findings: list[str] = Field(default_factory=list)


class SuggestedGroupChat(BaseModel):
    """A single LLM-suggested group chat with resolved member references.

    Carries all configuration the frontend needs to add a group chat
    to the scenario, including member device/contact IDs, activation
    timing, and a quality score.

    Attributes:
        name: Display name for the group chat.
        members: Resolved member references with device/contact IDs.
        vibe: One-sentence description of the group dynamic.
        message_volume: Expected message frequency (heavy/regular/light/minimal).
        start_date: ISO date when the group forms.
        end_date: ISO date when the group disbands (empty if ongoing).
        origin_event_id: Timeline event ID that triggered group formation.
        activation_mode: When the group becomes active (default ``"event_time"``).
        auto_pair_threads: Whether to auto-create paired DM threads.
        quality_score: Individual quality score for this group (0.0-1.0).

    """

    name: str = ""
    members: list[EventParticipant] = Field(default_factory=list)
    vibe: str = ""
    message_volume: str = "regular"
    start_date: str = ""
    end_date: str = ""
    origin_event_id: str = ""
    activation_mode: str = "event_time"
    auto_pair_threads: bool = True
    quality_score: float = 1.0


class SuggestGroupChatsResponse(BaseModel):
    """Response from the group chat suggestion endpoint.

    Contains suggested group chats with resolved member IDs and a
    batch-level quality assessment evaluating event linkage.

    Attributes:
        group_chats: List of suggested group chats with resolved members.
        quality: Batch-level quality assessment of event linkage.

    """

    group_chats: list[SuggestedGroupChat] = Field(default_factory=list)
    quality: GroupChatQuality = Field(default_factory=GroupChatQuality)


NORMAL_LIFE_ROLE_POOL: tuple[str, ...] = (
    "mom",
    "dad",
    "sister",
    "brother",
    "aunt",
    "uncle",
    "cousin",
    "best friend",
    "friend",
    "coworker",
    "classmate",
    "neighbor",
    "roommate",
    "boss",
    "manager",
    "barber",
    "doctor",
    "landlord",
)

PLOT_ROLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(informant|handler|asset|suspect|witness|accomplice|operative|agent|double agent)\b", re.IGNORECASE),
    re.compile(r"\b(detective|investigator|undercover|surveillance|source|fixer|hitman)\b", re.IGNORECASE),
)


def _is_plot_role(role: str) -> bool:
    """Return whether a role appears plot-heavy (crime/spy/thriller-like).

    Args:
        role: Candidate relationship role text.

    Returns:
        True when the role matches known plot-role patterns.

    """
    cleaned = (role or "").strip()
    if not cleaned:
        return False
    return any(pattern.search(cleaned) for pattern in PLOT_ROLE_PATTERNS)


def _fallback_normal_role(index: int) -> str:
    """Pick a stable fallback normal-life role.

    Args:
        index: Position index used to rotate through fallback roles.

    Returns:
        A deterministic normal-life role string.

    """
    return NORMAL_LIFE_ROLE_POOL[index % len(NORMAL_LIFE_ROLE_POOL)]


def _normalize_generated_roles(roles: list[str], names_count: int, theme: str, generation_mode: str, role_style: str) -> list[str]:
    """Constrain generated roles to realistic contact-list distributions.

    Standalone devices get only normal-life roles. Story devices may include
    a small number of plot-heavy roles for suitable themes, but never all.

    Args:
        roles: Generated role labels from the model.
        names_count: Number of names that need role assignments.
        theme: Scenario theme key.
        generation_mode: Per-device generation mode.
        role_style: Requested role-style distribution.

    Returns:
        A normalized role list with bounded plot-role density.

    """
    normalized = [((roles[i] if i < len(roles) else "") or "").strip() for i in range(names_count)]
    if names_count == 0:
        return normalized

    mode = (generation_mode or "story").strip().lower()
    style = (role_style or "normal").strip().lower()
    theme_key = (theme or "slice-of-life").strip().lower()
    allow_plot = mode != "standalone" and theme_key in {"crime", "espionage", "thriller", "corporate"}
    if not allow_plot:
        max_plot_roles = 0
    elif style == "story_heavy":
        max_plot_roles = max(1, int(names_count * 0.45))
    elif style == "mixed":
        max_plot_roles = max(1, int(names_count * 0.25))
    else:
        max_plot_roles = max(1, int(names_count * 0.10))

    plot_positions: list[int] = []
    for idx in range(names_count):
        if not normalized[idx]:
            normalized[idx] = _fallback_normal_role(idx)
        if _is_plot_role(normalized[idx]):
            plot_positions.append(idx)

    # Keep only the first few plot roles; convert the rest to everyday roles.
    for overflow_idx, pos in enumerate(plot_positions[max_plot_roles:]):
        normalized[pos] = _fallback_normal_role(names_count + overflow_idx)

    # Ensure most contacts are ordinary roles by filling any empty leftovers.
    for idx in range(names_count):
        if not normalized[idx]:
            normalized[idx] = _fallback_normal_role(idx)
    return normalized


class GeneratePersonalityRequest(BaseModel):
    """Request body for the personality generation endpoint.

    Attributes:
        name: The character's name.
        role: Their relationship role (e.g. "best friend", "coworker").
        age: Optional age hint.
        context: Optional additional context (city, scenario description).
        owner_name: The device owner's name, for relationship-aware generation.
        theme: Scenario genre preset (e.g. "espionage", "romance").
        culture: Cultural/geographic context (sets the world, not character identity).
        cultural_background: This character's personal ethnic/cultural heritage,
            independent of the scenario locale.  When non-empty the personality
            should reflect this background blended with the scenario setting.
        story_arc: The scenario's global narrative bible.
        character_arc: This specific character's narrative trajectory.
        model: Optional LLM model override.  Empty uses the default model.

    """

    name: str
    role: str = ""
    age: int | None = None
    context: str = ""
    owner_name: str = ""
    theme: str = "slice-of-life"
    culture: str = "american"
    cultural_background: str = ""
    story_arc: str = ""
    character_arc: str = ""
    model: str = ""


class SuggestEventsRequest(BaseModel):
    """Request body for the event suggestion endpoint.

    Attributes:
        characters: List of character summaries (name + role pairs).
        connections: List of connection descriptions for cross-device context.
        date_start: ISO date string for the scenario start.
        date_end: ISO date string for the scenario end.
        count: Number of events to suggest.
        model: Optional LLM model override.  Empty uses the default model.

    """

    characters: list[str] = Field(default_factory=list)
    connections: list[str] = Field(default_factory=list)
    date_start: str = "2025-01-01"
    date_end: str = "2025-12-31"
    count: int = 8
    model: str = ""


class RosterContact(BaseModel):
    """A single contact in the roster payload sent for full event generation.

    Attributes:
        contact_id: The contact slot ID (or ``__owner__`` for the device owner).
        name: Display name.
        role: Short relationship descriptor.
        personality_summary: One-line personality description for context.

    """

    contact_id: str = ""
    name: str = ""
    role: str = ""
    personality_summary: str = ""


class RosterDevice(BaseModel):
    """Device info in the roster payload sent for full event generation.

    Attributes:
        device_id: Unique device identifier.
        device_label: Human-readable label (e.g. "Device 1").
        owner_name: Name of the phone owner.
        contacts: List of contacts on this device (owner included separately).

    """

    device_id: str = ""
    device_label: str = ""
    owner_name: str = ""
    contacts: list[RosterContact] = Field(default_factory=list)


class SuggestFullEventsRequest(BaseModel):
    """Request body for the fully-populated event suggestion endpoint.

    Carries the full device/contact structure with IDs so the backend
    can build a numbered roster, ask the LLM to reference roster numbers,
    and map the results back to concrete ``device_id`` / ``contact_id``
    pairs before returning.

    Attributes:
        devices: Full device roster with IDs and personality summaries.
        date_start: ISO date string for the scenario start.
        date_end: ISO date string for the scenario end.
        count: Number of events to generate.
        existing_descriptions: Descriptions of events that already exist,
            so the LLM can avoid duplicating them.
        theme: Scenario genre preset (e.g. "espionage", "romance").
        model: Optional LLM model override.  Empty uses the default model.

    """

    devices: list[RosterDevice] = Field(default_factory=list)
    date_start: str = "2025-01-01"
    date_end: str = "2025-12-31"
    count: int = 6
    existing_descriptions: list[str] = Field(default_factory=list)
    theme: str = "slice-of-life"
    culture: str = "american"
    story_arc: str = ""
    model: str = ""


class SuggestConnectionsRequest(BaseModel):
    """Request body for the connection suggestion endpoint.

    Attributes:
        devices: List of device summaries, each containing owner + contacts.
        count: Number of connections to suggest.
        model: Optional LLM model override.  Empty uses the default model.

    """

    devices: list[str] = Field(default_factory=list)
    count: int = 3
    model: str = ""


# ---------------------------------------------------------------------------
# Endpoints (stubs — will be fully implemented in ai-assist-endpoints todo)
# ---------------------------------------------------------------------------


@router.post("/generate-names")
@limiter.limit("20/minute")
async def generate_names(request: Request, req: GenerateNamesRequest) -> GenerateNamesResponse:
    """Generate realistic character names with relationship roles using the LLM.

    Produces diverse, realistic full names suitable for an SMS conversation
    scenario, each paired with a relationship role relative to the phone
    owner (e.g. "coworker", "sister", "gym buddy").  Roles provide the
    connective tissue that informs personality generation and event planning.

    Args:
        request: The incoming HTTP request (used by the rate limiter).
        req: The name generation request with count, context, and owner name.

    Returns:
        Parallel lists of generated names and their relationship roles.

    """
    context_hint = f" Context: {req.context}" if req.context else ""
    owner_hint = f" The phone owner is {req.owner_name}." if req.owner_name else ""
    theme_hint = THEME_PROMPTS.get(req.theme, THEME_PROMPTS["slice-of-life"])
    culture_hint = CULTURE_PROMPTS.get(req.culture, CULTURE_PROMPTS["american"])

    diversity_directive = {
        "homogeneous": "All names should use naming conventions typical of the cultural setting above.",
        "mixed": (
            "The cast should be DIVERSE. Most names should be realistic for people living in "
            "the described region, but include a natural mix of ethnic and cultural backgrounds "
            "(immigrants, expats, mixed heritage, visitors). NOT everyone should share the same "
            "naming convention. A realistic contact list reflects the multicultural reality of "
            "modern cities."
        ),
        "highly_diverse": (
            "MAXIMIZE ethnic and cultural diversity. The cast should include people from many "
            "different backgrounds, heritages, and naming traditions. This is a highly "
            "cosmopolitan setting where people of all origins coexist."
        ),
    }.get(req.cast_diversity, "")

    system_prompt = (
        "You generate realistic character names for a fictional SMS dataset. "
        "Each character also needs a relationship role describing how they know the phone owner. "
        f"GENRE CONTEXT: {theme_hint}\n"
        f"CULTURAL CONTEXT: {culture_hint}\n"
        f"DEVICE MODE: {req.generation_mode}\n"
        f"ROLE STYLE: {req.role_style}\n"
        "Return a JSON object with two keys:\n"
        '  "names": ["Full Name", ...]\n'
        '  "roles": ["relationship role", ...]\n'
        f"CAST DIVERSITY: {diversity_directive}\n"
        "No famous or celebrity names. "
        "Prioritize realistic phone contacts: family, close friends, coworkers, neighbors, and normal services. "
        "Do NOT make everyone connected to one central plot. "
        "Most roles must be everyday life roles. "
        "If DEVICE MODE is 'standalone', roles must be normal-life only (no plot/spy/crime roles). "
        "ROLE STYLE controls maximum plot-role share when DEVICE MODE is story: "
        "normal≈10%, mixed≈25%, story_heavy≈45% (only if genre fits)."
    )
    user_prompt = f"Generate {req.count} realistic characters (name + role) for a phone's contact list.{owner_hint}{context_hint}"

    data = await _call_openai_json(system_prompt, user_prompt, model=req.model)
    names = _coerce_str_list(data.get("names"))
    roles = _coerce_str_list(data.get("roles"))
    names = names[: req.count]
    roles = _normalize_generated_roles(
        roles=roles,
        names_count=len(names),
        theme=req.theme,
        generation_mode=req.generation_mode,
        role_style=req.role_style,
    )
    return GenerateNamesResponse(names=names, roles=roles[: len(names)])


@router.post("/generate-personality")
@limiter.limit("20/minute")
async def generate_personality(request: Request, req: GeneratePersonalityRequest) -> FlexPersonalityProfile:
    """Generate a full PersonalityProfile for a character using the LLM.

    Creates a detailed personality including texting style, hobbies, local
    hangout spots, emotional range, and sample phrases.  When the request
    includes a ``cultural_background``, the LLM blends the character's
    heritage with the scenario locale.  The profile is returned as a
    :class:`FlexPersonalityProfile` so the frontend can populate form fields.

    Args:
        request: The incoming HTTP request (used by the rate limiter).
        req: The personality generation request with name, role, and context.

    Returns:
        A validated personality profile with all LLM-generated fields.

    """
    owner_ref = f" The phone owner is {req.owner_name}." if req.owner_name else ""
    age_ref = f" They are {req.age} years old." if req.age else ""
    context_ref = f" Additional context: {req.context}" if req.context else ""
    theme_ctx = THEME_PROMPTS.get(req.theme, THEME_PROMPTS["slice-of-life"])
    culture_ctx = CULTURE_PROMPTS.get(req.culture, CULTURE_PROMPTS["american"])

    background_directive = ""
    if req.cultural_background:
        background_directive = (
            f"\nCHARACTER BACKGROUND: This character's personal heritage is "
            f'"{req.cultural_background}". Their personality, food preferences, hobbies, '
            f"media consumption, humor, and texting quirks should reflect BOTH their personal "
            f"heritage AND their adaptation to the scenario's cultural setting above. "
            f"They may code-switch, reference foods or customs from their heritage, "
            f"or blend cultural influences naturally.\n"
        )

    system_prompt = (
        "You create detailed character profiles for a fictional SMS conversation dataset. "
        f"GENRE CONTEXT: {theme_ctx}\n"
        f"CULTURAL SETTING (where the story takes place): {culture_ctx}\n"
        f"{background_directive}"
        "The personality, backstory, daily routine, hobbies, food, hangout spots, and texting "
        "style should be consistent with this genre AND setting. When the character has a "
        "specific cultural background, blend their heritage with the local setting realistically. "
        "Use real locations, foods, social norms, and daily life details from the setting. "
        "Return a JSON object matching this EXACT schema:\n"
        "{\n"
        '  "actor_id": "",\n'
        '  "name": "string",\n'
        '  "age": number,\n'
        "  \"cultural_background\": \"string (ethnic/cultural heritage, e.g. 'local Chinese', 'Nigerian expat', 'mixed Korean-American')\",\n"
        '  "neighborhood": "string (neighborhood in the setting\'s region)",\n'
        '  "role": "string",\n'
        '  "job_details": "string",\n'
        '  "personality_summary": "string (3-5 sentences)",\n'
        '  "emotional_range": "string",\n'
        '  "backstory_details": "string",\n'
        '  "hobbies_and_interests": ["string", ...],\n'
        '  "favorite_media": ["string", ...],\n'
        '  "food_and_drink": "string",\n'
        '  "favorite_local_spots": ["string — real places in the setting\'s region", ...],\n'
        '  "current_life_situations": ["string", ...],\n'
        '  "topics_they_bring_up": ["string", ...],\n'
        '  "topics_they_avoid": ["string", ...],\n'
        '  "pet_peeves": ["string", ...],\n'
        '  "humor_style": "string",\n'
        '  "daily_routine_notes": "string",\n'
        '  "texting_style": {\n'
        '    "punctuation": "string",\n'
        '    "capitalization": "string",\n'
        '    "emoji_use": "string",\n'
        '    "abbreviations": "string",\n'
        '    "avg_message_length": "string",\n'
        '    "quirks": "string"\n'
        "  },\n"
        '  "how_owner_talks_to_them": "string (how the phone owner adapts their voice for this person)",\n'
        '  "relationship_arc": "string",\n'
        '  "sample_phrases": ["string", ...],\n'
        '  "suggested_message_volume": "heavy|regular|light|minimal"\n'
        "}\n"
        "VOLUME GUIDE for suggested_message_volume — pick based on how this person "
        "would realistically text the phone owner:\n"
        '  "heavy" — partner, BFF, someone they text daily with long exchanges\n'
        '  "regular" — good friend, sibling, close coworker, regular back-and-forth\n'
        '  "light" — acquaintance, distant friend, boss, occasional check-ins\n'
        '  "minimal" — service contact, barber, ex, doctor, rare transactional texts\n\n'
        "Be creative and specific. Use real places, real shows, real food spots. "
        "Make the texting style distinctive and different from generic patterns. "
        "If the character has a specific cultural background, let their heritage "
        "influence their food, media, humor, and speech patterns naturally."
    )
    if req.story_arc:
        system_prompt += f"\n\nSTORY BIBLE (personality must be consistent with this narrative):\n{req.story_arc}\n"
    if req.character_arc:
        system_prompt += (
            f"\nCHARACTER ARC (this person's role in the story):\n{req.character_arc}\n"
            "Make the personality, backstory, and daily routine consistent with this arc."
        )
    user_prompt = (
        f"Create a full personality profile for {req.name}, whose role is: {req.role or 'a contact'}.{owner_ref}{age_ref}{context_ref}"
    )

    data = await _call_openai_json(system_prompt, user_prompt, model=req.model)
    data["name"] = req.name
    if req.role:
        data["role"] = req.role
    return FlexPersonalityProfile(**data)


@router.post("/suggest-events")
@limiter.limit("20/minute")
async def suggest_events(request: Request, req: SuggestEventsRequest) -> SuggestEventsResponse:
    """Suggest shared timeline events given the cast of characters.

    The LLM produces plausible events that create cross-device connections,
    forensic breadcrumbs, and narrative interest across the scenario's
    date range.

    Args:
        request: The incoming HTTP request (used by the rate limiter).
        req: The event suggestion request with characters, connections, and date range.

    Returns:
        Response containing a list of suggested timeline events.

    """
    chars_block = "\n".join(f"- {c}" for c in req.characters) if req.characters else "No characters defined yet."
    conns_block = "\n".join(f"- {c}" for c in req.connections) if req.connections else "No connections defined yet."

    system_prompt = (
        "You suggest shared timeline events for a multi-device SMS conversation dataset. "
        "Events should create forensic links between devices — timestamp matches, location "
        "overlaps, character reactions that align across phones. Return JSON with key 'events', "
        "each event having: date (ISO), time (HH:MM or null), description, "
        "device1_impact, device2_impact, involved_d1_contacts (array), involved_d2_contacts (array)."
    )
    user_prompt = (
        f"Suggest {req.count} shared timeline events between {req.date_start} and {req.date_end}.\n\n"
        f"Characters:\n{chars_block}\n\nConnections:\n{conns_block}"
    )

    data = await _call_openai_json(system_prompt, user_prompt, model=req.model)
    raw_events = _coerce_object_list(data.get("events"))
    events = [SuggestedEvent(**{k: v for k, v in ev.items() if isinstance(k, str)}) for ev in raw_events]
    return SuggestEventsResponse(events=events)


@router.post("/suggest-connections")
@limiter.limit("20/minute")
async def suggest_connections(request: Request, req: SuggestConnectionsRequest) -> SuggestConnectionsResponse:
    """Suggest cross-device connections given the device configurations.

    The LLM identifies plausible shared characters, location links, and
    near-miss opportunities based on the contacts across devices.

    Args:
        request: The incoming HTTP request (used by the rate limiter).
        req: The connection suggestion request with device summaries.

    Returns:
        Response containing a list of suggested cross-device connections.

    """
    devices_block = "\n".join(f"- {d}" for d in req.devices) if req.devices else "No devices defined yet."

    system_prompt = (
        "You suggest cross-device connections for a multi-device SMS dataset. "
        "Connections can be: shared_character (same person on two phones behaving differently), "
        "location_link (a real place mentioned on both devices), or near_miss (characters from "
        "different phones in the same place at the same time unknowingly). "
        "Return JSON with key 'connections', each having: type, description, "
        "device1_contact, device2_contact, forensic_note."
    )
    user_prompt = f"Suggest {req.count} cross-device connections.\n\nDevices:\n{devices_block}"

    data = await _call_openai_json(system_prompt, user_prompt, model=req.model)
    raw_connections = _coerce_object_list(data.get("connections"))
    connections = [SuggestedConnection(**{k: v for k, v in conn.items() if isinstance(k, str)}) for conn in raw_connections]
    return SuggestConnectionsResponse(connections=connections)


# ---------------------------------------------------------------------------
# Fully-populated event generation (roster-based)
# ---------------------------------------------------------------------------


def _build_roster(devices: list[RosterDevice]) -> tuple[list[str], dict[int, dict[str, str]], dict[str, str]]:
    """Build a numbered roster string and reverse-mapping dictionaries.

    Assigns a sequential number to every person across all devices (owners
    first, then contacts).  Returns three things:

    1. A list of human-readable roster lines for the LLM prompt.
    2. A mapping from roster number → ``{"device_id": ..., "contact_id": ...}``.
    3. A mapping from device_label → device_id.

    Args:
        devices: The full device roster from the request.

    Returns:
        Tuple of (roster_lines, number_to_ref, label_to_device_id).

    """
    roster_lines: list[str] = []
    number_to_ref: dict[int, dict[str, str]] = {}
    label_to_device_id: dict[str, str] = {}
    idx = 1

    for dev in devices:
        label_to_device_id[dev.device_label] = dev.device_id
        roster_lines.append(f"[{idx}] {dev.owner_name or 'Unnamed Owner'} — owner of {dev.device_label}")
        number_to_ref[idx] = {"device_id": dev.device_id, "contact_id": "__owner__"}
        idx += 1

        for contact in dev.contacts:
            ps = f" — {contact.personality_summary[:120]}" if contact.personality_summary else ""
            roster_lines.append(f"[{idx}] {contact.name or 'Unnamed'} — {contact.role or 'contact'} on {dev.device_label}{ps}")
            number_to_ref[idx] = {"device_id": dev.device_id, "contact_id": contact.contact_id}
            idx += 1

    return roster_lines, number_to_ref, label_to_device_id


@router.post("/suggest-full-events")
@limiter.limit("20/minute")
async def suggest_full_events(request: Request, req: SuggestFullEventsRequest) -> SuggestFullEventsResponse:
    """Generate fully-populated timeline events with participants and per-device impacts.

    Unlike ``/suggest-events`` which returns minimal event stubs, this
    endpoint:

    1. Builds a numbered roster of every person across all devices.
    2. Asks the LLM to generate events referencing roster numbers and
       producing per-device impact descriptions.
    3. Maps roster numbers back to concrete ``device_id`` / ``contact_id``
       pairs and device labels back to device IDs.

    This lets the frontend add the returned events directly to the scenario
    without any manual mapping.

    Args:
        request: The incoming HTTP request (used by the rate limiter).
        req: The full event generation request with device roster and dates.

    Returns:
        Response containing fully-populated events with resolved participant
        IDs and per-device impact descriptions.

    """
    roster_lines, number_to_ref, label_to_device_id = _build_roster(req.devices)
    roster_block = "\n".join(roster_lines)

    existing_block = ""
    if req.existing_descriptions:
        existing_block = "\n\nEvents that ALREADY exist (do NOT duplicate these):\n" + "\n".join(
            f"- {d}" for d in req.existing_descriptions
        )

    device_labels = [dev.device_label for dev in req.devices]
    device_labels_str = ", ".join(f'"{lbl}"' for lbl in device_labels)

    theme_ctx = THEME_PROMPTS.get(req.theme, THEME_PROMPTS["slice-of-life"])
    culture_ctx = CULTURE_PROMPTS.get(req.culture, CULTURE_PROMPTS["american"])
    story_arc_block = ""
    if req.story_arc:
        story_arc_block = (
            f"\n\nSTORY BIBLE (events MUST be consistent with this narrative):\n"
            f"{req.story_arc}\n"
            "Events should advance or reflect the plot beats described above.\n"
        )
    system_prompt = (
        "You generate fully-detailed shared timeline events for a multi-device SMS conversation dataset. "
        f"GENRE CONTEXT: {theme_ctx}\n"
        f"CULTURAL CONTEXT: {culture_ctx}\n\n"
        f"{story_arc_block}"
        "The goal is to create events that connect people across different phones, producing forensic "
        "breadcrumbs—timestamp matches, location overlaps, character reactions visible on multiple devices.\n"
        "Events MUST be culturally appropriate — use real locations, venues, and social customs from "
        "the specified cultural region.\n\n"
        "ROSTER (reference people by their [number]):\n"
        f"{roster_block}\n\n"
        "Return a JSON object with key 'events', where each event has:\n"
        '  - "date": ISO date string (YYYY-MM-DD)\n'
        '  - "time": time string "HH:MM" or null\n'
        '  - "description": 2-4 sentences describing the event\n'
        '  - "participant_numbers": array of roster [numbers] involved (at least 2, from different devices)\n'
        '  - "device_impacts": object where each key is a device label '
        f"(one of {device_labels_str}) and the value is a 1-2 sentence description of "
        "how this event manifests on that device's text messages\n\n"
        "Make events specific, realistic, and culturally grounded. "
        "Each event MUST involve people from at least 2 different devices. "
        "Vary event types according to the genre and culture."
    )

    user_prompt = f"Generate {req.count} fully-detailed shared timeline events between {req.date_start} and {req.date_end}.{existing_block}"

    raw = await _call_openai_json(system_prompt, user_prompt, model=req.model)
    raw_events = _coerce_object_list(raw.get("events"))

    mapped_events: list[FullEvent] = []
    for ev in raw_events:
        participants: list[EventParticipant] = []
        for num in _coerce_str_list(ev.get("participant_numbers")):
            ref = number_to_ref.get(int(num))
            if ref:
                participants.append(EventParticipant(**ref))

        device_impacts: dict[str, str] = {}
        for label, impact_text in _coerce_mapping(ev.get("device_impacts")).items():
            dev_id = label_to_device_id.get(label, "")
            if dev_id:
                device_impacts[dev_id] = str(impact_text)

        mapped_events.append(
            FullEvent(
                date=str(ev.get("date", "")),
                time=str(ev.get("time", "")) or None,
                description=str(ev.get("description", "")),
                participants=participants,
                device_impacts=device_impacts,
            )
        )

    return SuggestFullEventsResponse(events=mapped_events)


# ---------------------------------------------------------------------------
# Story Arc generation
# ---------------------------------------------------------------------------


class GenerateStoryArcRequest(BaseModel):
    """Request body for the story arc generation endpoint.

    Attributes:
        theme: Scenario genre preset.
        culture: Cultural/geographic context for the narrative.
        cast_summary: Text block listing all device owners and their contacts.
        existing_events: Text block of events already defined (may be empty).
        date_start: ISO date string for the scenario timeline start.
        date_end: ISO date string for the scenario timeline end.
        num_events: Suggested number of events the arc should reference.
        model: Optional LLM model override.  Empty uses the default model.

    """

    theme: str = "slice-of-life"
    culture: str = "american"
    cast_summary: str = ""
    existing_events: str = ""
    date_start: str = "2025-01-01"
    date_end: str = "2025-12-31"
    num_events: int = 6
    model: str = ""


class GenerateCharacterArcsRequest(BaseModel):
    """Request body for the character arcs generation endpoint.

    Attributes:
        theme: Scenario genre preset.
        culture: Cultural/geographic context for character arcs.
        story_arc: The global story arc / narrative bible.
        cast_summary: Text block listing all characters.
        character_names: Canonical character names that MUST receive arcs.
        standalone_character_names: Names of characters on standalone-mode
            devices. These characters should receive normal life arcs that are
            independent from the global story arc.
        model: Optional LLM model override.  Empty uses the default model.

    """

    theme: str = "slice-of-life"
    culture: str = "american"
    story_arc: str = ""
    cast_summary: str = ""
    character_names: list[str] = Field(default_factory=list)
    standalone_character_names: list[str] = Field(default_factory=list)
    model: str = ""


@router.post("/generate-story-arc")
@limiter.limit("20/minute")
async def generate_story_arc(request: Request, req: GenerateStoryArcRequest) -> GenerateStoryArcResponse:
    """Generate the overarching narrative for the scenario.

    Creates a coherent story arc with a premise, key plot beats,
    escalation, and definitive resolution based on the theme, cast,
    and date range.  For mystery/thriller themes, this includes the
    "answer" — who did it, what the motive was, how it gets discovered.

    Args:
        request: The incoming HTTP request (used by the rate limiter).
        req: The story arc generation request.

    Returns:
        Response containing the story arc narrative text.

    """
    theme_ctx = THEME_PROMPTS.get(req.theme, THEME_PROMPTS["slice-of-life"])
    culture_ctx = CULTURE_PROMPTS.get(req.culture, CULTURE_PROMPTS["american"])
    events_block = f"\n\nExisting events (incorporate these):\n{req.existing_events}" if req.existing_events else ""

    system_prompt = (
        "You are a narrative designer creating the story bible for a fictional SMS conversation dataset. "
        f"GENRE: {theme_ctx}\n"
        f"CULTURAL SETTING: {culture_ctx}\n"
        "The entire story must be grounded in this cultural context — locations, social dynamics, "
        "customs, and conflicts should feel authentic to the region.\n\n"
        "Write a detailed STORY ARC that includes:\n"
        "1. PREMISE: What's the situation at the start?\n"
        "2. KEY CHARACTERS: Who's involved and what's their role in the story?\n"
        "3. INCITING INCIDENT: What kicks off the main conflict?\n"
        "4. ESCALATION: 3-5 major plot beats that build tension across the timeline.\n"
        "5. CLIMAX: The turning point.\n"
        "6. RESOLUTION: How it ends. Be DEFINITIVE — no ambiguity on the central question.\n"
        "   For mysteries: state who did it and why.\n"
        "   For thrillers: state what the threat was and how it's resolved.\n"
        "   For romance: state who ends up together and why.\n"
        "7. SECRETS: What do different characters know vs. not know?\n\n"
        "Return a JSON object with key 'story_arc' containing a detailed narrative paragraph (300-500 words). "
        "Write it as a story bible — factual, omniscient, spoilers included. This is the ANSWER KEY."
    )

    user_prompt = (
        f"Create a story arc for this scenario ({req.date_start} to {req.date_end}).\n"
        f"The timeline should naturally span about {req.num_events} key events across that window.\n\n"
        f"CAST:\n{req.cast_summary}{events_block}"
    )

    data = await _call_openai_json(system_prompt, user_prompt, model=req.model)
    raw_arc = data.get("story_arc", "")
    if not isinstance(raw_arc, str):
        raw_arc = json.dumps(raw_arc, indent=2) if raw_arc else ""
    return GenerateStoryArcResponse(story_arc=raw_arc)


@router.post("/generate-character-arcs")
@limiter.limit("20/minute")
async def generate_character_arcs(request: Request, req: GenerateCharacterArcsRequest) -> GenerateCharacterArcsResponse:
    """Generate individual narrative arcs for every character.

    Given the global story arc, assigns each character their own
    trajectory: what they know, what they're doing, how they change,
    and their role in the plot.  The phone owner is NOT assumed to be
    the protagonist — they could be the villain, a victim, or a
    bystander.

    Args:
        request: The incoming HTTP request (used by the rate limiter).
        req: The character arcs generation request.

    Returns:
        Response containing a mapping of character name to arc description.

    """
    theme_ctx = THEME_PROMPTS.get(req.theme, THEME_PROMPTS["slice-of-life"])
    culture_ctx = CULTURE_PROMPTS.get(req.culture, CULTURE_PROMPTS["american"])
    required_names = [name.strip() for name in req.character_names if (name or "").strip()]

    standalone_names = [name.strip() for name in req.standalone_character_names if (name or "").strip()]
    standalone_block = ""
    if standalone_names:
        standalone_block = (
            "STANDALONE CHARACTERS (NOT part of the main plot):\n"
            + "\n".join(f"- {name}" for name in standalone_names)
            + "\n\nFor these standalone characters:\n"
            "- Write ordinary life arcs (work stress, family plans, dating, money issues, health, travel, habits).\n"
            "- Keep arcs realistic and specific, but independent from the master story.\n"
            "- Do NOT give them privileged knowledge about the main plot.\n\n"
        )

    story_arc_block = ""
    if req.story_arc:
        story_arc_block = f"STORY ARC (the master narrative — treat as ground truth):\n{req.story_arc}\n\n"

    system_prompt = (
        "You are assigning individual narrative arcs to characters in a fictional SMS dataset. "
        f"GENRE: {theme_ctx}\n"
        f"CULTURAL CONTEXT: {culture_ctx}\n\n"
        f"{story_arc_block}{standalone_block}"
        "For EACH character listed, write a 2-4 sentence arc describing:\n"
        "- Their current life pressures and goals\n"
        "- What they know vs. what they hide (if relevant)\n"
        "- How they change over the timeline (start vs. end)\n"
        "- Concrete actions they take\n\n"
        "Make each arc distinct and non-repetitive; avoid repeating the same template for everyone.\n\n"
        "You MUST provide an arc for EVERY required character. Do not omit anyone.\n"
        "Return a JSON object with key 'arcs' — an object where each key is the character's "
        "full name (exactly as listed) and the value is their arc string."
    )

    required_block = ""
    if required_names:
        required_block = "\nRequired exact names:\n" + "\n".join(f"- {name}" for name in required_names)

    user_prompt = f"CAST:\n{req.cast_summary}{required_block}\n\nGenerate individual arcs for every person listed above."

    data = await _call_openai_json(system_prompt, user_prompt, model=req.model)
    raw_arcs = data.get("arcs", {})
    if isinstance(raw_arcs, dict):
        coerced: dict[str, str] = {}
        for name, arc_val in raw_arcs.items():
            if isinstance(arc_val, str):
                coerced[name] = arc_val
            else:
                coerced[name] = json.dumps(arc_val, indent=2) if arc_val else ""
        raw_arcs = coerced

    # Ensure every required name gets a value, even with casing/matching drift.
    if isinstance(raw_arcs, dict) and required_names:
        by_lower = {(k or "").strip().lower(): v for k, v in raw_arcs.items()}
        standalone_set = {name.lower() for name in standalone_names}
        completed: dict[str, str] = {}
        for name in required_names:
            key = (name or "").strip()
            if not key:
                continue
            value = raw_arcs.get(key) or by_lower.get(key.lower(), "")
            if not value:
                if key.lower() in standalone_set:
                    value = (
                        f"{key} is focused on day-to-day life and stays outside the core plot. "
                        "Their arc follows realistic pressures (work, family, money, health, or relationships) "
                        "with small but meaningful changes over time."
                    )
                else:
                    value = (
                        f"{key} has a gradual arc shaped by evolving relationships and the scenario timeline. "
                        "They react to key developments while maintaining believable daily routines and communication habits."
                    )
            completed[key] = value
        raw_arcs = completed
    return GenerateCharacterArcsResponse(arcs=raw_arcs if isinstance(raw_arcs, dict) else {})


# ---------------------------------------------------------------------------
# Group Chat suggestions
# ---------------------------------------------------------------------------


class SuggestGroupChatsDevice(BaseModel):
    """Minimal device info for group chat suggestion.

    Attributes:
        device_id: Unique device identifier.
        device_label: Human-readable device label.
        owner_name: Name of the device owner.
        contacts: List of contacts on this device.

    """

    device_id: str = ""
    device_label: str = ""
    owner_name: str = ""
    contacts: list[RosterContact] = Field(default_factory=list)


class SuggestGroupChatsEvent(BaseModel):
    """Event candidate used to anchor suggested group chats.

    Attributes:
        event_id: Existing timeline event ID.
        date: Event date (ISO).
        description: Event description text.
        participant_names: Human-readable participants involved in the event.

    """

    event_id: str = ""
    date: str = ""
    description: str = ""
    participant_names: list[str] = Field(default_factory=list)


class SuggestGroupChatsRequest(BaseModel):
    """Request body for the group chat suggestion endpoint.

    Attributes:
        theme: Scenario genre preset.
        story_arc: Global narrative bible.
        cast_summary: Text listing all characters.
        events_summary: Text listing existing events.
        events: Structured timeline events to anchor group formation.
        devices: Device roster with IDs for member mapping.
        model: Optional LLM model override.  Empty uses the default model.

    """

    theme: str = "slice-of-life"
    culture: str = "american"
    story_arc: str = ""
    cast_summary: str = ""
    events_summary: str = ""
    events: list[SuggestGroupChatsEvent] = Field(default_factory=list)
    devices: list[SuggestGroupChatsDevice] = Field(default_factory=list)
    model: str = ""


@router.post("/suggest-group-chats")
@limiter.limit("20/minute")
async def suggest_group_chats(request: Request, req: SuggestGroupChatsRequest) -> SuggestGroupChatsResponse:
    """Suggest group chats that would naturally form from the scenario.

    Analyzes the cast, events, and story arc to suggest 1-3 group
    chats with concrete member lists mapped back to device/contact IDs.

    Args:
        request: The incoming HTTP request (used by the rate limiter).
        req: The group chat suggestion request.

    Returns:
        Response containing suggested group chats with quality assessment.

    """
    theme_ctx = THEME_PROMPTS.get(req.theme, THEME_PROMPTS["slice-of-life"])
    culture_ctx = CULTURE_PROMPTS.get(req.culture, CULTURE_PROMPTS["american"])
    story_block = f"\nSTORY ARC:\n{req.story_arc}\n" if req.story_arc else ""
    events_block = f"\nEXISTING EVENTS:\n{req.events_summary}\n" if req.events_summary else ""
    structured_events_lines: list[str] = []
    for ev in req.events:
        who = ", ".join(ev.participant_names) if ev.participant_names else "unknown participants"
        structured_events_lines.append(f"- [{ev.event_id}] {ev.date}: {ev.description} | participants: {who}")
    structured_events_block = "\n".join(structured_events_lines) if structured_events_lines else "No structured events provided."

    roster_lines: list[str] = []
    id_lookup: dict[str, dict[str, str]] = {}
    num = 1
    for dev in req.devices:
        roster_lines.append(f"[{num}] {dev.owner_name} (owner of {dev.device_label})")
        id_lookup[str(num)] = {"device_id": dev.device_id, "contact_id": "__owner__"}
        num += 1
        for c in dev.contacts:
            roster_lines.append(f"[{num}] {c.name} ({c.role} on {dev.device_label})")
            id_lookup[str(num)] = {"device_id": dev.device_id, "contact_id": c.contact_id}
            num += 1

    roster_block = "\n".join(roster_lines)

    system_prompt = (
        "You suggest realistic group chats for a multi-device SMS conversation dataset. "
        f"GENRE: {theme_ctx}\n"
        f"CULTURE: {culture_ctx}\n"
        f"{story_block}{events_block}\n"
        "ROSTER (reference by [number]):\n"
        f"{roster_block}\n\n"
        "Based on the events, story, and cultural context, suggest 1-3 group chats that "
        "would NATURALLY form. Think about: friends who met at events, coworkers, family groups, "
        "hobby groups, or any group that makes sense for the genre and culture.\n\n"
        "EVENT CANDIDATES (choose origin_event_id from these IDs whenever possible):\n"
        f"{structured_events_block}\n\n"
        "Return JSON with key 'group_chats', array of objects with:\n"
        '  - "name": group chat name (e.g., "The Squad", "Work Crew")\n'
        '  - "member_numbers": array of roster [numbers] (at least 3 members)\n'
        '  - "vibe": 1-sentence description of the group dynamic\n'
        '  - "message_volume": "heavy", "regular", "light", or "minimal"\n'
        '  - "start_date": ISO date when the group forms (YYYY-MM-DD)\n'
        '  - "origin_event_id": event ID from EVENT CANDIDATES that best explains why this group formed\n'
        '  - "activation_mode": use "event_time" unless there is a strong reason otherwise\n'
        '  - "auto_pair_threads": true or false (default true)\n'
    )

    user_prompt = f"CAST:\n{req.cast_summary}\n\nSuggest group chats."
    data = await _call_openai_json(system_prompt, user_prompt, model=req.model)

    known_event_ids = {ev.event_id for ev in req.events if ev.event_id}
    mapped_groups: list[SuggestedGroupChat] = []
    findings: list[str] = []
    valid_links = 0
    for gc in _coerce_object_list(data.get("group_chats")):
        members: list[EventParticipant] = []
        for mnum in _coerce_str_list(gc.get("member_numbers")):
            ref = id_lookup.get(str(mnum))
            if ref:
                members.append(EventParticipant(**ref))
        if len(members) >= MIN_GROUP_MEMBERS:
            origin_event_id = str(gc.get("origin_event_id", "") or "")
            start_date = str(gc.get("start_date", "") or "")
            if origin_event_id and origin_event_id in known_event_ids:
                valid_links += 1
            elif origin_event_id:
                findings.append(f"Group '{gc.get('name', 'Group Chat')}' references unknown event id '{origin_event_id}'.")
            else:
                findings.append(f"Group '{gc.get('name', 'Group Chat')}' has no origin_event_id.")
            mapped_groups.append(
                SuggestedGroupChat(
                    name=str(gc.get("name", "Group Chat")),
                    members=members,
                    vibe=str(gc.get("vibe", "")),
                    message_volume=str(gc.get("message_volume", "regular")),
                    start_date=start_date,
                    end_date="",
                    origin_event_id=origin_event_id,
                    activation_mode=str(gc.get("activation_mode", "event_time")),
                    auto_pair_threads=bool(gc.get("auto_pair_threads", True)),
                    quality_score=1.0,
                )
            )

    link_score = valid_links / max(len(mapped_groups), 1) if mapped_groups else 1.0
    severity = "ok" if link_score >= GROUP_OK_THRESHOLD else ("warning" if link_score >= GROUP_WARNING_THRESHOLD else "critical")
    quality = GroupChatQuality(score=link_score, severity=severity, findings=findings)
    return SuggestGroupChatsResponse(group_chats=mapped_groups, quality=quality)
