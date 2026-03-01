"""LLM provider abstraction layer.

Defines a ``Protocol``-based interface for LLM providers and concrete
implementations.  The factory function :func:`get_provider` returns the
appropriate provider based on a name string, allowing ``call_llm`` in
``llm_client.py`` to remain provider-agnostic.

Currently ships with :class:`OpenAIProvider` (synchronous) and
:class:`StreamingOpenAIProvider` (async token-level streaming).
Additional providers (Anthropic, local models) can be added by
implementing the :class:`LLMProvider` protocol and registering in
:func:`get_provider`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Protocol

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, InternalServerError, RateLimitError

from source.llm_client import (
    RATE_LIMIT_SLEEP_SECONDS,
    LLMCallResult,
    QuotaExhaustedError,
    TokenUsage,
    get_openai_client,
)

logger = logging.getLogger(__name__)


class LLMProvider(Protocol):
    """Protocol defining the interface every LLM provider must satisfy.

    Any class whose ``generate`` method matches this signature is a valid
    provider — no explicit subclassing required.  The protocol is consumed
    by :func:`source.llm_client.call_llm` via the :func:`get_provider`
    factory.
    """

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMCallResult:
        """Generate a response from the LLM.

        Args:
            system_prompt (str): The system message providing context and
                personality instructions.
            user_prompt (str): The user message containing the batch
                skeleton or question.
            model (str): Provider-specific model identifier
                (e.g. ``"gpt-4o"``).
            temperature (float): Sampling temperature controlling
                randomness.
            max_tokens (int): Upper bound on tokens the model may
                generate.

        Returns:
            An :class:`LLMCallResult` with the raw response content,
            token usage breakdown, and model name.

        """
        ...


class OpenAIProvider:
    """OpenAI API implementation of the :class:`LLMProvider` protocol.

    Wraps the ``chat.completions.create`` endpoint with structured error
    handling for rate limits (transient sleep + re-raise), hard quota
    exhaustion (:class:`QuotaExhaustedError`), connection errors, and
    server errors.  Token usage is read from the API response and
    returned inside :class:`LLMCallResult`.

    The provider obtains its client via :func:`get_openai_client`, which
    caches the ``httpx`` connection pool and recreates only when the API
    key changes.

    Attributes:
        _empty: Pre-built empty-messages fallback returned when the API
            key is missing or the response content is ``None``.

    """

    def __init__(self) -> None:
        """Initialise the OpenAI provider with its empty-result fallback."""
        self._empty = LLMCallResult(content='{"messages": []}')

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMCallResult:
        """Call the OpenAI chat completions API and return a structured result.

        Falls back gracefully when the API key is missing, returning an
        empty-messages JSON payload with zero token usage.

        Handles:
        - ``RateLimitError`` with ``insufficient_quota`` → raises
          :class:`QuotaExhaustedError`.
        - Transient ``RateLimitError`` → sleeps then re-raises for the
          caller's retry loop.
        - ``APIConnectionError``, ``APITimeoutError``,
          ``InternalServerError`` → logs and re-raises.

        Args:
            system_prompt (str): The system message providing context.
            user_prompt (str): The user message with the batch skeleton.
            model (str): OpenAI model identifier (e.g. ``"gpt-4o"``).
            temperature (float): Sampling temperature.
            max_tokens (int): Maximum tokens to generate.

        Returns:
            An :class:`LLMCallResult` with content, usage, and model.
            Returns an empty-messages result when the API key is absent
            or the response content is ``None``.

        Raises:
            QuotaExhaustedError: When the API key has exhausted its
                credits.
            RateLimitError: On transient rate limits after the sleep.
            APIConnectionError: On network-level failures.
            APITimeoutError: When the request times out.
            InternalServerError: On 5xx responses from the provider.

        """
        client = get_openai_client()
        if client is None:
            logger.error("No OPENAI_API_KEY set")
            return self._empty

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except RateLimitError as exc:
            error_body = str(exc)
            if "insufficient_quota" in error_body:
                raise QuotaExhaustedError("OpenAI quota exhausted — check billing at platform.openai.com") from exc
            logger.warning("Rate limited (transient), sleeping %ds before retry...", RATE_LIMIT_SLEEP_SECONDS)
            time.sleep(RATE_LIMIT_SLEEP_SECONDS)
            raise
        except APIConnectionError:
            logger.warning("OpenAI API connection error — will retry if caller supports it")
            raise
        except APITimeoutError:
            logger.warning("OpenAI API request timed out — will retry if caller supports it")
            raise
        except InternalServerError:
            logger.warning("OpenAI API server error (5xx) — will retry if caller supports it")
            raise

        content = response.choices[0].message.content
        if content is None:
            return self._empty

        usage = TokenUsage()
        if response.usage is not None:
            usage = TokenUsage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            )

        return LLMCallResult(content=content, usage=usage, model=model)


_PROVIDERS: dict[str, type[OpenAIProvider]] = {
    "openai": OpenAIProvider,
}


def get_provider(provider_name: str = "openai") -> LLMProvider:
    """Return an LLM provider instance for the given name.

    Looks up the provider class in a registry and returns a fresh
    instance.  Falls back to :class:`OpenAIProvider` when the requested
    name is not recognised, logging a warning.

    Args:
        provider_name (str): Case-sensitive provider key.  Currently
            only ``"openai"`` is supported.

    Returns:
        A provider instance satisfying the :class:`LLMProvider`
        protocol.

    """
    provider_cls = _PROVIDERS.get(provider_name)
    if provider_cls is None:
        logger.warning("Unknown LLM provider %r, falling back to OpenAI", provider_name)
        provider_cls = OpenAIProvider
    return provider_cls()


# ---------------------------------------------------------------------------
# Streaming provider
# ---------------------------------------------------------------------------

_cached_async_client: AsyncOpenAI | None = None
_cached_async_key: str = ""


def _get_async_openai_client() -> AsyncOpenAI | None:
    """Return a cached async OpenAI client, recreating only when the API key changes.

    Mirrors :func:`get_openai_client` but returns an ``AsyncOpenAI``
    instance suitable for ``async for`` streaming iteration.

    Returns:
        An ``AsyncOpenAI`` client, or ``None`` when the key is absent.

    """
    import os  # noqa: PLC0415  — lightweight, avoids top-level side-effect

    global _cached_async_client, _cached_async_key

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None
    if _cached_async_client is None or api_key != _cached_async_key:
        _cached_async_client = AsyncOpenAI(api_key=api_key)
        _cached_async_key = api_key
    return _cached_async_client


class StreamingOpenAIProvider:
    """Async OpenAI streaming provider that yields tokens as they arrive.

    Uses ``stream=True`` on the chat completions endpoint and yields
    each chunk's delta content.  Callers collect the tokens to produce
    the full response text, then parse it through :func:`parse_llm_response`
    after the stream is exhausted.

    Handles the same error classes as :class:`OpenAIProvider`: rate
    limits, quota exhaustion, connection errors, and server errors.

    Attributes:
        _client_factory: Callable returning the async OpenAI client.
            Exists so the class can be tested with a mock factory.

    """

    def __init__(self) -> None:
        """Initialise the streaming provider with its client factory."""
        self._client_factory = _get_async_openai_client

    async def generate_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncGenerator[str, None]:
        """Stream token deltas from the OpenAI chat completions API.

        Opens a streaming connection and yields each content delta as a
        string.  The caller is responsible for concatenating the deltas
        to form the full response and parsing the assembled JSON.

        Args:
            system_prompt (str): The system message providing context.
            user_prompt (str): The user message with the batch skeleton.
            model (str): OpenAI model identifier (e.g. ``"gpt-4o"``).
            temperature (float): Sampling temperature.
            max_tokens (int): Maximum tokens to generate.

        Yields:
            Token-level string fragments as they arrive from the API.

        Raises:
            QuotaExhaustedError: When the API key has exhausted its credits.
            RateLimitError: On transient rate limits after the sleep.
            APIConnectionError: On network-level failures.
            APITimeoutError: When the request times out.
            InternalServerError: On 5xx responses from the provider.

        """
        client = self._client_factory()
        if client is None:
            logger.error("No OPENAI_API_KEY set — cannot stream")
            yield '{"messages": []}'
            return

        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                stream=True,
            )
        except RateLimitError as exc:
            error_body = str(exc)
            if "insufficient_quota" in error_body:
                raise QuotaExhaustedError("OpenAI quota exhausted — check billing at platform.openai.com") from exc
            logger.warning("Rate limited (transient) during stream, sleeping %ds", RATE_LIMIT_SLEEP_SECONDS)
            await asyncio.sleep(RATE_LIMIT_SLEEP_SECONDS)
            raise
        except APIConnectionError:
            logger.warning("OpenAI API connection error during stream setup")
            raise
        except APITimeoutError:
            logger.warning("OpenAI API request timed out during stream setup")
            raise
        except InternalServerError:
            logger.warning("OpenAI API server error (5xx) during stream setup")
            raise

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content


def get_streaming_provider(provider_name: str = "openai") -> StreamingOpenAIProvider:
    """Return a streaming LLM provider instance for the given name.

    Currently only ``"openai"`` is supported.  Falls back to the OpenAI
    streaming provider for unrecognised names, logging a warning.

    Args:
        provider_name (str): Case-sensitive provider key.

    Returns:
        A :class:`StreamingOpenAIProvider` instance.

    """
    if provider_name != "openai":
        logger.warning("Unknown streaming provider %r, falling back to OpenAI", provider_name)
    return StreamingOpenAIProvider()
