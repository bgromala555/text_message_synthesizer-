"""File I/O and persistence utilities for generated datasets.

Handles saving and loading device datasets, scenario manifests, quality
reports, and run logs.  All path construction is funnelled through
``sanitize_path_component`` to prevent directory-traversal attacks from
user-supplied scenario or device IDs.

Extracted from ``generator.py`` to isolate file-system side effects.
"""

import json
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from messageviewer.models import Actor, ConversationNode, Message, SmsDataset
from source.models import ScenarioConfig
from source.quality_models import QualityFinding, QualityReport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Directory constants
# ---------------------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
OUTPUT_DIR: Path = PROJECT_ROOT / "data" / "generated"
SCENARIOS_DIR: Path = PROJECT_ROOT / "data" / "scenarios"


# ---------------------------------------------------------------------------
# Security: path component sanitization
# ---------------------------------------------------------------------------


def sanitize_path_component(value: str) -> str:
    """Strip path separators and traversal characters from a path component.

    Prevents directory-traversal attacks when user-supplied IDs (scenario,
    device) are embedded in filenames.  Collapses runs of non-alphanumeric
    characters to a single underscore and strips leading/trailing underscores.

    Args:
        value: Raw user-supplied string intended for use in a filename.

    Returns:
        A safe filename fragment containing only ``[A-Za-z0-9_-]``.

    """
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (value or "").strip()).strip("_") or "unknown"


# ---------------------------------------------------------------------------
# Dataset serialization helpers
# ---------------------------------------------------------------------------


def _safe_label_slug(label: str) -> str:
    """Convert a device label into a safe filename slug.

    Args:
        label: Human-readable device label.

    Returns:
        Lowercased slug with non-alphanumeric characters replaced by underscores.

    """
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", (label or "").strip()).strip("_").lower()
    return cleaned or "device"


def parse_messages_schema_dataset(raw: dict[str, object]) -> SmsDataset:
    """Parse a dataset that uses top-level ``messages`` instead of ``nodes``.

    This supports compatibility with external files that follow the
    ``I_AM_PILGRIM``-style schema while still converting to the internal
    ``SmsDataset`` model for resume logic.

    Args:
        raw: Raw JSON dictionary loaded from disk.

    Returns:
        Parsed internal SmsDataset model.

    """
    raw_actors_value = raw.get("actors", [])
    raw_actors = raw_actors_value if isinstance(raw_actors_value, list) else []
    actors: list[Actor] = []
    for actor_raw in raw_actors:
        if not isinstance(actor_raw, dict):
            continue
        actor = actor_raw
        actor_id = str(actor.get("ActorId", actor.get("Id", "")))
        actor_name = str(actor.get("Name") or actor.get("DisplayName") or actor.get("FullName") or actor.get("GivenName") or actor_id)
        if actor_id:
            actors.append(Actor(ActorId=actor_id, Name=actor_name))

    nodes: list[ConversationNode] = []
    raw_messages_value = raw.get("messages", [])
    raw_messages = raw_messages_value if isinstance(raw_messages_value, list) else []
    for conv_raw in raw_messages:
        if not isinstance(conv_raw, dict):
            continue
        conv = conv_raw
        source = str(conv.get("source", ""))
        target_raw = conv.get("target")
        if isinstance(target_raw, list):
            targets = [str(t) for t in target_raw if t]
        elif isinstance(target_raw, str) and target_raw:
            targets = [target_raw]
        else:
            communicants = [str(c) for c in conv.get("communicants", []) if c]
            targets = [c for c in communicants if c != source]

        if not source:
            continue

        raw_message_content = conv.get("message_content", [])
        message_items = raw_message_content if isinstance(raw_message_content, list) else []
        message_content: list[Message] = []
        for msg_raw in message_items:
            if not isinstance(msg_raw, dict):
                continue
            message_content.append(
                Message(
                    SenderActorId=str(msg_raw.get("SenderActorId", "")),
                    Content=str(msg_raw.get("Content", "")),
                    TransferTime=str(msg_raw.get("TransferTime", "")),
                    Direction=str(msg_raw.get("Direction", "")),
                    ServiceName=str(msg_raw.get("ServiceName", "SMS")),
                )
            )

        nodes.append(
            ConversationNode(
                source=source,
                target=targets,
                type=str(conv.get("type", "SMS")),
                message_content=message_content,
            )
        )

    return SmsDataset(nodes=nodes, actors=actors)


def to_messages_schema_payload(dataset: SmsDataset) -> dict[str, object]:
    """Convert internal ``SmsDataset`` to external ``messages`` schema.

    Args:
        dataset: Internal dataset model with ``nodes`` and ``actors``.

    Returns:
        Dictionary payload containing top-level ``actors`` + ``messages``.

    """
    actor_lookup = {actor.ActorId: actor.Name for actor in dataset.actors}
    actors = [
        {
            "Id": actor.ActorId,
            "ActorId": actor.ActorId,
            "ActorIdScope": "global",
            "DisplayName": actor.Name,
            "FullName": actor.Name,
            "GivenName": actor.Name,
            "Name": actor.Name,
        }
        for actor in dataset.actors
    ]

    messages: list[dict[str, object]] = []
    for idx, node in enumerate(dataset.nodes, start=1):
        communicants = [node.source, *node.target]
        content = []
        for msg in node.message_content:
            recipient_value: str | list[str] = (node.target[0] if node.target else "") if len(node.target) <= 1 else list(node.target)

            msg_payload = msg.model_dump()
            msg_payload["RecipientActorIds"] = recipient_value
            content.append(msg_payload)

        start_date = node.message_content[0].TransferTime if node.message_content else ""
        end_date = node.message_content[-1].TransferTime if node.message_content else ""
        participant_name = actor_lookup.get(node.target[0], node.target[0]) if len(node.target) == 1 else "Group Conversation"

        messages.append(
            {
                "communicants": communicants,
                "source": node.source,
                "participant_name": participant_name,
                "start_date": start_date,
                "end_date": end_date,
                "message_count": len(node.message_content),
                "_record_type": "node",
                "id": f"conv_{idx:03d}",
                "target": node.target[0] if len(node.target) == 1 else list(node.target),
                "type": node.type,
                "message_content": content,
            }
        )

    return {"actors": actors, "messages": messages}


# ---------------------------------------------------------------------------
# Load / save device data
# ---------------------------------------------------------------------------


def load_existing_device_data(scenario_id: str, device_id: str) -> SmsDataset | None:
    """Attempt to load a previously generated device dataset for resume.

    Uses ``sanitize_path_component`` to prevent path-traversal from
    user-supplied IDs.

    Args:
        scenario_id: The scenario ID.
        device_id: The device ID.

    Returns:
        Parsed SmsDataset if the file exists and is valid, else None.

    """
    safe_scenario = sanitize_path_component(scenario_id)
    safe_device = sanitize_path_component(device_id)

    canonical_path = OUTPUT_DIR / f"{safe_scenario}_{safe_device}.json"
    path = canonical_path
    numbered_candidates = sorted(OUTPUT_DIR.glob(f"{safe_scenario}_{safe_device}_device*.json"))
    if numbered_candidates:
        path = numbered_candidates[-1]
    elif not path.exists():
        candidates = sorted(OUTPUT_DIR.glob(f"{safe_scenario}_*_{safe_device}.json"))
        if candidates:
            path = candidates[-1]
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if "nodes" in raw:
            return SmsDataset(**raw)
        if "messages" in raw:
            return parse_messages_schema_dataset(raw)
        return SmsDataset(**raw)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Could not load existing output %s — will regenerate: %s", path, exc)
        return None


def save_device_data(
    scenario_id: str,
    device_id: str,
    dataset: SmsDataset,
    device_label: str = "",
    device_number: int | None = None,
) -> Path:
    """Persist a device dataset to disk.

    Uses ``sanitize_path_component`` on all user-supplied ID segments
    before building file paths.

    Args:
        scenario_id: The scenario ID.
        device_id: The device ID.
        dataset: The dataset to save.
        device_label: Human-readable device label for friendly filename alias.
        device_number: Optional 1-based device index for explicit suffix naming.

    Returns:
        Path to the written file.

    """
    safe_scenario = sanitize_path_component(scenario_id)
    safe_device = sanitize_path_component(device_id)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = to_messages_schema_payload(dataset)
    canonical_path = OUTPUT_DIR / f"{safe_scenario}_{safe_device}.json"
    canonical_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    output_path = canonical_path
    if device_number is not None and device_number > 0:
        output_path = OUTPUT_DIR / f"{safe_scenario}_{safe_device}_device{device_number}.json"
        output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    elif device_label:
        friendly_path = OUTPUT_DIR / f"{safe_scenario}_{_safe_label_slug(device_label)}_{safe_device}.json"
        friendly_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        output_path = friendly_path
    return output_path


def persist_scenario_to_disk(scenario: ScenarioConfig) -> Path:
    """Write the scenario config to its JSON file in the scenarios directory.

    Called after the quality fix applies changes so they survive page
    reloads and server restarts.

    Args:
        scenario: The full scenario configuration to persist.

    Returns:
        Path to the written scenario JSON file.

    """
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = sanitize_path_component(scenario.id)
    file_path = SCENARIOS_DIR / f"{safe_id}.json"
    file_path.write_text(
        json.dumps(scenario.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Scenario persisted to %s after quality fix", file_path)
    return file_path


def save_quality_report(scenario_id: str, report: QualityReport) -> Path:
    """Persist the run-level quality report to disk.

    Args:
        scenario_id: The scenario ID for filename construction.
        report: The quality report to serialize.

    Returns:
        Path to the written report JSON file.

    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = sanitize_path_component(scenario_id)
    report_path = OUTPUT_DIR / f"{safe_id}_quality_report.json"
    report_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return report_path


def save_run_log(scenario_id: str, entries: list[dict[str, object]], started_at: float) -> Path:
    """Persist detailed generation run logs for post-run debugging.

    Args:
        scenario_id: The scenario ID for filename construction.
        entries: List of structured log entry dicts.
        started_at: Epoch timestamp when generation started.

    Returns:
        Path to the written run-log JSON file.

    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = sanitize_path_component(scenario_id)
    run_log_path = OUTPUT_DIR / f"{safe_id}_run_log.json"
    payload = {
        "scenario_id": scenario_id,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "started_at_epoch": started_at,
        "elapsed_seconds": round(time.time() - started_at, 3),
        "entry_count": len(entries),
        "entries": entries,
    }
    run_log_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return run_log_path


def _build_devices_summary(scenario: ScenarioConfig) -> list[dict[str, object]]:
    """Build the devices portion of the manifest.

    Args:
        scenario: Full scenario configuration.

    Returns:
        List of device summary dicts.

    """
    devices_summary: list[dict[str, object]] = []
    for dev in scenario.devices:
        contacts_info = []
        for c in dev.contacts:
            shared_with_labels = [
                other_dev.device_label
                for link in c.shared_with
                for other_dev in [next((d for d in scenario.devices if d.id == link.device_id), None)]
                if other_dev
            ]
            contacts_info.append(
                {
                    "name": c.name,
                    "role": c.role,
                    "actor_id": c.actor_id,
                    "message_volume": c.message_volume or "regular",
                    "has_personality": c.personality is not None,
                    "shared_across_devices": shared_with_labels,
                }
            )
        devices_summary.append(
            {
                "device_label": dev.device_label,
                "owner_name": dev.owner_name,
                "owner_actor_id": dev.owner_actor_id,
                "generation_mode": getattr(dev, "generation_mode", "story") or "story",
                "contact_count": len(dev.contacts),
                "contacts": contacts_info,
            }
        )
    return devices_summary


def _build_events_summary(scenario: ScenarioConfig) -> list[dict[str, object]]:
    """Build the events portion of the manifest.

    Args:
        scenario: Full scenario configuration.

    Returns:
        List of event summary dicts.

    """
    events_summary: list[dict[str, object]] = []
    for ev in scenario.timeline_events:
        participant_names: list[str] = []
        for p in ev.participants:
            p_dev = next((d for d in scenario.devices if d.id == p.device_id), None)
            if not p_dev:
                continue
            if p.contact_id == "__owner__":
                participant_names.append(f"{p_dev.owner_name} ({p_dev.device_label} owner)")
            else:
                p_contact = next((c for c in p_dev.contacts if c.id == p.contact_id), None)
                if p_contact:
                    participant_names.append(f"{p_contact.name} ({p_contact.role} on {p_dev.device_label})")
        events_summary.append(
            {
                "date": ev.date,
                "time": ev.time,
                "description": ev.description,
                "participants": participant_names,
                "device_impacts": ev.device_impacts,
            }
        )
    return events_summary


def _build_shared_pairs(scenario: ScenarioConfig) -> list[dict[str, str]]:
    """Build the shared-contacts portion of the manifest.

    Args:
        scenario: Full scenario configuration.

    Returns:
        List of shared-pair dicts.

    """
    shared_pairs: list[dict[str, str]] = []
    seen_pairs: set[str] = set()
    for dev in scenario.devices:
        for c in dev.contacts:
            for link in c.shared_with:
                pair_key = "::".join(sorted([c.id, link.contact_id]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                other_dev = next((d for d in scenario.devices if d.id == link.device_id), None)
                other_c = None
                if other_dev:
                    other_c = next((x for x in other_dev.contacts if x.id == link.contact_id), None)
                shared_pairs.append(
                    {
                        "name": c.name,
                        "device_a": dev.device_label,
                        "device_b": other_dev.device_label if other_dev else "?",
                        "role_on_a": c.role,
                        "role_on_b": other_c.role if other_c else "?",
                    }
                )
    return shared_pairs


def save_scenario_manifest(
    scenario: ScenarioConfig,
    output_dir: Path,
    elapsed_seconds: float,
    quality_summary: dict[str, object] | None = None,
) -> Path:
    """Save a manifest alongside generated data for cross-verification.

    The manifest contains everything needed to verify the generated messages
    match the scenario design: events with participants, shared contacts,
    cross-device links, locations, and generation settings.

    Args:
        scenario: The full scenario configuration.
        output_dir: Directory where generated data lives.
        elapsed_seconds: How long the generation took.
        quality_summary: Optional quality summary to include in the manifest.

    Returns:
        Path to the saved manifest file.

    """
    devices_summary = _build_devices_summary(scenario)
    events_summary = _build_events_summary(scenario)
    shared_pairs = _build_shared_pairs(scenario)

    character_arcs: dict[str, str] = {}
    for dev in scenario.devices:
        if dev.owner_story_arc:
            character_arcs[dev.owner_name or dev.device_label] = dev.owner_story_arc
        for c in dev.contacts:
            if c.story_arc:
                character_arcs[c.name or c.id] = c.story_arc

    manifest: dict[str, object] = {
        "scenario_id": scenario.id,
        "scenario_name": scenario.name,
        "theme": scenario.theme,
        "story_arc": scenario.story_arc,
        "character_arcs": character_arcs,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "generation_elapsed_seconds": round(elapsed_seconds, 1),
        "generation_settings": scenario.generation_settings.model_dump(),
        "devices": devices_summary,
        "shared_contacts": shared_pairs,
        "events": events_summary,
        "stats": {
            "total_devices": len(scenario.devices),
            "total_contacts": sum(len(d.contacts) for d in scenario.devices),
            "total_shared_pairs": len(shared_pairs),
            "total_events": len(scenario.timeline_events),
        },
    }
    if quality_summary:
        manifest["quality_summary"] = quality_summary

    safe_id = sanitize_path_component(scenario.id)
    manifest_path = output_dir / f"{safe_id}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved scenario manifest to %s", manifest_path)
    return manifest_path


def finding_to_sse(finding: QualityFinding) -> str:
    """Convert a quality finding to an SSE payload string.

    Args:
        finding: The quality finding to serialize.

    Returns:
        SSE-formatted ``data:`` line ready to yield from an event stream.

    """
    payload = {
        "type": "quality_warning",
        "check_id": finding.check_id.value,
        "severity": finding.severity.value,
        "score": round(finding.score, 3),
        "scope": finding.scope,
        "entity_id": finding.entity_id,
        "message": finding.message,
        "suggestion": finding.suggestion,
    }
    return f"data: {json.dumps(payload)}\n\n"
