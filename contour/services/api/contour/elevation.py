"""Geodesic polyline resampling and bilinear DEM sampling (design doc §4.4).

This module owns all DEM access (design doc §4.1's dependency diagram) and
the geodesic geometry helpers Task 6 builds the elevation profile, ascent
hysteresis, and grade computation on top of. Two responsibilities, kept in
one module because they share no state but are always used together:

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

from contour.types import LonLat

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


__all__ = ["DemSampler", "geodesic_length_m", "resample_polyline"]
