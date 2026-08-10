"""Tests for the fixture DEM generator (`scripts/make_fixture_dem.py`).

The generator lives in `scripts/`, not the `contour` package (design doc
§8 repo layout: `scripts/make_fixture_dem.py`, sibling to `services/`).
It imports `contour.constants.SF_BBOX` as its single source of truth for
the bounding box, so it needs `services/api` on `sys.path` — this file
adds `contour/scripts` to `sys.path` the same way, so the generator module
is importable from the package's own test tree without becoming part of
the installable package itself.

See docs/superpowers/specs/2026-08-09-contour-design.md §2.3 for why this
DEM is synthetic, and its Task 4 report for the resolutions this test
suite encodes (holdout exclusion, determinism, nodata, clamping, COG
structure, and the SF-shape sanity check).
"""

import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio

from contour.constants import SF_BBOX

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from make_fixture_dem import (  # noqa: E402
    DEFAULT_CSV_PATH,
    DISTRICT_COVERAGE_SAMPLES,
    EXPECTED_HOLDOUT_COUNT,
    MIN_CONTROL_HOLDOUT_SEPARATION_M,
    NEIGHBORS,
    NODATA,
    SMOOTHING,
    ControlPointValidationError,
    build_dem,
    haversine_m,
    load_control_points,
    run_loo_sweep,
    sample_and_report_districts,
    sample_and_report_holdouts,
    select_best,
    sha256_of,
)


@pytest.fixture(scope="session")
def dem_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("fixture_dem") / "sf_fixture_dem.tif"
    build_dem(out)
    return out


# ---------------------------------------------------------------------------
# Step 2 tests, as specified in task-4-brief.md (build_dem's return value is
# a DemBuildResult with a `.path`, not a bare Path, so it can also carry the
# raw-data sha256 required by resolution #1 of task-4-brief's ambiguities —
# hence `.path` in the two hash calls below rather than the brief's literal
# `sha256_of(a)`).
# ---------------------------------------------------------------------------


def test_fixture_dem_is_readable_and_covers_bbox(dem_path: Path) -> None:
    with rasterio.open(dem_path) as ds:
        assert ds.crs is not None
        assert ds.crs.to_epsg() == 4326
        assert ds.count == 1
        assert ds.dtypes[0] == "float32"
        b = ds.bounds
        assert b.left <= SF_BBOX[0]
        assert b.bottom <= SF_BBOX[1]
        assert b.right >= SF_BBOX[2]
        assert b.top >= SF_BBOX[3]


def test_fixture_dem_is_deterministic(tmp_path: Path) -> None:
    # A coarser grid (50 m, not the production 10 m) keeps this test fast.
    # Determinism is a property of the fit/hash code path, not of grid
    # resolution. Full-resolution (10 m) determinism is proven separately:
    # two full `make fixtures` runs with identical `dem_data_sha256`/
    # `dem_file_sha256`, recorded in task-4-report.md each round.
    a = build_dem(tmp_path / "a.tif", resolution_m=50.0)
    b = build_dem(tmp_path / "b.tif", resolution_m=50.0)
    assert sha256_of(a.path) == sha256_of(b.path)
    assert a.data_sha256 == b.data_sha256


# ---------------------------------------------------------------------------
# Control-point CSV validation (Step 1's "validate on load", not silently
# repair)
# ---------------------------------------------------------------------------


def test_committed_control_point_csv_validates() -> None:
    points = load_control_points(DEFAULT_CSV_PATH)
    holdouts = [p for p in points if p.role == "holdout"]
    controls = [p for p in points if p.role == "control"]
    assert len(holdouts) == EXPECTED_HOLDOUT_COUNT == 10
    # 105 original + 13 added in fix round 2 (targeted street-scale relief)
    # + 98 added in fix round 4 (district coverage for the southeast, the
    # west, the Presidio and the North Beach/waterfront valley, corrections
    # to the over-inflated central massif, and a 20-point ring of 0.0 m
    # sea-level anchors over open water) — see docs/DECISIONS.md.
    assert len(controls) == 216
    assert len(points) == len(holdouts) + len(controls)
    for p in points:
        assert SF_BBOX[0] <= p.lon <= SF_BBOX[2]
        assert SF_BBOX[1] <= p.lat <= SF_BBOX[3]
        assert 0.0 <= p.ele_m <= 290.0


def test_load_control_points_rejects_point_outside_bbox(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "name,lat,lon,ele_m,role\n"
        "Out of bounds,37.9999,-122.4000,10.0,control\n"
        + "".join(f"Filler {i},37.75,-122.4{i:02d},10.0,control\n" for i in range(10))
        + "".join(f"Holdout {i},37.76,-122.4{i:02d},20.0,holdout\n" for i in range(10))
    )
    with pytest.raises(ControlPointValidationError, match="bbox"):
        load_control_points(bad)


def test_load_control_points_rejects_elevation_out_of_range(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "name,lat,lon,ele_m,role\n"
        "Too high,37.75,-122.40,999.0,control\n"
        + "".join(f"Filler {i},37.75,-122.4{i:02d},10.0,control\n" for i in range(10))
        + "".join(f"Holdout {i},37.76,-122.4{i:02d},20.0,holdout\n" for i in range(10))
    )
    with pytest.raises(ControlPointValidationError, match="ele_m"):
        load_control_points(bad)


def test_load_control_points_rejects_wrong_holdout_count(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "name,lat,lon,ele_m,role\n"
        + "".join(f"Filler {i},37.75,-122.4{i:02d},10.0,control\n" for i in range(10))
        + "Holdout 0,37.76,-122.400,20.0,holdout\n"
    )
    with pytest.raises(ControlPointValidationError, match="holdout"):
        load_control_points(bad)


def test_load_control_points_rejects_control_too_close_to_a_holdout(tmp_path: Path) -> None:
    """A control point within `MIN_CONTROL_HOLDOUT_SEPARATION_M` of a holdout
    makes that holdout's accuracy check tautological — the interpolator is
    then being asked to reproduce a value it was handed from ~metres away.
    Exact-duplicate rejection is not enough: 1 m apart passes that check and
    is just as circular. The message must name both points so the offender
    is obvious from the failure alone."""
    bad = tmp_path / "bad.csv"
    rows = ["name,lat,lon,ele_m,role\n"]
    # ~11 m north of "Holdout 0" below — nowhere near an exact duplicate.
    rows.append("Too close to a holdout,37.7601,-122.4000,20.0,control\n")
    rows += [f"Filler {i},37.75,-122.4{i:02d},10.0,control\n" for i in range(10)]
    rows.append("Holdout 0,37.7600,-122.4000,20.0,holdout\n")
    rows += [f"Holdout {i},37.78,-122.4{i:02d},20.0,holdout\n" for i in range(1, 10)]
    bad.write_text("".join(rows))
    with pytest.raises(ControlPointValidationError) as excinfo:
        load_control_points(bad)
    message = str(excinfo.value)
    assert "Too close to a holdout" in message
    assert "Holdout 0" in message


def test_committed_csv_keeps_every_control_clear_of_every_holdout() -> None:
    """The committed table itself must satisfy the separation guard — not
    just synthetic fixtures. Recomputed here from the loaded points rather
    than trusted from the loader, so a loader bug that skipped the check
    would still be caught."""
    points = load_control_points(DEFAULT_CSV_PATH)
    controls = [p for p in points if p.role == "control"]
    holdouts = [p for p in points if p.role == "holdout"]
    closest = min(
        (haversine_m(c.lat, c.lon, h.lat, h.lon), c.name, h.name)
        for c in controls
        for h in holdouts
    )
    assert closest[0] >= MIN_CONTROL_HOLDOUT_SEPARATION_M, closest


def test_load_control_points_rejects_duplicate_coordinates(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    rows = ["name,lat,lon,ele_m,role\n"]
    rows.append("Dup A,37.7500,-122.4000,10.0,control\n")
    rows.append("Dup B,37.7500,-122.4000,12.0,control\n")
    rows += [f"Filler {i},37.75,-122.4{i:02d},10.0,control\n" for i in range(2, 10)]
    rows += [f"Holdout {i},37.76,-122.4{i:02d},20.0,holdout\n" for i in range(10)]
    bad.write_text("".join(rows))
    with pytest.raises(ControlPointValidationError, match="duplicate"):
        load_control_points(bad)


# ---------------------------------------------------------------------------
# Holdouts must never reach the interpolator (resolution: "holdouts are
# excluded from interpolation")
# ---------------------------------------------------------------------------


def test_holdout_points_are_excluded_from_interpolation(tmp_path: Path) -> None:
    """A DEM built from only the `control` rows must be byte-identical to
    one built from the full (control + holdout) file — proving the holdout
    rows contribute nothing to the interpolated surface."""
    points = load_control_points(DEFAULT_CSV_PATH)
    controls_only = tmp_path / "controls_only.csv"
    header = "name,lat,lon,ele_m,role,source,confidence\n"
    lines = [header]
    for p in points:
        if p.role == "control":
            lines.append(f"{p.name},{p.lat},{p.lon},{p.ele_m},control,x,high\n")
    # Pad with the real 10 holdouts so this file also passes the "exactly 10
    # holdout rows" structural check, but they must not move the surface.
    for p in points:
        if p.role == "holdout":
            lines.append(f"{p.name},{p.lat},{p.lon},{p.ele_m},holdout,x,high\n")
    controls_only.write_text("".join(lines))

    # Coarser grid for speed, same reasoning as test_fixture_dem_is_deterministic
    # — holdout exclusion is a property of which points reach the fit, not
    # of grid resolution.
    a = build_dem(tmp_path / "full.tif", csv_path=DEFAULT_CSV_PATH, resolution_m=50.0)
    b = build_dem(tmp_path / "controls_only.tif", csv_path=controls_only, resolution_m=50.0)
    assert a.data_sha256 == b.data_sha256


# ---------------------------------------------------------------------------
# nodata / clamping (resolutions #2 and #3)
# ---------------------------------------------------------------------------


def test_fixture_dem_has_zero_nodata_pixels(dem_path: Path) -> None:
    with rasterio.open(dem_path) as ds:
        assert ds.nodata == NODATA
        arr = ds.read(1)
        assert int(np.sum(arr == NODATA)) == 0


def test_every_district_reads_as_san_francisco(dem_path: Path) -> None:
    """The defect this replaces a vacuous test with.

    The previous test here asserted `arr.min() >= 0 and arr.max() <= 300` —
    which `np.clip(z, 0, 300)` guarantees unconditionally, so it could not
    fail. Meanwhile the DEM had whole districts reading 0.0 m (McLaren Park,
    the Excelsior, the Presidio) and a 30-50 m hill on open SF Bay, and no
    holdout or sanity point sat anywhere near any of them. The validation
    set determined what could be seen.

    `DISTRICT_COVERAGE_SAMPLES` is that missing coverage: every quadrant
    plus open water, with bands wide enough to be about "is this
    recognisably San Francisco" rather than about interpolation accuracy.
    A control point deleted, an axis flipped, or a district left
    unrepresented again all fail here."""
    failures = [
        (name, value, low, high)
        for name, value, low, high, ok in sample_and_report_districts(dem_path)
        if not ok
    ]
    assert not failures, failures


def test_district_coverage_table_spans_the_whole_city() -> None:
    """Guards the guard.

    The root cause of the defect above was not a bad interpolation — it
    was that every check pointed at the same central corridor. A table
    that quietly shrank back toward the middle would reintroduce exactly
    that blind spot while still passing, so the table's own spread is
    asserted here: at least three land samples in each quadrant of
    `SF_BBOX`, and open-water samples on both the Bay and the Pacific
    side."""
    west, south, east, north = SF_BBOX
    mid_lon, mid_lat = (west + east) / 2, (south + north) / 2
    quadrants = {"NW": 0, "NE": 0, "SW": 0, "SE": 0}
    water_east = water_west = 0
    for _name, lat, lon, _low, high in DISTRICT_COVERAGE_SAMPLES:
        quadrants[("N" if lat >= mid_lat else "S") + ("E" if lon >= mid_lon else "W")] += 1
        if high <= 10.0:  # an open-water sample: banded at sea level
            if lon >= mid_lon:
                water_east += 1
            else:
                water_west += 1
    assert all(n >= 3 for n in quadrants.values()), quadrants
    assert water_east >= 2 and water_west >= 2, (water_east, water_west)


# ---------------------------------------------------------------------------
# Interpolation parameter selection (task-4 fix round 1) — `smoothing` and
# `neighbors` must come from leave-one-out cross-validation on the control
# points, never from tuning against the holdouts.
# ---------------------------------------------------------------------------


def test_interpolation_parameters_match_cv_sweep_winner() -> None:
    """`SMOOTHING`/`NEIGHBORS` in make_fixture_dem.py must equal whatever
    `select_best(run_loo_sweep(...))` actually picks — pins the constants
    to the measurement that justifies them, so a hand-edit of one without
    the other (or a hand-edit that silently stops matching the sweep) is
    caught, not just documented."""
    points = load_control_points(DEFAULT_CSV_PATH)
    control_points = [p for p in points if p.role == "control"]
    winner = select_best(run_loo_sweep(control_points))
    assert winner.smoothing == SMOOTHING
    assert winner.neighbors == NEIGHBORS


# ---------------------------------------------------------------------------
# Holdout accuracy tolerance (task-4 fix round 3, final ruling) — +/-25 m for
# ordinary holdouts, +/-40 m for holdouts that are a local terrain maximum
# with no control point at their own peak (a principled definition, not
# "whichever holdouts happen to be failing" — fix round 2's two-name list
# was goalpost-fitting and was corrected here to three names chosen by
# principle). Both numbers are derived from the measured LOO median error
# (13.80 m), not chosen by taste; this gate exists to catch gross breakage
# — a flipped axis, a units error, a broken sampler — not to certify
# elevation accuracy. The fixture DEM cannot certify accuracy at any
# tolerance; the 3DEP run is the accuracy gate and has not been run.
# ---------------------------------------------------------------------------


def test_each_named_holdout_gets_the_tolerance_the_ruling_gave_it(dem_path: Path) -> None:
    """A golden table, not a re-implementation.

    This replaces two tests that could not fail: one re-derived
    `name in SUMMIT_HOLDOUT_NAMES ? 40 : 25` — the exact branch it was
    testing — and one restated the frozenset's own literal contents. Both
    would have followed any edit to the code they were guarding.

    The table below is written out by hand instead, one row per holdout.
    Editing `SUMMIT_HOLDOUT_NAMES`, either tolerance constant, or the
    holdout set itself now fails here and has to be re-decided
    deliberately, which is what the fix-round-3 ruling asked for."""
    expected = {
        "Twin Peaks summit (Eureka Peak)": 40.0,
        "Bernal Heights summit": 40.0,
        "Corona Heights summit": 40.0,
        "Ferry Building": 25.0,
        "Ocean Beach (at Judah)": 25.0,
        "Alamo Square": 25.0,
        "Lands End": 25.0,
        "Mission Dolores Park": 25.0,
        "Fort Mason": 25.0,
        "Candlestick Point": 25.0,
    }
    assert len(expected) == EXPECTED_HOLDOUT_COUNT
    results = sample_and_report_holdouts(dem_path)
    assert {r.name: r.tolerance_m for r in results} == expected


def test_all_holdouts_pass_the_gross_breakage_gate(dem_path: Path) -> None:
    """Final ruling (task-4 fix round 3): with tolerances set above the
    model's own measured LOO error rather than below it, all 10 holdouts
    are expected to pass — a real, hard gate now, not a reporting-only
    check. A failure here means something broke (axis flip, units error,
    sampler bug), not that the fixture DEM's ordinary interpolation error
    exceeded an arbitrary number."""
    results = sample_and_report_holdouts(dem_path)
    failures = [r for r in results if not r.within_tolerance]
    assert not failures, [(r.name, r.error_m, r.tolerance_m) for r in failures]


# ---------------------------------------------------------------------------
# COG structure (resolution #5)
# ---------------------------------------------------------------------------


def test_fixture_dem_is_a_valid_cog_structure(dem_path: Path) -> None:
    with rasterio.open(dem_path) as ds:
        assert ds.profile["tiled"] is True
        assert ds.block_shapes == [(512, 512)]
        assert ds.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
        assert len(ds.overviews(1)) > 0


# ---------------------------------------------------------------------------
# Shape sanity check (resolution #4) — catches a flipped axis or mis-signed
# longitude, which passes every structural test above but produces a
# completely wrong DEM.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "lat", "lon", "low", "high"),
    [
        ("Twin Peaks area", 37.7559, -122.4476, 230.0, 290.0),
        ("SoMa / 5th & Folsom", 37.7800, -122.4050, 0.0, 20.0),
        ("Ocean Beach", 37.7609, -122.5105, 0.0, 15.0),
        # Task-4 resolution #4 says "roughly 5-30 m"; the nearest control
        # points to this exact coordinate range from 24 m (Panhandle) to
        # 40 m (Church & Market), with the 30 m Duboce/Market point itself
        # closest — 35 m keeps the bound meaningful (a flipped-axis bug
        # would land at 150m+ or below sea level) without over-fitting to
        # a single control value the RBF is expected to smooth slightly.
        ("Duboce & Market (Wiggle east end)", 37.7695, -122.4290, 5.0, 35.0),
    ],
)
def test_fixture_dem_shape_matches_real_sf_topography(
    dem_path: Path, name: str, lat: float, lon: float, low: float, high: float
) -> None:
    with rasterio.open(dem_path) as ds:
        row, col = ds.index(lon, lat)
        value = float(ds.read(1)[row, col])
    assert low <= value <= high, f"{name}: sampled {value} m, expected [{low}, {high}]"
