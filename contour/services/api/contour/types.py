"""Core types shared across Contour's modules (design doc §4.2) plus the
versioned effort model.

Every dataclass here is frozen: instances flow one-way from Stage A
(candidate generation) through Stage B (re-ranking) to the API response,
and nothing downstream should be able to mutate a value another module
already scored against.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

LonLat = tuple[float, float]  # (lon, lat) — GeoJSON order, always


# ---------------------------------------------------------------------------
# Stage A / Stage B pipeline types (design doc §4.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteCandidate:
    """Stage A output — one alternative route as returned by a routing engine."""

    geometry: list[LonLat]
    engine_distance_m: float
    engine_duration_s: float
    way_tags: list[dict[str, str]]  # parallel to geometry edges, len == len(geometry) - 1
    source: str  # "fixture" | "graphhopper" | "valhalla"
    slope_weight: float  # which Stage A weight produced this


@dataclass(frozen=True)
class ProfilePoint:
    dist_m: float
    ele_m: float


@dataclass(frozen=True)
class ElevationProfile:
    points: list[ProfilePoint]
    ascent_m: float
    descent_m: float
    max_grade_pct: float


@dataclass(frozen=True)
class GradeSegment:
    start_idx: int
    end_idx: int
    grade_pct: float
    # `class` is a Python keyword, so the attribute is spelled `klass`; the
    # JSON API field is "class" (see SPEC.md §6 example). Renaming at the
    # pydantic/serialization boundary is a later task's job, not this one's.
    klass: str  # flat | gentle | moderate | steep | wall  (serialized as "class")


@dataclass(frozen=True)
class SteepSection:
    start_dist_m: float
    end_dist_m: float
    grade_pct: float
    street: str | None
    ascent_m: float


@dataclass(frozen=True)
class ScoredRoute:
    """Stage B output — a `RouteCandidate` after re-ranking."""

    candidate: RouteCandidate
    profile: ElevationProfile
    grade_segments: list[GradeSegment]
    steep_sections: list[SteepSection]
    distance_m: float  # recomputed geodesic, not the engine's
    flat_equivalent_m: float
    effort_score: float


# ---------------------------------------------------------------------------
# effort.py support types (not in §4.2 itself, but referenced by its
# function signatures in design doc §4.3; defined here so downstream tasks
# do not have to invent them independently — see task-2 brief resolutions).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentStat:
    """Per-segment ascent/descent/distance/grade, the input unit for
    `effort.flat_equivalent_m` (Task 11/design doc §4.3)."""

    ascent_m: float
    descent_m: float
    distance_m: float
    grade_pct: float


@dataclass(frozen=True)
class EffortModel:
    """A versioned effort model, loaded from `contour/models/<version>.json`.

    `k_table` and `k_scale`/`detour_budget` mirror `constants.GRADE_K_TABLE`,
    `constants.K_SCALE`, and `constants.DETOUR_BUDGET` for the "effort-v1"
    version specifically — `constants.py` is the authoritative Python-side
    definition (see `docs/DECISIONS.md`); later model versions are free to
    diverge, that's the point of versioning.
    """

    model_version: str
    climb_equiv_ratio: float
    k_table: tuple[tuple[float, float], ...]
    k_scale: Mapping[int, float]
    detour_budget: Mapping[int, float]


_MODELS_DIR = Path(__file__).parent / "models"


def load_effort_model(version: str = "effort-v1") -> EffortModel:
    """Load and parse a versioned effort model from `contour/models/`.

    Raises `FileNotFoundError` if no JSON file exists for `version`. Does
    not validate the loaded values against `constants.py` — that agreement
    is checked by a test for the "effort-v1" model specifically, since
    later model versions are expected to diverge from the v1 defaults.
    """
    path = _MODELS_DIR / f"{version}.json"
    if not path.exists():
        raise FileNotFoundError(f"no effort model found for version {version!r} at {path}")

    raw: dict[str, Any] = json.loads(path.read_text())

    k_table = tuple((float(grade), float(k)) for grade, k in raw["k_table"])
    k_scale = MappingProxyType({int(detent): float(v) for detent, v in raw["k_scale"].items()})
    detour_budget = MappingProxyType(
        {int(detent): float(v) for detent, v in raw["detour_budget"].items()}
    )

    return EffortModel(
        model_version=str(raw["model_version"]),
        climb_equiv_ratio=float(raw["climb_equiv_ratio"]),
        k_table=k_table,
        k_scale=k_scale,
        detour_budget=detour_budget,
    )


__all__ = [
    "LonLat",
    "RouteCandidate",
    "ProfilePoint",
    "ElevationProfile",
    "GradeSegment",
    "SteepSection",
    "ScoredRoute",
    "SegmentStat",
    "EffortModel",
    "load_effort_model",
]
