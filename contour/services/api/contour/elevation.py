"""Geodesic polyline resampling, bilinear DEM sampling, and elevation
profile assembly (design doc §4.4).

This module owns all DEM access (design doc §4.1's dependency diagram).
Task 5 built the geometry/sampling primitives; Task 6 builds the
elevation-profile algorithms on top of them:

- `resample_polyline` / `geodesic_length_m` — geodesic (not planar)
  interpolation along a route's geometry using `pyproj.Geod(ellps="WGS84")`.
  SF spans enough longitude that planar (equirectangular) interpolation
  drifts measurably over a multi-kilometre route; `Geod.inv`/`Geod.fwd`
  compute exact ellipsoidal distance and azimuth instead.
- `DemSampler` — bilinear sampling of a DEM COG, opened once and reused.
  `rasterio`'s own `sample()` is nearest-neighbour only, which stair-steps
  a 10 m grid sampled at 10 m spacing into alternating 0%/8% phantom
  grades (design doc §4.4 "Sampling"); bilinear interpolation is
  hand-rolled here instead (see `docs/DECISIONS.md` for why no dependency
  was added for it).
- `smooth` — Savitzky-Golay filtering of a raw sampled elevation series
  (design doc §4.4 "Smoothing"). A moving average flattens genuine hill
  crests; Savitzky-Golay preserves peak amplitude while still removing
  high-frequency DEM sampling noise. See `docs/DECISIONS.md` for the full
  justification (also pinned by a test contrasting the two directly).
- `accumulate_relief` — ascent/descent via peak-valley hysteresis (design
  doc §4.4 "Ascent with hysteresis"). Naive per-sample thresholding is
  wrong in both directions: filtering every delta below `min_rise_m`
  reports zero ascent for a long, genuinely-climbing shallow grade, and
  filtering nothing at all reports phantom ascent from sub-metre DEM
  noise on a flat road. Ascent and descent accumulate independently —
  descent never offsets ascent (Global Constraints: "Descent never
  credits effort").
- `windowed_grades` — centred-difference grade over a multi-sample window
  (design doc §4.4 "Grade"), not sample-to-sample, because a 10 m
  baseline on 10 m data amplifies noise into phantom double-digit grades.
- `build_profile` — the pipeline: resample -> sample -> smooth ->
  accumulate -> grade -> `ElevationProfile`.

Coordinate order is always `(lon, lat)` — see `types.LonLat` and Global
Constraints. Getting this backwards is the classic geospatial bug; several
tests in `tests/test_elevation_sampling.py` exist specifically to catch it.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Geod
from scipy.signal import savgol_filter

from contour.constants import (
    GRADE_WINDOW_SAMPLES,
    MIN_RISE_M,
    PROFILE_SAMPLE_M,
    SMOOTH_POLY_ORDER,
    SMOOTH_WINDOW_SAMPLES,
)
from contour.types import ElevationProfile, LonLat, ProfilePoint

_GEOD = Geod(ellps="WGS84")

# Below this, an edge is treated as zero-length (consecutive duplicate
# coordinates) and skipped rather than stepped along — real routing output
# contains exact duplicates (e.g. a snapped waypoint repeated at a segment
# boundary), and computing an azimuth for a zero-length edge is undefined.
# `Geod.inv` returns exactly 0.0 for identical points in float64, so this
# threshold only exists to absorb float noise, not to merge genuinely
# distinct nearby points.
_ZERO_LENGTH_EPS_M = 1e-6

# Floating-point slack when comparing an accumulated offset against the
# polyline's total geodesic length, so the last regular-spacing sample
# isn't emitted a second time (as a near-duplicate) right before the
# explicit final point.
_TOTAL_LENGTH_EPS_M = 1e-6


def geodesic_length_m(coords: Sequence[LonLat]) -> float:
    """Total geodesic length of a polyline, in metres.

    Sums `Geod.inv`'s ellipsoidal distance across consecutive coordinate
    pairs. Zero-length and single-point inputs return `0.0` rather than
    raising — both occur in real routing output (design doc §4.4 / brief
    "Degenerate inputs are not hypothetical").
    """
    if len(coords) < 2:
        return 0.0

    total = 0.0
    for (lon1, lat1), (lon2, lat2) in pairwise(coords):
        _fwd_az, _back_az, dist = _GEOD.inv(lon1, lat1, lon2, lat2)
        total += dist
    return total


def resample_polyline(coords: Sequence[LonLat], spacing_m: float) -> list[tuple[LonLat, float]]:
    """Resample a polyline at fixed geodesic spacing.

    Returns `(point, cumulative_distance_m)` pairs, starting at `(coords[0],
    0.0)` and ending at exactly `(coords[-1], geodesic_length_m(coords))` —
    the first and last input coordinates are preserved exactly (not
    recomputed via `Geod.fwd`, which could round differently than the
    caller's own floats), per the brief's step 1 requirement.

    Interior samples are computed by walking each edge with `Geod.inv`
    (per-edge distance + forward azimuth) and stepping along it with
    `Geod.fwd`, so the interpolation follows the ellipsoid rather than a
    planar approximation.

    Degenerate inputs:

    - Empty input returns `[]`.
    - A single-point input returns `[(coords[0], 0.0)]`.
    - Consecutive duplicate coordinates (zero-length edges, within
      `_ZERO_LENGTH_EPS_M`) are skipped rather than stepped along — an
      azimuth is undefined for a zero-length edge, and skipping avoids
      dividing by (or stepping along) a zero-length step. If *every* edge
      turns out to be zero-length (all input points identical), the result
      is a single sample at the shared coordinate, same as the
      single-point case.
    """
    if spacing_m <= 0:
        raise ValueError(f"spacing_m must be positive, got {spacing_m!r}")
    if len(coords) == 0:
        return []
    if len(coords) == 1:
        return [(coords[0], 0.0)]

    # (edge_start, azimuth_deg, edge_length_m) for every non-degenerate edge.
    segments: list[tuple[LonLat, float, float]] = []
    for (lon1, lat1), (lon2, lat2) in pairwise(coords):
        forward_az, _back_az, dist = _GEOD.inv(lon1, lat1, lon2, lat2)
        if dist <= _ZERO_LENGTH_EPS_M:
            continue
        segments.append(((lon1, lat1), forward_az, dist))

    if not segments:
        # Every input coordinate collapses to the same point.
        return [(coords[0], 0.0)]

    total_length_m = sum(length for _start, _az, length in segments)

    samples: list[tuple[LonLat, float]] = [(segments[0][0], 0.0)]
    seg_idx = 0
    cum_before_m = 0.0
    offset_m = spacing_m
    while offset_m < total_length_m - _TOTAL_LENGTH_EPS_M:
        while (
            offset_m > cum_before_m + segments[seg_idx][2] + _TOTAL_LENGTH_EPS_M
            and seg_idx < len(segments) - 1
        ):
            cum_before_m += segments[seg_idx][2]
            seg_idx += 1

        (start_lon, start_lat), azimuth, _length = segments[seg_idx]
        local_offset_m = offset_m - cum_before_m
        lon, lat, _back_az = _GEOD.fwd(start_lon, start_lat, azimuth, local_offset_m)
        samples.append(((lon, lat), offset_m))
        offset_m += spacing_m

    samples.append((coords[-1], total_length_m))
    return samples


class DemSampler:
    """Bilinear DEM sampler over a single-band GeoTIFF/COG.

    Opens `dem_path` once in `__init__` and keeps the dataset (and its one
    band, read fully into memory) for the instance's lifetime — reopening
    per call would dominate the route-request p95 latency budget (1200 ms
    for a whole route, which samples thousands of points; see
    `docs/DECISIONS.md`). The fixture DEM is a few megapixels, so holding
    the band in memory is cheap; a real 3DEP-scale COG would need windowed
    reads instead, which is out of scope here (design doc's stated
    fixtures-only constraint for this environment).

    `sample()` is vectorized with numpy over the whole `points` sequence —
    it never loops a `rasterio` read per point.

    Bilinear interpolation is hand-rolled (`rasterio.sample()` is
    nearest-neighbour only): each query point's (lon, lat) is converted to
    a fractional (row, col) via the dataset's inverse affine transform, the
    surrounding 2x2 pixel-centre neighbourhood is read, and the four values
    are interpolated. A point in the outermost half-pixel margin — inside
    the raster's bounding box, but without a full 2x2 neighbourhood on one
    side — is clamped to the nearest valid neighbourhood rather than
    rejected: real coastal/edge points are legitimate queries and
    shouldn't error just for being near the grid boundary. A point outside
    the raster's bounding box, or one whose neighbourhood touches a masked
    (nodata) pixel, raises `ValueError` — silently returning a nodata
    sentinel would get averaged into a downstream elevation profile
    without anyone noticing.
    """

    def __init__(self, dem_path: Path) -> None:
        self._dataset = rasterio.open(dem_path)
        band = self._dataset.read(1, masked=True)
        if band.ndim != 2:
            raise ValueError(f"expected a single-band 2D raster, got shape {band.shape!r}")

        self._values = np.ma.getdata(band).astype(np.float64)
        self._nodata_mask = np.ma.getmaskarray(band)
        self._height, self._width = self._values.shape
        self._inv_transform = ~self._dataset.transform

        left, bottom, right, top = self._dataset.bounds
        self._bounds = (left, bottom, right, top)

    def __del__(self) -> None:
        # Best-effort close; `_dataset` may not exist if rasterio.open()
        # itself raised (e.g. a bad path) before it was assigned.
        dataset = getattr(self, "_dataset", None)
        if dataset is not None:
            dataset.close()

    def sample(self, points: Sequence[LonLat]) -> np.ndarray:
        """Bilinearly sample the DEM at each `(lon, lat)` point.

        Returns a `float64` array of elevations in metres, one per input
        point, in input order. Raises `ValueError` if any point falls
        outside the raster's bounding box, or if any point's 2x2
        interpolation neighbourhood touches a masked (nodata) pixel.
        """
        if len(points) == 0:
            return np.empty(0, dtype=np.float64)

        lons = np.asarray([p[0] for p in points], dtype=np.float64)
        lats = np.asarray([p[1] for p in points], dtype=np.float64)

        left, bottom, right, top = self._bounds
        outside = (lons < left) | (lons > right) | (lats < bottom) | (lats > top)
        if np.any(outside):
            bad_idx = int(np.flatnonzero(outside)[0])
            raise ValueError(
                f"point {points[bad_idx]!r} lies outside the DEM extent "
                f"(lon in [{left}, {right}], lat in [{bottom}, {top}])"
            )

        # `~transform * (x, y)` gives fractional (col, row) measured from
        # the pixel *corner* grid (GDAL convention: integer (col, row) is a
        # pixel's upper-left corner, so pixel i's centre is at col=i+0.5).
        # Shift by -0.5 to get coordinates in "pixel-centre" units, where
        # flooring gives the index of the neighbouring centre at or before
        # the query point.
        cols_frac, rows_frac = self._inv_transform * (lons, lats)
        cols = np.asarray(cols_frac, dtype=np.float64) - 0.5
        rows = np.asarray(rows_frac, dtype=np.float64) - 0.5

        # Clamp into the interior so a full 2x2 neighbourhood always
        # exists — this is the "outermost half-pixel" edge case (resolution
        # #2): a point inside the raster's bounds but past the last pixel
        # centre gets pinned to the nearest valid neighbourhood instead of
        # raising.
        cols = np.clip(cols, 0.0, self._width - 1 - 1e-9)
        rows = np.clip(rows, 0.0, self._height - 1 - 1e-9)

        col0 = np.floor(cols).astype(np.int64)
        row0 = np.floor(rows).astype(np.int64)
        col1 = col0 + 1
        row1 = row0 + 1

        frac_x = cols - col0
        frac_y = rows - row0

        neighborhood_mask = (
            self._nodata_mask[row0, col0]
            | self._nodata_mask[row0, col1]
            | self._nodata_mask[row1, col0]
            | self._nodata_mask[row1, col1]
        )
        if np.any(neighborhood_mask):
            bad_idx = int(np.flatnonzero(neighborhood_mask)[0])
            raise ValueError(
                f"point {points[bad_idx]!r} samples a masked/nodata DEM pixel "
                "(its bilinear neighbourhood includes a nodata cell)"
            )

        v00 = self._values[row0, col0]
        v01 = self._values[row0, col1]
        v10 = self._values[row1, col0]
        v11 = self._values[row1, col1]

        top_interp = v00 * (1.0 - frac_x) + v01 * frac_x
        bottom_interp = v10 * (1.0 - frac_x) + v11 * frac_x
        values: np.ndarray = top_interp * (1.0 - frac_y) + bottom_interp * frac_y
        return values


def smooth(ele: np.ndarray) -> np.ndarray:
    """Savitzky-Golay smoothing of a sampled elevation series (design doc
    §4.4 "Smoothing").

    Window `SMOOTH_WINDOW_SAMPLES` (9 samples = 90 m at
    `PROFILE_SAMPLE_M` spacing), polynomial order `SMOOTH_POLY_ORDER` (2),
    `mode="nearest"` so the filter doesn't need to invent values past the
    ends of a route. See `docs/DECISIONS.md` for why this is
    Savitzky-Golay and not a moving average: a moving average is a local
    *mean*, which flattens the curvature at a genuine hill crest along
    with the noise; Savitzky-Golay fits a local polynomial and so tracks
    real curvature while still rejecting high-frequency sampling noise.
    `test_moving_average_loses_materially_more_peak_amplitude_than_savgol`
    (tests/test_elevation_profile.py) pins the contrast directly rather
    than asserting it only in prose.

    Short-circuits to the identity (a copy, cast to `float64`) when `ele`
    has fewer samples than the window — `scipy.signal.savgol_filter`
    raises in that case, and short routes (a single block, a snapped
    walk-up) are real inputs, not an edge case to reject.
    """
    ele = np.asarray(ele, dtype=np.float64)
    if ele.shape[0] < SMOOTH_WINDOW_SAMPLES:
        return ele.copy()
    result: np.ndarray = savgol_filter(
        ele, window_length=SMOOTH_WINDOW_SAMPLES, polyorder=SMOOTH_POLY_ORDER, mode="nearest"
    )
    return result


def accumulate_relief(ele: np.ndarray, min_rise_m: float) -> tuple[float, float]:
    """Total ascent and descent via peak-valley hysteresis (design doc
    §4.4 "Ascent with hysteresis").

    Naive per-sample thresholding is wrong in both directions: filtering
    every delta below `min_rise_m` reports **zero** ascent for a long run
    of small-but-consistent uphill steps that sum to a real climb;
    filtering nothing at all reports ascent from sub-metre DEM sampling
    noise on a flat road. The correct algorithm tracks a running extreme
    and only commits a completed leg (as ascent or descent) once the
    series has reversed by more than `min_rise_m` from that extreme.

    Two phases, because the very first leg's direction is not yet known:

    1. **Undetermined** — from the first sample, track both a running
       high and a running low simultaneously. Neither is "confirmed" as
       the start of a climb or descent until one of them has moved more
       than `min_rise_m` away from the anchor (`ele[0]`). Until that
       happens, nothing is committed — this is what makes sub-threshold
       oscillation around a flat baseline correctly report zero ascent
       *and* zero descent, rather than crediting whatever small drift
       happened to be visible when the series ends
       (`test_ascent_ignores_subthreshold_noise`).
    2. **Determined** (up or down) — track the running extreme in that
       direction; when the series reverses by more than `min_rise_m` from
       it, commit the completed leg (extreme minus the last confirmed
       pivot) to `ascent` or `descent`, set the pivot to that extreme, and
       flip direction.

    The boundary is inclusive: a reversal of *exactly* `min_rise_m`
    commits (`test_ascent_hysteresis_commits_at_exact_threshold_boundary`
    pins this both ways). The final open leg (whatever hasn't reversed by
    the end of the series) is always committed — this is what makes a
    long, never-reversing shallow climb count in full
    (`test_ascent_accumulates_long_shallow_climb`), including when that
    leg happens to still be in the undetermined phase (nothing committed:
    a genuinely flat series ends undetermined, so both totals stay 0.0).

    Ascent and descent accumulate independently in separate running
    totals — descent is never subtracted from ascent, and vice versa
    (Global Constraints: "Descent never credits effort").

    Returns `(ascent_m, descent_m)` as plain Python `float`s.
    """
    ele = np.asarray(ele, dtype=np.float64)
    n = ele.shape[0]
    if n == 0:
        return 0.0, 0.0

    ascent = 0.0
    descent = 0.0

    pivot = float(ele[0])  # last confirmed turning point
    direction = 0  # 0 = undetermined, 1 = up, -1 = down

    # While undetermined, track both a running high and a running low
    # since the anchor; while determined, only the active direction's
    # extreme is meaningful (the other variable is simply not read).
    hi = pivot
    lo = pivot

    for raw in ele[1:]:
        x = float(raw)

        if direction == 0:
            hi = max(hi, x)
            lo = min(lo, x)
            if hi - pivot >= min_rise_m:
                direction = 1
            elif pivot - lo >= min_rise_m:
                direction = -1
            continue

        if direction == 1:
            hi = max(hi, x)
            if x <= hi - min_rise_m:
                ascent += hi - pivot
                pivot = hi
                direction = -1
                lo = x
        else:  # direction == -1
            lo = min(lo, x)
            if x >= lo + min_rise_m:
                descent += pivot - lo
                pivot = lo
                direction = 1
                hi = x

    # Commit the final open leg, whatever direction it's in. An
    # undetermined series (never moved more than min_rise_m from its
    # start in either direction) commits nothing, by design.
    if direction == 1:
        ascent += hi - pivot
    elif direction == -1:
        descent += pivot - lo

    return float(ascent), float(descent)


def windowed_grades(ele: np.ndarray, spacing_m: float, window: int) -> np.ndarray:
    """Centred-difference grade, in percent, over a `window`-sample
    baseline (design doc §4.4 "Grade").

    A 10 m baseline sampled at 10 m spacing amplifies ordinary DEM noise
    into phantom double-digit grades; averaging the rise over a wider,
    centred window damps that without smearing genuine block-scale grade
    changes away (the window is 3 samples = 30 m by default,
    `GRADE_WINDOW_SAMPLES`, well under the ~150 m shortest real SF block).

    Near the two ends of the series, where a full centred window would
    reach past the array, the window **shrinks** to whatever samples
    exist on that side rather than padding with fabricated values — the
    baseline distance shrinks along with it, so the percentage stays a
    real "rise over run" computed only from real samples
    (`test_windowed_grades_edges_shrink_not_pad`).

    `ele` is assumed to be regularly spaced at `spacing_m` — the caller
    (`build_profile`) passes `PROFILE_SAMPLE_M`, matching
    `resample_polyline`'s nominal spacing. `resample_polyline`'s own
    final sample can fall slightly short of that nominal spacing (it
    always ends exactly on the route's last coordinate); this only
    perturbs the grade estimate in the last `window // 2` samples of a
    route by the same small amount `resample_polyline` already documents,
    not a full baseline's worth of error.
    """
    ele = np.asarray(ele, dtype=np.float64)
    n = ele.shape[0]
    if n == 0:
        return np.empty(0, dtype=np.float64)
    if n == 1:
        return np.zeros(1, dtype=np.float64)

    half = window // 2
    idx = np.arange(n)
    left = np.clip(idx - half, 0, n - 1)
    right = np.clip(idx + half, 0, n - 1)

    delta_ele = ele[right] - ele[left]
    delta_dist = (right - left).astype(np.float64) * spacing_m
    # left == right can't happen for n >= 2 (right/left clip to opposite
    # ends of a >=2-length array whenever half >= 1), but guard division
    # by zero defensively rather than relying on that invariant silently.
    safe_delta_dist = np.where(delta_dist > 0, delta_dist, 1.0)
    grades: np.ndarray = np.where(delta_dist > 0, delta_ele / safe_delta_dist * 100.0, 0.0)
    return grades


def build_profile(coords: Sequence[LonLat], sampler: DemSampler) -> ElevationProfile:
    """Assemble an `ElevationProfile` for a route's geometry (design doc
    §4.4, task-6-brief.md resolution #1).

    Pipeline, in order: geodesic resample at `PROFILE_SAMPLE_M` ->
    bilinear DEM sample -> Savitzky-Golay smooth -> hysteresis
    ascent/descent -> windowed grade. Smoothing runs exactly once, on the
    raw sampled series, before both `accumulate_relief` and
    `windowed_grades` — so ascent/descent and the grade series are
    computed from the *same* smoothed elevations, not smoothed
    independently or smoothed twice.

    `max_grade_pct` is the peak magnitude of the windowed grade series
    (not a raw sample-to-sample delta), so a single noisy pixel can't
    drive the steepness warnings the product's honesty claims rest on.

    An empty `coords` (or a `coords` that resamples to zero points, per
    `resample_polyline`'s own degenerate-input handling) returns an empty
    profile rather than raising.
    """
    resampled = resample_polyline(coords, PROFILE_SAMPLE_M)
    if not resampled:
        return ElevationProfile(points=(), ascent_m=0.0, descent_m=0.0, max_grade_pct=0.0)

    points_lonlat = [point for point, _dist_m in resampled]
    dists_m = [dist_m for _point, dist_m in resampled]

    raw_ele = sampler.sample(points_lonlat)
    smoothed = smooth(raw_ele)

    ascent_m, descent_m = accumulate_relief(smoothed, MIN_RISE_M)
    grades = windowed_grades(smoothed, PROFILE_SAMPLE_M, GRADE_WINDOW_SAMPLES)
    max_grade_pct = float(np.max(np.abs(grades))) if grades.size else 0.0

    points = tuple(
        ProfilePoint(dist_m=float(dist_m), ele_m=float(ele_m))
        for dist_m, ele_m in zip(dists_m, smoothed, strict=True)
    )

    return ElevationProfile(
        points=points,
        ascent_m=ascent_m,
        descent_m=descent_m,
        max_grade_pct=max_grade_pct,
    )


__all__ = [
    "DemSampler",
    "accumulate_relief",
    "build_profile",
    "geodesic_length_m",
    "resample_polyline",
    "smooth",
    "windowed_grades",
]
