"""Shared rate-limiter configuration for the FastAPI application.

Provides a single :class:`slowapi.Limiter` instance that any module can
import without introducing circular dependencies.  The default limit is
60 requests per minute per client IP address.  Individual endpoints may
override this with stricter limits using ``@limiter.limit()``.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter: Limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
