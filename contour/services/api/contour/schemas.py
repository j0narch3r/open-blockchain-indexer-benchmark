"""Pydantic v2 request/response models for the `/v1` API (SPEC.md §6).

`RouteResponse` (and everything it composes) is the binding contract:
`tests/schema/route_response.schema.json` is hand-derived from the same
SPEC section and `tests/test_api_fixture.py` proves the two agree. Task 15
replaces `/v1/route`'s fixture body with real routing output but must not
change this shape; Task 18 generates the mobile client's TypeScript types
from the JSON Schema.

`GradeSegment.klass` (design doc / `contour.types`) serializes as the JSON
key `"class"` via a pydantic alias — `class` is a Python keyword, so the
attribute keeps the `types.py` spelling while the wire format matches
SPEC.md §6 exactly.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Request (POST /v1/route)
# ---------------------------------------------------------------------------


class Coordinate(BaseModel):
    """A `{lat, lon}` pair as it appears on the wire. Internal code converts
    to `(lon, lat)` `LonLat` tuples at the boundary — see Global Constraints
    "Coordinate order"."""

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RouteRequest(BaseModel):
    """`POST /v1/route` request body, matching SPEC.md §6 field-for-field
    plus `units` (task resolution #1: request-only, imperial by default;
    every numeric field in the response stays metric regardless — only the
    rendered `explanation` string honours it, per design doc §3.1)."""

    model_config = ConfigDict(extra="forbid")

    origin: Coordinate
    destination: Coordinate
    effort_preference: Annotated[int, Field(ge=1, le=5)]
    bike_profile: Literal["commuter", "road", "mtb"]
    avoid_stairs: bool
    max_alternatives: Annotated[int, Field(ge=1, le=5)]
    units: Literal["imperial", "metric"] = "imperial"


# ---------------------------------------------------------------------------
# Response (200 OK)
# ---------------------------------------------------------------------------


class GradeSegmentOut(BaseModel):
    """Mirrors `contour.types.GradeSegment` but serializes `klass` as the
    JSON key `"class"` (task resolution #2)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    start_idx: int = Field(ge=0)
    end_idx: int = Field(ge=0)
    grade_pct: float
    klass: Literal["flat", "gentle", "moderate", "steep", "wall"] = Field(alias="class")


class ProfilePointOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dist_m: float = Field(ge=0)
    ele_m: float


class SteepSectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_dist_m: float = Field(ge=0)
    end_dist_m: float = Field(ge=0)
    grade_pct: float
    street: str | None
    ascent_m: float


class ComparisonToFastest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delta_distance_m: float
    delta_duration_s: float
    delta_ascent_m: float


class RouteOut(BaseModel):
    """One entry of `routes[]` in the §6 response."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str
    label: Literal["Gentlest", "Balanced", "Fastest", "Only route"]
    geometry: str  # encoded polyline6
    distance_m: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    ascent_m: float = Field(ge=0)
    descent_m: float = Field(ge=0)
    max_grade_pct: float
    flat_equivalent_m: float = Field(ge=0)
    effort_score: float = Field(ge=0)
    grade_segments: list[GradeSegmentOut]
    elevation_profile: list[ProfilePointOut]
    steep_sections: list[SteepSectionOut]
    comparison_to_fastest: ComparisonToFastest
    explanation: str


class WarningOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["UNAVOIDABLE_CLIMB", "STEEP_SECTION_AHEAD", "LONG_DETOUR"]
    message: str
    detail: dict[str, Any]


class RouteResponse(BaseModel):
    """`POST /v1/route` 200 response body, SPEC.md §6."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: str
    model_version: str
    data_manifest: str
    routes: list[RouteOut]
    warnings: list[WarningOut]


# ---------------------------------------------------------------------------
# GET /v1/health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """`GET /v1/health` response. `engine_reachable`/`dem_readable` must be
    honest (task resolution #5) — this task wires nothing that could make
    them true, so they report `false`/`null`, not hardcoded successes."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    engine_reachable: bool
    dem_readable: bool | None
    manifest_hash: str | None
    model_version: str


# ---------------------------------------------------------------------------
# Error body (every non-2xx response)
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """The top-level error shape every `ContourError` maps to (task
    resolution #3) — never nested under FastAPI's default `"detail"`
    envelope."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    detail: dict[str, Any]


__all__ = [
    "Coordinate",
    "RouteRequest",
    "GradeSegmentOut",
    "ProfilePointOut",
    "SteepSectionOut",
    "ComparisonToFastest",
    "RouteOut",
    "WarningOut",
    "RouteResponse",
    "HealthResponse",
    "ErrorResponse",
]
