"""FastAPI route handlers for the Synthesized Chat Generator pipeline.

Thin routing layer that exposes SSE-streaming generation, on-demand quality
checks, output file renaming, API status queries, and generation progress
inspection.  Heavy-lifting logic lives in :mod:`source.generation_pipeline`
(generation orchestration) and :mod:`source.quality_fix` (quality-check /
fix orchestration).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from source import persistence
from source.generation_pipeline import count_existing_conversations, run_pipeline
from source.quality_fix import execute_quality_check
from source.rate_limit import limiter

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class QualityCheckRequest(BaseModel):
    """Request body for on-demand scenario quality checks.

    Attributes:
        auto_adjust: When True, applies structural and AI-assisted fixes
            before re-evaluating quality.  When False, only reports the
            current quality state without modifying anything.

    """

    auto_adjust: bool = False


# ---------------------------------------------------------------------------
# POST /run — SSE generation endpoint
# ---------------------------------------------------------------------------


@router.post("/run")
@limiter.limit("5/minute")
async def run_generation(request: Request, resume: bool = False, override_checks: bool = False) -> StreamingResponse:
    """Run the full generation pipeline with SSE progress updates.

    Supports **resume mode**: when ``resume=True``, skips devices that
    already have complete output files and resumes mid-device from the
    last completed contact.  Each device file is saved incrementally
    after every contact, so crashes only lose the single in-flight
    conversation.

    If the API quota is exhausted mid-generation, the pipeline saves
    all completed work and reports the error through SSE instead of
    crashing.

    Args:
        request: The incoming HTTP request (used by the rate limiter).
        resume (bool): If True, skip already-completed devices/contacts.
        override_checks (bool): If True, skip the resume quality pre-check.

    Returns:
        Server-Sent Events stream with progress updates.

    """
    from source.app import get_scenario  # noqa: PLC0415 - deferred to avoid circular import with source.app

    scenario = get_scenario().model_copy(deep=True)

    return StreamingResponse(run_pipeline(scenario, resume, override_checks), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# POST /quality-check — on-demand quality check with optional auto-fix
# ---------------------------------------------------------------------------


@router.post("/quality-check")
@limiter.limit("5/minute")
async def run_quality_check(
    request: Request,
    req: QualityCheckRequest,
) -> JSONResponse:
    """Run an on-demand quality check on the current scenario.

    Evaluates the scenario configuration and any existing generated data
    against the full quality rubric.  When ``auto_adjust`` is True, delegates
    to specialized fix functions for structural repairs, AI-assisted
    personality and arc regeneration, timeline event-message consistency
    repair, and temporal sorting before re-evaluating.

    Returns before-and-after quality reports with a resolution writeup
    detailing every action taken.

    Args:
        request: The incoming HTTP request (used by the rate limiter).
        req (QualityCheckRequest): Request body controlling whether
            auto-adjustment is applied.

    Returns:
        JSON response with quality reports, adjustments summary, and
        readiness information.

    """
    from source.app import get_scenario, set_scenario  # noqa: PLC0415 - deferred to avoid circular import with source.app

    scenario = get_scenario()

    def _persist_scenario(sc: object) -> None:
        """Save the updated scenario to app state and disk.

        Args:
            sc: The scenario config to persist (typed as object to
                satisfy the generic callback signature).

        """
        from source.models import ScenarioConfig  # noqa: PLC0415

        if isinstance(sc, ScenarioConfig):
            set_scenario(sc)
            persistence.persist_scenario_to_disk(sc)

    result = await execute_quality_check(scenario, req.auto_adjust, _persist_scenario)
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# POST /refresh-output-names — rename outputs with friendly device labels
# ---------------------------------------------------------------------------


@router.post("/refresh-output-names")
async def refresh_output_names() -> JSONResponse:
    """Rewrite existing device outputs with friendly device-label filenames.

    Creates or refreshes files named like
    ``<scenario_id>_<device_label_slug>_<device_id>.json`` while preserving
    canonical ``<scenario_id>_<device_id>.json`` files.

    Returns:
        JSON response with counts and paths for refreshed and missing devices.

    """
    from source.app import get_scenario  # noqa: PLC0415 - deferred to avoid circular import with source.app

    scenario = get_scenario()
    refreshed: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []

    for device_idx, device in enumerate(scenario.devices, start=1):
        existing = persistence.load_existing_device_data(scenario.id, device.id)
        if not existing:
            missing.append({"device_id": device.id, "label": device.device_label or device.id})
            continue
        canonical_path = persistence.save_device_data(scenario.id, device.id, existing, device.device_label, device_idx)
        friendly_path = persistence.OUTPUT_DIR / f"{scenario.id}_{device.id}_device{device_idx}.json"
        refreshed.append(
            {
                "device_id": device.id,
                "label": device.device_label or device.id,
                "canonical_path": str(canonical_path),
                "friendly_path": str(friendly_path),
            }
        )

    return JSONResponse(
        content={
            "scenario_id": scenario.id,
            "refreshed_count": len(refreshed),
            "missing_count": len(missing),
            "refreshed": refreshed,
            "missing": missing,
        }
    )


# ---------------------------------------------------------------------------
# GET /status — API key availability check
# ---------------------------------------------------------------------------


@router.get("/status")
async def generation_status() -> JSONResponse:
    """Check whether an API key is configured for generation.

    Reads the ``OPENAI_API_KEY`` environment variable to determine
    availability.  No scenario data is accessed.

    Returns:
        JSON with ``api_available`` boolean and ``provider`` name.

    """
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    return JSONResponse(
        content={
            "api_available": bool(openai_key),
            "provider": "openai" if openai_key else "none",
        }
    )


# ---------------------------------------------------------------------------
# GET /progress — generation completion progress
# ---------------------------------------------------------------------------


@router.get("/progress")
async def generation_progress() -> JSONResponse:
    """Check which devices already have generated output for the current scenario.

    Used by the frontend to determine whether a Resume button should be
    shown.  For each device, reports whether output exists, how many
    conversation nodes are present, and whether the device appears
    complete (all contacts have conversations).

    Returns:
        JSON with a ``devices`` list and a ``has_partial`` boolean.

    """
    from source.app import get_scenario  # noqa: PLC0415 - deferred to avoid circular import with source.app

    scenario = get_scenario()
    # Kept as plain dicts since they are serialised directly into the JSON response
    device_progress: list[dict[str, object]] = []
    has_any_output = False
    all_complete = True

    for device in scenario.devices:
        existing = persistence.load_existing_device_data(scenario.id, device.id)
        if existing:
            has_any_output = True
            completed = count_existing_conversations(existing)
            all_contact_ids = {c.actor_id for c in device.contacts}
            real_completed = completed & all_contact_ids
            is_complete = all_contact_ids <= completed
            total_messages = sum(len(n.message_content) for n in existing.nodes)
            if not is_complete:
                all_complete = False
            device_progress.append(
                {
                    "device_id": device.id,
                    "label": device.device_label,
                    "has_output": True,
                    "complete": is_complete,
                    "contacts_done": len(real_completed),
                    "contacts_total": len(device.contacts),
                    "total_messages": total_messages,
                }
            )
        else:
            all_complete = False
            device_progress.append(
                {
                    "device_id": device.id,
                    "label": device.device_label,
                    "has_output": False,
                    "complete": False,
                    "contacts_done": 0,
                    "contacts_total": len(device.contacts),
                    "total_messages": 0,
                }
            )

    return JSONResponse(
        content={
            "devices": device_progress,
            "has_partial": has_any_output and not all_complete,
            "all_complete": has_any_output and all_complete,
        }
    )
