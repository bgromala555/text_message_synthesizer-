"""Timeline event extraction, injection, and encounter term matching.

Provides utilities for extracting timeline events relevant to specific
owner-contact pairs, splitting events into temporal buckets (buildup /
active / aftermath), augmenting message skeletons around event dates,
and formatting event directives for LLM prompts.  Also contains
multilingual encounter keyword dictionaries and forced coordination
injection for planned events.

The module is organised into five sections:

1. **Models & constants** — ``ConversationEvent`` Pydantic model,
   ``ENCOUNTER_PROMPTS``, and ``ENCOUNTER_TERMS``.
2. **Keyword helpers** — ``get_encounter_terms`` and
   ``_contact_name_in_impact``.
3. **Date helpers** — ``_safe_message_date``, ``_safe_date_from_iso``.
4. **Event extraction & splitting** — ``extract_conversation_events``,
   ``events_for_batch``, ``event_window_text``.
5. **Skeleton / prompt augmentation** — ``augment_skeleton_for_events``,
   ``format_event_directives``, ``force_planned_event_coordination``.
"""

from __future__ import annotations

import logging
import random
import re
from datetime import date, datetime, timedelta

from pydantic import BaseModel

from messageviewer.models import Message
from source.models import ContactSlot, DeviceScenario, FlexTimelineEvent
from source.skeleton import DEFAULT_TIMEZONE, SkeletonMessage

logger = logging.getLogger(__name__)

# Magic value constants for event temporal logic (PLR2004)
_MIN_NAME_PARTS = 2
_EVENT_BUILDUP_DAYS = 7
_EVENT_AFTERMATH_DAYS = 5
_PRE_EVENT_DELTA_MIN = -3

# ---------------------------------------------------------------------------
# LLM encounter-type prompts
# ---------------------------------------------------------------------------

ENCOUNTER_PROMPTS: dict[str, str] = {
    "planned": "This is a PLANNED event. Include clear coordination before it (timing/logistics), then natural follow-up after it.",
    "chance_encounter": (
        "This is a CHANCE ENCOUNTER. The participants did NOT plan to meet. "
        "Messages AFTER the event should reflect genuine surprise in the speakers' own voice. "
        "There should be NO coordination beforehand."
    ),
    "near_miss": (
        "This is a NEAR MISS. The participants were at the same place but did NOT "
        "see each other. They only discover this later through other people or social media. "
        "Messages should reflect delayed discovery and mild disbelief, without turning into scripted dialogue."
    ),
}


# ---------------------------------------------------------------------------
# Conversation event model
# ---------------------------------------------------------------------------


class ConversationEvent(BaseModel):
    """An event relevant to a specific owner-contact conversation.

    Extracted from the full scenario timeline and filtered to events
    that involve both the device owner and the specific contact being
    generated (primary), or events whose ``device_impacts`` text
    references this contact even though they are not a direct participant
    (secondary / ripple effects).

    Attributes:
        date: ISO date of the event.
        time: Optional time string.
        description: Full event description.
        encounter_type: "planned", "chance_encounter", or "near_miss".
        device_impact: The per-device impact text for this device.
        owner_name: The device owner's name (for prompt clarity).
        contact_name: The contact's name.
        is_secondary: True when this contact was not a direct participant
            but is affected by the event's ripple (via ``device_impacts``).

    """

    date: str
    time: str | None = None
    description: str = ""
    encounter_type: str = "planned"
    device_impact: str = ""
    owner_name: str = ""
    contact_name: str = ""
    is_secondary: bool = False


# ---------------------------------------------------------------------------
# Multilingual encounter term dictionaries
# ---------------------------------------------------------------------------

ENCOUNTER_TERMS: dict[str, dict[str, tuple[str, ...]]] = {
    "en": {
        "planning": ("meet", "see you", "lets meet", "let's meet", "be there", "on my way", "omw", "time works"),
        "direct_encounter": ("saw you", "ran into you", "bumped into", "good seeing you", "met you there", "saw u"),
        "discovery": ("were you there", "you were there", "no way", "how did we miss", "missed each other", "same place"),
    },
    "ar": {
        "planning": (
            "نتقابل",
            "نلتقي",
            "شوفك",
            "بشوفك",
            "نشوفك",
            "في الطريق",
            "جاي",
            "الموعد",
            "الوقت مناسب",
            "نتلاقى",
            "بنتقابل",
            "هنتقابل",
        ),
        "direct_encounter": (
            "شفتك",
            "قابلتك",
            "صادفتك",
            "التقيت بك",
            "التقيتك",
            "شفناك",
            "لقيتك",
            "صدفة شفتك",
        ),
        "discovery": (
            "كنت هناك",
            "كنتي هناك",
            "ما صدقت",
            "مش معقول",
            "كيف فاتنا",
            "فاتونا",
            "نفس المكان",
            "كنت في نفس",
            "كنتي في نفس",
        ),
    },
    "fr": {
        "planning": ("on se voit", "rendez-vous", "retrouver", "j'arrive", "en route", "à quelle heure"),
        "direct_encounter": ("je t'ai vu", "croisé", "rencontré", "tombé sur", "vu là-bas"),
        "discovery": ("tu étais là", "t'étais là", "pas possible", "on s'est raté", "même endroit"),
    },
}


# ---------------------------------------------------------------------------
# Keyword helpers
# ---------------------------------------------------------------------------


def get_encounter_terms(language: str) -> dict[str, tuple[str, ...]]:
    """Return encounter keyword sets for the given language, with English as fallback.

    Merges the target language terms with English so bilingual or
    code-switched messages are still caught.

    Args:
        language: ISO 639-1 language code (e.g. 'en', 'ar', 'fr').

    Returns:
        Dict with keys 'planning', 'direct_encounter', 'discovery',
        each mapping to a tuple of keyword phrases.

    """
    base = ENCOUNTER_TERMS.get("en", {})
    lang_terms = ENCOUNTER_TERMS.get(language, {})
    if language == "en" or not lang_terms:
        return base
    merged: dict[str, tuple[str, ...]] = {}
    for category in ("planning", "direct_encounter", "discovery"):
        merged[category] = base.get(category, ()) + lang_terms.get(category, ())
    return merged


def _contact_name_in_impact(contact_name: str, impact_text: str) -> bool:
    """Check whether a contact's name appears in a device-impact description.

    Uses strict matching to avoid false positives from common first names.
    Prefers full-name matches.  For two-part names, also allows
    "first ... last" in the same phrase window (up to 3 intervening words).

    Args:
        contact_name: Full display name of the contact (e.g. "Hassan Abdellaoui").
        impact_text: The ``device_impacts`` description string to search in.

    Returns:
        True if the contact is referenced in the impact text.

    """
    if not contact_name or not impact_text:
        return False

    normalized_name = " ".join(contact_name.split())
    # Full name match (word boundary around the whole name)
    if re.search(r"\b" + re.escape(normalized_name) + r"\b", impact_text, re.IGNORECASE):
        return True

    parts = normalized_name.split()
    # Fallback: for names with 2+ tokens, allow first+last in proximity.
    if len(parts) >= _MIN_NAME_PARTS:
        first = re.escape(parts[0])
        last = re.escape(parts[-1])
        proximity = rf"\b{first}\b(?:\W+\w+){{0,3}}\W+\b{last}\b"
        return re.search(proximity, impact_text, re.IGNORECASE) is not None
    return False


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _safe_message_date(msg: Message) -> date | None:
    """Parse message date from ISO transfer time.

    Extracts the date component from a Message's ``TransferTime`` field by
    slicing the first 10 characters and parsing as an ISO date.

    Args:
        msg: A Message object whose ``TransferTime`` may contain an ISO timestamp.

    Returns:
        The parsed date, or None if the transfer time is missing or malformed.

    """
    if not msg.TransferTime:
        return None
    raw = msg.TransferTime[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _safe_date_from_iso(iso_str: str | None) -> date | None:
    """Parse a date from a plain ISO timestamp string.

    A lightweight alternative to ``_safe_message_date`` that works on raw
    ISO strings rather than requiring a full ``Message`` object.  Useful
    when only a transfer-time string is available (e.g. from a
    ``SkeletonMessage``).

    Args:
        iso_str: An ISO 8601 timestamp string, or None.

    Returns:
        The parsed date, or None if the input is empty or malformed.

    """
    if not iso_str:
        return None
    try:
        return date.fromisoformat(iso_str[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Event extraction and splitting
# ---------------------------------------------------------------------------


def extract_conversation_events(
    device: DeviceScenario,
    contact_actor_id: str,
    contact_name: str,
    timeline_events: list[FlexTimelineEvent],
) -> list[ConversationEvent]:
    """Find all timeline events relevant to a specific owner-contact pair.

    An event is relevant if:

    1. **Primary**: both the device owner AND the contact are listed as
       participants in the event.
    2. **Secondary (ripple)**: the event has a ``device_impacts`` entry for
       this device whose text mentions the contact by name, OR the
       ``involved_contacts`` mapping explicitly lists this contact for the
       device.  Secondary events represent cross-thread ripple effects
       (e.g. "Khaled texts Nour about Leila's visit").

    Returns them sorted chronologically with primary events first for
    each date.

    Args:
        device: The device scenario (provides owner info and device ID).
        contact_actor_id: The contact's actor ID.
        contact_name: Display name of the contact.
        timeline_events: Full list of scenario timeline events.

    Returns:
        Sorted list of ConversationEvent objects for this pair.

    """
    contact_slot: ContactSlot | None = None
    for c in device.contacts:
        if c.actor_id == contact_actor_id:
            contact_slot = c
            break

    if not contact_slot:
        return []

    events: list[ConversationEvent] = []
    primary_event_ids: set[str] = set()

    # --- Pass 1: primary events (owner + contact both in participants) ---
    for ev in timeline_events:
        if not ev.date or not ev.participants:
            continue

        owner_in = False
        contact_in = False
        for p in ev.participants:
            if p.device_id == device.id:
                pid = p.contact_id
                if pid == "__owner__":
                    owner_in = True
                elif pid == contact_slot.id:
                    contact_in = True

        if owner_in and contact_in:
            primary_event_ids.add(ev.id)
            events.append(
                ConversationEvent(
                    date=ev.date,
                    time=ev.time,
                    description=ev.description,
                    encounter_type=ev.encounter_type or "planned",
                    device_impact=ev.device_impacts.get(device.id, ""),
                    owner_name=device.owner_name,
                    contact_name=contact_name,
                )
            )

    # --- Pass 2: secondary / ripple events via device_impacts ---
    # An event that was NOT matched as primary for this contact may still
    # describe a ripple effect on this device that targets this contact.
    # We detect this via:
    #   a) explicit ``involved_contacts`` mapping, or
    #   b) the contact's display name appearing in the device_impact text.
    for ev in timeline_events:
        if not ev.date:
            continue
        if ev.id in primary_event_ids:
            continue

        impact_text = ev.device_impacts.get(device.id, "")
        if not impact_text:
            continue

        # Explicit targeting via involved_contacts
        explicitly_listed = contact_slot.id in ev.involved_contacts.get(device.id, [])

        if explicitly_listed or _contact_name_in_impact(contact_name, impact_text):
            events.append(
                ConversationEvent(
                    date=ev.date,
                    time=ev.time,
                    description=ev.description,
                    encounter_type=ev.encounter_type or "planned",
                    device_impact=impact_text,
                    owner_name=device.owner_name,
                    contact_name=contact_name,
                    is_secondary=True,
                )
            )

    # Primary events sort before secondary on the same date
    events.sort(key=lambda e: (e.date, e.is_secondary))
    return events


def events_for_batch(
    batch_start: str,
    batch_end: str,
    conversation_events: list[ConversationEvent],
    already_injected: set[str],
) -> tuple[list[ConversationEvent], list[ConversationEvent], list[ConversationEvent]]:
    """Split events into buildup, active, and aftermath relative to a batch.

    - **Buildup**: events happening within 7 days AFTER the batch end
      (the LLM should start hinting/planning).
    - **Active**: events whose date falls within the batch's date range
      (the LLM should reference the event directly).
    - **Aftermath**: events that happened within 5 days BEFORE batch start
      and haven't been injected yet (the LLM should show reactions).

    Args:
        batch_start: ISO date string for the first message in the batch.
        batch_end: ISO date string for the last message in the batch.
        conversation_events: All events for this conversation.
        already_injected: Set of event dates already injected in prior batches.

    Returns:
        Tuple of (buildup, active, aftermath) event lists.

    """
    b_start = date.fromisoformat(batch_start)
    b_end = date.fromisoformat(batch_end)

    buildup: list[ConversationEvent] = []
    active: list[ConversationEvent] = []
    aftermath: list[ConversationEvent] = []

    for ev in conversation_events:
        ev_date = date.fromisoformat(ev.date)
        days_until = (ev_date - b_end).days
        days_since = (b_start - ev_date).days

        if b_start <= ev_date <= b_end:
            active.append(ev)
        elif 0 < days_until <= _EVENT_BUILDUP_DAYS:
            buildup.append(ev)
        elif 0 < days_since <= _EVENT_AFTERMATH_DAYS and ev.date not in already_injected:
            aftermath.append(ev)

    return buildup, active, aftermath


# ---------------------------------------------------------------------------
# Event window helper
# ---------------------------------------------------------------------------


def event_window_text(messages: list[Message], event_date: str) -> tuple[str, str]:
    """Return normalized text around an event date (before and after windows).

    Gathers message content from a 3-day window before the event and a
    5-day window starting on the event date.  All text is lowercased for
    case-insensitive keyword matching.

    Args:
        messages: The conversation messages to scan.
        event_date: ISO date string of the event to window around.

    Returns:
        A tuple of (before_text, after_text) where each is a single
        space-joined lowercased string of message content.

    """
    try:
        ev_date = date.fromisoformat(event_date)
    except ValueError:
        return "", ""

    before_msgs: list[str] = []
    after_msgs: list[str] = []
    for msg in messages:
        msg_date = _safe_message_date(msg)
        if msg_date is None or not msg.Content:
            continue
        if ev_date - timedelta(days=3) <= msg_date < ev_date:
            before_msgs.append(msg.Content.lower())
        elif ev_date <= msg_date <= ev_date + timedelta(days=5):
            after_msgs.append(msg.Content.lower())
    return " ".join(before_msgs), " ".join(after_msgs)


# ---------------------------------------------------------------------------
# Skeleton augmentation for events
# ---------------------------------------------------------------------------


def augment_skeleton_for_events(
    skeleton: list[SkeletonMessage],
    events: list[ConversationEvent],
    owner_actor_id: str,
    contact_actor_id: str,
) -> list[SkeletonMessage]:
    """Add minimal event-adjacent scaffolding so threads feel ongoing.

    Ensures event-linked conversations have enough messages around key dates
    to avoid unrealistic one-off mentions (e.g., one cryptic ping and silence).
    For primary planned events, a pre-event coordination slot is injected if
    none already exists in the 1-3 day window before the event.

    Args:
        skeleton: Existing chronological message skeleton.
        events: Event list relevant to this owner-contact thread.
        owner_actor_id: Owner actor ID.
        contact_actor_id: Contact actor ID.

    Returns:
        Updated skeleton with additional event-adjacent messages when needed.

    """
    if not skeleton or not events:
        return skeleton

    augmented = list(skeleton)

    def msgs_near(ev_day: date) -> int:
        """Count skeleton messages within +/- 1 day of the event.

        Returns:
            Number of messages in the augmented skeleton that fall within
            one calendar day of *ev_day*.

        """
        count = 0
        for msg in augmented:
            msg_day = _safe_date_from_iso(msg.transfer_time)
            if msg_day and abs((msg_day - ev_day).days) <= 1:
                count += 1
        return count

    def has_preplanned_window_msg(ev_day: date) -> bool:
        """Check whether a pre-event coordination slot already exists.

        Returns:
            True if at least one skeleton message falls in the 1-3 day
            window before *ev_day*.

        """
        for msg in augmented:
            msg_day = _safe_date_from_iso(msg.transfer_time)
            if msg_day is None:
                continue
            delta = (msg_day - ev_day).days
            if _PRE_EVENT_DELTA_MIN <= delta <= -1:
                return True
        return False

    for ev_idx, ev in enumerate(events):
        try:
            ev_day = date.fromisoformat(ev.date)
        except ValueError:
            continue

        # Force at least one pre-event message window for planned primary events.
        if ev.encounter_type == "planned" and not ev.is_secondary and not has_preplanned_window_msg(ev_day):
            pre_dt = datetime(
                ev_day.year,
                ev_day.month,
                ev_day.day,
                13,
                random.randint(0, 59),  # noqa: S311
                random.randint(0, 59),  # noqa: S311
                tzinfo=DEFAULT_TIMEZONE,
            ) - timedelta(days=1)
            pre_outgoing = ev_idx % 2 == 0
            augmented.append(
                SkeletonMessage(
                    sender_actor_id=owner_actor_id if pre_outgoing else contact_actor_id,
                    transfer_time=pre_dt.isoformat(),
                    direction="outgoing" if pre_outgoing else "incoming",
                )
            )

        # Primary events should have richer conversational footprint.
        target_near_count = 4 if not ev.is_secondary else 2
        current_near_count = msgs_near(ev_day)
        needed = max(0, target_near_count - current_near_count)
        if needed == 0:
            continue

        # Distribute add-ons as: slight lead-in, event-day, follow-up.
        anchor_offsets = (-1, 0, 1, 2)
        hour_slots = (11, 14, 18, 21)
        for i in range(needed):
            offset = anchor_offsets[min(i, len(anchor_offsets) - 1)]
            hour = hour_slots[(ev_idx + i) % len(hour_slots)]
            dt = datetime(
                ev_day.year,
                ev_day.month,
                ev_day.day,
                hour,
                random.randint(0, 59),  # noqa: S311
                random.randint(0, 59),  # noqa: S311
                tzinfo=DEFAULT_TIMEZONE,
            ) + timedelta(days=offset)
            is_outgoing = (ev_idx + i) % 2 == 0
            augmented.append(
                SkeletonMessage(
                    sender_actor_id=owner_actor_id if is_outgoing else contact_actor_id,
                    transfer_time=dt.isoformat(),
                    direction="outgoing" if is_outgoing else "incoming",
                )
            )

    augmented.sort(key=lambda m: m.transfer_time)
    return augmented


# ---------------------------------------------------------------------------
# LLM directive formatting
# ---------------------------------------------------------------------------


def format_event_directives(
    buildup: list[ConversationEvent],
    active: list[ConversationEvent],
    aftermath: list[ConversationEvent],
) -> str:
    """Format event lists into an LLM prompt directive block.

    Produces clear instructions telling the LLM exactly what events to
    reference in this batch and how to frame them.  Primary events (where
    both participants are directly involved) get full encounter-type
    prompting.  Secondary / ripple events (where this contact is NOT a
    direct participant but is affected by the event) get a softer framing
    that focuses on the ``device_impact`` description.

    Args:
        buildup: Events approaching soon (hint at them).
        active: Events happening during this batch (reference directly).
        aftermath: Events that just happened (show reactions).

    Returns:
        Formatted string block, or empty string if no events.

    """
    if not buildup and not active and not aftermath:
        return ""

    parts = [
        "\n=== SCENARIO EVENTS (MUST be reflected in messages) ===",
        "Thread mapping rule: This thread is only between this device owner and this contact.",
        "If an event is SECONDARY/RIPPLE, treat it as hearsay or downstream impact, not first-hand attendance.",
    ]

    # ---- active events ----
    for ev in active:
        if ev.is_secondary:
            parts.append(
                f"\nSECONDARY EVENT IMPACT on {ev.date}:\n"
                f"  Background (the contact in this thread was NOT at this event): {ev.description}\n"
                f"  How this event ripples into THIS conversation: {ev.device_impact}\n"
                f"  REQUIREMENT: 1-2 messages on or near {ev.date} should naturally reflect "
                f"this ripple effect.  Use the impact description above to shape what the "
                f"characters say — they may share news, react to what they heard, or "
                f"reference the event indirectly."
            )
        else:
            encounter_hint = ENCOUNTER_PROMPTS.get(ev.encounter_type, ENCOUNTER_PROMPTS["planned"])
            parts.append(
                f"\nACTIVE EVENT on {ev.date}:\n"
                f"  What happened: {ev.description}\n"
                f"  {encounter_hint}\n"
                f"  How it affects this device's texts: {ev.device_impact}\n"
                f"  REQUIREMENT: Messages on or near {ev.date} MUST reference this event. "
                f"Use specific details — at least one concrete location/business name when relevant, people's reactions, and what was said. "
                f"At least 2-3 messages should directly relate to this event in this owner↔contact thread."
            )

    # ---- buildup (upcoming) events ----
    for ev in buildup:
        if ev.is_secondary:
            # Secondary buildup only makes sense when the impact hints at
            # anticipation — include with lighter framing.
            parts.append(
                f"\nUPCOMING RIPPLE on {ev.date} (something is coming that will affect this thread):\n"
                f"  What's happening elsewhere: {ev.description}\n"
                f"  Expected ripple into this conversation: {ev.device_impact}\n"
                f"  Optionally hint at or foreshadow this — 1 message is enough."
            )
        elif ev.encounter_type == "planned":
            parts.append(
                f"\nUPCOMING EVENT on {ev.date} (buildup — it's coming soon):\n"
                f"  What's planned: {ev.description}\n"
                f"  Start hinting at this: making plans, confirming times, expressing "
                f"anticipation or anxiety. 1-2 messages should reference upcoming plans."
            )

    # ---- aftermath events ----
    for ev in aftermath:
        if ev.is_secondary:
            parts.append(
                f"\nRECENT EVENT RIPPLE from {ev.date} (aftermath — this contact heard about it):\n"
                f"  What happened elsewhere: {ev.description}\n"
                f"  Ripple in this conversation: {ev.device_impact}\n"
                f"  Characters should reference this: sharing the news, reacting, "
                f"asking follow-up questions. 1-2 messages should reflect the ripple."
            )
        else:
            parts.append(
                f"\nRECENT EVENT from {ev.date} (aftermath — just happened):\n"
                f"  What happened: {ev.description}\n"
                f"  Characters should be discussing this: reactions, follow-ups, 'that was fun', "
                f"'still can't believe...'. 1-2 messages should reference this."
            )

    parts.append("=== END SCENARIO EVENTS ===")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Forced coordination injection
# ---------------------------------------------------------------------------


def force_planned_event_coordination(
    messages: list[Message],
    events: list[ConversationEvent],
    owner_actor_id: str,
    language: str = "en",
) -> tuple[list[Message], int]:
    """Inject minimal pre-event coordination when planned events are missing it.

    This is a deterministic fallback used during generation/repair so a
    planned event always has at least one scheduling signal before it.
    For each primary planned event that lacks planning keywords in the
    3-day pre-event window, a short coordination message ("let's meet
    tomorrow?") is inserted one day before the event.

    Args:
        messages: Generated messages for one direct thread.
        events: Relevant events for this thread.
        owner_actor_id: Actor ID used for injected coordination messages.
        language: ISO language code for planning term selection.

    Returns:
        Tuple of (possibly updated messages, count of injected messages).

    """
    if not messages or not events:
        return messages, 0

    terms = get_encounter_terms(language)
    planning_terms = terms.get("planning", ())
    planning_seed = planning_terms[0] if planning_terms else "let's meet"
    injected = 0
    updated = list(messages)

    for ev in events:
        if ev.is_secondary or ev.encounter_type != "planned":
            continue
        before_text, _ = event_window_text(updated, ev.date)
        if planning_terms and any(term in before_text for term in planning_terms):
            continue
        try:
            ev_day = date.fromisoformat(ev.date)
        except ValueError:
            continue

        # Place the message one day before the event so validators see
        # explicit causal planning for planned encounters.
        dt = datetime(
            ev_day.year, ev_day.month, ev_day.day, 13, 20, random.randint(0, 59), tzinfo=DEFAULT_TIMEZONE  # noqa: S311
        ) - timedelta(days=1)
        updated.append(
            Message(
                SenderActorId=owner_actor_id,
                Content=f"{planning_seed} tomorrow?",
                TransferTime=dt.isoformat(),
                Direction="outgoing",
                ServiceName="SMS",
            )
        )
        injected += 1

    if injected:
        updated.sort(key=lambda m: m.TransferTime or "")
    return updated, injected
