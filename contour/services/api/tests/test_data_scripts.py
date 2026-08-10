"""Tests for the real OSM/3DEP data pipeline scripts (Task 8).

These scripts (`scripts/clip_osm.py`, `scripts/fetch_3dep.py`,
`scripts/net_fetch.py`) are written but never executed in this
environment — see the banner at the top of each and
`docs/DECISIONS.md`. This test file exercises exactly the *pure* parts,
per Global Constraints ("No test may touch the network"): tile-name
computation, manifest-checksum stability, the bbox out-of-service-area
guard, and the conditional/resume download control flow against a stub
`Fetcher` that never opens a socket.

Same `sys.path` pattern as `tests/test_fixture_dem.py` for reaching
`scripts/` from this package's test tree.
"""

import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from contour.constants import SF_BBOX
from contour.errors import OutOfServiceArea

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from clip_osm import validate_service_area_bbox  # noqa: E402
from fetch_3dep import (  # noqa: E402
    _manifest_dict,
    skadi_tile_name,
    tile_url,
    tiles_for_bbox,
)
from net_fetch import (  # noqa: E402
    DownloadOutcome,
    FetchResponse,
    UnexpectedStatusError,
    conditional_resumable_download,
)

# ---------------------------------------------------------------------------
# 3DEP tile-name computation
# ---------------------------------------------------------------------------


def test_sf_bbox_resolves_to_the_verified_tile() -> None:
    """`.research/API_FACTS.md` §7 directly verifies (live S3 bucket
    listing) that SF_BBOX falls entirely within tile `n38w123`."""
    assert tiles_for_bbox(SF_BBOX) == ["n38w123"]


def test_bbox_spanning_a_tile_boundary_returns_two() -> None:
    bbox = (-123.1, 37.5, -122.9, 37.6)  # straddles the w123/w124 boundary
    assert tiles_for_bbox(bbox) == ["n38w123", "n38w124"]


def test_bbox_spanning_a_latitude_tile_boundary_returns_two() -> None:
    bbox = (-122.45, 37.95, -122.35, 38.05)  # straddles the n38/n39 boundary
    assert tiles_for_bbox(bbox) == ["n38w123", "n39w123"]


def test_bbox_spanning_all_four_neighbours_returns_four() -> None:
    bbox = (-123.05, 37.95, -122.95, 38.05)
    assert tiles_for_bbox(bbox) == ["n38w123", "n38w124", "n39w123", "n39w124"]


def test_tile_url_matches_the_verified_s3_path() -> None:
    assert tile_url("n38w123") == (
        "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/"
        "current/n38w123/USGS_13_n38w123.tif"
    )


def test_tile_url_rejects_a_malformed_tile_id() -> None:
    with pytest.raises(ValueError, match="tile id"):
        tile_url("not-a-tile")


def test_tiles_for_bbox_rejects_the_wrong_hemisphere() -> None:
    with pytest.raises(ValueError, match="hemisphere"):
        tiles_for_bbox((122.35, 37.7, 122.53, 37.83))  # positive (eastern) lon


# ---------------------------------------------------------------------------
# 3DEP <-> Skadi tile-name conversion
# ---------------------------------------------------------------------------


def test_skadi_tile_name_matches_the_verified_example() -> None:
    """`.research/API_FACTS.md` §2 directly quotes the SF Skadi tile URL as
    `.../skadi/N37/N37W123.hgt.gz` — same physical cell as 3DEP's
    `n38w123`, different labelling convention (Skadi labels by the cell's
    south edge, 3DEP by its north edge)."""
    assert skadi_tile_name("n38w123") == "N37W123"


def test_skadi_tile_name_rejects_a_malformed_tile_id() -> None:
    with pytest.raises(ValueError, match="tile id"):
        skadi_tile_name("N37W123")  # already-Skadi-format input is invalid here


# ---------------------------------------------------------------------------
# Manifest checksum stability
# ---------------------------------------------------------------------------


def test_manifest_builder_is_stable_for_identical_inputs() -> None:
    fixed_time = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
    kwargs = dict(
        bbox=SF_BBOX,
        dem_rel_path="dem/sf_dem.tif",
        generated_at=fixed_time,
        dem_data_sha256="a" * 64,
        dem_file_sha256="b" * 64,
        source_tiles=["n38w123"],
        source_tile_sha256={"n38w123": "c" * 64},
        total_pixel_count=1_000_000,
    )
    a = _manifest_dict(**kwargs)
    b = _manifest_dict(**kwargs)
    assert a == b
    assert a["manifest_sha256"] == b["manifest_sha256"]


def test_manifest_builder_changes_hash_when_an_input_changes() -> None:
    fixed_time = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
    base = dict(
        bbox=SF_BBOX,
        dem_rel_path="dem/sf_dem.tif",
        generated_at=fixed_time,
        dem_data_sha256="a" * 64,
        dem_file_sha256="b" * 64,
        source_tiles=["n38w123"],
        source_tile_sha256={"n38w123": "c" * 64},
        total_pixel_count=1_000_000,
    )
    a = _manifest_dict(**base)
    changed = dict(base, dem_data_sha256="d" * 64)
    b = _manifest_dict(**changed)
    assert a["manifest_sha256"] != b["manifest_sha256"]


def test_manifest_carries_source_real() -> None:
    fixed_time = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
    manifest = _manifest_dict(
        bbox=SF_BBOX,
        dem_rel_path="dem/sf_dem.tif",
        generated_at=fixed_time,
        dem_data_sha256="a" * 64,
        dem_file_sha256="b" * 64,
        source_tiles=["n38w123"],
        source_tile_sha256={"n38w123": "c" * 64},
        total_pixel_count=1_000_000,
    )
    assert manifest["source"] == "real"


def test_manifest_key_set_matches_the_fixture_manifest_shape() -> None:
    """Global Constraints / task-8-brief: 'match it — same keys, same
    hashing approach — so /v1/health and the eval harness work unchanged
    against either.' Every key `make_fixture_dem.write_manifest` emits
    (see its docstring) must also appear here — the real manifest is a
    superset (it adds `source_tiles`/`source_tile_urls`), never a subset."""
    fixture_keys = {
        "source",
        "bbox",
        "dem_path",
        "generated_at",
        "control_point_sha256",
        "dem_data_sha256",
        "dem_file_sha256",
        "control_point_count",
        "clamped_pixel_count",
        "total_pixel_count",
        "in_hull_clamped_pixel_count",
        "in_hull_pixel_count",
        "interpolation_smoothing",
        "interpolation_neighbors",
        "manifest_sha256",
    }
    fixed_time = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
    manifest = _manifest_dict(
        bbox=SF_BBOX,
        dem_rel_path="dem/sf_dem.tif",
        generated_at=fixed_time,
        dem_data_sha256="a" * 64,
        dem_file_sha256="b" * 64,
        source_tiles=["n38w123"],
        source_tile_sha256={"n38w123": "c" * 64},
        total_pixel_count=1_000_000,
    )
    assert fixture_keys <= set(manifest.keys())


def test_manifest_reports_no_clamping_for_real_data() -> None:
    """Real 3DEP data is never RBF-extrapolated/clamped — those fixture-
    only fields must read as their honest 'not applicable' real-pipeline
    value (see `_manifest_dict`'s docstring)."""
    fixed_time = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
    manifest = _manifest_dict(
        bbox=SF_BBOX,
        dem_rel_path="dem/sf_dem.tif",
        generated_at=fixed_time,
        dem_data_sha256="a" * 64,
        dem_file_sha256="b" * 64,
        source_tiles=["n38w123"],
        source_tile_sha256={"n38w123": "c" * 64},
        total_pixel_count=1_000_000,
    )
    assert manifest["clamped_pixel_count"] == 0
    assert manifest["in_hull_clamped_pixel_count"] == 0
    assert manifest["in_hull_pixel_count"] == manifest["total_pixel_count"]
    assert manifest["interpolation_smoothing"] is None
    assert manifest["interpolation_neighbors"] is None


# ---------------------------------------------------------------------------
# Bbox out-of-service-area guard
# ---------------------------------------------------------------------------


def test_clip_bbox_within_service_area_is_accepted() -> None:
    validate_service_area_bbox(SF_BBOX)  # must not raise


def test_clip_bbox_outside_service_area_is_rejected() -> None:
    nyc_bbox = (-74.05, 40.60, -73.90, 40.80)
    with pytest.raises(OutOfServiceArea) as excinfo:
        validate_service_area_bbox(nyc_bbox)
    assert excinfo.value.code == "OUT_OF_SERVICE_AREA"


def test_clip_bbox_partially_outside_service_area_is_rejected() -> None:
    west, south, east, north = SF_BBOX
    bbox = (west, south, east + 1.0, north)  # extends east past the service area
    with pytest.raises(OutOfServiceArea) as excinfo:
        validate_service_area_bbox(bbox)
    assert excinfo.value.code == "OUT_OF_SERVICE_AREA"


# ---------------------------------------------------------------------------
# Conditional-request + resume download control flow (stub Fetcher, no
# network — Global Constraints: "No test may touch the network")
# ---------------------------------------------------------------------------


class StubFetcher:
    """Records every request made and returns pre-programmed responses in
    order. Implements the `net_fetch.Fetcher` Protocol structurally (no
    inheritance needed)."""

    def __init__(self, responses: list[FetchResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[str, str, Mapping[str, str]]] = []

    def request(
        self, method: str, url: str, *, headers: Mapping[str, str] | None = None
    ) -> FetchResponse:
        self.requests.append((method, url, dict(headers or {})))
        return self._responses.pop(0)


def test_conditional_download_uses_cache_on_304(tmp_path: Path) -> None:
    dest = tmp_path / "extract.pbf"
    dest.write_bytes(b"cached bytes")
    meta_path = tmp_path / "extract.pbf.meta.json"
    meta_path.write_text(
        '{"etag": "\\"abc123\\"", "last_modified": "Sun, 09 Aug 2026 00:00:00 GMT"}'
    )

    fetcher = StubFetcher([FetchResponse(status=304, headers={}, content=b"")])
    result = conditional_resumable_download(fetcher, "https://example.invalid/extract.pbf", dest)

    assert result.outcome == DownloadOutcome.NOT_MODIFIED
    assert dest.read_bytes() == b"cached bytes"  # untouched
    # The conditional headers must actually have been sent.
    _, _, sent_headers = fetcher.requests[0]
    assert sent_headers["If-None-Match"] == '"abc123"'
    assert sent_headers["If-Modified-Since"] == "Sun, 09 Aug 2026 00:00:00 GMT"


def test_conditional_download_fetches_on_200(tmp_path: Path) -> None:
    dest = tmp_path / "extract.pbf"
    dest.write_bytes(b"stale bytes")
    meta_path = tmp_path / "extract.pbf.meta.json"
    meta_path.write_text('{"etag": "\\"abc123\\"", "last_modified": null}')

    fetcher = StubFetcher(
        [FetchResponse(status=200, headers={"ETag": '"xyz789"'}, content=b"fresh bytes")]
    )
    result = conditional_resumable_download(fetcher, "https://example.invalid/extract.pbf", dest)

    assert result.outcome == DownloadOutcome.DOWNLOADED
    assert dest.read_bytes() == b"fresh bytes"
    assert json.loads(meta_path.read_text())["etag"] == '"xyz789"'


def test_conditional_download_with_no_prior_cache_sends_no_conditional_headers(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "extract.pbf"
    fetcher = StubFetcher([FetchResponse(status=200, headers={}, content=b"first download")])
    result = conditional_resumable_download(fetcher, "https://example.invalid/extract.pbf", dest)

    assert result.outcome == DownloadOutcome.DOWNLOADED
    assert dest.read_bytes() == b"first download"
    _, _, sent_headers = fetcher.requests[0]
    assert "If-None-Match" not in sent_headers
    assert "If-Modified-Since" not in sent_headers


def test_conditional_download_304_without_a_cached_file_is_an_error(tmp_path: Path) -> None:
    dest = tmp_path / "extract.pbf"  # does not exist
    fetcher = StubFetcher([FetchResponse(status=304, headers={}, content=b"")])
    with pytest.raises(UnexpectedStatusError):
        conditional_resumable_download(fetcher, "https://example.invalid/extract.pbf", dest)


def test_resume_continues_a_partial_file_rather_than_restarting(tmp_path: Path) -> None:
    dest = tmp_path / "tile.tif"
    partial = tmp_path / "tile.tif.partial"
    partial.write_bytes(b"FIRST HALF ")

    fetcher = StubFetcher([FetchResponse(status=206, headers={}, content=b"SECOND HALF")])
    result = conditional_resumable_download(fetcher, "https://example.invalid/tile.tif", dest)

    assert result.outcome == DownloadOutcome.RESUMED
    assert dest.read_bytes() == b"FIRST HALF SECOND HALF"  # continued, not restarted
    assert not partial.exists()  # renamed away
    _, _, sent_headers = fetcher.requests[0]
    assert sent_headers["Range"] == "bytes=11-"


def test_resume_with_content_range_stays_incomplete_until_total_reached(tmp_path: Path) -> None:
    dest = tmp_path / "tile.tif"
    partial = tmp_path / "tile.tif.partial"
    partial.write_bytes(b"0123456789")  # 10 bytes so far

    # Server reports a total of 30 bytes but only sends 10 more this round.
    fetcher = StubFetcher(
        [
            FetchResponse(
                status=206,
                headers={"Content-Range": "bytes 10-19/30"},
                content=b"ABCDEFGHIJ",
            )
        ]
    )
    result = conditional_resumable_download(fetcher, "https://example.invalid/tile.tif", dest)

    assert result.outcome == DownloadOutcome.RESUMED_INCOMPLETE
    assert not dest.exists()
    assert partial.read_bytes() == b"0123456789ABCDEFGHIJ"


def test_resume_without_a_prior_partial_is_a_fresh_download(tmp_path: Path) -> None:
    dest = tmp_path / "tile.tif"
    fetcher = StubFetcher([FetchResponse(status=200, headers={}, content=b"whole file")])
    result = conditional_resumable_download(fetcher, "https://example.invalid/tile.tif", dest)
    assert result.outcome == DownloadOutcome.DOWNLOADED
    assert dest.read_bytes() == b"whole file"


def test_unsolicited_206_without_a_range_request_is_an_error(tmp_path: Path) -> None:
    dest = tmp_path / "tile.tif"
    fetcher = StubFetcher([FetchResponse(status=206, headers={}, content=b"???")])
    with pytest.raises(UnexpectedStatusError):
        conditional_resumable_download(fetcher, "https://example.invalid/tile.tif", dest)


def test_unexpected_status_raises(tmp_path: Path) -> None:
    dest = tmp_path / "tile.tif"
    fetcher = StubFetcher([FetchResponse(status=500, headers={}, content=b"")])
    with pytest.raises(UnexpectedStatusError):
        conditional_resumable_download(fetcher, "https://example.invalid/tile.tif", dest)
