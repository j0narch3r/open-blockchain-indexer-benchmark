"""Core dataclasses (design doc §4.2), the versioned effort model, and the
error taxonomy.
"""

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from contour import constants, errors
from contour.types import (
    EffortModel,
    ElevationProfile,
    GradeSegment,
    ProfilePoint,
    RouteCandidate,
    ScoredRoute,
    SegmentStat,
    SteepSection,
    load_effort_model,
    validate_effort_model,
)

# ---------------------------------------------------------------------------
# §4.2 core dataclasses
# ---------------------------------------------------------------------------


def test_route_candidate_is_frozen_and_has_exact_fields() -> None:
    candidate = RouteCandidate(
        geometry=((-122.43, 37.77), (-122.42, 37.78)),
        engine_distance_m=1200.0,
        engine_duration_s=300.0,
        way_tags=({"highway": "residential"},),
        source="fixture",
        slope_weight=0.5,
    )
    assert candidate.geometry == ((-122.43, 37.77), (-122.42, 37.78))
    assert candidate.source == "fixture"
    assert dataclasses.is_dataclass(candidate)
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.source = "valhalla"  # type: ignore[misc]


def test_route_candidate_collections_reject_in_place_mutation() -> None:
    # frozen=True only blocks attribute *rebinding*; the collection fields
    # themselves must be immutable types (tuple, not list) so in-place
    # mutation is impossible too, not merely undocumented.
    candidate = RouteCandidate(
        geometry=((-122.43, 37.77), (-122.42, 37.78)),
        engine_distance_m=1200.0,
        engine_duration_s=300.0,
        way_tags=({"highway": "residential"},),
        source="fixture",
        slope_weight=0.5,
    )
    with pytest.raises(AttributeError):
        candidate.geometry.append((-122.41, 37.79))  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        candidate.geometry[0] = (-122.41, 37.79)  # type: ignore[index]
    with pytest.raises(AttributeError):
        candidate.way_tags.append({"highway": "primary"})  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        candidate.way_tags[0] = {"highway": "primary"}  # type: ignore[index]


def test_profile_point_is_frozen() -> None:
    point = ProfilePoint(dist_m=10.0, ele_m=42.5)
    assert point.dist_m == 10.0
    assert point.ele_m == 42.5
    with pytest.raises(dataclasses.FrozenInstanceError):
        point.ele_m = 0.0  # type: ignore[misc]


def test_elevation_profile_is_frozen_and_has_exact_fields() -> None:
    profile = ElevationProfile(
        points=(ProfilePoint(dist_m=0.0, ele_m=10.0),),
        ascent_m=5.0,
        descent_m=2.0,
        max_grade_pct=8.5,
    )
    assert profile.ascent_m == 5.0
    assert profile.descent_m == 2.0
    assert profile.max_grade_pct == 8.5
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.ascent_m = 0.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        profile.points.append(ProfilePoint(dist_m=10.0, ele_m=11.0))  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        profile.points[0] = ProfilePoint(dist_m=0.0, ele_m=0.0)  # type: ignore[index]


def test_grade_segment_field_is_klass_not_class() -> None:
    # `class` is a Python keyword, so the attribute is spelled `klass`;
    # serialization to the JSON field name "class" happens at the
    # pydantic boundary in a later task, not here.
    segment = GradeSegment(start_idx=0, end_idx=34, grade_pct=1.2, klass="flat")
    assert segment.klass == "flat"
    assert not hasattr(segment, "class")
    with pytest.raises(dataclasses.FrozenInstanceError):
        segment.klass = "steep"  # type: ignore[misc]


def test_steep_section_allows_optional_street() -> None:
    section = SteepSection(
        start_dist_m=1200.0,
        end_dist_m=1380.0,
        grade_pct=9.4,
        street="Duboce Ave",
        ascent_m=17.0,
    )
    assert section.street == "Duboce Ave"
    unnamed = SteepSection(
        start_dist_m=0.0, end_dist_m=10.0, grade_pct=10.0, street=None, ascent_m=1.0
    )
    assert unnamed.street is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        section.ascent_m = 0.0  # type: ignore[misc]


def test_scored_route_is_frozen_and_composes_the_others() -> None:
    candidate = RouteCandidate(
        geometry=((-122.43, 37.77), (-122.42, 37.78)),
        engine_distance_m=1200.0,
        engine_duration_s=300.0,
        way_tags=({"highway": "residential"},),
        source="fixture",
        slope_weight=0.5,
    )
    profile = ElevationProfile(
        points=(ProfilePoint(dist_m=0.0, ele_m=10.0),),
        ascent_m=5.0,
        descent_m=2.0,
        max_grade_pct=8.5,
    )
    scored = ScoredRoute(
        candidate=candidate,
        profile=profile,
        grade_segments=(GradeSegment(start_idx=0, end_idx=1, grade_pct=1.2, klass="flat"),),
        steep_sections=(),
        distance_m=1210.0,
        flat_equivalent_m=1710.0,
        effort_score=1710.0,
    )
    assert scored.candidate is candidate
    assert scored.profile is profile
    assert scored.distance_m == 1210.0
    assert scored.flat_equivalent_m == 1710.0
    assert scored.effort_score == 1710.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        scored.effort_score = 0.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        scored.grade_segments.append(  # type: ignore[attr-defined]
            GradeSegment(start_idx=1, end_idx=2, grade_pct=5.0, klass="gentle")
        )
    with pytest.raises(TypeError):
        scored.grade_segments[0] = GradeSegment(  # type: ignore[index]
            start_idx=0, end_idx=1, grade_pct=0.0, klass="flat"
        )


def test_segment_stat_is_frozen_and_has_exact_fields() -> None:
    stat = SegmentStat(ascent_m=1.0, descent_m=0.5, distance_m=100.0, grade_pct=1.0)
    assert stat.ascent_m == 1.0
    assert stat.descent_m == 0.5
    assert stat.distance_m == 100.0
    assert stat.grade_pct == 1.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        stat.grade_pct = 0.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EffortModel + load_effort_model
# ---------------------------------------------------------------------------


def test_effort_model_is_frozen_and_has_exact_fields() -> None:
    model = EffortModel(
        model_version="effort-v1",
        climb_equiv_ratio=100.0,
        k_table=((4.0, 0.0), (6.0, 0.3)),
        k_scale={1: 0.0, 2: 0.5},
        detour_budget={1: 1.05, 2: 1.15},
    )
    assert model.model_version == "effort-v1"
    with pytest.raises(dataclasses.FrozenInstanceError):
        model.model_version = "effort-v2"  # type: ignore[misc]


def test_load_effort_model_default_version() -> None:
    model = load_effort_model()
    assert model.model_version == "effort-v1"
    assert model.climb_equiv_ratio == constants.CLIMB_EQUIV_RATIO
    assert model.k_table == constants.GRADE_K_TABLE
    assert dict(model.k_scale) == dict(constants.K_SCALE)
    assert dict(model.detour_budget) == dict(constants.DETOUR_BUDGET)


def test_load_effort_model_unknown_version_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_effort_model(version="effort-v999")


def _valid_model() -> EffortModel:
    """A structurally valid, in-memory `EffortModel` for driving
    `validate_effort_model` invariant tests without touching the filesystem.
    """
    return EffortModel(
        model_version="effort-v1",
        climb_equiv_ratio=100.0,
        k_table=((4.0, 0.0), (6.0, 0.3), (9.0, 1.0)),
        k_scale={1: 0.0, 2: 0.5, 3: 1.0, 4: 2.0, 5: 4.0},
        detour_budget={1: 1.05, 2: 1.15, 3: 1.25, 4: 1.35, 5: 1.50},
    )


def test_validate_effort_model_accepts_a_valid_model() -> None:
    validate_effort_model(_valid_model())  # must not raise


def test_validate_effort_model_rejects_empty_model_version() -> None:
    model = dataclasses.replace(_valid_model(), model_version="")
    with pytest.raises(ValueError, match="model_version"):
        validate_effort_model(model)


def test_validate_effort_model_rejects_non_positive_climb_equiv_ratio() -> None:
    model = dataclasses.replace(_valid_model(), climb_equiv_ratio=0.0)
    with pytest.raises(ValueError, match="climb_equiv_ratio"):
        validate_effort_model(model)


def test_validate_effort_model_rejects_empty_k_table() -> None:
    model = dataclasses.replace(_valid_model(), k_table=())
    with pytest.raises(ValueError, match="k_table"):
        validate_effort_model(model)


def test_validate_effort_model_rejects_non_ascending_k_table() -> None:
    model = dataclasses.replace(_valid_model(), k_table=((9.0, 1.0), (4.0, 0.0)))
    with pytest.raises(ValueError, match="k_table"):
        validate_effort_model(model)


def test_validate_effort_model_rejects_non_strictly_ascending_k_table() -> None:
    # a repeated grade threshold is not "strictly" ascending
    model = dataclasses.replace(_valid_model(), k_table=((4.0, 0.0), (4.0, 0.3)))
    with pytest.raises(ValueError, match="k_table"):
        validate_effort_model(model)


def test_validate_effort_model_rejects_missing_k_scale_key() -> None:
    model = dataclasses.replace(_valid_model(), k_scale={1: 0.0, 2: 0.5, 3: 1.0, 4: 2.0})
    with pytest.raises(ValueError, match="k_scale"):
        validate_effort_model(model)


def test_validate_effort_model_rejects_extra_detour_budget_key() -> None:
    model = dataclasses.replace(
        _valid_model(),
        detour_budget={1: 1.05, 2: 1.15, 3: 1.25, 4: 1.35, 5: 1.50, 6: 1.60},
    )
    with pytest.raises(ValueError, match="detour_budget"):
        validate_effort_model(model)


def test_validate_effort_model_rejects_detour_budget_below_one() -> None:
    model = dataclasses.replace(
        _valid_model(),
        detour_budget={1: 0.95, 2: 1.15, 3: 1.25, 4: 1.35, 5: 1.50},
    )
    with pytest.raises(ValueError, match="detour_budget"):
        validate_effort_model(model)


def test_effort_v1_json_agrees_with_constants() -> None:
    """Two sources of truth (constants.py and effort-v1.json) must never
    silently diverge. This test proves the guard is live: it is designed
    to fail loudly if either copy of the k-table, k-scale, or detour
    budget is edited without the other.
    """
    json_path = Path(__file__).parent.parent / "contour" / "models" / "effort-v1.json"
    data = json.loads(json_path.read_text())

    assert data["model_version"] == "effort-v1"
    assert data["climb_equiv_ratio"] == constants.CLIMB_EQUIV_RATIO
    assert [tuple(row) for row in data["k_table"]] == list(constants.GRADE_K_TABLE)
    assert {int(k): v for k, v in data["k_scale"].items()} == dict(constants.K_SCALE)
    assert {int(k): v for k, v in data["detour_budget"].items()} == dict(constants.DETOUR_BUDGET)


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


def test_contour_error_base_carries_code_message_detail() -> None:
    err = errors.ContourError(code="SOMETHING", message="oops", detail={"x": 1})
    assert err.code == "SOMETHING"
    assert err.message == "oops"
    assert err.detail == {"x": 1}
    assert str(err) == "oops"


@pytest.mark.parametrize(
    ("exc_cls", "expected_code"),
    [
        (errors.OutOfServiceArea, "OUT_OF_SERVICE_AREA"),
        (errors.NoRouteFound, "NO_ROUTE_FOUND"),
        (errors.OriginUnsnappable, "ORIGIN_UNSNAPPABLE"),
        (errors.EngineUnavailable, "ENGINE_UNAVAILABLE"),
        (errors.InvalidRequest, "INVALID_REQUEST"),
    ],
)
def test_error_subclasses_hardcode_exact_codes(
    exc_cls: Callable[..., errors.ContourError], expected_code: str
) -> None:
    err = exc_cls(message="details here", detail={})
    assert err.code == expected_code
    assert isinstance(err, errors.ContourError)
