"""LLM client wrapper for the generation pipeline.

Encapsulates all direct interactions with the OpenAI API: client
caching, request dispatch with retry logic, response parsing, and
story-state merging.  Extracted from ``generator.py`` to keep the
pipeline's LLM coupling in one place.

This module is the **single source of truth** for obtaining a cached
OpenAI client.  Both the generation pipeline (``generator.py``) and
AI-assist endpoints (``ai_assist.py``) import ``get_openai_client``
from here instead of maintaining their own caches.

If a second provider (Anthropic, local models) is added later, only
this module needs to change.
"""

from __future__ import annotations

import functools
import json
import logging
import math
import os

import tiktoken
from fastapi import HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field

from source.models import GenerationSettings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants (previously magic numbers)
# ---------------------------------------------------------------------------

MAX_LLM_TOKENS: int = 4096
RATE_LIMIT_SLEEP_SECONDS: int = 30
DEFAULT_MODEL: str = "gpt-4o"
MAX_SMALL_SHORTFALL: int = 2


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class QuotaExhaustedError(Exception):
    """Raised when the OpenAI API key has run out of credits.

    Distinct from transient rate limits — retrying won't help.
    The caller should save partial progress and stop.
    """


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class StoryState(BaseModel):
    """Running memory of conversation progress, matching the rewriter pattern.

    Accumulated across batches so the LLM avoids repetition and maintains
    narrative continuity.

    Attributes:
        topics_covered: Topics already discussed.
        key_events: Important things that happened.
        unresolved_threads: Mentioned but not yet followed up.
        relationship_vibe: Current emotional temperature.
        owner_state: Where the phone owner is emotionally.
        contact_state: Where the contact is emotionally.

    """

    topics_covered: list[str] = Field(default_factory=list)
    key_events: list[str] = Field(default_factory=list)
    unresolved_threads: list[str] = Field(default_factory=list)
    relationship_vibe: str = "(not yet established)"
    owner_state: str = "(not yet established)"
    contact_state: str = "(not yet established)"


class ConversationBatchSchema(BaseModel):
    """Expected schema for conversation batch LLM responses.

    Used for Pydantic validation of parsed LLM JSON output.  When
    validation succeeds, the caller gets type-safe access to messages
    and story state.  When it fails (the LLM returned unexpected
    structure), the caller falls back to manual dict-based parsing.

    Attributes:
        messages: Generated message content strings for the batch.
        story_state: Running narrative memory for conversation continuity.

    """

    messages: list[str] = Field(default_factory=list)
    story_state: StoryState | None = None


class AiAssistJsonSchema(BaseModel):
    """Marker schema for AI-assist endpoint JSON responses.

    Provides a Pydantic-backed structure for validating the top-level
    shape of AI-assist LLM responses before individual field extraction.
    Allows extra fields so endpoint-specific keys pass through validation.

    Attributes:
        result: The primary result payload from the LLM.

    """

    model_config = {"extra": "allow"}

    result: dict[str, object] = Field(default_factory=dict)


class TokenUsage(BaseModel):
    """Token counts for a single LLM API call.

    Captures the prompt (input), completion (output), and combined total
    token counts returned by the provider in the API response.

    Attributes:
        prompt_tokens: Number of tokens in the request (system + user messages).
        completion_tokens: Number of tokens the model generated.
        total_tokens: Sum of prompt and completion tokens.

    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMCallResult(BaseModel):
    """Result from a single LLM API call including content and token usage.

    Wraps the raw response text together with the provider-reported token
    counts so callers can track actual usage instead of relying on estimates.

    Attributes:
        content: Raw JSON string returned by the model.
        usage: Token usage breakdown for this call.
        model: The model identifier that serviced the request.

    """

    content: str = ""
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str = ""


class AccumulatedUsage(BaseModel):
    """Accumulated token usage across multiple LLM calls in a generation run.

    Provides running totals of calls made, tokens consumed, and estimated
    dollar cost.  Use :meth:`add_call` after each ``call_llm`` invocation
    to keep the counters up to date.

    Attributes:
        total_calls: Number of LLM API calls made so far.
        total_prompt_tokens: Sum of prompt tokens across all calls.
        total_completion_tokens: Sum of completion tokens across all calls.
        total_tokens: Sum of total tokens across all calls.
        estimated_cost_usd: Running dollar estimate based on model pricing.

    """

    total_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def add_call(self, usage: TokenUsage, model: str) -> None:
        """Add a single call's token usage to the running totals.

        Increments call count, accumulates token counts, and updates the
        estimated cost using the per-1K-token rates in
        ``_MODEL_COST_PER_1K_TOKENS``.  Unknown models fall back to
        gpt-4o pricing.

        Args:
            usage (TokenUsage): Token counts from the call to accumulate.
            model (str): Model identifier used for the call, looked up
                in the cost table to compute the dollar estimate.

        """
        self.total_calls += 1
        self.total_prompt_tokens += usage.prompt_tokens
        self.total_completion_tokens += usage.completion_tokens
        self.total_tokens += usage.total_tokens

        rates = _MODEL_COST_PER_1K_TOKENS.get(model, _MODEL_COST_PER_1K_TOKENS["gpt-4o"])
        call_cost = (usage.prompt_tokens / 1000) * rates["input"] + (usage.completion_tokens / 1000) * rates["output"]
        self.estimated_cost_usd = round(self.estimated_cost_usd + call_cost, 6)


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

# Approximate cost per 1K tokens for common models (as of 2025)
_MODEL_COST_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
}

# Rough token-per-call assumptions (system prompt + batch skeleton / JSON response)
_EST_INPUT_TOKENS_PER_CALL: int = 2000
_EST_OUTPUT_TOKENS_PER_CALL: int = 1500


class CostEstimate(BaseModel):
    """Estimated cost for a generation run.

    Attributes:
        api_calls: Total number of LLM API calls expected.
        estimated_input_tokens: Approximate total input tokens across all calls.
        estimated_output_tokens: Approximate total output tokens across all calls.
        estimated_cost_usd: Rough dollar cost based on model pricing.

    """

    api_calls: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_cost_usd: float = 0.0


def estimate_generation_cost(
    num_contacts: int,
    avg_messages_per_contact: int,
    batch_size: int,
    model: str = "gpt-4o",
) -> CostEstimate:
    """Estimate the number of API calls and approximate cost for a generation run.

    Provides a rough cost estimate based on the number of contacts, expected
    messages per contact, and batch size.  Each contact requires
    ``ceil(avg_messages / batch_size)`` generation calls.  A small overhead
    (~10 %) is added for AI-assist calls (name generation, personality
    generation, etc.) that happen before the main generation loop.

    Token counts are approximated at ~2 000 input tokens per call (system
    prompt + batch skeleton) and ~1 500 output tokens per call (JSON
    response).  The dollar estimate uses per-1K-token rates for the
    requested model; unknown models fall back to gpt-4o pricing.

    Args:
        num_contacts (int): Total contacts across all devices.
        avg_messages_per_contact (int): Expected average messages per contact thread.
        batch_size (int): Messages per LLM call.
        model (str): The LLM model name for cost lookup.

    Returns:
        CostEstimate with api_calls, estimated_input_tokens,
        estimated_output_tokens, and estimated_cost_usd.

    """
    if num_contacts <= 0 or avg_messages_per_contact <= 0 or batch_size <= 0:
        return CostEstimate()

    calls_per_contact = math.ceil(avg_messages_per_contact / batch_size)
    gen_calls = num_contacts * calls_per_contact

    # ~10% overhead for AI-assist (names, personalities, events, arcs)
    assist_overhead = max(1, int(gen_calls * 0.10))
    total_calls = gen_calls + assist_overhead

    input_tokens = total_calls * _EST_INPUT_TOKENS_PER_CALL
    output_tokens = total_calls * _EST_OUTPUT_TOKENS_PER_CALL

    rates = _MODEL_COST_PER_1K_TOKENS.get(model, _MODEL_COST_PER_1K_TOKENS["gpt-4o"])
    cost = (input_tokens / 1000) * rates["input"] + (output_tokens / 1000) * rates["output"]

    return CostEstimate(
        api_calls=total_calls,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
        estimated_cost_usd=round(cost, 4),
    )


# ---------------------------------------------------------------------------
# Token budgeting
# ---------------------------------------------------------------------------

MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-3.5-turbo": 16_385,
}

FALLBACK_ENCODING: str = "cl100k_base"


@functools.lru_cache(maxsize=8)
def _get_encoding(model: str) -> tiktoken.Encoding:
    """Return a cached tiktoken encoding for *model*, falling back to cl100k_base.

    The underlying ``tiktoken.encoding_for_model`` call is expensive because
    it downloads and compiles a BPE vocabulary on first use.  Wrapping it
    with ``lru_cache`` ensures each model is resolved at most once per
    process lifetime.

    Args:
        model (str): OpenAI model name (e.g. ``"gpt-4o"``).

    Returns:
        The ``tiktoken.Encoding`` instance for the model, or the
        ``cl100k_base`` fallback if the model is unrecognised.

    """
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        logger.debug("Unknown model %r for tiktoken — falling back to %s", model, FALLBACK_ENCODING)
        return tiktoken.get_encoding(FALLBACK_ENCODING)


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """Count the number of tokens in *text* using the BPE encoding for *model*.

    Uses tiktoken for an exact token count rather than the rough
    ``len(text) // 4`` heuristic.  The encoding object is cached via
    ``_get_encoding`` so repeated calls are essentially free.

    Args:
        text (str): The string to tokenise.
        model (str): The OpenAI model whose tokeniser should be used.
            Defaults to ``"gpt-4o"``.  If the model is unknown, the
            ``cl100k_base`` encoding is used instead.

    Returns:
        The exact number of BPE tokens produced by encoding *text*.

    """
    encoding = _get_encoding(model)
    return len(encoding.encode(text))


def budget_prompt(
    system_prompt: str,
    user_prompt: str,
    model: str,
    max_output_tokens: int,
) -> tuple[str, str, bool]:
    """Ensure system + user prompts fit within the model's context window.

    Counts tokens for both prompts, adds ``max_output_tokens``, and compares
    the total against the model's context window (looked up in
    ``MODEL_CONTEXT_WINDOWS``; unknown models fall back to 128 000).

    If the total exceeds the budget, the *user_prompt* is truncated from the
    end — line by line — until the combined token count fits.  The final
    instruction line (the one starting with ``"Generate ORIGINAL content"``)
    is preserved so the LLM always receives the closing directive.

    Args:
        system_prompt (str): The system message (never truncated).
        user_prompt (str): The user message that may be trimmed.
        model (str): Model name used to look up context-window size and
            tokeniser.
        max_output_tokens (int): Tokens reserved for the model's reply.

    Returns:
        A 3-tuple ``(system_prompt, user_prompt, was_truncated)`` where
        *was_truncated* is ``True`` when the user prompt had to be trimmed.

    """
    context_window = MODEL_CONTEXT_WINDOWS.get(model, 128_000)
    available = context_window - max_output_tokens

    system_tokens = count_tokens(system_prompt, model)
    user_tokens = count_tokens(user_prompt, model)
    total_input = system_tokens + user_tokens

    if total_input <= available:
        return system_prompt, user_prompt, False

    overshoot = total_input - available
    logger.warning(
        "Prompt exceeds context window by ~%d tokens (system=%d, user=%d, max_output=%d, window=%d). Truncating user_prompt.",
        overshoot,
        system_tokens,
        user_tokens,
        max_output_tokens,
        context_window,
    )

    # Preserve the last line if it looks like the closing directive
    lines = user_prompt.splitlines(keepends=True)
    preserved_tail = ""
    if lines and lines[-1].strip().startswith("Generate ORIGINAL content"):
        preserved_tail = lines.pop()

    # Remove lines from the end until within budget
    while lines:
        candidate = "".join(lines) + preserved_tail
        if count_tokens(candidate, model) + system_tokens <= available:
            return system_prompt, candidate, True
        lines.pop()

    # Even with all body lines removed, return the tail only
    return system_prompt, preserved_tail, True


# ---------------------------------------------------------------------------
# Client caching
# ---------------------------------------------------------------------------

_cached_client: OpenAI | None = None
_cached_key: str = ""


def get_openai_client(*, raise_on_missing: bool = False) -> OpenAI | None:
    """Return a cached OpenAI client, recreating only when the API key changes.

    Reuses the same ``httpx`` connection pool across calls.  The client
    is recreated only when the environment key changes (e.g. via the
    ``/api/apikey/set`` endpoint).

    When ``raise_on_missing`` is *True* (used by the AI-assist layer), a
    missing key raises an ``HTTPException(401)`` instead of returning
    *None*.  The generation pipeline passes the default *False* so it
    can handle a missing key gracefully with an empty JSON fallback.

    Args:
        raise_on_missing (bool): If *True*, raise an HTTP 401 error when
            the ``OPENAI_API_KEY`` environment variable is absent rather
            than returning *None*.

    Returns:
        A reusable OpenAI client, or *None* when the API key is absent
        and ``raise_on_missing`` is *False*.

    Raises:
        HTTPException: 401 when ``raise_on_missing`` is *True* and no
            API key is set.

    """
    global _cached_client, _cached_key

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        if raise_on_missing:
            raise HTTPException(
                status_code=401,
                detail=(
                    "OpenAI API key is not configured. "
                    "Paste your key into the API Key field in the top-right corner of the UI, "
                    "or create a .env file in the project root with: OPENAI_API_KEY=sk-..."
                ),
            )
        return None
    if _cached_client is None or api_key != _cached_key:
        _cached_client = OpenAI(api_key=api_key)
        _cached_key = api_key
    return _cached_client


# ---------------------------------------------------------------------------
# Core LLM call
# ---------------------------------------------------------------------------


def call_llm(system_prompt: str, user_prompt: str, settings: GenerationSettings) -> LLMCallResult:
    """Send a generation request to the configured LLM provider.

    Applies token budgeting to ensure prompts fit within the model's
    context window, then delegates to the provider selected by
    ``settings.llm_provider`` via :func:`source.llm_provider.get_provider`.
    The function signature is preserved for backward compatibility so all
    existing callers continue to work unchanged.

    The returned :class:`LLMCallResult` includes the raw JSON content,
    the actual token usage reported by the provider, and the model name.

    Args:
        system_prompt (str): The system message with personality context.
        user_prompt (str): The user message with the batch skeleton.
        settings (GenerationSettings): Generation settings including
            provider name, model, and temperature.

    Returns:
        LLMCallResult containing the response content, token usage, and
        model identifier.  On missing API key or ``None`` content the
        result contains an empty-messages JSON with zero usage.

    """
    from source.llm_provider import get_provider  # noqa: PLC0415  # circular import: llm_provider imports from llm_client

    model = settings.llm_model or DEFAULT_MODEL

    system_prompt, user_prompt, was_truncated = budget_prompt(
        system_prompt,
        user_prompt,
        model,
        max_output_tokens=MAX_LLM_TOKENS,
    )
    if was_truncated:
        logger.warning("User prompt was truncated to fit within %s context window", model)

    provider = get_provider(settings.llm_provider)
    return provider.generate(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
        temperature=settings.temperature,
        max_tokens=MAX_LLM_TOKENS,
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _strip_code_fences(raw: str) -> str:
    """Remove markdown code fences from a raw LLM response string.

    Some models wrap their JSON output in triple-backtick fences
    (e.g. ````` ```json ... ``` `````).  This helper strips them so the
    caller receives clean JSON.

    Args:
        raw (str): The raw response string, possibly wrapped in fences.

    Returns:
        The unwrapped JSON string.

    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return cleaned


def _reconcile_message_count(messages: list[str], expected_count: int) -> list[str]:
    """Adjust message list length to match the expected count.

    Truncates when the LLM returns too many, pads small shortfalls with
    ellipsis placeholders, and raises when the shortfall is too large
    for graceful recovery.

    Args:
        messages (list[str]): Raw messages parsed from the LLM response.
        expected_count (int): Exact number of messages expected by the
            calling batch.

    Returns:
        A message list of exactly ``expected_count`` length.

    Raises:
        ValueError: When the shortfall exceeds ``MAX_SMALL_SHORTFALL``.

    """
    if len(messages) == expected_count:
        return messages
    if len(messages) > expected_count:
        return messages[:expected_count]
    if len(messages) >= expected_count - MAX_SMALL_SHORTFALL:
        while len(messages) < expected_count:
            messages.append("...")
        return messages

    msg = f"LLM returned {len(messages)} messages, expected {expected_count}."
    raise ValueError(msg)


def parse_llm_response(raw: str, expected_count: int) -> tuple[list[str], StoryState | None]:
    """Parse the LLM JSON response into messages and story state.

    Tries Pydantic validation via :class:`ConversationBatchSchema` first
    for type-safe parsing.  On validation failure, falls back to manual
    dict-based extraction so partial or non-conforming LLM output is
    still handled gracefully.

    Handles count mismatches via :func:`_reconcile_message_count`, which
    truncates excess messages, pads small shortfalls with ellipsis
    placeholders, and raises ``ValueError`` when the shortfall exceeds
    ``MAX_SMALL_SHORTFALL``.

    Args:
        raw (str): Raw JSON string from the LLM.
        expected_count (int): Exact number of messages expected.

    Returns:
        Tuple of (message strings, optional StoryState).

    """
    cleaned = _strip_code_fences(raw)
    data = json.loads(cleaned)

    try:
        validated = ConversationBatchSchema.model_validate(data)
        validated_messages = _reconcile_message_count(validated.messages, expected_count)
        return validated_messages, validated.story_state
    except (ValueError, KeyError, TypeError):
        logger.debug("Pydantic validation of LLM response failed — falling back to manual parsing")

    fallback_messages: list[str] = data.get("messages", [])

    new_state: StoryState | None = None
    raw_state = data.get("story_state")
    if isinstance(raw_state, dict):
        try:
            new_state = StoryState.model_validate(raw_state)
        except (ValueError, KeyError, TypeError):
            logger.warning("Could not parse story_state from LLM response")

    fallback_messages = _reconcile_message_count(fallback_messages, expected_count)
    return fallback_messages, new_state


# ---------------------------------------------------------------------------
# Story state merging
# ---------------------------------------------------------------------------

TOPICS_CAP: int = 50
KEY_EVENTS_CAP: int = 25
UNRESOLVED_CAP: int = 10


def merge_story_states(accumulated: StoryState, new_batch: StoryState | None) -> StoryState:
    """Merge a new batch's story state into the running total.

    List fields are concatenated and capped to prevent unbounded growth.
    Scalar fields prefer the newest non-empty value.

    Args:
        accumulated: All previous story state.
        new_batch: Story state from the latest batch.

    Returns:
        Merged StoryState with capped list sizes.

    """
    if new_batch is None:
        return accumulated
    return StoryState(
        topics_covered=(accumulated.topics_covered + new_batch.topics_covered)[-TOPICS_CAP:],
        key_events=(accumulated.key_events + new_batch.key_events)[-KEY_EVENTS_CAP:],
        unresolved_threads=new_batch.unresolved_threads[-UNRESOLVED_CAP:],
        relationship_vibe=new_batch.relationship_vibe or accumulated.relationship_vibe,
        owner_state=new_batch.owner_state or accumulated.owner_state,
        contact_state=new_batch.contact_state or accumulated.contact_state,
    )
