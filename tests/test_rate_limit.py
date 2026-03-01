"""Tests for slowapi rate-limiting middleware integration."""

# ruff: noqa: S101

from __future__ import annotations

from fastapi.testclient import TestClient

from source import app as app_module
from source.models import ScenarioConfig
from source.rate_limit import limiter


def _reset_limiter_storage() -> None:
    """Flush all in-memory rate-limit counters so tests start from zero.

    Accesses the underlying ``limits`` storage backend and calls its
    ``reset`` method.  This is necessary because the limiter singleton
    persists across the entire pytest session.
    """
    storage = getattr(limiter, "_storage", None) or getattr(limiter._limiter, "storage", None)
    if storage is not None and callable(getattr(storage, "reset", None)):
        storage.reset()


def test_rate_limit_returns_429_when_exceeded() -> None:
    """Verify that exceeding the per-endpoint rate limit returns a 429 JSON error.

    The ``/api/generate/quality-check`` endpoint is rate-limited to
    5 requests per minute.  This test fires 6 requests in rapid
    succession and asserts the 6th is rejected with a 429 status code
    and the expected structured JSON error body.
    """
    _reset_limiter_storage()
    app_module.app.state.scenario = ScenarioConfig()
    client = TestClient(app_module.app)

    payload = {"auto_adjust": False}

    for i in range(5):
        resp = client.post("/api/generate/quality-check", json=payload)
        assert resp.status_code != 429, f"Request {i + 1} of 5 was rate-limited prematurely"

    blocked = client.post("/api/generate/quality-check", json=payload)
    assert blocked.status_code == 429

    body = blocked.json()
    assert body["error"] == "rate_limit_exceeded"
    assert "detail" in body


def test_rate_limit_429_response_contains_retry_after_header() -> None:
    """The 429 response should include a Retry-After header for well-behaved clients.

    After exhausting the rate limit, the blocked response must include
    the ``Retry-After`` header so clients know how long to wait before
    retrying.
    """
    _reset_limiter_storage()
    app_module.app.state.scenario = ScenarioConfig()
    client = TestClient(app_module.app)

    payload = {"auto_adjust": False}
    for _ in range(5):
        client.post("/api/generate/quality-check", json=payload)

    blocked = client.post("/api/generate/quality-check", json=payload)

    assert blocked.status_code == 429
    assert "retry-after" in blocked.headers


def test_rate_limit_non_limited_endpoint_always_succeeds() -> None:
    """Endpoints without explicit rate limits should use the global default (60/min).

    The GET /api/scenario endpoint does not have its own limiter.limit()
    decorator, so it relies on the global 60/min default.  Six rapid
    requests should all succeed.
    """
    _reset_limiter_storage()
    app_module.app.state.scenario = ScenarioConfig()
    client = TestClient(app_module.app)

    for _ in range(6):
        resp = client.get("/api/scenario")
        assert resp.status_code == 200


def test_limiter_singleton_has_default_limit() -> None:
    """The shared limiter instance should have a default rate limit configured."""
    assert limiter._default_limits is not None
    assert len(limiter._default_limits) > 0
