"""FastAPI service — SPEC.md §6 API contract.

`/v1/health` reports honest (not hardcoded) reachability of dependencies
that this task does not wire up. `/v1/route` validates the bbox and, for
now, returns the committed fixture (`contour/fixtures/route_fixture.json`,
loaded as *package data*) unchanged — Task 15 replaces the fixture body
with real routing but must not change `RouteResponse`'s shape.
"""

import json
from importlib import resources
from typing import Any, Final, Literal

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from contour import constants
from contour.errors import ContourError, OutOfServiceArea
from contour.schemas import Coordinate, HealthResponse, RouteRequest, RouteResponse
from contour.types import load_effort_model

app = FastAPI(title="Contour API")

# Package data, not a filesystem path relative to `__file__` — that breaks
# under a zipped/wheel install. `importlib.resources` works regardless of
# how `contour` is packaged. Single copy: `tests/` reads the same resource
# (see `tests/conftest.py`) so the API and its tests can never drift apart.
FIXTURE_RESOURCE: Final = resources.files("contour") / "fixtures" / "route_fixture.json"

# Task resolution #3 (task-3-brief.md): OUT_OF_SERVICE_AREA / INVALID_REQUEST /
# NO_ROUTE_FOUND / ORIGIN_UNSNAPPABLE -> 422; ENGINE_UNAVAILABLE -> 503.
_ERROR_STATUS: Final[dict[str, int]] = {
    "OUT_OF_SERVICE_AREA": 422,
    "INVALID_REQUEST": 422,
    "NO_ROUTE_FOUND": 422,
    "ORIGIN_UNSNAPPABLE": 422,
    "ENGINE_UNAVAILABLE": 503,
}


@app.exception_handler(ContourError)
async def contour_error_handler(request: Request, exc: ContourError) -> JSONResponse:
    """Maps every `ContourError` to the flat `{code, message, detail}` body
    (task resolution #3) — never FastAPI's default `{"detail": ...}` envelope."""
    status_code = _ERROR_STATUS.get(exc.code, 422)
    return JSONResponse(
        status_code=status_code,
        content={"code": exc.code, "message": exc.message, "detail": dict(exc.detail)},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Body/query validation failures (malformed requests that never reach a
    handler) get the same flat shape as `ContourError`, tagged
    `INVALID_REQUEST`, instead of FastAPI's default envelope."""
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(
            {
                "code": "INVALID_REQUEST",
                "message": "Request failed validation.",
                "detail": {"errors": exc.errors()},
            }
        ),
    )


def _in_service_area(coord: Coordinate) -> bool:
    """SPEC.md §4.2 bounding box check. `SF_BBOX` is `(W, S, E, N)`."""
    west, south, east, north = constants.SF_BBOX
    return west <= coord.lon <= east and south <= coord.lat <= north


def _load_fixture_response() -> RouteResponse:
    raw: dict[str, Any] = json.loads(FIXTURE_RESOURCE.read_text())
    return RouteResponse.model_validate(raw)


@app.get("/v1/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Nothing in this task wires up the routing engine or a real DEM, so
    `engine_reachable`/`dem_readable`/`manifest_hash` are honest negatives,
    not hardcoded successes (task resolution #5)."""
    model = load_effort_model()
    engine_reachable = False
    dem_readable: bool | None = None
    manifest_hash: str | None = None
    status: Literal["ok", "degraded"] = "ok" if engine_reachable and dem_readable else "degraded"
    return HealthResponse(
        status=status,
        engine_reachable=engine_reachable,
        dem_readable=dem_readable,
        manifest_hash=manifest_hash,
        model_version=model.model_version,
    )


@app.post("/v1/route", response_model=RouteResponse)
def post_route(payload: RouteRequest) -> RouteResponse:
    """Validates the bbox, then returns the committed fixture unchanged.
    Task 15 replaces the fixture body with real Stage A/B routing."""
    if not _in_service_area(payload.origin) or not _in_service_area(payload.destination):
        raise OutOfServiceArea(
            "Origin or destination is outside the San Francisco service area.",
            detail={"bbox": list(constants.SF_BBOX)},
        )
    return _load_fixture_response()


__all__ = ["app"]
