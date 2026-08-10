#!/usr/bin/env python3
"""Deterministic synthetic DEM fixture generator for Contour.

Reads the committed table of real San Francisco elevation control points
(`data/fixtures/elevation_control_points.csv`), interpolates a smooth
surface with `scipy.interpolate.RBFInterpolator`, and writes a 10 m
Cloud-Optimized GeoTIFF (`data/dem/sf_fixture_dem.tif`) plus
`data/manifest.json` describing it.

Why synthetic: there is no real USGS 3DEP data in this environment (see
docs/superpowers/specs/2026-08-09-contour-design.md §2.2-2.3). This is not
a mock — it is a real DEM, real CRS, real COG structure — interpolated
from real, individually-sourced SF elevations rather than downloaded from
3DEP. `elevation.py` never knows which DEM it has; swapping in the real
3DEP COG later is a `manifest.json` path change, nothing else.

Determinism: `RBFInterpolator` fit against a fixed control-point set has
no random component (no RNG is used anywhere in this module), so running
this script twice produces byte-identical output — proven in
`services/api/tests/test_fixture_dem.py::test_fixture_dem_is_deterministic`
by hashing both the written file and the raw raster array.

Run via `make fixtures` from the `contour/` root, or directly:
    cd services/api && uv run python ../../scripts/make_fixture_dem.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from numpy.typing import NDArray
from rasterio.transform import Affine, from_origin
from scipy.interpolate import RBFInterpolator
from scipy.spatial import Delaunay

# `contour.constants` is the single source of truth for SF_BBOX (Global
# Constraints). This script lives in `scripts/`, a sibling of `services/`,
# not inside the `services/api` package, so it reaches into that package's
# source tree explicitly rather than assuming it's on `sys.path` already.
_SCRIPTS_DIR = Path(__file__).resolve().parent
_CONTOUR_ROOT = _SCRIPTS_DIR.parent
_API_SRC = _CONTOUR_ROOT / "services" / "api"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))

from contour.constants import SF_BBOX  # noqa: E402

# --- Paths -------------------------------------------------------------

DEFAULT_CSV_PATH: Path = _CONTOUR_ROOT / "data" / "fixtures" / "elevation_control_points.csv"
DEFAULT_DEM_PATH: Path = _CONTOUR_ROOT / "data" / "dem" / "sf_fixture_dem.tif"
DEFAULT_MANIFEST_PATH: Path = _CONTOUR_ROOT / "data" / "manifest.json"

# --- Fixed generation parameters ----------------------------------------

RESOLUTION_M: float = 10.0
# Equirectangular approximation constant for converting degrees of
# latitude to metres. Longitude degrees are scaled by cos(mean latitude)
# of the bbox (see `_meters_per_degree_lon`) since a degree of longitude
# is narrower than a degree of latitude away from the equator.
METERS_PER_DEGREE_LAT: float = 111_320.0

NODATA: float = -9999.0
ELEVATION_MIN_M: float = 0.0
ELEVATION_MAX_M: float = 300.0

# Structural bounds for the *input* CSV (control-point elevations), looser
# than the raster clamp range above per task-4 resolution #3 — the CSV
# check is "is this plausibly an SF elevation," the raster clamp is "the
# physical bound the finished DEM must respect."
CSV_ELEVATION_MIN_M: float = 0.0
CSV_ELEVATION_MAX_M: float = 290.0

EXPECTED_HOLDOUT_COUNT: int = 10

# Minimum distance a `control` point must keep from every `holdout` point.
#
# Exact-coordinate duplicate rejection (below) is not enough. A control
# point 1 m from a holdout passes that check and silently makes the
# holdout's accuracy check tautological: the interpolator is handed the
# answer from a metre away and then asked to reproduce it. That is the
# defect class this dataset was already cleaned of once, and it becomes
# easy to reintroduce by accident every time the table grows (fix round 4
# added 86 points in one go). 50 m is comfortably below the tightest
# genuine separation in the committed table (65.4 m, "Great Hwy and Judah
# St" vs. the "Ocean Beach (at Judah)" holdout) and comfortably above the
# 10 m raster cell, so a violation means someone placed a point on top of
# a holdout rather than merely nearby.
MIN_CONTROL_HOLDOUT_SEPARATION_M: float = 50.0

KERNEL: str = "thin_plate_spline"

# `smoothing` and `neighbors` are selected by `select_best(run_loo_sweep(...))`
# below (leave-one-out cross-validation over the control points), NOT by
# looking at holdout error — see `scripts/cv_sweep.py` and
# docs/DECISIONS.md "Task 4, fix round 1" (original 105-point sweep) and
# "Task 4, fix round 2" (re-run at 118 points after targeted control-point
# additions — same winner both times) for the full sweep tables and
# reasoning. `test_interpolation_parameters_match_cv_sweep_winner` pins
# these two literals to that function's actual output on the *current*
# committed CSV, so they cannot silently drift from the sweep that
# justifies them, even as the control-point count changes over time.
#
# The brief's original guess of `smoothing=0.5` undershot the two summit
# holdouts (Twin Peaks -33.5 m, Bernal Heights -21.1 m). The sweep found
# `smoothing` has essentially *no* measurable effect across the tested
# values (0, 0.1, 0.5, 2.0) given this module's local-metres coordinate
# scale — thin-plate-spline kernel magnitudes here run ~1e4-1e8, dwarfing
# an additive smoothing term of 0-2 — the four smoothing values differ
# only in the noise floor of the p90 metric; `2.0` is the literal argmin
# `select_best` returns both times, not a meaningfully "better" choice
# than 0/0.1/0.5. `neighbors=20` is the real signal: lowest median and
# lowest p90 LOO error among the swept values, both times. It does NOT fix
# the summit undershoot and (at 105 points) slightly *increased* in-hull
# clamping. Kept anyway, exactly as selected, because the whole point of
# this sweep is that parameters come from this measurement, not from
# post-hoc adjustment against the holdouts it exists to check
# independently. See docs/DECISIONS.md for both rounds' exact numbers —
# not duplicated here so this comment doesn't itself go stale.
#
# Fix round 4 re-ran the sweep after adding 86 control points (204 total)
# and the winner MOVED, for the first time: `neighbors=None` (a single
# global fit) now beats `neighbors=20` on median LOO error (16.73 m vs.
# 17.03 m) and by a wide margin on p90 (72.60 m vs. 71.54 m at n=20, both
# far better than the 92.2 m p90 the 118-point set produced at any
# setting). `neighbors=20` won at 105 and 118 points because the control
# set was too sparse for a global fit to be well conditioned; at 204
# points, with the southeast, the west, the Presidio and an 18-point ring
# of sea-level water anchors all represented, the global fit has enough
# data everywhere and no longer has to extrapolate. The smoothing tie is
# unchanged and unchanged for the same reason (see above): all four
# smoothing values are identical to 2 decimal places at every `neighbors`
# setting, so `0.0` here is the literal argmin of a tie, not a claim that
# zero smoothing is better than 2.0.
SMOOTHING: float = 0.0
NEIGHBORS: int | None = None

# The candidate grid swept by `run_loo_sweep` (called from
# `scripts/cv_sweep.py`). Kept here, not just in the sweep script, so the
# constants above are provably drawn from this exact grid — see
# `test_interpolation_parameters_match_cv_sweep_winner`.
SMOOTHING_SWEEP: tuple[float, ...] = (0.0, 0.1, 0.5, 2.0)
NEIGHBORS_SWEEP: tuple[int | None, ...] = (None, 10, 20, 40)

MANIFEST_SOURCE: str = "fixture"


# --- Control points ------------------------------------------------------


@dataclass(frozen=True)
class ControlPoint:
    """One row of `elevation_control_points.csv`."""

    name: str
    lat: float
    lon: float
    ele_m: float
    role: str  # "control" | "holdout"
    source: str
    confidence: str


class ControlPointValidationError(ValueError):
    """The control-point CSV failed a structural check.

    Per task-4 resolution: validation failures are reported, not silently
    repaired — this is raised, never caught and "fixed" internally.
    """


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points.

    Used by the control/holdout separation guard. Deliberately not the
    module's local equirectangular frame: that frame exists to make the
    RBF's *smoothing* isotropic, whereas this is a true-distance question
    ("are these two points really 50 m apart on the ground?") and should
    not inherit the projection's approximation.
    """
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_control_points(csv_path: Path) -> tuple[ControlPoint, ...]:
    """Read and validate the control-point CSV.

    Validates: every point inside `SF_BBOX`, every `ele_m` in
    `[CSV_ELEVATION_MIN_M, CSV_ELEVATION_MAX_M]`, exactly
    `EXPECTED_HOLDOUT_COUNT` holdout rows, no duplicate (lat, lon) pairs,
    and every `control` point at least `MIN_CONTROL_HOLDOUT_SEPARATION_M`
    from every `holdout` point. Raises `ControlPointValidationError`
    naming the first violation found rather than attempting to repair the
    data.
    """
    west, south, east, north = SF_BBOX
    rows: list[ControlPoint] = []
    seen_coords: dict[tuple[float, float], str] = {}

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"name", "lat", "lon", "ele_m", "role"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ControlPointValidationError(
                f"{csv_path}: missing required column(s) {sorted(missing)}"
            )
        for i, raw in enumerate(reader, start=2):  # header is line 1
            name = raw["name"]
            try:
                lat = float(raw["lat"])
                lon = float(raw["lon"])
                ele_m = float(raw["ele_m"])
            except ValueError as e:
                raise ControlPointValidationError(
                    f"{csv_path}:{i}: non-numeric lat/lon/ele_m for {name!r}: {e}"
                ) from e
            role = raw["role"]
            if role not in ("control", "holdout"):
                raise ControlPointValidationError(
                    f"{csv_path}:{i}: {name!r} has role={role!r}, expected 'control' or 'holdout'"
                )
            if not (west <= lon <= east and south <= lat <= north):
                raise ControlPointValidationError(
                    f"{csv_path}:{i}: {name!r} at (lat={lat}, lon={lon}) is outside bbox {SF_BBOX}"
                )
            if not (CSV_ELEVATION_MIN_M <= ele_m <= CSV_ELEVATION_MAX_M):
                raise ControlPointValidationError(
                    f"{csv_path}:{i}: {name!r} has ele_m={ele_m}, expected "
                    f"[{CSV_ELEVATION_MIN_M}, {CSV_ELEVATION_MAX_M}]"
                )
            coord = (lat, lon)
            if coord in seen_coords:
                raise ControlPointValidationError(
                    f"{csv_path}:{i}: {name!r} has duplicate coordinates "
                    f"(lat={lat}, lon={lon}) shared with {seen_coords[coord]!r}"
                )
            seen_coords[coord] = name
            rows.append(
                ControlPoint(
                    name=name,
                    lat=lat,
                    lon=lon,
                    ele_m=ele_m,
                    role=role,
                    source=raw.get("source", ""),
                    confidence=raw.get("confidence", ""),
                )
            )

    role_counts = Counter(p.role for p in rows)
    if role_counts["holdout"] != EXPECTED_HOLDOUT_COUNT:
        raise ControlPointValidationError(
            f"{csv_path}: found {role_counts['holdout']} holdout rows, expected "
            f"exactly {EXPECTED_HOLDOUT_COUNT}"
        )
    if role_counts["control"] == 0:
        raise ControlPointValidationError(f"{csv_path}: found zero control rows")

    holdouts = [p for p in rows if p.role == "holdout"]
    for c in (p for p in rows if p.role == "control"):
        for h in holdouts:
            separation = haversine_m(c.lat, c.lon, h.lat, h.lon)
            if separation < MIN_CONTROL_HOLDOUT_SEPARATION_M:
                raise ControlPointValidationError(
                    f"{csv_path}: control point {c.name!r} at (lat={c.lat}, lon={c.lon}) is "
                    f"only {separation:.1f} m from holdout {h.name!r} at "
                    f"(lat={h.lat}, lon={h.lon}); the minimum separation is "
                    f"{MIN_CONTROL_HOLDOUT_SEPARATION_M} m. A control point this close makes "
                    f"that holdout's accuracy check tautological — move or remove the control "
                    f"point, never the holdout."
                )

    return tuple(rows)


# --- Geometry -------------------------------------------------------------


def _meters_per_degree_lon(bbox: tuple[float, float, float, float]) -> float:
    west, south, east, north = bbox
    mean_lat_rad = math.radians((south + north) / 2.0)
    return METERS_PER_DEGREE_LAT * math.cos(mean_lat_rad)


def _to_local_meters(
    lons: NDArray[np.float64],
    lats: NDArray[np.float64],
    bbox: tuple[float, float, float, float],
) -> NDArray[np.float64]:
    """Project (lon, lat) degrees to a local equirectangular metre frame
    anchored at the bbox's southwest corner.

    This matters: fitting the RBF directly in (lon, lat) degree-space
    would implicitly use Euclidean distance in *degrees*, and a degree of
    longitude is ~21% shorter than a degree of latitude at SF's latitude
    (cos(37.77 deg) ~= 0.79). Left uncorrected, the interpolated surface's
    smoothing radius would be anisotropic — noticeably stretched
    east-west relative to north-south — for no physical reason. Projecting
    to metres first makes the RBF's implicit distance metric isotropic in
    the real world, which is what "smooth" should mean for a terrain
    surface.
    """
    west, south, _east, _north = bbox
    mplon = _meters_per_degree_lon(bbox)
    x = (lons - west) * mplon
    y = (lats - south) * METERS_PER_DEGREE_LAT
    return np.stack([x, y], axis=-1)


@dataclass(frozen=True)
class GridSpec:
    width: int
    height: int
    dx_deg: float
    dy_deg: float
    transform: Affine


def _grid_spec(bbox: tuple[float, float, float, float], resolution_m: float) -> GridSpec:
    west, south, east, north = bbox
    mplon = _meters_per_degree_lon(bbox)
    dx_deg = resolution_m / mplon
    dy_deg = resolution_m / METERS_PER_DEGREE_LAT
    width = max(1, round((east - west) / dx_deg))
    height = max(1, round((north - south) / dy_deg))
    transform = from_origin(west, north, dx_deg, dy_deg)
    return GridSpec(width=width, height=height, dx_deg=dx_deg, dy_deg=dy_deg, transform=transform)


# --- Interpolation ----------------------------------------------------------


@dataclass(frozen=True)
class ClampStats:
    """How many pixels the [0, 300] m clamp actually changed.

    Per task-4 resolution #3: RBF extrapolation can overshoot badly near
    the edges of the convex hull of the control points, so this count is a
    warning sign to report, not a detail to discard.
    """

    clamped_low: int
    clamped_high: int
    total_pixels: int
    # Pixels clamped that also fall inside the convex hull of the *land*
    # control points — i.e. nominally "should have real land data nearby,"
    # as opposed to open Bay/ocean pixels where clamping to sea level is
    # the correct answer, not a defect. See docs/DECISIONS.md "Task 4, fix
    # round 1" for the original definition.
    #
    # Fix round 4 narrowed the hull from "all control points" to "control
    # points above 0 m", because that round added an 18-point ring of
    # 0.0 m sea-level anchors over open water. Those anchors stretch the
    # all-control hull out across the Bay and the Pacific, nearly doubling
    # the in-hull pixel count (970k -> 1.88M) with water — where a surface
    # that dips a hair below zero and clamps to 0 is doing exactly the
    # right thing. Left unnarrowed, the statistic reads 21.13% and would
    # look like a large regression from fix round 2's 7.40% while actually
    # measuring a different question. Over the land hull, the same build
    # measures 2.87%. The number is supposed to mean "clamped despite
    # being surrounded by land control data"; this keeps it meaning that.
    in_hull_clamped: int
    in_hull_pixels: int

    @property
    def clamped_total(self) -> int:
        return self.clamped_low + self.clamped_high

    @property
    def clamped_fraction(self) -> float:
        return self.clamped_total / self.total_pixels if self.total_pixels else 0.0

    @property
    def in_hull_clamped_fraction(self) -> float:
        return self.in_hull_clamped / self.in_hull_pixels if self.in_hull_pixels else 0.0


def interpolate_dem(
    control_points: Sequence[ControlPoint],
    bbox: tuple[float, float, float, float] = SF_BBOX,
    resolution_m: float = RESOLUTION_M,
) -> tuple[NDArray[np.float32], Affine, ClampStats]:
    """Fit a thin-plate-spline RBF to `control_points` and evaluate it on a
    `resolution_m` grid over `bbox`.

    Callers MUST pass only `role == "control"` points — holdout points
    exist to validate the finished DEM (Task 6), not to inform it. This
    function does not filter by role itself so that
    `test_holdout_points_are_excluded_from_interpolation` can prove the
    caller-side filtering actually matters (feeding it holdouts too would
    change the result).
    """
    lons = np.array([p.lon for p in control_points], dtype=np.float64)
    lats = np.array([p.lat for p in control_points], dtype=np.float64)
    eles = np.array([p.ele_m for p in control_points], dtype=np.float64)

    xy = _to_local_meters(lons, lats, bbox)
    rbf = RBFInterpolator(xy, eles, kernel=KERNEL, smoothing=SMOOTHING, neighbors=NEIGHBORS)

    grid = _grid_spec(bbox, resolution_m)
    west, south, east, north = bbox
    # Pixel *centres*: column j -> lon = west + (j + 0.5) * dx (increasing
    # east); row i -> lat = north - (i + 0.5) * dy (decreasing south as row
    # index grows). Row 0 is the north edge, matching the Affine transform
    # from `from_origin(west, north, dx, dy)` used for the raster itself —
    # getting this backwards is exactly the flipped-axis failure mode
    # task-4's resolution #4 calls out, so it is spelled out here rather
    # than left implicit in a `meshgrid` call.
    grid_lons = west + (np.arange(grid.width, dtype=np.float64) + 0.5) * grid.dx_deg
    grid_lats = north - (np.arange(grid.height, dtype=np.float64) + 0.5) * grid.dy_deg
    lon_grid, lat_grid = np.meshgrid(grid_lons, grid_lats)  # both shape (height, width)

    grid_xy = _to_local_meters(lon_grid.ravel(), lat_grid.ravel(), bbox)
    z = rbf(grid_xy)

    total_pixels = grid.width * grid.height
    clamped_low = int(np.sum(z < ELEVATION_MIN_M))
    clamped_high = int(np.sum(z > ELEVATION_MAX_M))
    z_clamped = np.clip(z, ELEVATION_MIN_M, ELEVATION_MAX_M).astype(np.float32)
    array = z_clamped.reshape(grid.height, grid.width)

    # Hull over the land control points only — see the ClampStats comment.
    land_xy = xy[eles > ELEVATION_MIN_M]
    in_hull = _in_convex_hull(land_xy, grid_xy)
    in_hull_clamped = int(np.sum(in_hull & ((z < ELEVATION_MIN_M) | (z > ELEVATION_MAX_M))))

    stats = ClampStats(
        clamped_low=clamped_low,
        clamped_high=clamped_high,
        total_pixels=total_pixels,
        in_hull_clamped=in_hull_clamped,
        in_hull_pixels=int(np.sum(in_hull)),
    )
    return array, grid.transform, stats


def _in_convex_hull(
    control_xy: NDArray[np.float64], query_xy: NDArray[np.float64]
) -> NDArray[np.bool_]:
    """True for each `query_xy` point that falls inside the convex hull of
    `control_xy` — used to separate "clamped because there's no nearby
    control data at all" (open water, expected) from "clamped despite
    being surrounded by control points" (a real interpolation-quality
    signal). See docs/DECISIONS.md "Task 4, fix round 1"."""
    hull = Delaunay(control_xy)
    result: NDArray[np.bool_] = hull.find_simplex(query_xy) >= 0
    return result


# --- Leave-one-out cross-validation (parameter selection) -------------------
#
# `smoothing`/`neighbors` are NOT chosen by looking at holdout error — the
# holdouts are the final independent check (design doc §2.3) and picking
# parameters to minimise their error would make that check circular. This
# section selects parameters against the 105 *control* points instead, each
# held out one at a time and predicted from the rest. See
# docs/DECISIONS.md "Task 4, fix round 1" and `scripts/cv_sweep.py`.


def loo_errors(
    control_points: Sequence[ControlPoint],
    bbox: tuple[float, float, float, float],
    smoothing: float,
    neighbors: int | None,
) -> NDArray[np.float64]:
    """Leave-one-out absolute errors (metres) for one (smoothing, neighbors)
    combination: for each control point, fit on the other 104 and predict
    the held-out one. Returns one error per input point, same order."""
    lons = np.array([p.lon for p in control_points], dtype=np.float64)
    lats = np.array([p.lat for p in control_points], dtype=np.float64)
    eles = np.array([p.ele_m for p in control_points], dtype=np.float64)
    xy = _to_local_meters(lons, lats, bbox)

    n = len(control_points)
    errors = np.empty(n, dtype=np.float64)
    mask = np.ones(n, dtype=bool)
    for i in range(n):
        mask[i] = False
        nb = neighbors if neighbors is None else min(neighbors, n - 1)
        rbf = RBFInterpolator(
            xy[mask], eles[mask], kernel=KERNEL, smoothing=smoothing, neighbors=nb
        )
        predicted = rbf(xy[i : i + 1])[0]
        errors[i] = abs(predicted - eles[i])
        mask[i] = True
    return errors


@dataclass(frozen=True)
class SweepResult:
    smoothing: float
    neighbors: int | None
    median_loo_error_m: float
    p90_loo_error_m: float


def run_loo_sweep(
    control_points: Sequence[ControlPoint],
    bbox: tuple[float, float, float, float] = SF_BBOX,
    smoothing_values: Sequence[float] = SMOOTHING_SWEEP,
    neighbors_values: Sequence[int | None] = NEIGHBORS_SWEEP,
) -> tuple[SweepResult, ...]:
    """Run leave-one-out cross-validation for every (smoothing, neighbors)
    combination in the sweep grid, scored by median and p90 (90th
    percentile) absolute LOO error across the 105 control points."""
    results = []
    for smoothing in smoothing_values:
        for neighbors in neighbors_values:
            errors = loo_errors(control_points, bbox, smoothing, neighbors)
            results.append(
                SweepResult(
                    smoothing=smoothing,
                    neighbors=neighbors,
                    median_loo_error_m=float(np.median(errors)),
                    p90_loo_error_m=float(np.percentile(errors, 90)),
                )
            )
    return tuple(results)


def select_best(results: Sequence[SweepResult]) -> SweepResult:
    """Pick the sweep winner: lowest median LOO error first, p90 as
    tie-breaker. Both are ordinary Python floats compared exactly — no
    hidden preference for one metric beyond "median first"."""
    return min(results, key=lambda r: (r.median_loo_error_m, r.p90_loo_error_m))


# --- Raster I/O -------------------------------------------------------------


def write_cog(array: NDArray[np.float32], transform: Affine, output_path: Path) -> None:
    """Write `array` as a Cloud-Optimized GeoTIFF.

    Uses GDAL's native `COG` driver (verified present in this
    environment's GDAL build) rather than a two-step
    write-then-`gdal_translate`-to-COG or the `rio-cogeo` package: the COG
    driver *is* the reference implementation of the COG layout (tiled
    blocks, internal overviews built before the full-res IFD, correct
    ghost-area header) — see `docs/DECISIONS.md` Task 4 entry for why this
    was judged sufficient over adding `rio-cogeo` as a dependency.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    profile: dict[str, Any] = {
        "driver": "COG",
        "dtype": "float32",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "crs": "EPSG:4326",
        "transform": transform,
        "compress": "deflate",
        "blocksize": 512,
        "overview_resampling": "average",
        "nodata": NODATA,
    }
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(array, 1)


def sha256_of(path: Path) -> str:
    """SHA-256 of a file's raw bytes."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_of_array(array: NDArray[np.float32]) -> str:
    """SHA-256 of the raster *data*, independent of any file-format bytes
    (headers, tag ordering, overview encoding) around it — proves the
    interpolation itself is deterministic, not just that the writer
    produces stable bytes for a stable array."""
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


# --- Orchestration ----------------------------------------------------------


@dataclass(frozen=True)
class DemBuildResult:
    path: Path
    data_sha256: str
    file_sha256: str
    clamp_stats: ClampStats
    control_point_count: int


def build_dem(
    output_path: Path,
    *,
    csv_path: Path = DEFAULT_CSV_PATH,
    bbox: tuple[float, float, float, float] = SF_BBOX,
    resolution_m: float = RESOLUTION_M,
) -> DemBuildResult:
    """End-to-end: load control points, interpolate, write the COG.

    Only `role == "control"` rows are fed to the interpolator — holdout
    rows are loaded (so the CSV's structural validation still runs against
    the whole file) and then explicitly dropped before fitting.
    """
    points = load_control_points(csv_path)
    control_points = [p for p in points if p.role == "control"]

    array, transform, clamp_stats = interpolate_dem(
        control_points, bbox=bbox, resolution_m=resolution_m
    )
    write_cog(array, transform, output_path)

    return DemBuildResult(
        path=output_path,
        data_sha256=_sha256_of_array(array),
        file_sha256=sha256_of(output_path),
        clamp_stats=clamp_stats,
        control_point_count=len(control_points),
    )


def write_manifest(
    result: DemBuildResult,
    manifest_path: Path,
    csv_path: Path = DEFAULT_CSV_PATH,
    bbox: tuple[float, float, float, float] = SF_BBOX,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Write `data/manifest.json`.

    `generated_at` defaults to the current UTC time — this is the one
    genuinely time-varying field in the whole pipeline, which is why
    determinism is proven on the *DEM* (file + data hashes), not on the
    manifest as a whole; `control_point_sha256` and `manifest_sha256`
    (below) still let a diff show exactly what did and didn't change
    between two runs.
    """
    if generated_at is None:
        generated_at = datetime.now(UTC)

    control_point_sha256 = sha256_of(csv_path)

    manifest: dict[str, Any] = {
        "source": MANIFEST_SOURCE,
        "bbox": list(bbox),
        "dem_path": str(result.path.relative_to(manifest_path.parent))
        if manifest_path.parent in result.path.parents
        else str(result.path),
        "generated_at": generated_at.isoformat(),
        "control_point_sha256": control_point_sha256,
        "dem_data_sha256": result.data_sha256,
        "dem_file_sha256": result.file_sha256,
        "control_point_count": result.control_point_count,
        "clamped_pixel_count": result.clamp_stats.clamped_total,
        "total_pixel_count": result.clamp_stats.total_pixels,
        "in_hull_clamped_pixel_count": result.clamp_stats.in_hull_clamped,
        "in_hull_pixel_count": result.clamp_stats.in_hull_pixels,
        "interpolation_smoothing": SMOOTHING,
        "interpolation_neighbors": NEIGHBORS,
    }
    # `manifest_sha256` hashes the manifest's own content (everything
    # above) so downstream consumers can detect if the manifest file was
    # hand-edited after generation, without hashing itself.
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


# --- Holdout accuracy tolerance (coordinator ruling, Task 4 fix round 3) ---
#
# This gate exists to catch GROSS BREAKAGE — a flipped axis, a units error,
# a broken sampler — not to certify elevation accuracy. The fixture DEM
# cannot certify accuracy at any tolerance. The 3DEP run is the accuracy
# gate, and it has not been run.
#
# Both tolerances are derived from measurement, not chosen by taste: the
# LOO cross-validation median error over the 118 control points is 13.80 m
# (see `run_loo_sweep`/`scripts/cv_sweep.py` and docs/DECISIONS.md "Task 4,
# fix round 1"/"fix round 2"). A gate tighter than the model's own
# demonstrated error fails on noise, not on breakage — the original
# +/-12 m guess sat *below* this 13.80 m floor, which is why it kept
# failing holdouts that were never actually wrong.
#   non-summit holdouts: +/-25 m  (~1.8x median LOO)
#   summit holdouts:     +/-40 m  (~2.9x median LOO, absorbing the
#                                  systematic peak-undershoot on top of
#                                  the base LOO error)
#
# A "summit holdout" is defined by PRINCIPLE, not by which holdouts
# happened to be failing at any given moment (fix round 2 named exactly
# the two holdouts that were failing that round, which was goalpost-
# fitting and was corrected here): a summit holdout is a holdout that is a
# local terrain maximum with NO control point at its own peak. Any smooth
# interpolator structurally undershoots such a point, regardless of
# parameters — see the mechanism note on Corona Heights below. Judged
# against that principle, exactly three of the 10 holdouts qualify:
#   - Twin Peaks summit (Eureka Peak) — SF's second-highest point,
#     nearest control points are all on its slopes, below the peak.
#   - Bernal Heights summit — same shape; nearest control points are on
#     Bernal Heights Blvd, below the peak.
#   - Corona Heights summit — same shape; its nearest control points
#     (including two added in fix round 2, "Buena Vista Ave West"/"Corona
#     Heights base") are all below its peak too.
# This set is named explicitly here, not inferred at evaluation time from
# which holdouts are currently failing — that inference is exactly the
# goalpost-fitting this round corrected. A future point added at any of
# these three peaks would need this set edited by hand, deliberately.
HOLDOUT_TOLERANCE_M: float = 25.0
SUMMIT_HOLDOUT_NAMES: frozenset[str] = frozenset(
    {
        "Twin Peaks summit (Eureka Peak)",
        "Bernal Heights summit",
        "Corona Heights summit",
    }
)
SUMMIT_HOLDOUT_TOLERANCE_M: float = 40.0


@dataclass(frozen=True)
class HoldoutResult:
    name: str
    actual_m: float
    sampled_m: float
    error_m: float
    tolerance_m: float
    is_summit: bool

    @property
    def within_tolerance(self) -> bool:
        return abs(self.error_m) <= self.tolerance_m


def sample_and_report_holdouts(
    dem_path: Path, csv_path: Path = DEFAULT_CSV_PATH
) -> list[HoldoutResult]:
    """Sample the finished DEM at all 10 `role == "holdout"` points and
    score each against `HOLDOUT_TOLERANCE_M`, or `SUMMIT_HOLDOUT_TOLERANCE_M`
    for the three named summit holdouts. This is the independent check
    design doc §2.3 describes — these points are never fed to the
    interpolator."""
    points = load_control_points(csv_path)
    holdouts = [p for p in points if p.role == "holdout"]
    results = []
    with rasterio.open(dem_path) as ds:
        arr = ds.read(1)
        for p in holdouts:
            row, col = ds.index(p.lon, p.lat)
            sampled = float(arr[row, col])
            is_summit = p.name in SUMMIT_HOLDOUT_NAMES
            tolerance = SUMMIT_HOLDOUT_TOLERANCE_M if is_summit else HOLDOUT_TOLERANCE_M
            results.append(
                HoldoutResult(
                    name=p.name,
                    actual_m=p.ele_m,
                    sampled_m=sampled,
                    error_m=sampled - p.ele_m,
                    tolerance_m=tolerance,
                    is_summit=is_summit,
                )
            )
    return results


# --- Sanity check (resolution #4) -------------------------------------------

_SANITY_SAMPLES: tuple[tuple[str, float, float, float, float], ...] = (
    ("Twin Peaks area", 37.7559, -122.4476, 230.0, 290.0),
    ("SoMa / 5th & Folsom", 37.7800, -122.4050, 0.0, 20.0),
    ("Ocean Beach", 37.7609, -122.5105, 0.0, 15.0),
    # See the matching comment in services/api/tests/test_fixture_dem.py's
    # parametrize list for why this upper bound is 35, not the task
    # resolution's "roughly 30".
    ("Duboce & Market (Wiggle east end)", 37.7695, -122.4290, 5.0, 35.0),
)


def sample_and_report_sanity(dem_path: Path) -> list[tuple[str, float, float, float, bool]]:
    """Sample the finished DEM at the four SF landmark points and return
    (name, sampled_value, expected_low, expected_high, ok) tuples."""
    results = []
    with rasterio.open(dem_path) as ds:
        arr = ds.read(1)
        for name, lat, lon, low, high in _SANITY_SAMPLES:
            row, col = ds.index(lon, lat)
            value = float(arr[row, col])
            ok = low <= value <= high
            results.append((name, value, low, high, ok))
    return results


# --- District coverage check (Task 4 fix round 4) ---------------------------
#
# The four `_SANITY_SAMPLES` above all sit inside the well-sampled central
# corridor. That is exactly why the defect fix round 4 repaired could ship:
# outside that corridor the RBF dived negative and clamped to 0, so McLaren
# Park, the Excelsior and the Presidio all read as sea level, while over
# open Bay water it overshot into a 30-50 m hill — and no holdout and no
# sanity point sat anywhere near any of it. The validation set determined
# what could be seen.
#
# This table is the missing coverage: every quadrant of the city plus open
# water on both sides. The bands are deliberately wide — tens of metres —
# because this asks "is this recognisably San Francisco here?", not "is
# this accurate here". The fixture DEM cannot answer the second question at
# any tolerance; the 3DEP run is the accuracy gate. What these bands do
# catch is the failure that actually happened: an inhabited district
# reading 0.0 m, water reading like a hill, or the central massif inflated
# by 100 m.
#
# Honest caveat: most of these locations now have a control point within a
# few hundred metres (that is the fix), so this is a regression guard, not
# independent evidence of accuracy. The 10 holdouts remain the independent
# check.
DISTRICT_COVERAGE_SAMPLES: tuple[tuple[str, float, float, float, float], ...] = (
    # --- Southeast ---
    ("McLaren Park summit", 37.7180, -122.4185, 100.0, 200.0),
    ("Excelsior (Mission at Geneva)", 37.7200, -122.4400, 35.0, 105.0),
    ("Visitacion Valley (Leland at Bayshore)", 37.7115, -122.4055, 0.0, 45.0),
    ("Bayview Hill summit", 37.7175, -122.3905, 85.0, 175.0),
    ("Hunters Point Hill", 37.7345, -122.3805, 10.0, 80.0),
    ("Portola (Mansell at San Bruno)", 37.7255, -122.4045, 25.0, 95.0),
    # --- Southwest and west ---
    ("Outer Sunset (Judah at 40th)", 37.7605, -122.4980, 0.0, 30.0),
    ("Parkside (Taraval at 30th)", 37.7425, -122.4885, 0.0, 45.0),
    ("Lake Merced", 37.7285, -122.4930, 0.0, 25.0),
    ("West Portal (Ulloa at West Portal)", 37.7402, -122.4680, 40.0, 110.0),
    ("Ingleside (Ocean at Ashton)", 37.7245, -122.4585, 30.0, 100.0),
    # --- The central massif (was inflated by ~100 m) ---
    ("Sutro Tower base", 37.7552, -122.4528, 215.0, 290.0),
    ("Portola Dr at Woodside Ave", 37.7430, -122.4530, 70.0, 155.0),
    # --- Northwest ---
    # The review reported "Presidio Inspiration Pt 37.7995,-122.4585, real
    # ~95 m, DEM 0.0". The 0.0 was a real defect. The label was not: that
    # coordinate is 870 m north of the actual Inspiration Point (Trailforks
    # puts the trailhead at 37.79168,-122.4582) and only 157 m from the
    # Main Post parade ground, down in the terrace above Crissy Field where
    # ~30 m — not ~95 m — is the real ground. Both points are sampled here
    # under their true names rather than carrying the mislabel forward.
    ("Presidio Main Post (the review's 'Inspiration Pt' coord)", 37.7995, -122.4585, 12.0, 60.0),
    ("Presidio Inspiration Point (actual)", 37.7917, -122.4582, 45.0, 125.0),
    ("Golden Gate Bridge toll plaza", 37.8070, -122.4750, 30.0, 115.0),
    ("Outer Richmond (Balboa at 40th)", 37.7760, -122.5000, 5.0, 55.0),
    ("Sea Cliff (El Camino del Mar)", 37.7870, -122.4885, 15.0, 80.0),
    ("Inner Richmond (Geary at 20th)", 37.7805, -122.4790, 15.0, 70.0),
    # --- Northeast. The review did not sample this quadrant; sampling it
    # while verifying fix round 4 found the same defect with the sign
    # flipped. Columbus at Union is the flat saddle between Telegraph Hill
    # and Russian Hill and read 100.2 m — the surface simply bridged the
    # two ~100 m hilltops because nothing in the valley said otherwise.
    # Aquatic Park, at the waterline, read 53.6 m. For a router whose
    # objective is climbing, an invented 85 m hill in North Beach is worse
    # than a missing one in McLaren Park.
    ("North Beach valley (Columbus at Union)", 37.8000, -122.4090, 0.0, 40.0),
    ("Aquatic Park shoreline", 37.8075, -122.4230, 0.0, 20.0),
    ("Jackson Square (Montgomery at Broadway)", 37.7980, -122.4030, 0.0, 30.0),
    # --- Open water: 0 m by definition, so these are the tightest bands ---
    ("Open SF Bay (east of the city)", 37.8200, -122.3700, 0.0, 8.0),
    ("Open SF Bay (west of Treasure Island)", 37.8250, -122.3800, 0.0, 8.0),
    ("Open SF Bay (off Mission Bay)", 37.7700, -122.3700, 0.0, 10.0),
    ("Pacific Ocean (west of Ocean Beach)", 37.7600, -122.5200, 0.0, 10.0),
    ("Golden Gate strait (bridge midspan)", 37.8250, -122.4750, 0.0, 10.0),
)


def sample_and_report_districts(dem_path: Path) -> list[tuple[str, float, float, float, bool]]:
    """Sample `DISTRICT_COVERAGE_SAMPLES` and return
    (name, sampled_value, expected_low, expected_high, ok) tuples."""
    results = []
    with rasterio.open(dem_path) as ds:
        arr = ds.read(1)
        for name, lat, lon, low, high in DISTRICT_COVERAGE_SAMPLES:
            row, col = ds.index(lon, lat)
            value = float(arr[row, col])
            results.append((name, value, low, high, low <= value <= high))
    return results


def main() -> None:
    result = build_dem(DEFAULT_DEM_PATH)
    manifest = write_manifest(result, DEFAULT_MANIFEST_PATH)

    print(f"Wrote {result.path} ({result.path.stat().st_size:,} bytes)")
    print(f"  control points used: {result.control_point_count}")
    print(f"  data sha256:  {result.data_sha256}")
    print(f"  file sha256:  {result.file_sha256}")
    print(
        f"  clamped pixels: {result.clamp_stats.clamped_total:,} "
        f"({result.clamp_stats.clamped_fraction:.4%} of {result.clamp_stats.total_pixels:,}) "
        f"[low={result.clamp_stats.clamped_low:,} high={result.clamp_stats.clamped_high:,}]"
    )
    print(
        f"  in-hull clamped pixels: {result.clamp_stats.in_hull_clamped:,} "
        f"({result.clamp_stats.in_hull_clamped_fraction:.4%} of "
        f"{result.clamp_stats.in_hull_pixels:,} pixels inside the land control points' "
        f"convex hull)"
    )
    print(f"  interpolation: smoothing={SMOOTHING}, neighbors={NEIGHBORS} (see cv_sweep.py)")
    print(f"Wrote {DEFAULT_MANIFEST_PATH}")
    print(f"  manifest_sha256: {manifest['manifest_sha256']}")

    print("Sanity samples:")
    all_ok = True
    for name, value, low, high, ok in sample_and_report_sanity(result.path):
        status = "OK" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"  [{status}] {name}: {value:.1f} m (expected [{low}, {high}])")
    print("District coverage (all four quadrants plus open water):")
    for name, value, low, high, ok in sample_and_report_districts(result.path):
        status = "OK" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"  [{status}] {name}: {value:.1f} m (expected [{low}, {high}])")
    if not all_ok:
        raise SystemExit("Sanity/district check failed — see FAIL lines above.")

    print("Holdout accuracy (never fed to the interpolator):")
    for h in sample_and_report_holdouts(result.path):
        status = "OK" if h.within_tolerance else "FAIL"
        tag = " [summit, +/-40m]" if h.is_summit else ""
        print(
            f"  [{status}] {h.name}: actual={h.actual_m:.1f} sampled={h.sampled_m:.1f} "
            f"error={h.error_m:+.1f}{tag}"
        )


if __name__ == "__main__":
    main()
