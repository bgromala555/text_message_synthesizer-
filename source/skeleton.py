"""Message skeleton generation for the Synthesized Chat Generator.

Builds chronological skeletons of timestamped message stubs (sender, direction,
timestamp) before any LLM content is generated.  Two entry points exist:

* ``generate_skeleton`` — for 1-to-1 conversations between the device owner
  and a single contact.
* ``build_group_skeleton`` — for multi-sender group chats where the owner gets
  roughly 40 % of outgoing messages and the remaining participants share the
  rest.

Both functions respect the ``VOLUME_SCALE`` table, which maps qualitative
volume labels (heavy / regular / light / minimal) to per-day density and
skip-day probability.  These values are calibrated from Pew Research 2024
texting-frequency data so that 6-month scenarios produce realistic message
counts per contact tier.
"""

from __future__ import annotations

import logging
import random
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel

from source.models import GenerationSettings

logger = logging.getLogger(__name__)

# US Eastern Standard Time (UTC-5), used as the default timezone for all
# generated skeleton timestamps.
DEFAULT_TIMEZONE: timezone = timezone(timedelta(hours=-5))

# Probability that the device owner sends a given message in a 1-to-1 chat.
_OWNER_SEND_PROBABILITY: float = 0.55

# Probability that the device owner sends a given message in a group chat.
_GROUP_OWNER_SEND_PROBABILITY: float = 0.4

# Calibrated from real-world texting data (Pew Research 2024):
# Americans average ~41.5 texts/day across ALL contacts; young adults ~110/day.
# With a base of 2-8 msgs/day (avg 5), these produce realistic per-contact totals
# over a 6-month (180-day) scenario:
#   heavy   → ~5 msgs/active day, active 88% → ~792/6mo (partner, BFF)
#   regular → ~3 msgs/active day, active 55% → ~297/6mo (friend, sibling)
#   light   → ~1.5 msgs/active day, active 18% → ~49/6mo (acquaintance, coworker)
#   minimal → ~1 msg/active day, active 4% → ~7/6mo (barber, ex, doctor)
VOLUME_SCALE: dict[str, dict[str, float]] = {
    "heavy": {"density": 0.65, "skip_chance": 0.12},
    "regular": {"density": 0.35, "skip_chance": 0.45},
    "light": {"density": 0.18, "skip_chance": 0.82},
    "minimal": {"density": 0.10, "skip_chance": 0.96},
}


class SkeletonMessage(BaseModel):
    """A message stub with metadata but no content yet.

    Used as the intermediate representation between skeleton generation
    and LLM content filling.

    Attributes:
        sender_actor_id: Actor ID of the sender.
        transfer_time: ISO 8601 timestamp string.
        direction: "incoming" or "outgoing" relative to the device owner.
        service_name: Message service type, defaults to "SMS".

    """

    sender_actor_id: str
    transfer_time: str
    direction: str
    service_name: str = "SMS"


def generate_skeleton(
    owner_actor_id: str,
    contact_actor_id: str,
    settings: GenerationSettings,
    message_volume: str = "regular",
) -> list[SkeletonMessage]:
    """Generate a chronological skeleton of messages for one conversation.

    Distributes messages across the date range with random density per day,
    realistic time-of-day distribution (weighted toward afternoon/evening),
    and alternating directions with slight sender bias.

    Message density is scaled by the ``message_volume`` parameter so that
    close contacts (heavy) produce many more messages than acquaintances
    (minimal).

    Args:
        owner_actor_id (str): The device owner's actor ID.
        contact_actor_id (str): The contact's actor ID.
        settings (GenerationSettings): Generation settings controlling date
            range and density.
        message_volume (str): One of "heavy", "regular", "light", "minimal".
            Controls both per-day message count and skip-day probability.

    Returns:
        Chronologically sorted list of SkeletonMessage stubs.

    """
    vol = VOLUME_SCALE.get(message_volume, VOLUME_SCALE["regular"])
    density = vol["density"]
    skip_chance = vol["skip_chance"]

    scaled_min = max(1, round(settings.messages_per_day_min * density))
    scaled_max = max(scaled_min, round(settings.messages_per_day_max * density))

    start = date.fromisoformat(settings.date_start)
    end = date.fromisoformat(settings.date_end)
    total_days = (end - start).days + 1

    messages: list[SkeletonMessage] = []
    current = start

    for _ in range(total_days):
        if random.random() < skip_chance:  # noqa: S311
            current += timedelta(days=1)
            continue

        count = random.randint(scaled_min, scaled_max)  # noqa: S311
        hours: list[float] = sorted(random.gauss(15.0, 4.0) for _ in range(count))

        for h in hours:
            clamped_h = max(7.0, min(23.5, h))
            hour_int = int(clamped_h)
            minute = int((clamped_h - hour_int) * 60)
            second = random.randint(0, 59)  # noqa: S311

            dt = datetime(
                current.year,
                current.month,
                current.day,
                hour_int,
                minute,
                second,
                tzinfo=DEFAULT_TIMEZONE,
            )

            is_outgoing = random.random() < _OWNER_SEND_PROBABILITY  # noqa: S311
            direction = "outgoing" if is_outgoing else "incoming"
            sender = owner_actor_id if is_outgoing else contact_actor_id

            messages.append(
                SkeletonMessage(
                    sender_actor_id=sender,
                    transfer_time=dt.isoformat(),
                    direction=direction,
                )
            )

        current += timedelta(days=1)

    messages.sort(key=lambda m: m.transfer_time)
    logger.info(
        "Skeleton: %s <-> %s volume=%s → %d messages (density=%.0f%%, skip=%.0f%%)",
        owner_actor_id,
        contact_actor_id,
        message_volume,
        len(messages),
        density * 100,
        skip_chance * 100,
    )
    return messages


def build_group_skeleton(
    owner_actor_id: str,
    member_actor_ids: list[str],
    settings: GenerationSettings,
    start_date: str,
    end_date: str,
    message_volume: str = "regular",
) -> list[SkeletonMessage]:
    """Build a message skeleton for a group conversation.

    Similar to ``generate_skeleton`` but distributes messages across
    multiple senders.  The owner gets ~40 % of outgoing messages while
    other members share the rest.

    Args:
        owner_actor_id (str): The device owner's actor ID (phone number).
        member_actor_ids (list[str]): Actor IDs of all other group members.
        settings (GenerationSettings): Generation settings for date range
            and density.
        start_date (str): ISO date when the group chat starts.
        end_date (str): ISO date when the group chat ends (or settings end).
        message_volume (str): Density level for the group.

    Returns:
        Chronologically sorted list of SkeletonMessage objects.

    """
    volume_cfg = VOLUME_SCALE.get(message_volume, VOLUME_SCALE["regular"])
    density = volume_cfg["density"]
    skip = volume_cfg["skip_chance"]

    msg_min = max(1, int(settings.messages_per_day_min * density))
    msg_max = max(msg_min, int(settings.messages_per_day_max * density))

    effective_start = date.fromisoformat(start_date) if start_date else date.fromisoformat(settings.date_start)
    effective_end = date.fromisoformat(end_date) if end_date else date.fromisoformat(settings.date_end)

    skeleton: list[SkeletonMessage] = []
    current = effective_start

    while current <= effective_end:
        if random.random() < skip:  # noqa: S311
            current += timedelta(days=1)
            continue

        count = random.randint(msg_min, msg_max)  # noqa: S311
        for _ in range(count):
            hour_mean = 15.0
            hour = max(7.0, min(23.5, random.gauss(hour_mean, 4.0)))
            hour_int = int(hour)
            minute = int((hour - hour_int) * 60)
            second = random.randint(0, 59)  # noqa: S311
            dt = datetime(current.year, current.month, current.day, hour_int, minute, second, tzinfo=DEFAULT_TIMEZONE)

            # Owner sends ~40%, others share remaining
            if random.random() < _GROUP_OWNER_SEND_PROBABILITY:  # noqa: S311
                sender = owner_actor_id
                direction = "outgoing"
            else:
                sender = random.choice(member_actor_ids) if member_actor_ids else owner_actor_id  # noqa: S311
                direction = "incoming"

            skeleton.append(
                SkeletonMessage(
                    sender_actor_id=sender,
                    transfer_time=dt.isoformat(),
                    direction=direction,
                )
            )
        current += timedelta(days=1)

    skeleton.sort(key=lambda m: m.transfer_time)
    logger.info(
        "Group skeleton: %s + %d members volume=%s → %d messages",
        owner_actor_id,
        len(member_actor_ids),
        message_volume,
        len(skeleton),
    )
    return skeleton
