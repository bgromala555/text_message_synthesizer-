"""FastAPI application for the Synthesized Chat Generator.

Serves the single-page web UI and exposes REST endpoints for scenario
management, AI-assisted content generation, and the message generation
pipeline. The scenario state lives in server memory and is persisted to
a JSON file on disk when the user explicitly saves.

On startup the app loads any ``.env`` file in the project root so API
keys set there are available via ``os.environ`` without requiring the
user to export them manually in every terminal session.

Run with:
    uvicorn source.app:app --reload --port 8080
"""

import json
import logging
import mimetypes
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from source.ai_assist import router as ai_router
from source.generator import router as gen_router
from source.log_config import configure_logging
from source.models import (
    ConnectionLink,
    ContactSlot,
    DeviceScenario,
    FlexTimelineEvent,
    GenerationSettings,
    ScenarioConfig,
)
from source.persistence import sanitize_path_component
from source.rate_limit import limiter

# Windows registry often maps .js to text/plain (Windows Script Host),
# which causes browsers to refuse loading ES modules.  Force the correct
# type both via the mimetypes registry AND a StaticFiles subclass so it
# survives mimetypes.init() re-reads from the registry.
mimetypes.init()
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")

_JS_CONTENT_TYPES: dict[str, str] = {
    ".js": "application/javascript",
    ".mjs": "application/javascript",
}


class _PatchedStaticFiles(StaticFiles):
    """StaticFiles subclass that guarantees correct MIME types on Windows.

    Windows may map ``.js`` to ``text/plain`` in the registry, which prevents
    browsers from executing ES modules.  This subclass intercepts responses
    for known extensions and forces the correct ``Content-Type`` header.
    """

    def file_response(self, *args: object, **kwargs: object) -> Response:  # type: ignore[override]
        """Build a file response, patching Content-Type for JS files.

        Returns:
            The file response with a corrected Content-Type when needed.

        """
        response: Response = super().file_response(*args, **kwargs)  # type: ignore[arg-type]
        content_type = response.headers.get("content-type", "")
        if "text/plain" in content_type:
            path_str = str(getattr(response, "path", ""))
            suffix = Path(path_str).suffix.lower()
            if suffix in _JS_CONTENT_TYPES:
                response.headers["content-type"] = _JS_CONTENT_TYPES[suffix]
        return response


configure_logging(json_format=os.environ.get("LOG_JSON", "0") == "1")
logger = logging.getLogger(__name__)

SOURCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SOURCE_DIR.parent
SCENARIOS_DIR = PROJECT_ROOT / "data" / "scenarios"
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(dotenv_path=ENV_FILE, override=True)

app = FastAPI(title="Synthesized Chat Generator", version="0.1.0")

app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return a structured JSON error when a client exceeds the rate limit.

    Args:
        request: The incoming HTTP request that triggered the limit.
        exc: The rate-limit exception raised by slowapi.

    Returns:
        A 429 JSON response with an error message and the ``Retry-After``
        header indicating how long the client should wait.

    """
    retry_after = exc.detail or "Rate limit exceeded"
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limit_exceeded", "detail": str(retry_after)},
        headers={"Retry-After": str(getattr(exc, "retry_after", 60))},
    )


templates = Jinja2Templates(directory=str(SOURCE_DIR / "templates"))
app.mount("/static", _PatchedStaticFiles(directory=str(SOURCE_DIR / "static")), name="static")

app.include_router(ai_router, prefix="/api/ai", tags=["ai-assist"])
app.include_router(gen_router, prefix="/api/generate", tags=["generation"])

# In-memory scenario state stored on app.state for dependency injection
app.state.scenario = ScenarioConfig()

# Unique token regenerated every server start to bust browser caches
_CACHE_BUST: str = hex(int(time.time()))[2:]


def get_scenario_state(request: Request) -> ScenarioConfig:
    """FastAPI dependency that returns the current in-memory scenario.

    Intended for use with ``Depends(get_scenario_state)`` in route
    handlers that only need to read or mutate the existing scenario.

    Args:
        request: The incoming HTTP request providing access to app state.

    Returns:
        The active ScenarioConfig instance from ``app.state``.

    """
    return request.app.state.scenario


ScenarioDep = Annotated[ScenarioConfig, Depends(get_scenario_state)]


def get_scenario() -> ScenarioConfig:
    """Return the current in-memory scenario config.

    Backward-compatible module-level accessor used by modules that import
    this function via deferred imports (e.g. ``source.generator``).

    Returns:
        The active ScenarioConfig instance from ``app.state``.

    """
    return app.state.scenario


def set_scenario(scenario: ScenarioConfig) -> None:
    """Replace the current in-memory scenario config.

    Backward-compatible module-level mutator used by modules that import
    this function via deferred imports (e.g. ``source.generator``).

    Args:
        scenario: The new ScenarioConfig to use.

    """
    app.state.scenario = scenario


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Serve the main single-page application.

    Args:
        request: The incoming HTTP request.

    Returns:
        Rendered HTML page for the scenario builder UI.

    """
    return templates.TemplateResponse("index.html", {"request": request, "cache_bust": _CACHE_BUST})


# ---------------------------------------------------------------------------
# Scenario CRUD
# ---------------------------------------------------------------------------


@app.get("/api/scenario")
async def fetch_scenario(scenario: ScenarioDep) -> JSONResponse:
    """Return the full current scenario configuration.

    Args:
        scenario: The current scenario injected via dependency.

    Returns:
        JSON representation of the active ScenarioConfig.

    """
    return JSONResponse(content=scenario.model_dump())


@app.put("/api/scenario")
async def update_scenario(request: Request, config: ScenarioConfig) -> JSONResponse:
    """Replace the entire scenario configuration.

    Args:
        request: The incoming HTTP request providing access to app state.
        config: The new ScenarioConfig received from the frontend.

    Returns:
        JSON confirmation with the updated scenario.

    """
    request.app.state.scenario = config
    return JSONResponse(content={"status": "ok", "scenario": config.model_dump()})


@app.post("/api/scenario/new")
async def new_scenario(request: Request) -> JSONResponse:
    """Reset the in-memory scenario to a blank default state.

    Creates a fresh ScenarioConfig with a new unique ID, clearing all
    devices, contacts, story arcs, events, and connections.

    Args:
        request: The incoming HTTP request providing access to app state.

    Returns:
        JSON confirmation with the new empty scenario.

    """
    fresh = ScenarioConfig()
    request.app.state.scenario = fresh
    logger.info("New scenario created: %s", fresh.id)
    return JSONResponse(content={"status": "ok", "scenario": fresh.model_dump()})


@app.post("/api/scenario/save")
async def save_scenario(scenario: ScenarioDep) -> JSONResponse:
    """Persist the current scenario to a JSON file on disk.

    Creates the scenarios directory if it does not exist, then writes the
    full scenario config as formatted JSON.

    Args:
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation with the file path.

    """
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = sanitize_path_component(scenario.id)
    file_path = SCENARIOS_DIR / f"{safe_id}.json"
    file_path.write_text(
        json.dumps(scenario.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Scenario saved to %s", file_path)
    return JSONResponse(content={"status": "saved", "path": str(file_path)})


@app.post("/api/scenario/load/{scenario_id}")
async def load_scenario(scenario_id: str, request: Request) -> JSONResponse:
    """Load a scenario from disk by its ID.

    Args:
        scenario_id: The unique ID of the scenario file to load.
        request: The incoming HTTP request providing access to app state.

    Returns:
        JSON representation of the loaded scenario, or an error.

    """
    safe_id = sanitize_path_component(scenario_id)
    file_path = SCENARIOS_DIR / f"{safe_id}.json"
    if file_path.resolve().parent != SCENARIOS_DIR.resolve():
        return JSONResponse(status_code=400, content={"error": "Invalid scenario ID"})
    if not file_path.exists():
        return JSONResponse(status_code=404, content={"error": "Scenario not found"})
    raw = json.loads(file_path.read_text(encoding="utf-8"))
    loaded = ScenarioConfig.model_validate(raw)
    request.app.state.scenario = loaded
    return JSONResponse(content=loaded.model_dump())


class ScenarioListItem(BaseModel):
    """Summary of a saved scenario file for the file-browser listing.

    Attributes:
        id: Scenario identifier extracted from the file name.
        name: Display name stored inside the scenario JSON.
        device_count: Number of devices configured in the scenario.
        modified_date: ISO 8601 timestamp of the file's last modification.
        file_path: Absolute path to the JSON file on disk.

    """

    id: str
    name: str
    device_count: int
    modified_date: str
    file_path: str


@app.get("/api/scenario/list")
async def list_scenarios(request: Request) -> JSONResponse:
    """List all saved scenario JSON files in the data/scenarios directory.

    Scans the scenarios directory for ``*.json`` files, extracts minimal
    metadata from each (name, device count, modification date), and
    returns the list sorted by modification date descending so the most
    recently saved scenario appears first.

    Args:
        request: The incoming HTTP request (required for rate-limit
            middleware compatibility).

    Returns:
        A JSON response containing a ``scenarios`` list of
        :class:`ScenarioListItem` objects.

    """
    if not SCENARIOS_DIR.exists():
        return JSONResponse(content={"scenarios": []})

    items: list[dict[str, str | int]] = []
    for fp in SCENARIOS_DIR.glob("*.json"):
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
            mod_time = datetime.fromtimestamp(fp.stat().st_mtime, tz=UTC)
            items.append(
                ScenarioListItem(
                    id=fp.stem,
                    name=raw.get("name", "Untitled Scenario"),
                    device_count=len(raw.get("devices", [])),
                    modified_date=mod_time.isoformat(),
                    file_path=str(fp),
                ).model_dump()
            )
        except (json.JSONDecodeError, OSError):
            logger.warning("Skipping unreadable scenario file: %s", fp)
            continue

    items.sort(key=lambda x: x.get("modified_date", ""), reverse=True)
    return JSONResponse(content={"scenarios": items})


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------


class ApiKeyRequest(BaseModel):
    """Request body for setting an API key at runtime.

    Attributes:
        key: The OpenAI (or Anthropic) API key string.

    """

    key: str


@app.get("/api/apikey/status")
async def apikey_status() -> JSONResponse:
    """Check whether an OpenAI API key is currently available.

    Returns:
        JSON with ``available`` bool and a masked preview of the key.

    """
    raw_key = os.environ.get("OPENAI_API_KEY", "")
    if raw_key:
        masked = raw_key[:7] + "..." + raw_key[-4:]
        return JSONResponse(content={"available": True, "masked": masked})
    return JSONResponse(content={"available": False, "masked": ""})


@app.post("/api/apikey/set")
async def set_apikey(req: ApiKeyRequest) -> JSONResponse:
    """Set the OpenAI API key for the current server session.

    Also persists the key to the project ``.env`` file so it survives
    server restarts.  The key is written with simple ``KEY=VALUE`` syntax.

    Args:
        req: Request containing the API key.

    Returns:
        JSON confirmation.

    """
    os.environ["OPENAI_API_KEY"] = req.key

    # Persist to .env so it loads automatically next time
    persist_env_key("OPENAI_API_KEY", req.key)

    logger.info("OPENAI_API_KEY set via UI (persisted to .env)")
    return JSONResponse(content={"status": "ok"})


def persist_env_key(key: str, value: str) -> None:
    """Write or update a single key in the project ``.env`` file.

    If the key already exists in the file the line is replaced in place.
    Otherwise a new line is appended.

    Args:
        key: Environment variable name.
        value: The value to store.

    """
    lines: list[str] = []
    found = False

    if ENV_FILE.exists():
        for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if stripped.startswith((key + "=", key + " =")):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(raw_line)

    if not found:
        lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Device endpoints
# ---------------------------------------------------------------------------


@app.post("/api/devices")
async def add_device(device: DeviceScenario, scenario: ScenarioDep) -> JSONResponse:
    """Add a new device to the current scenario.

    Args:
        device: The DeviceScenario to add.
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation with the added device.

    """
    scenario.devices.append(device)
    return JSONResponse(content={"status": "ok", "device": device.model_dump()})


@app.put("/api/devices/{device_id}")
async def update_device(device_id: str, device: DeviceScenario, scenario: ScenarioDep) -> JSONResponse:
    """Update an existing device by its ID.

    Args:
        device_id: The unique ID of the device to update.
        device: The new DeviceScenario data.
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation, or 404 if the device was not found.

    """
    for i, d in enumerate(scenario.devices):
        if d.id == device_id:
            scenario.devices[i] = device
            return JSONResponse(content={"status": "ok", "device": device.model_dump()})
    return JSONResponse(status_code=404, content={"error": "Device not found"})


@app.delete("/api/devices/{device_id}")
async def delete_device(device_id: str, scenario: ScenarioDep) -> JSONResponse:
    """Remove a device from the scenario.

    Args:
        device_id: The unique ID of the device to remove.
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation of removal.

    """
    scenario.devices = [d for d in scenario.devices if d.id != device_id]
    return JSONResponse(content={"status": "ok"})


# ---------------------------------------------------------------------------
# Contact endpoints
# ---------------------------------------------------------------------------


@app.post("/api/devices/{device_id}/contacts")
async def add_contact(device_id: str, contact: ContactSlot, scenario: ScenarioDep) -> JSONResponse:
    """Add a contact to a specific device.

    Args:
        device_id: The device to add the contact to.
        contact: The ContactSlot to add.
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation with the added contact, or 404 if device not found.

    """
    for device in scenario.devices:
        if device.id == device_id:
            device.contacts.append(contact)
            return JSONResponse(content={"status": "ok", "contact": contact.model_dump()})
    return JSONResponse(status_code=404, content={"error": "Device not found"})


@app.put("/api/devices/{device_id}/contacts/{contact_id}")
async def update_contact(device_id: str, contact_id: str, contact: ContactSlot, scenario: ScenarioDep) -> JSONResponse:
    """Update an existing contact on a device.

    Args:
        device_id: The device containing the contact.
        contact_id: The unique ID of the contact to update.
        contact: The new ContactSlot data.
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation, or 404 if device or contact was not found.

    """
    for device in scenario.devices:
        if device.id == device_id:
            for i, c in enumerate(device.contacts):
                if c.id == contact_id:
                    device.contacts[i] = contact
                    return JSONResponse(content={"status": "ok", "contact": contact.model_dump()})
            return JSONResponse(status_code=404, content={"error": "Contact not found"})
    return JSONResponse(status_code=404, content={"error": "Device not found"})


@app.delete("/api/devices/{device_id}/contacts/{contact_id}")
async def delete_contact(device_id: str, contact_id: str, scenario: ScenarioDep) -> JSONResponse:
    """Remove a contact from a device.

    Args:
        device_id: The device containing the contact.
        contact_id: The unique ID of the contact to remove.
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation of removal.

    """
    for device in scenario.devices:
        if device.id == device_id:
            device.contacts = [c for c in device.contacts if c.id != contact_id]
            return JSONResponse(content={"status": "ok"})
    return JSONResponse(status_code=404, content={"error": "Device not found"})


# ---------------------------------------------------------------------------
# Connection endpoints
# ---------------------------------------------------------------------------


@app.post("/api/connections")
async def add_connection(link: ConnectionLink, scenario: ScenarioDep) -> JSONResponse:
    """Add a cross-device connection to the scenario.

    Args:
        link: The ConnectionLink to add.
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation with the added connection.

    """
    scenario.connections.append(link)
    return JSONResponse(content={"status": "ok", "connection": link.model_dump()})


@app.put("/api/connections/{connection_id}")
async def update_connection(connection_id: str, link: ConnectionLink, scenario: ScenarioDep) -> JSONResponse:
    """Update an existing cross-device connection.

    Args:
        connection_id: The unique ID of the connection to update.
        link: The new ConnectionLink data.
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation, or 404 if connection not found.

    """
    for i, conn in enumerate(scenario.connections):
        if conn.id == connection_id:
            scenario.connections[i] = link
            return JSONResponse(content={"status": "ok", "connection": link.model_dump()})
    return JSONResponse(status_code=404, content={"error": "Connection not found"})


@app.delete("/api/connections/{connection_id}")
async def delete_connection(connection_id: str, scenario: ScenarioDep) -> JSONResponse:
    """Remove a cross-device connection from the scenario.

    Args:
        connection_id: The unique ID of the connection to remove.
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation of removal.

    """
    scenario.connections = [c for c in scenario.connections if c.id != connection_id]
    return JSONResponse(content={"status": "ok"})


# ---------------------------------------------------------------------------
# Timeline event endpoints
# ---------------------------------------------------------------------------


@app.post("/api/events")
async def add_event(event: FlexTimelineEvent, scenario: ScenarioDep) -> JSONResponse:
    """Add a shared timeline event to the scenario.

    Accepts a raw dict and validates it as a FlexTimelineEvent before
    appending to the scenario.

    Args:
        event: Dictionary of event fields.
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation with the added event.

    """
    scenario.timeline_events.append(event)
    return JSONResponse(content={"status": "ok", "event": event.model_dump()})


@app.put("/api/events/{event_index}")
async def update_event(event_index: int, event: FlexTimelineEvent, scenario: ScenarioDep) -> JSONResponse:
    """Update a timeline event by its list index.

    Args:
        event_index: Zero-based index of the event to update.
        event: Dictionary of updated event fields.
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation, or 404 if index is out of range.

    """
    if 0 <= event_index < len(scenario.timeline_events):
        scenario.timeline_events[event_index] = event
        return JSONResponse(content={"status": "ok", "event": event.model_dump()})
    return JSONResponse(status_code=404, content={"error": "Event index out of range"})


@app.delete("/api/events/{event_index}")
async def delete_event(event_index: int, scenario: ScenarioDep) -> JSONResponse:
    """Remove a timeline event by its list index.

    Args:
        event_index: Zero-based index of the event to remove.
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation of removal.

    """
    if 0 <= event_index < len(scenario.timeline_events):
        scenario.timeline_events.pop(event_index)
        return JSONResponse(content={"status": "ok"})
    return JSONResponse(status_code=404, content={"error": "Event index out of range"})


# ---------------------------------------------------------------------------
# Generation settings
# ---------------------------------------------------------------------------


@app.put("/api/settings")
async def update_settings(settings: GenerationSettings, scenario: ScenarioDep) -> JSONResponse:
    """Update the generation settings for the current scenario.

    Args:
        settings: The new GenerationSettings.
        scenario: The current scenario injected via dependency.

    Returns:
        JSON confirmation with the updated settings.

    """
    scenario.generation_settings = settings
    return JSONResponse(content={"status": "ok", "settings": settings.model_dump()})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the Synthesized Chat Generator web server on port 8080."""
    uvicorn.run(app, host="127.0.0.1", port=8080)


if __name__ == "__main__":
    main()
