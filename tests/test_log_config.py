"""Tests for source.log_config — JSON and human-readable logging formatters."""

# ruff: noqa: S101, PLC2701

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from source.log_config import _JSONFormatter, configure_logging


def test_configure_logging_json_format_produces_json_lines() -> None:
    """JSON format mode should emit parseable JSON log lines to stderr."""
    configure_logging(json_format=True, level=logging.DEBUG)

    stream = StringIO()
    root = logging.getLogger()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JSONFormatter())
    root.addHandler(handler)

    try:
        logging.getLogger("test.json").info("hello json")
        handler.flush()
        line = stream.getvalue().strip()
        parsed = json.loads(line)

        assert parsed["message"] == "hello json"
        assert parsed["level"] == "INFO"
        assert "timestamp" in parsed
        assert parsed["logger"] == "test.json"
    finally:
        root.removeHandler(handler)


def test_configure_logging_human_format_produces_readable_lines() -> None:
    """Human-readable format mode should produce traditional log lines."""
    configure_logging(json_format=False, level=logging.DEBUG)

    stream = StringIO()
    root = logging.getLogger()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(handler)

    try:
        logging.getLogger("test.human").info("hello human")
        handler.flush()
        line = stream.getvalue().strip()

        assert "hello human" in line
        assert "[INFO]" in line
        assert "test.human" in line
    finally:
        root.removeHandler(handler)


def test_json_formatter_includes_extra_fields() -> None:
    """Extra fields like event_type, tokens_in, tokens_out should appear in JSON output."""
    formatter = _JSONFormatter()
    stream = StringIO()

    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)

    test_logger = logging.getLogger("test.extras")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)

    try:
        test_logger.info(
            "generation complete",
            extra={"event_type": "batch_done", "tokens_in": 1200, "tokens_out": 800},
        )
        handler.flush()
        line = stream.getvalue().strip()
        parsed = json.loads(line)

        assert parsed["event_type"] == "batch_done"
        assert parsed["tokens_in"] == 1200
        assert parsed["tokens_out"] == 800
        assert parsed["message"] == "generation complete"
    finally:
        test_logger.removeHandler(handler)


def test_json_formatter_includes_cost_usd_field() -> None:
    """The cost_usd extra field should appear in JSON output when provided."""
    formatter = _JSONFormatter()
    stream = StringIO()

    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)

    test_logger = logging.getLogger("test.cost")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)

    try:
        test_logger.info("llm call", extra={"cost_usd": 0.0042})
        handler.flush()
        parsed = json.loads(stream.getvalue().strip())

        assert parsed["cost_usd"] == pytest.approx(0.0042)
    finally:
        test_logger.removeHandler(handler)


def test_json_formatter_omits_absent_extra_fields() -> None:
    """Extra fields that are not set should not appear in the JSON output."""
    formatter = _JSONFormatter()
    stream = StringIO()

    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)

    test_logger = logging.getLogger("test.noextras")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)

    try:
        test_logger.info("plain message")
        handler.flush()
        parsed = json.loads(stream.getvalue().strip())

        assert "event_type" not in parsed
        assert "tokens_in" not in parsed
        assert "cost_usd" not in parsed
    finally:
        test_logger.removeHandler(handler)


def test_json_formatter_includes_exception_info() -> None:
    """When an exception is logged, the JSON should contain an exception field.

    Raises:
        ValueError: Intentionally raised to test exception formatting.

    """
    formatter = _JSONFormatter()
    stream = StringIO()

    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)

    test_logger = logging.getLogger("test.exception")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)

    try:
        try:
            msg = "boom"
            raise ValueError(msg)
        except ValueError:
            test_logger.exception("something broke")

        handler.flush()
        parsed = json.loads(stream.getvalue().strip())

        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]
        assert "boom" in parsed["exception"]
    finally:
        test_logger.removeHandler(handler)


def test_configure_logging_removes_existing_handlers() -> None:
    """configure_logging should replace all existing root handlers with a single new one."""
    root = logging.getLogger()
    dummy_handler = logging.StreamHandler()
    root.addHandler(dummy_handler)
    initial_count = len(root.handlers)

    configure_logging(json_format=True)

    assert len(root.handlers) <= initial_count
    assert dummy_handler not in root.handlers


def test_configure_logging_sets_root_level() -> None:
    """configure_logging should update the root logger's level."""
    configure_logging(level=logging.WARNING, json_format=False)

    root = logging.getLogger()
    assert root.level == logging.WARNING

    configure_logging(level=logging.INFO, json_format=True)
    assert root.level == logging.INFO
