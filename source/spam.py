"""Template-based spam and noise message generation.

Generates marketing texts, wrong-number exchanges, and one-time service
interactions without any LLM calls.  All content is drawn from curated
template lists and randomized to create realistic junk-mail noise on
each device.

Extracted from ``generator.py`` for single-responsibility and testability.
"""

import logging
import random
from datetime import date, datetime, timedelta, timezone

from messageviewer.models import Actor, ConversationNode, Message
from source.models import DeviceScenario, GenerationSettings

logger = logging.getLogger(__name__)

# Timezone used for generated timestamps (US Eastern)
_EST = timezone(timedelta(hours=-5))

# ---------------------------------------------------------------------------
# Spam density configuration
# ---------------------------------------------------------------------------

SPAM_DENSITY_RANGE: dict[str, tuple[int, int]] = {
    "low": (5, 15),
    "medium": (20, 40),
    "high": (50, 100),
}

# ---------------------------------------------------------------------------
# Interest → spam category mapping
# ---------------------------------------------------------------------------

INTEREST_SPAM_MAP: dict[str, str] = {
    "gaming": "tech",
    "tech": "tech",
    "coding": "tech",
    "programming": "tech",
    "computers": "tech",
    "fashion": "beauty",
    "beauty": "beauty",
    "makeup": "beauty",
    "skincare": "beauty",
    "cooking": "food",
    "food": "food",
    "baking": "food",
    "restaurant": "food",
    "investing": "finance",
    "finance": "finance",
    "crypto": "finance",
    "stocks": "finance",
    "business": "finance",
    "fitness": "health",
    "gym": "health",
    "yoga": "health",
    "running": "health",
    "travel": "travel",
    "hiking": "travel",
}

# ---------------------------------------------------------------------------
# Spam message templates
# ---------------------------------------------------------------------------

SPAM_TEMPLATES: dict[str, list[str]] = {
    "tech": [
        "FLASH SALE: 40% off all gaming peripherals! Use code GAMER40. Shop now at TechDealsHub.com",
        "Your GeForce NOW subscription renews in 3 days. Update payment at nvidia.com/account",
        "New PS5 Pro bundle available! Limited stock. Order at GameStop.com/ps5pro",
        "Windows Security Alert: Your license expires soon. Renew at microsoft-support-renew.com",
        "Amazon: Your order #112-4839201 has shipped! Track at amzn.to/3kX9wP",
        'Best Buy: Exclusive member deal - Samsung 65" 4K TV $499. Today only!',
        "Steam: Your wishlist item 'Elden Ring DLC' is now 25% off!",
    ],
    "beauty": [
        "Sephora: Your Beauty Insider points are expiring! Redeem 500pts now",
        "ULTA: Buy 2 get 1 FREE on all NYX products this weekend only!",
        "Glossier just dropped a new serum. Shop the launch at glossier.com/new",
        "Fenty Beauty: 30% off site-wide. Code FENTYFALL. Don't miss out!",
        "Your Ipsy Glam Bag has shipped! Track your package at ipsy.com/tracking",
        "SkinCeuticals: Free vitamin C sample with any purchase over $50",
    ],
    "food": [
        "DoorDash: $5 off your next order! Use code DASH5. Expires midnight",
        "Uber Eats: Free delivery on your next 3 orders. No minimum!",
        "Sweetgreen: New fall menu just dropped! Try the Harvest Bowl",
        "Grubhub: You haven't ordered in a while. Here's 20% off: COMEBACK20",
        "Your Instacart order is being shopped! ETA 45 mins",
        "Chipotle: BOGO free entree today only. Show this text in-store",
    ],
    "finance": [
        "Robinhood: AAPL is up 3.2% today. Check your portfolio",
        "ALERT: Unusual activity on your Chase card ending 4821. Call 1-800-935-9935",
        "Venmo: You have $47.50 pending. Accept payment from @marcus_j",
        "Coinbase: BTC is up 8% this week. Your portfolio: +$234.12",
        "Your Capital One payment of $127.43 is due in 3 days",
        "SCAM WARNING: IRS does not contact via text. Report to phishing@irs.gov",
        "Congratulations! You've been pre-approved for a $50,000 credit line. Apply now at totally-legit-bank.com",
    ],
    "health": [
        "Peloton: New HIIT class dropped! Join live at 7pm ET",
        "MyFitnessPal: You're on a 15-day streak! Keep logging to hit your goal",
        "CVS Pharmacy: Your prescription is ready for pickup at 345 Main St",
        "Headspace: New sleep meditation available. Wind down tonight",
        "Your health insurance claim #HX-39201 has been processed. See details at portal.aetna.com",
    ],
    "travel": [
        "Delta: Flight DL1847 JFK to LAX gate changed to B32. Boards 4:15pm",
        "Airbnb: Your host confirmed your stay Jan 15-18 in Miami Beach!",
        "TSA PreCheck: Your enrollment expires in 30 days. Renew at tsa.gov",
        "Expedia: Prices dropped on your watched hotel in Barcelona! Now $89/night",
        "Uber: Your ride is arriving in 3 minutes. White Toyota Camry, plate HK-4921",
    ],
    "general": [
        "USPS: Your package is out for delivery. Track: 9400111899223847650321",
        "FedEx: Delivery attempted. Package held at facility. Reschedule at fedex.com",
        "Verification code: 847291. Do not share this code with anyone.",
        "Your verification code is 392047. Expires in 10 minutes.",
        "T-Mobile: Your bill of $85.00 is due on the 15th. Pay at t-mobile.com/pay",
        "AT&T: You've used 80% of your data plan this cycle",
        "VOTE YES on Prop 47 for affordable housing! Paid for by Citizens for Change",
        "Congratulations! You've won a $1000 Walmart gift card! Claim at bit.ly/w4lm4rt",
        "Hi this is the Amazon delivery driver, where should I leave ur package?",
        "Appointment reminder: Dr. Patel on Thursday 2/13 at 10:30am. Reply C to confirm",
        "Your Lyft ride is 2 min away. Look for the black Honda Accord",
        "Netflix: Your payment method was declined. Update at netflix.com/account",
        "Spotify: Your Wrapped 2025 is here! See your top songs",
    ],
}

# ---------------------------------------------------------------------------
# Wrong-number and service exchange templates
# ---------------------------------------------------------------------------

WRONG_NUMBER_EXCHANGES: list[list[dict[str, str]]] = [
    [
        {"dir": "incoming", "text": "Hey is this Mike??"},
        {"dir": "outgoing", "text": "Wrong number"},
        {"dir": "incoming", "text": "Oh sorry about that!"},
    ],
    [
        {"dir": "incoming", "text": "Yo you still coming tonight?"},
        {"dir": "outgoing", "text": "I think you have the wrong number"},
        {"dir": "incoming", "text": "My bad lol"},
    ],
    [
        {"dir": "incoming", "text": "Can you pick up milk on the way home"},
        {"dir": "outgoing", "text": "Wrong number sorry"},
    ],
    [
        {"dir": "incoming", "text": "The meeting got moved to 3pm"},
        {"dir": "outgoing", "text": "Who is this?"},
        {"dir": "incoming", "text": "Wait is this not Jessica?"},
        {"dir": "outgoing", "text": "Nope wrong number"},
        {"dir": "incoming", "text": "Sorry!"},
    ],
    [
        {"dir": "incoming", "text": "Happy birthday!!"},
        {"dir": "outgoing", "text": "Thanks but I think u have the wrong #"},
        {"dir": "incoming", "text": "Omg so sorry haha"},
    ],
]

SERVICE_EXCHANGES: list[list[dict[str, str]]] = [
    [
        {"dir": "incoming", "text": "Hi this is your DoorDash driver. Im outside"},
        {"dir": "outgoing", "text": "Ok coming down"},
    ],
    [
        {"dir": "incoming", "text": "Uber here. I'm at the corner of 5th and Main"},
        {"dir": "outgoing", "text": "Be right there"},
    ],
    [
        {"dir": "incoming", "text": "Delivery for apt 4B. Nobody answering the buzzer"},
        {"dir": "outgoing", "text": "Sorry buzzer is broken just come up"},
        {"dir": "incoming", "text": "Ok heading up"},
    ],
    [
        {"dir": "incoming", "text": "Hey I found your wallet at the coffee shop on Houston. Is this your number?"},
        {"dir": "outgoing", "text": "Oh my god yes!! Thank you so much"},
        {"dir": "incoming", "text": "No problem. I left it with the barista"},
    ],
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _random_phone_short_code() -> str:
    """Generate a random 5-6 digit short code for marketing spam.

    Returns:
        A string of 5-6 digits.

    """
    return str(random.randint(10000, 999999))  # noqa: S311


def _random_phone_full() -> str:
    """Generate a random full US phone number for wrong-number / service texts.

    Returns:
        A US-formatted phone number string like ``+12125551234``.

    """
    area = random.choice([212, 718, 347, 646, 917, 929, 551, 201, 973, 862])  # noqa: S311
    return f"+1{area}{random.randint(1000000, 9999999)}"  # noqa: S311


def _detect_spam_categories(device: DeviceScenario) -> list[str]:
    """Analyze the device owner's personality to pick relevant spam categories.

    Scans hobbies, interests, job details, and food preferences from the
    owner's personality profile.  Returns a list of spam category keys
    that match, plus ``"general"`` which is always included.

    Args:
        device: The device scenario with owner personality data.

    Returns:
        List of spam category strings (e.g., ``["tech", "food", "general"]``).

    """
    categories: set[str] = {"general"}
    profile = device.owner_personality
    if not profile:
        return list(categories)

    searchable = " ".join(
        [
            " ".join(profile.hobbies_and_interests),
            " ".join(profile.favorite_media),
            profile.food_and_drink or "",
            profile.job_details or "",
            profile.personality_summary or "",
        ]
    ).lower()

    for keyword, category in INTEREST_SPAM_MAP.items():
        if keyword in searchable:
            categories.add(category)

    return list(categories)


def _random_timestamp(date_start: str, date_end: str, *, business_hours: bool = False) -> str:
    """Generate a random ISO timestamp within the given date range.

    Args:
        date_start: ISO date string for range start.
        date_end: ISO date string for range end.
        business_hours: If True, bias toward 9am-6pm; otherwise fully random.

    Returns:
        ISO 8601 timestamp string with EST offset.

    """
    start = date.fromisoformat(date_start)
    end = date.fromisoformat(date_end)
    days_span = max(1, (end - start).days)
    rand_day = start + timedelta(days=random.randint(0, days_span))  # noqa: S311
    hour = random.randint(9, 17) if business_hours else random.randint(7, 23)  # noqa: S311
    minute = random.randint(0, 59)  # noqa: S311
    second = random.randint(0, 59)  # noqa: S311
    dt = datetime(rand_day.year, rand_day.month, rand_day.day, hour, minute, second, tzinfo=_EST)
    return dt.isoformat()


def _build_exchange_thread(
    exchange: list[dict[str, str]],
    owner_id: str,
    sender_id: str,
    base_ts: str,
    minute_gap_range: tuple[int, int] = (1, 5),
) -> list[Message]:
    """Build a list of ``Message`` objects from a scripted exchange template.

    Eliminates duplication between wrong-number and service exchange
    generation by providing a single builder for both.

    Args:
        exchange: List of ``{"dir": ..., "text": ...}`` turn dicts.
        owner_id: The device owner's actor ID.
        sender_id: The other party's actor ID.
        base_ts: ISO timestamp for the first message.
        minute_gap_range: Min/max minutes between turns.

    Returns:
        List of Message objects with sequential timestamps.

    """
    base_dt = datetime.fromisoformat(base_ts)
    msgs: list[Message] = []
    for i, turn in enumerate(exchange):
        turn_ts = (base_dt + timedelta(minutes=i * random.randint(*minute_gap_range))).isoformat()  # noqa: S311
        sender = sender_id if turn["dir"] == "incoming" else owner_id
        msgs.append(
            Message(
                SenderActorId=sender,
                Content=turn["text"],
                TransferTime=turn_ts,
                Direction=turn["dir"],
                ServiceName="SMS",
            )
        )
    return msgs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_spam_messages(
    device: DeviceScenario,
    settings: GenerationSettings,
) -> tuple[list[ConversationNode], list[Actor]]:
    """Generate template-based spam and noise messages for a device.

    Spam density is read from the **device** (``device.spam_density``),
    not from global settings.  Each phone has its own noise level.

    Analyzes the device owner's personality to select relevant spam
    categories, then builds randomized marketing texts, wrong-number
    exchanges, and one-time service interactions.  No LLM calls are
    made — all content is pulled from curated template lists.

    Args:
        device: The device scenario with owner personality, actor ID,
            and per-device ``spam_density`` ("none"/"low"/"medium"/"high").
        settings: Generation settings (date range used for timestamps).

    Returns:
        Tuple of (list of ConversationNode for spam threads,
        list of Actor entries for the spam senders).

    """
    device_spam = getattr(device, "spam_density", "medium") or "medium"
    if device_spam == "none":
        return [], []

    lo, hi = SPAM_DENSITY_RANGE.get(device_spam, SPAM_DENSITY_RANGE["medium"])
    total_threads = random.randint(lo, hi)  # noqa: S311

    categories = _detect_spam_categories(device)
    owner_id = device.owner_actor_id

    nodes: list[ConversationNode] = []
    actors: list[Actor] = []

    marketing_count = int(total_threads * 0.6)
    wrong_number_count = int(total_threads * 0.25)
    service_count = total_threads - marketing_count - wrong_number_count

    for _ in range(marketing_count):
        cat = random.choice(categories)  # noqa: S311
        templates = SPAM_TEMPLATES.get(cat, SPAM_TEMPLATES["general"])
        text = random.choice(templates)  # noqa: S311
        sender_id = _random_phone_short_code()
        ts = _random_timestamp(settings.date_start, settings.date_end, business_hours=True)

        actors.append(Actor(ActorId=sender_id, Name=sender_id))
        nodes.append(
            ConversationNode(
                source=owner_id,
                target=[sender_id],
                type="SMS",
                message_content=[
                    Message(
                        SenderActorId=sender_id,
                        Content=text,
                        TransferTime=ts,
                        Direction="incoming",
                        ServiceName="SMS",
                    )
                ],
            )
        )

    for _ in range(wrong_number_count):
        exchange = random.choice(WRONG_NUMBER_EXCHANGES)  # noqa: S311
        sender_id = _random_phone_full()
        base_ts = _random_timestamp(settings.date_start, settings.date_end)
        msgs = _build_exchange_thread(exchange, owner_id, sender_id, base_ts)
        actors.append(Actor(ActorId=sender_id, Name=sender_id))
        nodes.append(ConversationNode(source=owner_id, target=[sender_id], type="SMS", message_content=msgs))

    for _ in range(service_count):
        exchange = random.choice(SERVICE_EXCHANGES)  # noqa: S311
        sender_id = _random_phone_full()
        base_ts = _random_timestamp(settings.date_start, settings.date_end)
        msgs = _build_exchange_thread(exchange, owner_id, sender_id, base_ts, minute_gap_range=(1, 3))
        actors.append(Actor(ActorId=sender_id, Name=sender_id))
        nodes.append(ConversationNode(source=owner_id, target=[sender_id], type="SMS", message_content=msgs))

    logger.info("Generated %d spam threads for %s (%s)", len(nodes), device.owner_name, ", ".join(categories))
    return nodes, actors
