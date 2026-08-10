"""Tests for `contour.elevation` — geodesic polyline resampling and
bilinear DEM sampling (design doc §4.4, task-5-brief.md).

The DEM tests run against the real fixture COG at `data/dem/sf_fixture_dem.tif`
(built by `make fixtures`, Task 4) for the "known landmark" and "lon/lat
order" checks, and against small synthetic in-memory rasters (built here)
for the bilinear-vs-nearest contrast, the nodata/masked-pixel error, and
the edge-clamping behaviour — those need exact, hand-picked values that
the real DEM's smoothed surface can't guarantee.
"""

from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import Geod

from contour.elevation import DemSampler, geodesic_length_m, resample_polyline
from contour.types import LonLat

_GEOD = Geod(ellps="WGS84")

_CONTOUR_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_DEM_PATH = _CONTOUR_ROOT / "data" / "dem" / "sf_fixture_dem.tif"

# Known landmark coordinates, cross-checked against
# tests/test_fixture_dem.py's own shape-sanity-check parametrization and a
# direct sample of the committed fixture DEM.
_TWIN_PEAKS: LonLat = (-122.4476, 37.7559)  # ~230-290 m (hill)
_SOMA: LonLat = (-122.4050, 37.7800)  # ~0-20 m (flat, near sea level)


# ---------------------------------------------------------------------------
# geodesic_length_m
# ---------------------------------------------------------------------------


def test_geodesic_length_m_of_empty_is_zero() -> None:
    assert geodesic_length_m([]) == 0.0


def test_geodesic_length_m_of_single_point_is_zero() -> None:
    assert geodesic_length_m([(-122.42, 37.77)]) == 0.0


def test_geodesic_length_m_matches_geod_inv_directly() -> None:
    a: LonLat = (-122.4194, 37.7749)
    b: LonLat = (-122.4194, 37.7849)  # ~0.01 deg north
    _fwd, _back, expected = _GEOD.inv(a[0], a[1], b[0], b[1])
    assert geodesic_length_m([a, b]) == pytest.approx(expected, abs=1e-6)


def test_geodesic_length_m_sums_across_multiple_vertices() -> None:
    a: LonLat = (-122.42, 37.77)
    b: LonLat = (-122.42, 37.775)
    c: LonLat = (-122.415, 37.775)
    _f1, _b1, d1 = _GEOD.inv(a[0], a[1], b[0], b[1])
    _f2, _b2, d2 = _GEOD.inv(b[0], b[1], c[0], c[1])
    assert geodesic_length_m([a, b, c]) == pytest.approx(d1 + d2, abs=1e-6)


# ---------------------------------------------------------------------------
# resample_polyline — regular cases
# ---------------------------------------------------------------------------


def test_resample_polyline_of_empty_input_is_empty() -> None:
    assert resample_polyline([], 10.0) == []


def test_resample_polyline_rejects_non_positive_spacing() -> None:
    with pytest.raises(ValueError, match="spacing_m"):
        resample_polyline([(-122.42, 37.77), (-122.41, 37.78)], 0.0)
    with pytest.raises(ValueError, match="spacing_m"):
        resample_polyline([(-122.42, 37.77), (-122.41, 37.78)], -5.0)


def test_resample_polyline_single_point_returns_one_sample_at_zero() -> None:
    point: LonLat = (-122.42, 37.77)
    assert resample_polyline([point], 10.0) == [(point, 0.0)]


def test_resample_1km_north_south_line_has_101_points() -> None:
    """Brief step 1: a straight 1 km north-south line resamples to 101
    points (0, 10, 20, ..., 1000 m at 10 m spacing) with the final
    cumulative distance within 0.5 m of `pyproj.Geod`'s direct answer."""
    start: LonLat = (-122.42, 37.77)
    _lon, lat_1km, _back_az = _GEOD.fwd(start[0], start[1], 0.0, 1000.0)
    end: LonLat = (start[0], lat_1km)

    samples = resample_polyline([start, end], 10.0)

    assert len(samples) == 101
    expected_length = geodesic_length_m([start, end])
    assert samples[-1][1] == pytest.approx(expected_length, abs=0.5)
    assert samples[-1][1] == pytest.approx(1000.0, abs=0.5)


def test_resample_polyline_preserves_exact_first_and_last_coordinates() -> None:
    start: LonLat = (-122.4321, 37.76543)
    end: LonLat = (-122.4001, 37.78123)
    samples = resample_polyline([start, end], 10.0)
    assert samples[0][0] == start
    assert samples[0][1] == 0.0
    assert samples[-1][0] == end


def test_resample_polyline_round_trip_accuracy_multi_vertex() -> None:
    """Round-trip accuracy check (task instructions, beyond the brief's
    step 1): a multi-vertex zigzag path's final cumulative distance must
    match `pyproj.Geod`'s independently computed total length to within
    0.5 m, not just a straight two-point line."""
    coords: list[LonLat] = [
        (-122.45, 37.75),
        (-122.448, 37.752),
        (-122.4501, 37.7555),
        (-122.446, 37.758),
    ]
    samples = resample_polyline(coords, 10.0)
    expected_length = geodesic_length_m(coords)
    assert samples[-1][1] == pytest.approx(expected_length, abs=0.5)
    assert samples[-1][0] == coords[-1]
    assert samples[0][0] == coords[0]
    # Every sample beyond the first should be spaced ~10 m apart along the
    # cumulative-distance axis (last leg may be shorter).
    for (_p0, d0), (_p1, d1) in pairwise(samples):
        assert 0.0 < d1 - d0 <= 10.0 + 1e-6


def test_resample_polyline_intermediate_samples_are_monotonic_in_distance() -> None:
    coords: list[LonLat] = [(-122.45, 37.75), (-122.45, 37.76), (-122.44, 37.76)]
    samples = resample_polyline(coords, 25.0)
    distances = [d for _p, d in samples]
    assert distances == sorted(distances)
    assert len(set(distances)) == len(distances)  # strictly increasing


# ---------------------------------------------------------------------------
# resample_polyline — degenerate inputs (brief: "not hypothetical")
# ---------------------------------------------------------------------------


def test_resample_polyline_skips_zero_length_duplicate_edge_mid_route() -> None:
    a: LonLat = (-122.45, 37.75)
    b: LonLat = (-122.45, 37.76)  # duplicated below
    c: LonLat = (-122.44, 37.76)
    coords = [a, b, b, c]  # consecutive duplicate in the middle

    samples = resample_polyline(coords, 25.0)

    # Must not raise/divide-by-zero, and must produce the same total length
    # as the de-duplicated path (the zero-length edge contributes nothing).
    expected_length = geodesic_length_m([a, b, c])
    assert samples[-1][1] == pytest.approx(expected_length, abs=0.5)
    assert samples[0][0] == a
    assert samples[-1][0] == c


def test_resample_polyline_leading_and_trailing_duplicates() -> None:
    a: LonLat = (-122.45, 37.75)
    b: LonLat = (-122.44, 37.76)
    coords = [a, a, b, b]

    samples = resample_polyline(coords, 25.0)

    assert samples[0][0] == a
    assert samples[0][1] == 0.0
    assert samples[-1][0] == b


def test_resample_polyline_all_points_identical_returns_single_sample() -> None:
    p: LonLat = (-122.45, 37.75)
    samples = resample_polyline([p, p, p], 10.0)
    assert samples == [(p, 0.0)]


def test_resample_polyline_spacing_larger_than_total_length_returns_endpoints() -> None:
    """Spacing that overshoots the whole polyline's length must not produce
    an out-of-range interior sample — only the two exact endpoints."""
    a: LonLat = (-122.45, 37.75)
    b: LonLat = (-122.4499, 37.7501)  # a few metres away
    samples = resample_polyline([a, b], 2000.0)
    expected_length = geodesic_length_m([a, b])
    assert expected_length < 2000.0
    assert samples == [(a, 0.0), (b, expected_length)]


# ---------------------------------------------------------------------------
# DemSampler — against the real fixture COG
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dem_sampler() -> DemSampler:
    assert _FIXTURE_DEM_PATH.exists(), (
        f"fixture DEM not found at {_FIXTURE_DEM_PATH}; run `make fixtures`"
    )
    return DemSampler(_FIXTURE_DEM_PATH)


def test_dem_sampler_known_holdout_point_is_finite_and_in_range(dem_sampler: DemSampler) -> None:
    """Brief step 1: sample on a known holdout point returns a finite value
    inside [0, 300] (SF's full elevation range per test_fixture_dem.py)."""
    values = dem_sampler.sample([_TWIN_PEAKS])
    assert values.shape == (1,)
    assert np.isfinite(values[0])
    assert 0.0 <= float(values[0]) <= 300.0
    # Twin Peaks specifically is a hill, not sea level.
    assert 200.0 <= float(values[0]) <= 300.0


def test_dem_sampler_raises_on_point_outside_dem_extent(dem_sampler: DemSampler) -> None:
    """Brief step 1: sampling a point outside the DEM raises a clear error
    rather than returning nodata silently."""
    with pytest.raises(ValueError, match="outside the DEM extent"):
        dem_sampler.sample([(-121.0, 37.0)])  # well outside SF_BBOX


def test_dem_sampler_lon_lat_order_is_not_swapped(dem_sampler: DemSampler) -> None:
    """The classic geospatial bug: swapping (lon, lat) -> (lat, lon).

    Correctly ordered, Twin Peaks (a hill) and SoMa (near sea level) sample
    to clearly different, individually plausible elevations. SF's bounding
    box has non-overlapping lon (~-122.5) and lat (~37.7) magnitude ranges,
    so a swapped call cannot land inside the DEM's extent at all — it must
    raise, which is itself the tell that the order was wrong: a correct
    implementation raises only for genuinely out-of-bounds points, and a
    swapped SF coordinate becomes exactly that.
    """
    twin_peaks_value = float(dem_sampler.sample([_TWIN_PEAKS])[0])
    soma_value = float(dem_sampler.sample([_SOMA])[0])
    assert twin_peaks_value - soma_value > 150.0  # hill vs. near-sea-level

    swapped_twin_peaks: LonLat = (_TWIN_PEAKS[1], _TWIN_PEAKS[0])  # (lat, lon) by mistake
    with pytest.raises(ValueError, match="outside the DEM extent"):
        dem_sampler.sample([swapped_twin_peaks])


def test_dem_sampler_vectorized_matches_per_point_calls(dem_sampler: DemSampler) -> None:
    points = [_TWIN_PEAKS, _SOMA, (-122.5105, 37.7609)]
    batch = dem_sampler.sample(points)
    singles = [float(dem_sampler.sample([p])[0]) for p in points]
    assert batch.tolist() == pytest.approx(singles, abs=1e-9)


def test_dem_sampler_empty_points_returns_empty_array(dem_sampler: DemSampler) -> None:
    result = dem_sampler.sample([])
    assert result.shape == (0,)


# ---------------------------------------------------------------------------
# DemSampler — synthetic rasters, for exact/deterministic bilinear behaviour
# ---------------------------------------------------------------------------


def _write_synthetic_dem(
    path: Path,
    values: list[list[float]],
    *,
    origin_lon: float = -122.5,
    origin_lat: float = 37.8,
    pixel_deg: float = 0.001,
    nodata: float | None = -9999.0,
) -> None:
    """Write a small single-band GeoTIFF with hand-picked pixel values, for
    tests that need to know the exact expected bilinear result rather than
    relying on the real (smoothed, terrain-shaped) fixture DEM."""
    array = np.array(values, dtype=np.float32)
    height, width = array.shape
    transform = Affine(pixel_deg, 0.0, origin_lon, 0.0, -pixel_deg, origin_lat)
    kwargs: dict[str, Any] = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": transform,
    }
    if nodata is not None:
        kwargs["nodata"] = nodata
    with rasterio.open(path, "w", **kwargs) as dst:
        dst.write(array, 1)


def test_dem_sampler_bilinear_differs_from_nearest_neighbor(tmp_path: Path) -> None:
    """A point deliberately offset from a pixel centre must produce a
    bilinear result that differs from rasterio's own nearest-neighbour
    `sample()` — if they're identical, the interpolation isn't running."""
    dem_path = tmp_path / "gradient.tif"
    # A 4x4 grid with a clean left-to-right gradient (0..30), so any
    # off-centre point in the interior has unambiguous, non-degenerate
    # bilinear vs. nearest-neighbour values.
    _write_synthetic_dem(
        dem_path,
        [
            [0.0, 10.0, 20.0, 30.0],
            [0.0, 10.0, 20.0, 30.0],
            [0.0, 10.0, 20.0, 30.0],
            [0.0, 10.0, 20.0, 30.0],
        ],
    )

    # Pixel (row=1, col=1) centre is at (origin_lon + 1.5*pixel, origin_lat
    # - 1.5*pixel). Offset by 0.3 pixels to the east, well inside the
    # raster and away from any pixel centre.
    origin_lon, origin_lat, pixel_deg = -122.5, 37.8, 0.001
    query_lon = origin_lon + (1.5 + 0.3) * pixel_deg
    query_lat = origin_lat - 1.5 * pixel_deg

    with rasterio.open(dem_path) as ds:
        nearest = float(next(ds.sample([(query_lon, query_lat)]))[0])

    sampler = DemSampler(dem_path)
    bilinear = float(sampler.sample([(query_lon, query_lat)])[0])

    assert bilinear != pytest.approx(nearest, abs=1e-6)
    # Expected: row 1 is constant 10 between col1/col2, interpolated 0.3 of
    # the way from 10 (col1) to 20 (col2) = 13.0; nearest-neighbour at 0.3
    # pixels off-centre still snaps to the col1 value (10.0).
    assert bilinear == pytest.approx(13.0, abs=1e-4)
    assert nearest == pytest.approx(10.0, abs=1e-6)


def test_dem_sampler_bilinear_matches_hand_computed_value_at_grid_center(tmp_path: Path) -> None:
    """Exact-value check: querying the point equidistant from all four
    pixel centres of a 2x2 raster must return their arithmetic mean."""
    dem_path = tmp_path / "corners.tif"
    _write_synthetic_dem(dem_path, [[0.0, 10.0], [20.0, 30.0]])

    origin_lon, origin_lat, pixel_deg = -122.5, 37.8, 0.001
    # Centre of the whole 2x2 grid = midpoint between all four pixel centres.
    center_lon = origin_lon + pixel_deg
    center_lat = origin_lat - pixel_deg

    sampler = DemSampler(dem_path)
    value = float(sampler.sample([(center_lon, center_lat)])[0])
    assert value == pytest.approx((0.0 + 10.0 + 20.0 + 30.0) / 4.0, abs=1e-4)


def test_dem_sampler_clamps_at_raster_edge_instead_of_raising(tmp_path: Path) -> None:
    """Resolution #2: a point in the outermost half-pixel margin (inside
    the raster's bounding box but past the last pixel centre) is clamped
    to the nearest valid neighbourhood, not rejected."""
    dem_path = tmp_path / "edge.tif"
    _write_synthetic_dem(dem_path, [[5.0, 15.0], [25.0, 35.0]])

    with rasterio.open(dem_path) as ds:
        left, bottom, right, top = ds.bounds

    sampler = DemSampler(dem_path)
    # Just inside the raster's top-left corner, in the half-pixel margin
    # outside any pixel centre.
    near_corner_lon = left + 1e-9
    near_corner_lat = top - 1e-9
    value = float(sampler.sample([(near_corner_lon, near_corner_lat)])[0])
    assert np.isfinite(value)
    assert value == pytest.approx(5.0, abs=1e-3)  # clamps to the corner pixel's value

    # Sanity: genuinely outside the bounding box still raises.
    with pytest.raises(ValueError, match="outside the DEM extent"):
        sampler.sample([(left - 1.0, top)])
    with pytest.raises(ValueError, match="outside the DEM extent"):
        sampler.sample([(right + 1.0, bottom)])


def test_dem_sampler_raises_on_masked_nodata_neighborhood(tmp_path: Path) -> None:
    """Fail loudly, per the brief: a query whose 2x2 neighbourhood includes
    a masked (nodata) pixel must raise, not silently interpolate through
    the nodata sentinel value."""
    dem_path = tmp_path / "with_nodata.tif"
    nodata = -9999.0
    _write_synthetic_dem(
        dem_path,
        [
            [10.0, 20.0, 30.0],
            [10.0, nodata, 30.0],
            [10.0, 20.0, 30.0],
        ],
        nodata=nodata,
    )

    origin_lon, origin_lat, pixel_deg = -122.5, 37.8, 0.001
    # Query point whose 2x2 neighbourhood includes the masked centre pixel
    # (row=1, col=1).
    query_lon = origin_lon + 1.3 * pixel_deg
    query_lat = origin_lat - 1.3 * pixel_deg

    sampler = DemSampler(dem_path)
    with pytest.raises(ValueError, match="nodata"):
        sampler.sample([(query_lon, query_lat)])


def test_dem_sampler_no_nodata_present_does_not_raise(tmp_path: Path) -> None:
    """Control case for the previous test: an ordinary neighbourhood with
    no nodata pixels must sample cleanly."""
    dem_path = tmp_path / "no_nodata.tif"
    _write_synthetic_dem(dem_path, [[10.0, 20.0], [10.0, 20.0]], nodata=-9999.0)
    sampler = DemSampler(dem_path)
    origin_lon, origin_lat, pixel_deg = -122.5, 37.8, 0.001
    value = float(sampler.sample([(origin_lon + pixel_deg, origin_lat - pixel_deg)])[0])
    assert np.isfinite(value)
