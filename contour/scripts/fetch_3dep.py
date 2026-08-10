#!/usr/bin/env python3
"""Real elevation pipeline: USGS 3DEP → mosaicked, reprojected COG (+ Skadi
tiles for the routing engine).

============================================================================
Written against documentation verified 2026-08-09; NEVER EXECUTED. The
first real run should expect to debug it. Verify tile availability,
checksum behaviour, and GraphHopper graph-build memory before trusting
output. See contour/.research/API_FACTS.md and docs/DECISIONS.md.
============================================================================

Implements SPEC.md §4.3 steps 2-3 and 5:
  2. Download 3DEP DEM tiles for bbox -> mosaic -> reproject to EPSG:4326
     -> write Cloud-Optimized GeoTIFF at `data/dem/sf_dem.tif`.
  3. Generate Skadi-format elevation tiles from the DEM for GraphHopper's
     `skadi` elevation provider (verified real and working in GraphHopper
     11.0 — `.research/API_FACTS.md` §1-2 — so the SPEC's documented
     fallback, pre-tagging OSM node `ele` values, is not needed).
  5. Emit `data/manifest.json` (this pipeline always writes
     `"source": "real"`; see `_manifest_dict` for the key-for-key mapping
     against `scripts/make_fixture_dem.py`'s manifest shape).

Tile source: USGS 3DEP 1/3 arc-second (~10 m) staged GeoTIFFs on the public
`prd-tnm` S3 bucket. URL pattern and the exact SF tile (`n38w123`, ~213 MB)
are directly verified via a live bucket listing — `.research/API_FACTS.md`
§7. SPEC.md §4.1 requires "1/3 arc-second (~10 m) at minimum, 1 m ...
preferred where available"; only the 1/3-arc-second source is implemented
here (a single ~213 MB tile for SF vs. the 1 m lidar project's 4 tiles /
~500 MB, `CA_SanFrancisco_B23`, also verified in the same section) — the
minimum-required resolution, not the preferred one. Swapping in the 1 m
lidar source is future work (`docs/BACKLOG.md`), not a flag this script
exposes today; doing so blind, with no real run to check the result
against, felt like more unverified surface than this task should add.

RISK carried forward from `.research/API_FACTS.md` UNVERIFIED #7: "USGS
3DEP GeoTIFFs being true Cloud-Optimized GeoTIFFs" was not independently
confirmed by inspecting a real header, only stated as USGS program policy.
This pipeline reads the tiles with `rasterio`/GDAL regardless (which
handles both COG and plain GeoTIFF transparently), so nothing here breaks
if that turns out false — it would just mean the source tiles aren't
efficiently range-readable, which only matters for a future
read-directly-from-S3 optimization this script doesn't attempt.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

_SCRIPTS_DIR = Path(__file__).resolve().parent
_CONTOUR_ROOT = _SCRIPTS_DIR.parent
_API_SRC = _CONTOUR_ROOT / "services" / "api"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from net_fetch import (  # noqa: E402
    DEFAULT_MAX_RESUME_ATTEMPTS,
    DownloadOutcome,
    Fetcher,
    HttpxFetcher,
    download_until_complete,
    sha256_of,
)

from contour.constants import SF_BBOX  # noqa: E402

# --- Paths -------------------------------------------------------------

DEFAULT_RAW_TILE_DIR: Path = _CONTOUR_ROOT / "data" / "raw" / "3dep"
DEFAULT_DEM_PATH: Path = _CONTOUR_ROOT / "data" / "dem" / "sf_dem.tif"
DEFAULT_SKADI_DIR: Path = _CONTOUR_ROOT / "data" / "skadi"
DEFAULT_MANIFEST_PATH: Path = _CONTOUR_ROOT / "data" / "manifest.json"

NODATA: float = -9999.0
MANIFEST_SOURCE: str = "real"

# --- 3DEP tile resolution -----------------------------------------------

# https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/{tile}/USGS_13_{tile}.tif
# Verified via live bucket listing, .research/API_FACTS.md §7.
_THIRD_ARCSEC_URL_TEMPLATE = (
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/"
    "current/{tile}/USGS_13_{tile}.tif"
)

_TILE_ID_RE = re.compile(r"^n(\d+)w(\d+)$")


def tiles_for_bbox(bbox: tuple[float, float, float, float]) -> list[str]:
    """Return the sorted list of 3DEP 1x1-degree tile ids (`"n38w123"`
    style) whose 1x1-degree cell overlaps `bbox`.

    `.research/API_FACTS.md` §7 gives the naming rule as
    `{tile} = n{ceil(N)}w{ceil(|W|)}` for a bbox fully inside one cell.
    Generalised here to a bbox that may span more than one cell: a cell
    with south edge `s` (integer) and west edge `-w` (integer, `w > 0`) is
    labelled `n{s+1}w{w+1}` — substituting `s = floor(south)`,
    `w = floor(-east)` for the bbox's south-west-most cell reproduces the
    single-tile formula exactly (`ceil(x) == floor(x) + 1` for non-integer
    `x`), and iterating every cell edge the bbox's extent touches covers
    the multi-tile case the same way.

    Only implemented for the northern (`lat > 0`) and western (`lon < 0`)
    hemispheres — the only case `SF_BBOX` or any bbox this pipeline is
    ever called with can be in.
    """
    west, south, east, north = bbox
    if not (south > 0 and north > 0 and west < 0 and east < 0):
        raise ValueError(
            f"tiles_for_bbox only supports the northern+western hemisphere; got {bbox}"
        )
    row_lo = math.floor(south)
    row_hi = math.ceil(north) - 1
    # lon-west-magnitude space: cell west edge magnitude `w` (integer) means
    # the cell spans real longitude (-(w+1), -w]. The bbox's own east edge
    # (least-negative) sets the smallest such `w` that can be touched; its
    # west edge (most-negative) sets the largest.
    lonw_min = -east
    lonw_max = -west
    col_lo = math.floor(lonw_min)
    col_hi = math.ceil(lonw_max) - 1

    tiles = [
        f"n{row + 1}w{col + 1}"
        for row in range(row_lo, row_hi + 1)
        for col in range(col_lo, col_hi + 1)
    ]
    return sorted(tiles)


def tile_url(tile: str) -> str:
    """The direct S3 download URL for a 3DEP 1/3-arc-second staged tile."""
    if not _TILE_ID_RE.match(tile):
        raise ValueError(f"not a recognized 3DEP tile id: {tile!r}")
    return _THIRD_ARCSEC_URL_TEMPLATE.format(tile=tile)


def skadi_tile_name(dep_tile: str) -> str:
    """Convert a 3DEP-convention tile id (e.g. `"n38w123"`) to the
    Skadi/SRTM-convention tile id (e.g. `"N37W123"`) for the *same*
    physical 1x1-degree cell.

    3DEP labels a cell by its north edge (`south_edge + 1`); Skadi labels
    the same cell by `floor(lat)`, i.e. its south edge directly
    (`.research/API_FACTS.md` §2, `SkadiProvider.getLatString`) — so the
    latitude label is exactly one less. The longitude label is numerically
    identical between the two conventions (both express "how many whole
    degrees west of the prime meridian", rounded up in magnitude — 3DEP's
    own doc gives the formula as `ceil(|W|)`, and `floor(lon)` of any
    interior point of a western-hemisphere, non-integer-boundary cell
    produces the same magnitude). Verified against the one directly-quoted
    example in `.research/API_FACTS.md`: 3DEP `"n38w123"` <-> Skadi
    `"N37W123"` for the exact same SF cell.
    """
    m = _TILE_ID_RE.match(dep_tile)
    if not m:
        raise ValueError(f"not a recognized 3DEP tile id: {dep_tile!r}")
    lat_label, lon_label = int(m.group(1)), int(m.group(2))
    skadi_lat = lat_label - 1
    skadi_lon = lon_label
    return f"N{skadi_lat:02d}W{skadi_lon:03d}"


# --- Download --------------------------------------------------------------


@dataclass(frozen=True)
class TileDownload:
    tile: str
    path: Path
    outcome: DownloadOutcome
    sha256: str


def download_tiles(
    fetcher: Fetcher,
    tiles: list[str],
    *,
    dest_dir: Path = DEFAULT_RAW_TILE_DIR,
    max_attempts: int = DEFAULT_MAX_RESUME_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> list[TileDownload]:
    """Download every tile in `tiles`, resuming/skipping per
    `net_fetch.download_until_complete` (Global Constraints: never
    re-download an unchanged extract, rate-limit outbound downloads — the
    rate limiting lives in `HttpxFetcher` itself, so it applies uniformly
    to every tile without special-casing here).

    Fix round 1: this used to call `net_fetch.conditional_resumable_download`
    directly (a single request) and then hash `dest` unconditionally — a
    `RESUMED_INCOMPLETE` outcome left only a `.partial` file on disk, so
    `sha256_of(dest)` raised a confusing `FileNotFoundError` instead of
    either completing the resume or failing with a clear message. Routed
    through `download_until_complete` instead, which retries a genuinely
    incomplete resume up to `max_attempts` times and only returns once the
    file is actually whole (or raises `ResumeIncompleteError` naming the
    file and how far it got); `sha256_of` is now called on `result.path`
    (the actually-finished file `download_until_complete` returns), never
    on the pre-download `dest` path, so it can never target a file that
    doesn't exist yet. `sleep` is a parameter (default `time.sleep`) purely
    so a test can drive a multi-round resume without a real backoff delay.
    """
    results = []
    for tile in tiles:
        dest = dest_dir / f"USGS_13_{tile}.tif"
        result = download_until_complete(
            fetcher, tile_url(tile), dest, max_attempts=max_attempts, sleep=sleep
        )
        results.append(
            TileDownload(
                tile=tile, path=result.path, outcome=result.outcome, sha256=sha256_of(result.path)
            )
        )
    return results


# --- Mosaic, reproject, crop, write COG -------------------------------------


def mosaic_reproject_crop(
    tile_paths: list[Path],
    bbox: tuple[float, float, float, float] = SF_BBOX,
) -> tuple[NDArray[np.float32], Any, Any]:
    """Mosaic `tile_paths` (1 or more source rasters, any CRS), reproject
    to EPSG:4326, and crop to `bbox`. Returns `(array, transform, crs)`.

    Uses `rasterio.merge.merge` for the mosaic step (handles both the
    single-tile and multi-tile case identically) and
    `rasterio.warp.reproject` for the CRS transform, matching SPEC.md
    §4.3 step 2 literally ("mosaic -> reproject to EPSG:4326"). Cropping
    happens *after* reprojection via a windowed read, so the crop bbox is
    always in the same (lon/lat) frame it's specified in, regardless of
    the source tiles' native CRS (3DEP staged products are commonly NAD83,
    not WGS84 — this step also folds in that datum conversion; see
    RISKS/UNVERIFIED item below).
    """
    import rasterio
    from rasterio.crs import CRS
    from rasterio.merge import merge
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    from rasterio.windows import Window, from_bounds

    dst_crs = CRS.from_epsg(4326)

    srcs = [rasterio.open(p) for p in tile_paths]
    try:
        mosaic_arr, mosaic_transform = merge(srcs)
        src_crs = srcs[0].crs
        src_nodata = srcs[0].nodata
    finally:
        for s in srcs:
            s.close()

    height, width = mosaic_arr.shape[-2], mosaic_arr.shape[-1]
    dst_transform, dst_width, dst_height = calculate_default_transform(
        src_crs, dst_crs, width, height, *_bounds_from_transform(mosaic_transform, width, height)
    )
    dst_arr = np.full((dst_height, dst_width), NODATA, dtype=np.float32)
    reproject(
        source=mosaic_arr[0],
        destination=dst_arr,
        src_transform=mosaic_transform,
        src_crs=src_crs,
        src_nodata=src_nodata,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        dst_nodata=NODATA,
        resampling=Resampling.bilinear,
    )

    window = from_bounds(*bbox, transform=dst_transform)
    window = window.round_lengths().round_offsets()
    dst_h, dst_w = dst_arr.shape
    # Clamp to the reprojected array's actual extent — `bbox` is expected to
    # sit inside the mosaic, but rounding a fractional pixel window can push
    # an edge out by one pixel; clamping avoids an out-of-bounds slice
    # rather than silently wrapping or raising deep inside a never-run path.
    col_off = max(0, min(int(window.col_off), dst_w))
    row_off = max(0, min(int(window.row_off), dst_h))
    win_w = max(0, min(int(window.width), dst_w - col_off))
    win_h = max(0, min(int(window.height), dst_h - row_off))
    cropped = dst_arr[row_off : row_off + win_h, col_off : col_off + win_w]
    cropped_transform = rasterio.windows.transform(
        Window(col_off, row_off, win_w, win_h), dst_transform
    )

    return cropped.astype(np.float32), cropped_transform, dst_crs


def _bounds_from_transform(
    transform: Any, width: int, height: int
) -> tuple[float, float, float, float]:
    """`(left, bottom, right, top)` for a raster given its transform and
    pixel dimensions — the four inputs `calculate_default_transform` wants
    that aren't already an open dataset handle (we only have the merged
    in-memory array + transform at this point, not a dataset)."""
    left, top = transform * (0, 0)
    right, bottom = transform * (width, height)
    return (left, min(top, bottom), right, max(top, bottom))


def write_cog(array: NDArray[np.float32], transform: Any, crs: Any, output_path: Path) -> None:
    """Write `array` as a Cloud-Optimized GeoTIFF. Same profile as
    `scripts/make_fixture_dem.py::write_cog` (COG driver, 512x512 tiles,
    deflate, `nodata=-9999`) — deliberately duplicated rather than
    imported: that script is fixture-only tooling, and this real pipeline
    should not depend on it (or vice versa)."""
    import rasterio

    output_path.parent.mkdir(parents=True, exist_ok=True)
    profile: dict[str, Any] = {
        "driver": "COG",
        "dtype": "float32",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "crs": crs,
        "transform": transform,
        "compress": "deflate",
        "blocksize": 512,
        "overview_resampling": "average",
        "nodata": NODATA,
    }
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(array, 1)


def _sha256_of_array(array: NDArray[np.float32]) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


# --- Skadi tile generation (SPEC.md §4.3 step 3) -----------------------------

# SRTM/Skadi tiles are always exactly 3601x3601 samples (1 arc-second,
# 1x1-degree cell, 1-pixel overlap at each edge) — `.research/API_FACTS.md`
# §2, confirmed from `SkadiProvider`'s constructor argument to
# `AbstractSRTMElevationProvider`.
SKADI_TILE_SAMPLES: int = 3601
SKADI_NODATA: int = -32768


def write_skadi_tile(
    dem_path: Path,
    dep_tile: str,
    output_dir: Path = DEFAULT_SKADI_DIR,
) -> Path:
    """Resample the mosaicked/reprojected DEM at `dem_path` onto the exact
    3601x3601 1-arc-second grid Skadi's binary format expects for the
    1x1-degree cell `dep_tile` covers, and write it as big-endian 16-bit
    signed integers, gzip-compressed, at
    `output_dir/{skadi_tile}.hgt.gz` (lower-cased) — see the RISK note
    below on the exact local cache-file naming/layout GraphHopper's
    `SkadiProvider` expects when pre-seeded, which is not fully verified.

    Binary format (`.research/API_FACTS.md` §2): 16-bit signed integers,
    big-endian, row-major (north row first, west-to-east within a row), no
    header, void value -32768, units metres.
    """
    import rasterio
    from rasterio.crs import CRS
    from rasterio.warp import Resampling, reproject

    skadi_id = skadi_tile_name(dep_tile)
    lat_label = int(skadi_id[1:3])
    lon_label = int(skadi_id[4:7])
    # Cell spans [lat_label, lat_label+1] x [-(lon_label), -(lon_label-1)],
    # 1 arc-second per pixel, corner-registered (3601 samples = 3600
    # intervals per degree, so the first and last row/column sit exactly
    # on the cell's edges).
    west, south = -float(lon_label), float(lat_label)
    dx = dy = 1.0 / (SKADI_TILE_SAMPLES - 1)
    dst_transform = rasterio.transform.from_origin(west, south + 1.0, dx, dy)
    dst_crs = CRS.from_epsg(4326)

    dst_arr = np.full(
        (SKADI_TILE_SAMPLES, SKADI_TILE_SAMPLES), float(SKADI_NODATA), dtype=np.float32
    )
    with rasterio.open(dem_path) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=dst_arr,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=float(SKADI_NODATA),
            resampling=Resampling.bilinear,
        )

    int_arr = np.where(dst_arr <= float(SKADI_NODATA), SKADI_NODATA, np.round(dst_arr)).astype(
        ">i2"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{skadi_id.lower()}.hgt.gz"
    with gzip.open(out_path, "wb") as f:
        f.write(int_arr.tobytes())
    return out_path


# --- Manifest ----------------------------------------------------------------


def _manifest_dict(
    *,
    bbox: tuple[float, float, float, float],
    dem_rel_path: str,
    generated_at: datetime,
    dem_data_sha256: str,
    dem_file_sha256: str,
    source_tiles: list[str],
    source_tile_sha256: dict[str, str],
    total_pixel_count: int,
) -> dict[str, Any]:
    """Pure manifest builder (no I/O) — same key set and hashing approach
    as `scripts/make_fixture_dem.py::write_manifest`, so `/v1/health` and
    the eval harness (Task 15, not yet wired) can read either manifest
    without branching on `source`.

    Some fixture-manifest keys describe a step that has no equivalent in
    this pipeline (RBF interpolation from sparse control points) and are
    filled with their honest real-data value rather than omitted, so the
    key set matches exactly:
      - `control_point_sha256` / `control_point_count`: repurposed to
        describe *this* pipeline's actual input identity — the sorted set
        of source 3DEP tile ids and their own file hashes, joined and
        hashed the same way; the count of tiles mosaicked. Same structural
        role ("hash + count of what fed the DEM"), different input.
      - `clamped_pixel_count` / `in_hull_clamped_pixel_count`: always `0`.
        The fixture's RBF interpolator can extrapolate outside its control
        points' convex hull and needs clamping to `[0, 300]`; a real 3DEP
        mosaic is direct survey/lidar-derived data with no interpolation
        step, so there is nothing to clamp.
      - `in_hull_pixel_count`: equals `total_pixel_count`. There is no
        "convex hull of sparse control points" for real data — every pixel
        in the crop is real, directly-sourced terrain.
      - `interpolation_smoothing` / `interpolation_neighbors`: always
        `null`. No RBF fit happens in this pipeline.
    Extra keys not present in the fixture manifest (`source_tiles`,
    `source_tile_urls`) are additive and safe for any consumer that reads
    known keys by name.
    """
    control_point_sha256 = hashlib.sha256(
        "\n".join(f"{t}:{source_tile_sha256[t]}" for t in sorted(source_tiles)).encode("utf-8")
    ).hexdigest()

    manifest: dict[str, Any] = {
        "source": MANIFEST_SOURCE,
        "bbox": list(bbox),
        "dem_path": dem_rel_path,
        "generated_at": generated_at.isoformat(),
        "control_point_sha256": control_point_sha256,
        "dem_data_sha256": dem_data_sha256,
        "dem_file_sha256": dem_file_sha256,
        "control_point_count": len(source_tiles),
        "clamped_pixel_count": 0,
        "total_pixel_count": total_pixel_count,
        "in_hull_clamped_pixel_count": 0,
        "in_hull_pixel_count": total_pixel_count,
        "interpolation_smoothing": None,
        "interpolation_neighbors": None,
        "source_tiles": sorted(source_tiles),
        "source_tile_urls": [tile_url(t) for t in sorted(source_tiles)],
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    return manifest


def write_manifest(
    *,
    dem_path: Path,
    dem_data_sha256: str,
    manifest_path: Path,
    source_tiles: list[str],
    source_tile_sha256: dict[str, str],
    total_pixel_count: int,
    bbox: tuple[float, float, float, float] = SF_BBOX,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    if generated_at is None:
        generated_at = datetime.now(UTC)
    dem_rel_path = (
        str(dem_path.relative_to(manifest_path.parent))
        if manifest_path.parent in dem_path.parents
        else str(dem_path)
    )
    manifest = _manifest_dict(
        bbox=bbox,
        dem_rel_path=dem_rel_path,
        generated_at=generated_at,
        dem_data_sha256=dem_data_sha256,
        dem_file_sha256=sha256_of(dem_path),
        source_tiles=source_tiles,
        source_tile_sha256=source_tile_sha256,
        total_pixel_count=total_pixel_count,
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


# --- Orchestration -----------------------------------------------------------


def main() -> None:
    fetcher = HttpxFetcher()
    tiles = tiles_for_bbox(SF_BBOX)
    print(f"Tiles for SF_BBOX: {tiles}")

    downloads = download_tiles(fetcher, tiles)
    for d in downloads:
        print(f"  {d.tile}: {d.outcome.value} (sha256={d.sha256})")

    array, transform, crs = mosaic_reproject_crop([d.path for d in downloads], SF_BBOX)
    write_cog(array, transform, crs, DEFAULT_DEM_PATH)
    print(f"Wrote {DEFAULT_DEM_PATH}")

    for d in downloads:
        skadi_path = write_skadi_tile(DEFAULT_DEM_PATH, d.tile, DEFAULT_SKADI_DIR)
        print(f"Wrote Skadi tile {skadi_path}")

    manifest = write_manifest(
        dem_path=DEFAULT_DEM_PATH,
        dem_data_sha256=_sha256_of_array(array),
        manifest_path=DEFAULT_MANIFEST_PATH,
        source_tiles=tiles,
        source_tile_sha256={d.tile: d.sha256 for d in downloads},
        total_pixel_count=int(array.size),
    )
    print(f"Wrote {DEFAULT_MANIFEST_PATH} (source={manifest['source']})")


if __name__ == "__main__":
    main()
