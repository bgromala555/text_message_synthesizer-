"""Tests for core FastAPI scenario endpoints."""

# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from source import app as app_module
from source.models import FlexTimelineEvent, ScenarioConfig


def test_add_and_update_event_endpoint_roundtrip() -> None:
    """Add then update an event using typed endpoint contracts."""
    app_module.app.state.scenario = ScenarioConfig()
    client = TestClient(app_module.app)

    event_payload = {
        "id": "ev-1",
        "date": "2025-05-01",
        "time": "10:30",
        "description": "Coffee meetup in Midtown",
        "encounter_type": "planned",
        "device_impacts": {"d1": "Owner chats about the meetup"},
        "involved_contacts": {"d1": ["c1"]},
        "participants": [{"device_id": "d1", "contact_id": "c1"}],
    }
    add_response = client.post("/api/events", json=event_payload)
    assert add_response.status_code == 200
    assert add_response.json()["event"]["description"] == "Coffee meetup in Midtown"

    updated_payload = {**event_payload, "description": "Coffee meetup moved to afternoon"}
    update_response = client.put("/api/events/0", json=updated_payload)
    assert update_response.status_code == 200
    assert update_response.json()["event"]["description"] == "Coffee meetup moved to afternoon"


def test_persist_env_key_replaces_existing_key(tmp_path: Path) -> None:
    """Update an existing key without duplicating lines."""
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=old-key\nANOTHER=value\n", encoding="utf-8")

    original_env_file = app_module.ENV_FILE
    app_module.ENV_FILE = env_file
    try:
        app_module.persist_env_key("OPENAI_API_KEY", "new-key")
    finally:
        app_module.ENV_FILE = original_env_file

    persisted = env_file.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=new-key" in persisted
    assert "OPENAI_API_KEY=old-key" not in persisted
    assert "ANOTHER=value" in persisted
    assert persisted.count("OPENAI_API_KEY=") == 1


def test_event_model_validation_on_endpoint_payload() -> None:
    """Ensure endpoint payload maps to FlexTimelineEvent shape."""
    payload = {
        "id": "ev-typed",
        "date": "2025-08-01",
        "description": "Concert night",
    }
    validated = FlexTimelineEvent.model_validate(payload)
    assert validated.id == "ev-typed"
    assert validated.description == "Concert night"
