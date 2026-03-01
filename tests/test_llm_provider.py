"""Tests for source.llm_provider — LLM provider abstraction layer."""

# ruff: noqa: S101

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import RateLimitError

from source.llm_client import LLMCallResult, QuotaExhaustedError
from source.llm_provider import (
    OpenAIProvider,
    StreamingOpenAIProvider,
    get_provider,
    get_streaming_provider,
)

# ---------------------------------------------------------------------------
# get_provider
# ---------------------------------------------------------------------------


def test_get_provider_returns_openai_for_known_name() -> None:
    """get_provider('openai') should return an OpenAIProvider instance."""
    provider = get_provider("openai")

    assert isinstance(provider, OpenAIProvider)


def test_get_provider_falls_back_to_openai_for_unknown_name() -> None:
    """get_provider with an unknown name should fall back to OpenAIProvider."""
    provider = get_provider("totally-unknown")

    assert isinstance(provider, OpenAIProvider)


def test_get_provider_default_is_openai() -> None:
    """get_provider with no argument should default to OpenAIProvider."""
    provider = get_provider()

    assert isinstance(provider, OpenAIProvider)


# ---------------------------------------------------------------------------
# get_streaming_provider
# ---------------------------------------------------------------------------


def test_get_streaming_provider_returns_streaming_provider() -> None:
    """get_streaming_provider should return a StreamingOpenAIProvider."""
    provider = get_streaming_provider("openai")

    assert isinstance(provider, StreamingOpenAIProvider)


def test_get_streaming_provider_falls_back_for_unknown() -> None:
    """Unknown name should still produce a StreamingOpenAIProvider."""
    provider = get_streaming_provider("fake-provider")

    assert isinstance(provider, StreamingOpenAIProvider)


# ---------------------------------------------------------------------------
# OpenAIProvider.generate — no API key
# ---------------------------------------------------------------------------


def test_openai_provider_returns_empty_when_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """When get_openai_client returns None, generate should return empty-messages JSON."""
    monkeypatch.setattr("source.llm_provider.get_openai_client", lambda: None)

    provider = OpenAIProvider()
    result = provider.generate("system", "user", "gpt-4o", 0.7, 4096)

    assert isinstance(result, LLMCallResult)
    assert '"messages": []' in result.content


# ---------------------------------------------------------------------------
# OpenAIProvider.generate — mocked success
# ---------------------------------------------------------------------------


def test_openai_provider_generate_returns_content_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful API call should return content, usage, and model."""
    mock_usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    mock_choice = SimpleNamespace(message=SimpleNamespace(content='{"messages": ["hi"]}'))
    mock_response = SimpleNamespace(choices=[mock_choice], usage=mock_usage)

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    monkeypatch.setattr("source.llm_provider.get_openai_client", lambda: mock_client)

    provider = OpenAIProvider()
    result = provider.generate("system", "user", "gpt-4o", 0.7, 4096)

    assert result.content == '{"messages": ["hi"]}'
    assert result.usage.prompt_tokens == 100
    assert result.usage.completion_tokens == 50
    assert result.model == "gpt-4o"


def test_openai_provider_returns_empty_when_content_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the API returns None content, generate should return the empty fallback."""
    mock_choice = SimpleNamespace(message=SimpleNamespace(content=None))
    mock_response = SimpleNamespace(choices=[mock_choice], usage=None)

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    monkeypatch.setattr("source.llm_provider.get_openai_client", lambda: mock_client)

    provider = OpenAIProvider()
    result = provider.generate("system", "user", "gpt-4o", 0.7, 4096)

    assert '"messages": []' in result.content


# ---------------------------------------------------------------------------
# OpenAIProvider.generate — quota exhausted
# ---------------------------------------------------------------------------


def test_openai_provider_raises_quota_exhausted_on_insufficient_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """RateLimitError with 'insufficient_quota' should raise QuotaExhaustedError."""
    mock_client = MagicMock()
    error_response = MagicMock()
    error_response.status_code = 429
    mock_client.chat.completions.create.side_effect = RateLimitError(
        message="insufficient_quota",
        response=error_response,
        body=None,
    )
    monkeypatch.setattr("source.llm_provider.get_openai_client", lambda: mock_client)

    provider = OpenAIProvider()
    with pytest.raises(QuotaExhaustedError, match="quota exhausted"):
        provider.generate("system", "user", "gpt-4o", 0.7, 4096)


# ---------------------------------------------------------------------------
# StreamingOpenAIProvider — no client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_provider_yields_empty_when_no_client() -> None:
    """When no API key is set, the stream should yield empty-messages JSON."""
    provider = StreamingOpenAIProvider()
    provider._client_factory = lambda: None  # type: ignore[assignment]

    chunks: list[str] = []
    async for chunk in provider.generate_stream("system", "user", "gpt-4o", 0.7, 4096):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert '"messages": []' in chunks[0]


# ---------------------------------------------------------------------------
# StreamingOpenAIProvider — mocked streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_provider_yields_token_deltas() -> None:
    """Mocked stream should yield content deltas from chunk choices."""

    async def _mock_stream() -> Any:  # noqa: RUF029
        """Simulate an async streaming response.

        Yields:
            SimpleNamespace chunks mimicking the OpenAI streaming format.

        """
        for text in ["hello", " ", "world"]:
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_mock_stream())

    provider = StreamingOpenAIProvider()
    provider._client_factory = lambda: mock_client  # type: ignore[assignment]

    chunks: list[str] = []
    async for chunk in provider.generate_stream("system", "user", "gpt-4o", 0.7, 4096):
        chunks.append(chunk)

    assert chunks == ["hello", " ", "world"]
