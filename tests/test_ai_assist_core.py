"""Tests for AI-assist prompt post-processing helpers."""

# ruff: noqa: S101

from __future__ import annotations

from source import ai_assist


def test_normalize_roles_in_standalone_mode_removes_plot_roles() -> None:
    """Standalone mode should force normal-life relationship roles."""
    roles = ["handler", "best friend", "detective", "coworker"]
    normalized = ai_assist._normalize_generated_roles(
        roles=roles,
        names_count=4,
        theme="espionage",
        generation_mode="standalone",
        role_style="story_heavy",
    )
    assert "handler" not in normalized
    assert "detective" not in normalized
    assert len(normalized) == 4


def test_build_roster_maps_owner_and_contacts_to_ids() -> None:
    """Roster mapping should include owner and contact references."""
    devices = [
        ai_assist.RosterDevice(
            device_id="dev-1",
            device_label="Device 1",
            owner_name="Alex",
            contacts=[
                ai_assist.RosterContact(contact_id="c-1", name="Sam", role="friend"),
                ai_assist.RosterContact(contact_id="c-2", name="Lee", role="coworker"),
            ],
        )
    ]

    roster_lines, number_to_ref, label_to_device_id = ai_assist._build_roster(devices)
    assert len(roster_lines) == 3
    assert number_to_ref[1] == {"device_id": "dev-1", "contact_id": "__owner__"}
    assert number_to_ref[2] == {"device_id": "dev-1", "contact_id": "c-1"}
    assert number_to_ref[3] == {"device_id": "dev-1", "contact_id": "c-2"}
    assert label_to_device_id["Device 1"] == "dev-1"
