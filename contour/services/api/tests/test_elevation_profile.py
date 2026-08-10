"""Tests for `contour.elevation`'s Task 6 additions — smoothing, ascent
hysteresis, windowed grade, and profile assembly (design doc §4.4,
task-6-brief.md).

Two of these tests (`test_holdout_landmarks_within_tolerance`,
`test_estimated_holdouts_reported_not_gated`) sample the real committed
fixture DEM through `DemSampler` (Task 5's bilinear sampler), not through
the DEM generator's own nearest-neighbour holdout gate in
`tests/test_fixture_dem.py`. They validate the *sampling machinery* this
task builds on, not the DEM's accuracy — see design doc §2.3 and the
brief's own note. The tolerance values (`HOLDOUT_TOLERANCE_M`,
`SUMMIT_HOLDOUT_TOLERANCE_M`) are imported from the DEM generator rather
than duplicated as literals, so this file cannot silently drift from the
measured ruling in `docs/DECISIONS.md` ("Task 4, fix round 3").
"""

import json
import sys
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from contour.constants import (
    GRADE_WINDOW_SAMPLES,
    MIN_RISE_M,
    PROFILE_SAMPLE_M,
    SMOOTH_WINDOW_SAMPLES,
)
from contour.elevation import DemSampler, accumulate_relief, build_profile, smooth, windowed_grades
from contour.types import ElevationProfile, LonLat, ProfilePoint

_CONTOUR_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_DEM_PATH = _CONTOUR_ROOT / "data" / "dem" / "sf_fixture_dem.tif"
_FIXTURE_GRAPH_PATH = _CONTOUR_ROOT / "data" / "fixtures" / "sf_graph.geojson"

_SCRIPTS_DIR = _CONTOUR_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from make_fixture_dem import (  # noqa: E402
    DEFAULT_CSV_PATH,
    HOLDOUT_TOLERANCE_M,
    SUMMIT_HOLDOUT_TOLERANCE_M,
    load_control_points,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sampler() -> DemSampler:
    assert _FIXTURE_DEM_PATH.exists(), (
        f"fixture DEM not found at {_FIXTURE_DEM_PATH}; run `make fixtures`"
    )
    return DemSampler(_FIXTURE_DEM_PATH)


# ---------------------------------------------------------------------------
# Step 1 (brief, verbatim): the two known ascent-hysteresis failure modes
# ---------------------------------------------------------------------------


def test_ascent_ignores_subthreshold_noise() -> None:
    # 100 samples oscillating +-0.4 m around 50 m: real ascent is 0
    ele = 50.0 + 0.4 * np.array([1 if i % 2 else -1 for i in range(100)])
    ascent, descent = accumulate_relief(ele, MIN_RISE_M)
    assert ascent == pytest.approx(0.0, abs=0.5)
    assert descent == pytest.approx(0.0, abs=0.5)


def test_ascent_accumulates_long_shallow_climb() -> None:
    # 40 steps of +0.9 m each: real ascent is 36 m, NOT 0
    ele = np.cumsum(np.full(41, 0.9)) + 10.0
    ascent, _ = accumulate_relief(ele, MIN_RISE_M)
    assert ascent == pytest.approx(36.0, abs=1.0)


def test_descent_is_reported_separately_and_never_credits() -> None:
    ele = np.concatenate([np.linspace(0, 50, 51), np.linspace(50, 0, 51)])
    ascent, descent = accumulate_relief(ele, MIN_RISE_M)
    assert ascent == pytest.approx(50, abs=1.0)
    assert descent == pytest.approx(50, abs=1.0)


def test_smoothing_preserves_peak_amplitude() -> None:
    """A 20 m crest over 300 m (a raised-cosine hill, 31 samples at the
    profile's 10 m spacing) must not lose more than 10% of its height."""
    n = 31  # 0..300 m at 10 m spacing
    x = np.arange(n) * PROFILE_SAMPLE_M
    amplitude = 20.0
    ele = amplitude * 0.5 * (1.0 + np.cos(np.pi * (x - 150.0) / 150.0))
    assert ele.max() == pytest.approx(amplitude, abs=1e-9)

    smoothed = smooth(ele)
    peak_after = float(smoothed.max())
    assert peak_after >= 0.9 * amplitude


def test_holdout_landmarks_within_tolerance(sampler: DemSampler) -> None:
    """GATING tier: only holdouts with an INDEPENDENT published source —
    Twin Peaks, Alamo Square, Corona Heights, Mission Dolores Park, Fort
    Mason, Ferry Building, Candlestick Point.

    NOTE: this validates the sampling machinery (bilinear `DemSampler`
    against the committed fixture DEM), not the DEM itself. The real
    accuracy gate is re-running against 3DEP; see design doc §2.3.

    Tolerance: the brief's stated ±12 m predates Task 4's own measured
    revision (docs/DECISIONS.md "Task 4, fix round 3" / design doc §2.3):
    a ±12 m gate sits *below* the interpolator's own median LOO error
    (13.80 m) and cannot be met by any parameter choice. This test uses
    the superseding, measured tolerances instead (±25 m non-summit, ±40 m
    summit — imported from the DEM generator, not duplicated), which is
    the same ruling `tests/test_fixture_dem.py` already gates on. See
    `docs/DECISIONS.md` (this task's entry) for the full reasoning.
    """
    gated_names = {
        "Twin Peaks summit (Eureka Peak)",
        "Alamo Square",
        "Corona Heights summit",
        "Mission Dolores Park",
        "Fort Mason",
        "Ferry Building",
        "Candlestick Point",
    }
    summit_names = {"Twin Peaks summit (Eureka Peak)", "Corona Heights summit"}

    points = load_control_points(DEFAULT_CSV_PATH)
    holdouts = {p.name: p for p in points if p.role == "holdout"}
    assert gated_names <= holdouts.keys()

    for name in sorted(gated_names):
        p = holdouts[name]
        sampled = float(sampler.sample([(p.lon, p.lat)])[0])
        tolerance = SUMMIT_HOLDOUT_TOLERANCE_M if name in summit_names else HOLDOUT_TOLERANCE_M
        assert sampled == pytest.approx(p.ele_m, abs=tolerance), (
            f"{name}: sampled {sampled:.1f} m vs actual {p.ele_m:.1f} m (tolerance ±{tolerance} m)"
        )


def test_estimated_holdouts_reported_not_gated(
    sampler: DemSampler, capsys: pytest.CaptureFixture[str]
) -> None:
    """NON-GATING tier: Ocean Beach (at Judah) and Lands End were
    themselves "estimated from surrounding terrain" (see the control-point
    CSV's own `source` field). Asserting that interpolation recovers them
    would be circular — it would test the interpolator against its own
    assumption, not against the world. Print the deltas for a human to
    eyeball; assert nothing but that sampling returns a finite value.
    """
    estimated_names = {"Ocean Beach (at Judah)", "Lands End"}
    points = load_control_points(DEFAULT_CSV_PATH)
    holdouts = {p.name: p for p in points if p.role == "holdout"}
    assert estimated_names <= holdouts.keys()

    for name in sorted(estimated_names):
        p = holdouts[name]
        sampled = float(sampler.sample([(p.lon, p.lat)])[0])
        assert np.isfinite(sampled)
        print(f"{name}: sampled {sampled:.1f} m vs estimated {p.ele_m:.1f} m (not gated)")

    captured = capsys.readouterr()
    for name in estimated_names:
        assert name in captured.out


# ---------------------------------------------------------------------------
# Additional tests (task instructions, beyond the brief's Step 1 list)
# ---------------------------------------------------------------------------


def test_monotone_climb_has_exactly_zero_descent() -> None:
    """A strictly increasing profile must report descent_m == 0.0 exactly.
    Any nonzero descent means the hysteresis is leaking a spurious
    down-leg out of pure monotonic noise."""
    ele = np.linspace(0.0, 100.0, 101)  # 1 m/step, well above MIN_RISE_M
    ascent, descent = accumulate_relief(ele, MIN_RISE_M)
    assert descent == 0.0
    assert ascent == pytest.approx(100.0, abs=1e-6)


def test_round_trip_ascent_approx_equals_descent() -> None:
    """Out-and-back over the same geometry must give ascent ~= descent —
    a strong invariant that catches asymmetric bugs in the direction
    flip. Built from a real multi-block SF path (base -> Twin Peaks summit
    -> down the other side) run there and back."""
    coords = _twin_peaks_climb_coords()
    round_trip = list(coords) + list(reversed(coords[:-1]))

    sampler_local = DemSampler(_FIXTURE_DEM_PATH)
    profile = build_profile(round_trip, sampler_local)

    assert abs(profile.ascent_m - profile.descent_m) <= 3.0 * MIN_RISE_M


def test_ascent_hysteresis_commits_at_exact_threshold_boundary() -> None:
    """Pin which side of the MIN_RISE_M boundary commits a reversal, so a
    later refactor cannot silently move it. Climb to a peak, dip by
    exactly `delta`, then climb further; the dip commits the first leg
    (and later un-commits it as a separate descent leg) only when
    `delta >= MIN_RISE_M`."""
    peak = 10.0
    climb_to = 20.0

    delta_at_threshold = MIN_RISE_M
    ele_at = np.array([0.0, peak, peak - delta_at_threshold, climb_to])
    ascent_at, descent_at = accumulate_relief(ele_at, MIN_RISE_M)
    # Confirmed reversal: 0->10 (10) then 9->20 (11) = 21 total ascent,
    # plus the confirmed 10->9 dip counted as 1 m of descent.
    assert ascent_at == pytest.approx(21.0, abs=1e-6)
    assert descent_at == pytest.approx(1.0, abs=1e-6)

    delta_below_threshold = MIN_RISE_M - 0.01
    ele_below = np.array([0.0, peak, peak - delta_below_threshold, climb_to])
    ascent_below, descent_below = accumulate_relief(ele_below, MIN_RISE_M)
    # Sub-threshold dip never confirms: the whole thing reads as one
    # continuous climb from 0 to 20, net ascent 20, zero descent.
    assert ascent_below == pytest.approx(20.0, abs=1e-6)
    assert descent_below == pytest.approx(0.0, abs=1e-6)


def test_moving_average_loses_materially_more_peak_amplitude_than_savgol() -> None:
    """Justifies Savitzky-Golay over a moving average with a test, not just
    a comment: on the same 20 m/300 m crest, a same-window moving average
    must lose materially more amplitude than `smooth` does."""
    n = 31
    x = np.arange(n) * PROFILE_SAMPLE_M
    amplitude = 20.0
    ele = amplitude * 0.5 * (1.0 + np.cos(np.pi * (x - 150.0) / 150.0))

    savgol_peak = float(smooth(ele).max())
    moving_avg_peak = float(_moving_average(ele, SMOOTH_WINDOW_SAMPLES).max())

    savgol_loss = amplitude - savgol_peak
    moving_avg_loss = amplitude - moving_avg_peak
    assert moving_avg_loss > savgol_loss * 1.5


def _moving_average(arr: np.ndarray, window: int) -> np.ndarray:
    half = window // 2
    padded = np.pad(arr, (half, half), mode="edge")
    kernel = np.ones(window) / window
    result: np.ndarray = np.convolve(padded, kernel, mode="valid")
    return result


# ---------------------------------------------------------------------------
# windowed_grades
# ---------------------------------------------------------------------------


def test_windowed_grades_matches_hand_computed_centered_difference() -> None:
    # Constant 1 m rise every 10 m -> 10% grade everywhere except the
    # shrunk-window edges (which use a smaller, still-correct baseline).
    ele = np.arange(11, dtype=np.float64)  # 0..10 m, 10 m spacing
    grades = windowed_grades(ele, PROFILE_SAMPLE_M, GRADE_WINDOW_SAMPLES)
    assert grades.shape == (11,)
    assert np.allclose(grades, 10.0)


def test_windowed_grades_edges_shrink_not_pad() -> None:
    """The first and last samples must use a real, computed one-sided
    baseline (shrinking the window), never a padded/fabricated value —
    verified against a profile where naive zero-padding would visibly
    change the edge values."""
    ele = np.array([0.0, 10.0, 10.0, 10.0, 0.0])  # 10 m spacing
    grades = windowed_grades(ele, PROFILE_SAMPLE_M, GRADE_WINDOW_SAMPLES)
    # First sample's shrunk window is [0, 1]: (10-0)/10*100 = 100%.
    assert grades[0] == pytest.approx(100.0)
    # Last sample's shrunk window is [3, 4]: (0-10)/10*100 = -100%.
    assert grades[-1] == pytest.approx(-100.0)


def test_windowed_grades_flat_profile_is_zero() -> None:
    ele = np.full(20, 42.0)
    grades = windowed_grades(ele, PROFILE_SAMPLE_M, GRADE_WINDOW_SAMPLES)
    assert np.allclose(grades, 0.0)


def test_windowed_grades_single_point_is_zero() -> None:
    grades = windowed_grades(np.array([5.0]), PROFILE_SAMPLE_M, GRADE_WINDOW_SAMPLES)
    assert grades.shape == (1,)
    assert grades[0] == 0.0


# ---------------------------------------------------------------------------
# build_profile
# ---------------------------------------------------------------------------


def _twin_peaks_climb_coords() -> list[LonLat]:
    """A real, connected multi-block path through the fixture graph
    (`data/fixtures/sf_graph.geojson`): Portola Drive up to the base of
    Twin Peaks Boulevard, over the summit, down Clarendon Avenue —
    verified connected (each segment's end matches the next segment's
    start) directly against the committed fixture graph file."""
    return [
        (-122.4387, 37.7484),
        (-122.442, 37.7476),
        (-122.4443, 37.7475),
        (-122.447, 37.747),
        (-122.4472, 37.749),
        (-122.4466, 37.7508),
        (-122.447, 37.7526),
        (-122.4468, 37.7541),
        (-122.446, 37.755),
        (-122.4456, 37.7562),
        (-122.4462, 37.7574),
        (-122.4514, 37.7574),
        (-122.449, 37.7584),
        (-122.446, 37.7588),
        (-122.4429, 37.759),
    ]


def test_twin_peaks_climb_coords_are_a_connected_path_in_the_fixture_graph() -> None:
    """Guards the hand-picked coordinate list above against silently
    drifting from the fixture graph it claims to represent."""
    graph = json.loads(_FIXTURE_GRAPH_PATH.read_text())
    edge_pairs: set[tuple[LonLat, LonLat]] = set()
    for feature in graph["features"]:
        way_coords = feature["geometry"]["coordinates"]
        for a, b in pairwise(way_coords):
            edge_pairs.add((tuple(a), tuple(b)))
            edge_pairs.add((tuple(b), tuple(a)))

    coords = _twin_peaks_climb_coords()
    for a, b in pairwise(coords):
        assert (a, b) in edge_pairs, f"{a} -> {b} is not an edge in the fixture graph"


def test_build_profile_on_real_sf_path_is_physically_plausible(sampler: DemSampler) -> None:
    """A genuine multi-block climb over Twin Peaks. Not a precise value —
    a sanity band: this route climbs roughly 200 m of real relief (SF's
    second-highest point, ~281 m at the true summit) and then descends
    partway down the far side, so both ascent and descent should be
    substantial and ascent should dominate (net gain, not a flat loop)."""
    coords = _twin_peaks_climb_coords()
    profile = build_profile(coords, sampler)

    assert isinstance(profile, ElevationProfile)
    assert isinstance(profile.points, tuple)
    assert len(profile.points) > 10
    assert all(isinstance(p, ProfilePoint) for p in profile.points)
    assert isinstance(profile.ascent_m, float)
    assert isinstance(profile.descent_m, float)
    assert isinstance(profile.max_grade_pct, float)

    # Sanity band, not a golden value (brief's own instruction). See
    # task-6-report.md for the measured figure and why this band was
    # chosen.
    assert 100.0 <= profile.ascent_m <= 350.0
    assert 20.0 <= profile.descent_m <= 200.0
    assert profile.ascent_m > profile.descent_m
    assert 0.0 < profile.max_grade_pct <= 40.0


def test_build_profile_short_route_does_not_crash_on_savgol_window() -> None:
    """A route shorter than the smoothing window (9 samples) is real —
    short trips exist — and must not raise."""
    coords: list[LonLat] = [(-122.4387, 37.7484), (-122.4395, 37.7480)]
    sampler_local = DemSampler(_FIXTURE_DEM_PATH)
    profile = build_profile(coords, sampler_local)
    assert len(profile.points) >= 2
    assert np.isfinite(profile.ascent_m)
    assert np.isfinite(profile.descent_m)
    assert np.isfinite(profile.max_grade_pct)


def test_build_profile_points_dist_and_ele_match_resampled_smoothed_series(
    sampler: DemSampler,
) -> None:
    coords: list[LonLat] = [(-122.4387, 37.7484), (-122.442, 37.7476), (-122.4443, 37.7475)]
    profile = build_profile(coords, sampler)
    dists = [p.dist_m for p in profile.points]
    assert dists == sorted(dists)
    assert dists[0] == 0.0
    assert all(isinstance(p.ele_m, float) for p in profile.points)


def test_build_profile_does_not_leak_numpy_scalars(sampler: DemSampler) -> None:
    """Resolution #4: returned scalars must be plain `float`, not
    `np.float64` — it serializes badly."""
    coords: list[LonLat] = [(-122.4387, 37.7484), (-122.442, 37.7476), (-122.4443, 37.7475)]
    profile = build_profile(coords, sampler)
    assert type(profile.ascent_m) is float
    assert type(profile.descent_m) is float
    assert type(profile.max_grade_pct) is float
    for p in profile.points:
        assert type(p.dist_m) is float
        assert type(p.ele_m) is float


def test_build_profile_min_rise_m_is_a_caller_supplied_parameter(sampler: DemSampler) -> None:
    """Resolution #3: `min_rise_m` stays a parameter on `accumulate_relief`
    (defaulting callers use `MIN_RISE_M`), so the eval harness can vary
    it directly."""
    ele = 50.0 + 0.4 * np.array([1 if i % 2 else -1 for i in range(100)])
    strict_ascent, _ = accumulate_relief(ele, min_rise_m=0.1)
    loose_ascent, _ = accumulate_relief(ele, min_rise_m=1.0)
    assert strict_ascent > loose_ascent
