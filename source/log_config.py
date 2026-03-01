"""Structured logging configuration for the Synthesized Chat Generator.

Provides a JSON-structured log formatter and a one-call ``configure_logging``
function that replaces the default ``logging.basicConfig`` output with
machine-readable JSON lines.  Human-readable coloured output is available
as a fallback when ``json_format=False``.

Usage::

    from source.log_config import configure_logging
    configure_logging()          # JSON output (default)
    configure_logging(json_format=False)  # human-readable fallback
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class _JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line.

    Fields: ``timestamp``, ``level``, ``logger``, ``message``, and any
    extra key-value pairs attached to the record via ``extra={...}``.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as a compact JSON object.

        Args:
            record: The log record to format.

        Returns:
            A single-line JSON string.

        """
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        for key in ("event_type", "device_id", "contact_id", "tokens_in", "tokens_out", "cost_usd"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(*, level: int = logging.INFO, json_format: bool = True) -> None:
    """Configure the root logger with a structured or human-readable formatter.

    Replaces any existing handlers on the root logger so the application
    gets a single consistent output format from startup.

    Args:
        level: Minimum severity level to emit (default ``logging.INFO``).
        json_format: When ``True`` (the default) emit JSON lines.  Set to
            ``False`` for traditional human-readable output during local
            development.

    """
    root = logging.getLogger()
    root.setLevel(level)

    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    if json_format:
        handler.setFormatter(_JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    root.addHandler(handler)
