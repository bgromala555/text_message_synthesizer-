"""Tests for source.llm_client — LLM call, response parsing, and story state."""

# ruff: noqa: S101

from __future__ import annotations

import json

import pytest

import source.llm_client as llm_client_mod
from source.llm_client import (
    KEY_EVENTS_CAP,
    MAX_SMALL_SHORTFALL,
    MODEL_CONTEXT_WINDOWS,
    TOPICS_CAP,
    UNRESOLVED_CAP,
    AccumulatedUsage,
    ConversationBatchSchema,
    CostEstimate,
    LLMCallResult,
    StoryState,
    TokenUsage,
    budget_prompt,
    call_llm,
    count_tokens,
    estimate_generation_cost,
    merge_story_states,
    parse_llm_response,
)
from source.models import GenerationSettings

# ---------------------------------------------------------------------------
# call_llm
# ---------------------------------------------------------------------------


def test_call_llm_returns_empty_when_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """When OPENAI_API_KEY is unset, call_llm returns an LLMCallResult with empty-messages JSON."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(llm_client_mod, "_cached_client", None)
    monkeypatch.setattr(llm_client_mod, "_cached_key", "")

    result = call_llm("system", "user", GenerationSettings())

    assert isinstance(result, LLMCallResult)
    parsed = json.loads(result.content)
    assert parsed == {"messages": []}
    assert result.usage.prompt_tokens == 0
    assert result.usage.completion_tokens == 0
    assert result.usage.total_tokens == 0


# ---------------------------------------------------------------------------
# TokenUsage / LLMCallResult / AccumulatedUsage
# ---------------------------------------------------------------------------


def test_token_usage_defaults_to_zero() -> None:
    """TokenUsage initializes all counters to zero when no arguments are given."""
    usage = TokenUsage()

    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0
    assert usage.total_tokens == 0


def test_llm_call_result_defaults() -> None:
    """LLMCallResult defaults to empty content, zero usage, and empty model."""
    result = LLMCallResult()

    assert not result.content
    assert result.usage.total_tokens == 0
    assert not result.model


def test_accumulated_usage_add_call_increments() -> None:
    """AccumulatedUsage.add_call accumulates token counts and cost across calls."""
    acc = AccumulatedUsage()
    usage_a = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    usage_b = TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300)

    acc.add_call(usage_a, "gpt-4o")
    acc.add_call(usage_b, "gpt-4o")

    assert acc.total_calls == 2
    assert acc.total_prompt_tokens == 300
    assert acc.total_completion_tokens == 150
    assert acc.total_tokens == 450
    assert acc.estimated_cost_usd > 0


def test_accumulated_usage_unknown_model_uses_gpt4o_rates() -> None:
    """Unknown model names fall back to gpt-4o pricing in add_call."""
    acc_known = AccumulatedUsage()
    acc_unknown = AccumulatedUsage()
    usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)

    acc_known.add_call(usage, "gpt-4o")
    acc_unknown.add_call(usage, "totally-unknown-model")

    assert acc_known.estimated_cost_usd == acc_unknown.estimated_cost_usd


# ---------------------------------------------------------------------------
# parse_llm_response
# ---------------------------------------------------------------------------


def test_parse_llm_response_handles_code_fence() -> None:
    """Markdown code fences wrapping JSON are stripped before parsing."""
    raw = '```json\n{"messages": ["hello", "world"]}\n```'

    messages, state = parse_llm_response(raw, expected_count=2)

    assert messages == ["hello", "world"]
    assert state is None


def test_parse_llm_response_truncates_excess() -> None:
    """When the LLM returns more messages than requested, the surplus is truncated."""
    raw = json.dumps({"messages": ["a", "b", "c", "d", "e"]})

    messages, _ = parse_llm_response(raw, expected_count=3)

    assert messages == ["a", "b", "c"]


def test_parse_llm_response_pads_small_shortfall() -> None:
    """A shortfall of 1-2 messages is recovered by padding with '...' placeholders."""
    raw = json.dumps({"messages": ["only-one"]})

    messages, _ = parse_llm_response(raw, expected_count=1 + MAX_SMALL_SHORTFALL)

    assert len(messages) == 1 + MAX_SMALL_SHORTFALL
    assert messages[0] == "only-one"
    assert all(m == "..." for m in messages[1:])


def test_parse_llm_response_raises_on_large_shortfall() -> None:
    """A shortfall exceeding MAX_SMALL_SHORTFALL raises ValueError with counts."""
    raw = json.dumps({"messages": ["one"]})
    expected = 10

    with pytest.raises(ValueError, match=f"expected {expected}"):
        parse_llm_response(raw, expected_count=expected)


# ---------------------------------------------------------------------------
# merge_story_states
# ---------------------------------------------------------------------------


def test_merge_story_states_prefers_latest() -> None:
    """Non-empty scalar fields from new_batch override accumulated values."""
    accumulated = StoryState(
        relationship_vibe="neutral",
        owner_state="calm",
        contact_state="happy",
    )
    new_batch = StoryState(
        relationship_vibe="tense",
        owner_state="anxious",
        contact_state="withdrawn",
    )

    merged = merge_story_states(accumulated, new_batch)

    assert merged.relationship_vibe == "tense"
    assert merged.owner_state == "anxious"
    assert merged.contact_state == "withdrawn"


def test_merge_story_states_caps_lists() -> None:
    """Merged list fields are capped at TOPICS_CAP, KEY_EVENTS_CAP, UNRESOLVED_CAP."""
    accumulated = StoryState(
        topics_covered=[f"old-{i}" for i in range(TOPICS_CAP)],
        key_events=[f"evt-{i}" for i in range(KEY_EVENTS_CAP)],
        unresolved_threads=[f"thr-{i}" for i in range(UNRESOLVED_CAP)],
    )
    new_batch = StoryState(
        topics_covered=["new-topic"],
        key_events=["new-event"],
        unresolved_threads=["new-thread"],
    )

    merged = merge_story_states(accumulated, new_batch)

    assert len(merged.topics_covered) == TOPICS_CAP
    assert merged.topics_covered[-1] == "new-topic"
    assert len(merged.key_events) == KEY_EVENTS_CAP
    assert merged.key_events[-1] == "new-event"
    assert len(merged.unresolved_threads) == 1
    assert merged.unresolved_threads[0] == "new-thread"


def test_merge_story_states_handles_none() -> None:
    """When new_batch is None, the accumulated state is returned unchanged."""
    accumulated = StoryState(
        topics_covered=["existing"],
        relationship_vibe="warm",
        owner_state="happy",
        contact_state="content",
    )

    merged = merge_story_states(accumulated, None)

    assert merged.topics_covered == ["existing"]
    assert merged.relationship_vibe == "warm"
    assert merged is accumulated


def test_story_state_default_factory() -> None:
    """StoryState list fields use default_factory so instances don't share state."""
    state_a = StoryState()
    state_b = StoryState()

    state_a.topics_covered.append("only-in-a")

    assert state_b.topics_covered == []
    assert state_a.topics_covered == ["only-in-a"]
    assert state_a.key_events is not state_b.key_events
    assert state_a.unresolved_threads is not state_b.unresolved_threads


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


def test_count_tokens_returns_positive_int() -> None:
    """count_tokens returns a positive integer for non-empty text."""
    result = count_tokens("Hello, world!", model="gpt-4o")

    assert isinstance(result, int)
    assert result > 0


def test_count_tokens_empty_string() -> None:
    """An empty string produces zero tokens."""
    assert count_tokens("", model="gpt-4o") == 0


def test_count_tokens_unknown_model_falls_back() -> None:
    """An unknown model name falls back to cl100k_base without raising."""
    result = count_tokens("Hello, world!", model="totally-fake-model-xyz")

    assert isinstance(result, int)
    assert result > 0


def test_count_tokens_scales_with_length() -> None:
    """Longer text produces more tokens than shorter text."""
    short = count_tokens("hi")
    long = count_tokens("hi " * 500)

    assert long > short


# ---------------------------------------------------------------------------
# budget_prompt
# ---------------------------------------------------------------------------


def test_budget_prompt_no_truncation_when_within_budget() -> None:
    """When total tokens fit, the prompts are returned unchanged and was_truncated is False."""
    system = "You are a helpful assistant."
    user = "Tell me a joke."

    sys_out, user_out, truncated = budget_prompt(system, user, "gpt-4o", max_output_tokens=4096)

    assert sys_out == system
    assert user_out == user
    assert truncated is False


def test_budget_prompt_truncates_when_over_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """When prompts exceed a tiny context window, user_prompt is truncated."""
    # Patch the context window to something very small to force truncation
    monkeypatch.setitem(MODEL_CONTEXT_WINDOWS, "gpt-4o", 100)

    system = "System prompt here."
    user_lines = [f"Line {i}: some content that takes up tokens." for i in range(50)]
    user_lines.append("Generate ORIGINAL content based on the above.")
    user = "\n".join(user_lines)

    sys_out, user_out, truncated = budget_prompt(system, user, "gpt-4o", max_output_tokens=20)

    assert sys_out == system
    assert truncated is True
    assert len(user_out) < len(user)
    assert "Generate ORIGINAL content" in user_out


def test_budget_prompt_preserves_generate_directive() -> None:
    """The closing 'Generate ORIGINAL content...' line is preserved even under heavy truncation."""
    # Use a model with a tiny window to guarantee truncation
    system = "System."
    body = "\n".join([f"filler line {i}" for i in range(200)])
    directive = "Generate ORIGINAL content — do not copy."
    user = body + "\n" + directive

    _, user_out, _ = budget_prompt(system, user, "gpt-3.5-turbo", max_output_tokens=16_000)

    assert "Generate ORIGINAL content" in user_out


def test_budget_prompt_unknown_model_uses_128k_fallback() -> None:
    """Unknown models default to 128 000 context window — small prompts aren't truncated."""
    system = "System."
    user = "Short user prompt."

    _, user_out, truncated = budget_prompt(system, user, "unknown-future-model", max_output_tokens=4096)

    assert truncated is False
    assert user_out == user


# ---------------------------------------------------------------------------
# ConversationBatchSchema
# ---------------------------------------------------------------------------


def test_conversation_batch_schema_valid_data() -> None:
    """ConversationBatchSchema should accept a well-formed messages list with story state."""
    batch = ConversationBatchSchema(
        messages=["Hey!", "What's up?", "Not much"],
        story_state=StoryState(
            topics_covered=["greeting"],
            relationship_vibe="warm",
        ),
    )

    assert len(batch.messages) == 3
    assert batch.story_state is not None
    assert batch.story_state.relationship_vibe == "warm"


def test_conversation_batch_schema_empty_messages() -> None:
    """ConversationBatchSchema should accept an empty messages list."""
    batch = ConversationBatchSchema(messages=[])

    assert batch.messages == []
    assert batch.story_state is None


def test_conversation_batch_schema_defaults_to_empty() -> None:
    """ConversationBatchSchema with no arguments should default to empty messages and None state."""
    batch = ConversationBatchSchema()

    assert batch.messages == []
    assert batch.story_state is None


def test_conversation_batch_schema_validates_from_dict() -> None:
    """ConversationBatchSchema.model_validate should accept a plain dictionary."""
    data = {
        "messages": ["a", "b"],
        "story_state": {
            "topics_covered": ["weather"],
            "key_events": [],
            "unresolved_threads": [],
            "relationship_vibe": "friendly",
            "owner_state": "relaxed",
            "contact_state": "chatty",
        },
    }
    batch = ConversationBatchSchema.model_validate(data)

    assert batch.messages == ["a", "b"]
    assert batch.story_state is not None
    assert batch.story_state.relationship_vibe == "friendly"


def test_parse_llm_response_pydantic_path_with_valid_schema() -> None:
    """parse_llm_response should use the Pydantic path for well-formed JSON."""
    raw = json.dumps(
        {
            "messages": ["hello", "world"],
            "story_state": {
                "topics_covered": ["test"],
                "key_events": [],
                "unresolved_threads": [],
                "relationship_vibe": "neutral",
                "owner_state": "fine",
                "contact_state": "fine",
            },
        }
    )

    messages, state = parse_llm_response(raw, expected_count=2)

    assert messages == ["hello", "world"]
    assert state is not None
    assert state.topics_covered == ["test"]


# ---------------------------------------------------------------------------
# estimate_generation_cost
# ---------------------------------------------------------------------------


def test_estimate_generation_cost_returns_positive_values() -> None:
    """A valid request should produce a CostEstimate with positive values."""
    estimate = estimate_generation_cost(
        num_contacts=5,
        avg_messages_per_contact=50,
        batch_size=10,
        model="gpt-4o",
    )

    assert isinstance(estimate, CostEstimate)
    assert estimate.api_calls > 0
    assert estimate.estimated_cost_usd > 0


def test_estimate_generation_cost_zero_contacts_returns_empty() -> None:
    """Zero contacts should return a zeroed CostEstimate."""
    estimate = estimate_generation_cost(
        num_contacts=0,
        avg_messages_per_contact=50,
        batch_size=10,
    )

    assert estimate.api_calls == 0
    assert estimate.estimated_cost_usd == pytest.approx(0.0)


def test_estimate_generation_cost_mini_model_is_cheaper() -> None:
    """gpt-4o-mini should produce a lower cost estimate than gpt-4o for the same load."""
    est_4o = estimate_generation_cost(5, 50, 10, model="gpt-4o")
    est_mini = estimate_generation_cost(5, 50, 10, model="gpt-4o-mini")

    assert est_mini.estimated_cost_usd < est_4o.estimated_cost_usd
