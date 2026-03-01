"""Pydantic models for parsing and representing SMS conversation data.

These models map directly to the JSON schema used in the year-long SMS dataset,
providing type-safe access to actors, messages, and conversation nodes.
"""

from pydantic import BaseModel


class Actor(BaseModel):
    """Represents a participant in the SMS conversations.

    Each actor has a unique identifier and a display name used
    throughout the conversation threads.

    Attributes:
        ActorId: Unique identifier for this participant (e.g. "PA001", "C01").
        Name: Human-readable display name (e.g. "Alex Rivera").

    """

    ActorId: str
    Name: str


class Message(BaseModel):
    """Represents a single SMS message within a conversation thread.

    Contains all metadata needed to render the message in a phone-style
    UI, including sender identity, content, timestamp, and direction.

    Attributes:
        SenderActorId: The ActorId of the person who sent this message.
        Content: The text body of the SMS message.
        TransferTime: ISO 8601 timestamp of when the message was sent/received.
        Direction: Either "incoming" or "outgoing" relative to the primary actor.
        ServiceName: The messaging service used (e.g. "SMS").

    """

    SenderActorId: str
    Content: str
    TransferTime: str
    Direction: str
    ServiceName: str


class ConversationNode(BaseModel):
    """Represents a conversation thread between the primary actor and one or more contacts.

    Each node contains the full message history for a single conversation,
    along with metadata about the participants and message type.

    Attributes:
        source: The ActorId of the primary participant (the phone owner).
        target: List of ActorIds for the other participants in this conversation.
        type: The communication type (e.g. "SMS").
        message_content: Chronologically ordered list of messages in this thread.

    """

    source: str
    target: list[str]
    type: str
    message_content: list[Message]


class SmsDataset(BaseModel):
    """Root model for the complete SMS dataset JSON file.

    Contains all conversation threads and the actor registry that maps
    ActorIds to human-readable names.

    Attributes:
        nodes: List of conversation threads, each between the primary actor and contacts.
        actors: Registry of all participants with their IDs and display names.

    """

    nodes: list[ConversationNode]
    actors: list[Actor]
