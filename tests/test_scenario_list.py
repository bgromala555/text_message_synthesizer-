"""Tests for the GET /api/scenario/list endpoint."""

# ruff: noqa: S101

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from source import app as app_module


def _write_scenario(directory: Path, scenario_id: str, name: str, device_count: int = 1) -> Path:
    """Write a minimal scenario JSON file to a directory.

    Args:
        directory: Target directory for the JSON file.
        scenario_id: Scenario identifier used as the filename stem.
        name: Display name stored inside the scenario JSON.
        device_count: Number of device placeholder entries.

    Returns:
        Path to the written JSON file.

    """
    data = {
        "id": scenario_id,
        "name": name,
        "devices": [{"id": f"d{i}"} for i in range(device_count)],
    }
    fp = directory / f"{scenario_id}.json"
    fp.write_text(json.dumps(data), encoding="utf-8")
    return fp


def test_list_scenarios_returns_saved_scenarios(tmp_path: Path) -> None:
    """Endpoint should list scenario files with id, name, device_count, and modified_date."""
    _write_scenario(tmp_path, "scen-1", "First Scenario", device_count=2)
    _write_scenario(tmp_path, "scen-2", "Second Scenario", device_count=3)

    with patch.object(app_module, "SCENARIOS_DIR", tmp_path):
        client = TestClient(app_module.app)
        response = client.get("/api/scenario/list")

    assert response.status_code == 200
    body = response.json()
    assert "scenarios" in body
    assert len(body["scenarios"]) == 2

    ids = {s["id"] for s in body["scenarios"]}
    assert "scen-1" in ids
    assert "scen-2" in ids

    for scenario in body["scenarios"]:
        assert "name" in scenario
        assert "device_count" in scenario
        assert "modified_date" in scenario
        assert "file_path" in scenario


def test_list_scenarios_returns_empty_when_no_directory(tmp_path: Path) -> None:
    """Endpoint should return an empty list when the scenarios directory does not exist."""
    nonexistent = tmp_path / "missing_dir"

    with patch.object(app_module, "SCENARIOS_DIR", nonexistent):
        client = TestClient(app_module.app)
        response = client.get("/api/scenario/list")

    assert response.status_code == 200
    assert response.json()["scenarios"] == []


def test_list_scenarios_returns_empty_for_empty_directory(tmp_path: Path) -> None:
    """Endpoint should return an empty list when the directory exists but has no JSON files."""
    with patch.object(app_module, "SCENARIOS_DIR", tmp_path):
        client = TestClient(app_module.app)
        response = client.get("/api/scenario/list")

    assert response.status_code == 200
    assert response.json()["scenarios"] == []


def test_list_scenarios_skips_malformed_json(tmp_path: Path) -> None:
    """Malformed JSON files should be skipped without crashing."""
    _write_scenario(tmp_path, "good-one", "Good Scenario")
    bad_file = tmp_path / "bad-one.json"
    bad_file.write_text("{not valid json", encoding="utf-8")

    with patch.object(app_module, "SCENARIOS_DIR", tmp_path):
        client = TestClient(app_module.app)
        response = client.get("/api/scenario/list")

    assert response.status_code == 200
    scenarios = response.json()["scenarios"]
    assert len(scenarios) == 1
    assert scenarios[0]["id"] == "good-one"


def test_list_scenarios_sorted_by_modified_date_descending(tmp_path: Path) -> None:
    """Scenarios should be returned sorted by modification date, newest first."""
    fp1 = _write_scenario(tmp_path, "older", "Older Scenario")
    fp2 = _write_scenario(tmp_path, "newer", "Newer Scenario")

    os.utime(fp1, (1000000, 1000000))
    os.utime(fp2, (2000000, 2000000))

    with patch.object(app_module, "SCENARIOS_DIR", tmp_path):
        client = TestClient(app_module.app)
        response = client.get("/api/scenario/list")

    scenarios = response.json()["scenarios"]
    assert scenarios[0]["id"] == "newer"
    assert scenarios[1]["id"] == "older"


def test_list_scenarios_uses_untitled_for_missing_name(tmp_path: Path) -> None:
    """Scenario with no name field should default to 'Untitled Scenario'."""
    fp = tmp_path / "nameless.json"
    fp.write_text(json.dumps({"id": "nameless", "devices": []}), encoding="utf-8")

    with patch.object(app_module, "SCENARIOS_DIR", tmp_path):
        client = TestClient(app_module.app)
        response = client.get("/api/scenario/list")

    scenarios = response.json()["scenarios"]
    assert scenarios[0]["name"] == "Untitled Scenario"
