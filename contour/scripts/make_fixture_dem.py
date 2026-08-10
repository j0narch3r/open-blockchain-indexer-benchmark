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

KERNEL: str = "thin_plate_spline"

# `smoothing` and `neighbors` are selected by `select_best(run_loo_sweep(...))`
# below (leave-one-out cross-validation over the 105 control points), NOT
# by looking at holdout error — see `scripts/cv_sweep.py` and
# docs/DECISIONS.md "Task 4, fix round 1" for the full sweep table and
# reasoning. `test_interpolation_parameters_match_cv_sweep_winner` pins
# these two literals to that function's actual output so they cannot
# silently drift from the sweep that justifies them.
#
# The brief's original guess of `smoothing=0.5` undershot the two summit
# holdouts (Twin Peaks -33.5 m, Bernal Heights -21.1 m). The sweep found
# `smoothing` has essentially *no* measurable effect across the tested
# values (0, 0.1, 0.5, 2.0) given this module's local-metres coordinate
# scale — thin-plate-spline kernel magnitudes here run ~1e4-1e8, dwarfing
# an additive smoothing term of 0-2 — the four smoothing values differ
# only in the noise floor of the p90 metric (92.22 m vs 92.23 m, i.e.
# nothing); `2.0` is the literal argmin `select_best` returns, not a
# meaningfully "better" choice than 0/0.1/0.5. `neighbors=20` is the real
# signal: lowest median (14.43 m vs 14.55 m global) AND lowest p90
# (92.22 m vs 97.15 m global) among the swept values. It does NOT fix the
# summit undershoot (Twin Peaks -33.3 m, Bernal Heights -20.9 m with this
# config — virtually unchanged) and it slightly *increases* in-hull
# clamping (7.67% vs 7.27% for the old global config). Kept anyway,
# exactly as selected, because the whole point of this sweep is that
# parameters come from this measurement, not from post-hoc adjustment
# against the holdouts it exists to check independently.
SMOOTHING: float = 2.0
NEIGHBORS: int | None = 20

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


def load_control_points(csv_path: Path) -> tuple[ControlPoint, ...]:
    """Read and validate the control-point CSV.

    Validates: every point inside `SF_BBOX`, every `ele_m` in
    `[CSV_ELEVATION_MIN_M, CSV_ELEVATION_MAX_M]`, exactly
    `EXPECTED_HOLDOUT_COUNT` holdout rows, no duplicate (lat, lon) pairs.
    Raises `ControlPointValidationError` naming the first violation found
    rather than attempting to repair the data.
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
    # Pixels clamped that also fall inside the convex hull of the control
    # points — i.e. nominally "should have real land data nearby," as
    # opposed to the open Bay/ocean pixels outside the hull where there is
    # no control data at all and clamping to sea level is expected. See
    # docs/DECISIONS.md "Task 4, fix round 1".
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

    in_hull = _in_convex_hull(xy, grid_xy)
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
        f"{result.clamp_stats.in_hull_pixels:,} pixels inside the control points' convex hull)"
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
    if not all_ok:
        raise SystemExit("Sanity check failed — see FAIL lines above.")


if __name__ == "__main__":
    main()
