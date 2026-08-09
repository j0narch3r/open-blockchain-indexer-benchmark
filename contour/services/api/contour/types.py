"""Core types shared across Contour's modules (design doc §4.2) plus the
versioned effort model.

Every dataclass here is frozen, and every collection-typed field is a
`tuple` (never `list`) or a `Mapping` (never `dict`): instances flow
one-way from Stage A (candidate generation) through Stage B (re-ranking)
to the API response, and `@dataclass(frozen=True)` alone only blocks
attribute *rebinding* — a `list` field would stay mutable in place, so a
downstream module could reorder a candidate's geometry after another
module had already scored it, and nothing would raise. Using `tuple`
makes that a real `AttributeError`/`TypeError` at the call site, not just
a documented intention. See design doc §4.2 (fixed in commit `d7ba35b`
after the Task 2 review caught the original `list[...]` spec).
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

    geometry: tuple[LonLat, ...]
    engine_distance_m: float
    engine_duration_s: float
    way_tags: tuple[Mapping[str, str], ...]  # parallel to edges, len == len(geometry) - 1
    source: str  # "fixture" | "graphhopper" | "valhalla"
    slope_weight: float  # which Stage A weight produced this


@dataclass(frozen=True)
class ProfilePoint:
    dist_m: float
    ele_m: float


@dataclass(frozen=True)
class ElevationProfile:
    points: tuple[ProfilePoint, ...]
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
    grade_segments: tuple[GradeSegment, ...]
    steep_sections: tuple[SteepSection, ...]
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

# The five slider detents (SPEC.md §3.4) — every model version, whatever its
# k-table/climb_equiv_ratio values, must key its per-detent maps by exactly
# these integers.
_REQUIRED_DETENTS = frozenset({1, 2, 3, 4, 5})


def validate_effort_model(model: EffortModel) -> None:
    """Check invariants that must hold for *any* effort model version.

    This deliberately does not check value-equality against `constants.py`
    — that agreement is a property of the "effort-v1" model specifically
    (checked by a test), not a structural requirement every future model
    version must satisfy. What must always hold, regardless of version:

    - `model_version` is a non-empty string
    - `climb_equiv_ratio` is positive
    - `k_table` is non-empty and sorted strictly ascending by grade
    - `k_scale` and `detour_budget` each have exactly the keys {1..5}
    - every `detour_budget` value is >= 1.0 (a budget below 1.0 would
      reject the fastest route against itself in the ranker's budget
      filter, design doc §4.6 step 3)

    Raises `ValueError` naming the offending field on the first violation
    found.
    """
    if not model.model_version:
        raise ValueError("EffortModel.model_version must be a non-empty string")

    if model.climb_equiv_ratio <= 0:
        raise ValueError(
            f"EffortModel.climb_equiv_ratio must be > 0, got {model.climb_equiv_ratio!r}"
        )

    if not model.k_table:
        raise ValueError("EffortModel.k_table must be non-empty")
    grades = [grade for grade, _k in model.k_table]
    if any(grades[i] <= grades[i - 1] for i in range(1, len(grades))):
        raise ValueError(
            f"EffortModel.k_table must be sorted strictly ascending by grade, got {grades!r}"
        )

    k_scale_keys = set(model.k_scale.keys())
    if k_scale_keys != _REQUIRED_DETENTS:
        raise ValueError(
            f"EffortModel.k_scale must have exactly keys {sorted(_REQUIRED_DETENTS)}, "
            f"got {sorted(k_scale_keys)!r}"
        )

    detour_budget_keys = set(model.detour_budget.keys())
    if detour_budget_keys != _REQUIRED_DETENTS:
        raise ValueError(
            f"EffortModel.detour_budget must have exactly keys {sorted(_REQUIRED_DETENTS)}, "
            f"got {sorted(detour_budget_keys)!r}"
        )

    for detent, budget in model.detour_budget.items():
        if budget < 1.0:
            raise ValueError(f"EffortModel.detour_budget[{detent}] must be >= 1.0, got {budget!r}")


def load_effort_model(version: str = "effort-v1") -> EffortModel:
    """Load, parse, and structurally validate a versioned effort model from
    `contour/models/`.

    Raises `FileNotFoundError` if no JSON file exists for `version`, or
    `ValueError` if the loaded model fails `validate_effort_model`'s
    invariants (see that function for exactly what is checked). Does not
    validate the loaded *values* against `constants.py` — that agreement
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

    model = EffortModel(
        model_version=str(raw["model_version"]),
        climb_equiv_ratio=float(raw["climb_equiv_ratio"]),
        k_table=k_table,
        k_scale=k_scale,
        detour_budget=detour_budget,
    )
    validate_effort_model(model)
    return model


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
    "validate_effort_model",
]
