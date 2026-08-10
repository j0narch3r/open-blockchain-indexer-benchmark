#!/usr/bin/env python3
"""Real OSM extract pipeline: Geofabrik NorCal → clipped to `SF_BBOX`.

============================================================================
Written against documentation verified 2026-08-09; NEVER EXECUTED. The
first real run should expect to debug it. Verify tile availability,
checksum behaviour, and GraphHopper graph-build memory before trusting
output. See contour/.research/API_FACTS.md and docs/DECISIONS.md.
============================================================================

Implements SPEC.md §4.3 step 1: "Download OSM extract: Geofabrik
`norcal-latest.osm.pbf` → clip to bbox with `osmium extract`."

Two things are worth flagging honestly up front:

1. **The Geofabrik NorCal URL is NOT covered by `.research/API_FACTS.md`.**
   Global Constraints names GraphHopper, Valhalla, MapLibre RN, Expo,
   pmtiles, and 3DEP as the APIs that file verifies — Geofabrik's download
   layout isn't one of them. The URL below
   (`https://download.geofabrik.de/north-america/us/california/norcal-latest.osm.pbf`)
   is Geofabrik's long-standing, publicly documented regional-extract path
   convention (California is split into `norcal`/`socal` sub-regions on
   their download site), not a value pulled from a primary source in this
   session. Spot-check it before the first real run.
2. **This script shells out to the `osmium` CLI** (`osmium-tool`), not a
   Python OSM library. `pyosmium` was considered and rejected — adding a
   new Python dependency needs a `docs/DECISIONS.md` justification per
   Global Constraints, and `osmium-tool` is already the natural fit for a
   single `extract --bbox` call baked into the `routing-engine` Docker
   image (see `services/routing-engine/Dockerfile`), so no Python
   dependency is added at all.

Only the two *pure* pieces are unit-tested without touching the network
(Global Constraints: "No test may touch the network"):
`validate_service_area_bbox` (bbox rejection) and the conditional/resume
download control flow, which lives in `scripts/net_fetch.py` and is shared
with `fetch_3dep.py`.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

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
    meta_path_for,
    sha256_of,
)

from contour.constants import SF_BBOX  # noqa: E402
from contour.errors import OutOfServiceArea  # noqa: E402

# --- Geofabrik source (see module docstring caveat #1) ----------------------

GEOFABRIK_NORCAL_PBF_URL: str = (
    "https://download.geofabrik.de/north-america/us/california/norcal-latest.osm.pbf"
)
# Geofabrik publishes an MD5 sidecar alongside every extract, historically
# at exactly this suffix ("<file>.md5", one "<md5>  <filename>" line —
# BSD/GNU `md5sum`-compatible format). Same caveat as the base URL above.
GEOFABRIK_NORCAL_MD5_URL: str = GEOFABRIK_NORCAL_PBF_URL + ".md5"

DEFAULT_RAW_PBF_PATH: Path = _CONTOUR_ROOT / "data" / "raw" / "norcal-latest.osm.pbf"
DEFAULT_CLIPPED_PBF_PATH: Path = _CONTOUR_ROOT / "data" / "osm" / "sf.osm.pbf"


# --- Service-area bbox guard --------------------------------------------------


def validate_service_area_bbox(
    bbox: tuple[float, float, float, float],
    *,
    service_area: tuple[float, float, float, float] = SF_BBOX,
) -> None:
    """Raise `OutOfServiceArea` unless `bbox` is fully contained in
    `service_area`. We are clipping OSM data *for* Contour's service area —
    a clip target that extends beyond it (or misses it entirely) is a
    request the pipeline cannot honestly satisfy, same error code the API
    uses for an out-of-area route request (`errors.OutOfServiceArea`,
    Global Constraints exact string `OUT_OF_SERVICE_AREA`)."""
    west, south, east, north = bbox
    sa_west, sa_south, sa_east, sa_north = service_area
    if not (sa_west <= west and sa_south <= south and east <= sa_east and north <= sa_north):
        raise OutOfServiceArea(
            f"Clip bbox {bbox} is not contained in the service area {service_area}.",
            detail={"bbox": list(bbox), "service_area": list(service_area)},
        )


# --- MD5 sidecar parsing (pure) ---------------------------------------------


def parse_md5_sidecar(text: str) -> str:
    """Parse a `<md5>  <filename>` (or bare `<md5>`) sidecar line into just
    the hex digest. Raises `ValueError` on an empty/malformed sidecar."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty md5 sidecar")
    return stripped.split()[0]


# --- Orchestration (never executed here; real network + subprocess calls) ---


def download_norcal_extract(
    fetcher: Fetcher,
    *,
    pbf_path: Path = DEFAULT_RAW_PBF_PATH,
    pbf_url: str = GEOFABRIK_NORCAL_PBF_URL,
    max_attempts: int = DEFAULT_MAX_RESUME_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> DownloadOutcome:
    """Fetch (or confirm-cached) the full NorCal extract. Checksum
    verification against the MD5 sidecar is attempted only after a fresh
    download (`DOWNLOADED`/`RESUMED`) — a `NOT_MODIFIED` result means the
    bytes on disk already matched what a previous run verified, so no
    redundant network fetch of the MD5 sidecar happens on that path.

    Fix round 1: routed through `net_fetch.download_until_complete` (was
    a single `conditional_resumable_download` call) so a genuinely
    multi-round resume on a ~1 GB extract can actually finish, up to
    `max_attempts` tries, instead of resume support that can only ever
    handle a resume completing in one more request. `sleep` is a parameter
    (default `time.sleep`) purely so a test can drive a multi-round resume
    without a real backoff delay.
    """
    result = download_until_complete(
        fetcher, pbf_url, pbf_path, max_attempts=max_attempts, sleep=sleep
    )
    if result.outcome in (DownloadOutcome.DOWNLOADED, DownloadOutcome.RESUMED):
        _verify_checksum(fetcher, result.path)
    return result.outcome


def _verify_checksum(fetcher: Fetcher, pbf_path: Path) -> None:
    md5_resp = fetcher.request("GET", GEOFABRIK_NORCAL_MD5_URL)
    if md5_resp.status != 200:
        raise RuntimeError(
            f"Could not fetch MD5 sidecar for {pbf_path.name} "
            f"(HTTP {md5_resp.status}); refusing to trust an unverified download."
        )
    expected = parse_md5_sidecar(md5_resp.content.decode("ascii", errors="replace"))
    # MD5, not a security use — matching Geofabrik's own published checksum
    # algorithm to detect transfer corruption, nothing more.
    actual = hashlib.md5(pbf_path.read_bytes()).hexdigest()
    if actual != expected:
        # Fix round 1: a corrupt file that fails its checksum must not be
        # left on disk under its normal name with a cache-meta sidecar
        # already written (conditional_resumable_download writes it on
        # every 200/206, before we get a chance to verify) — otherwise the
        # *next* run sees `pbf_path` exists, sends the cached ETag, the
        # remote is unchanged, gets a 304, and trusts the corrupt bytes
        # forever. Delete both the bad file and its cache meta so the next
        # run is forced to download fresh, never silently truncated.
        pbf_path.unlink(missing_ok=True)
        meta_path_for(pbf_path).unlink(missing_ok=True)
        raise RuntimeError(
            f"Checksum mismatch for {pbf_path}: expected {expected}, got {actual}. "
            f"The corrupt file has been deleted; re-run to download fresh."
        )


def clip_to_bbox(
    source_pbf: Path,
    dest_pbf: Path,
    bbox: tuple[float, float, float, float] = SF_BBOX,
) -> None:
    """Shell out to `osmium extract --bbox=W,S,E,N`. Requires the `osmium`
    CLI (from `osmium-tool`) on PATH — provided by the `routing-engine`
    Docker image (see `services/routing-engine/Dockerfile`), not a Python
    dependency of this package."""
    validate_service_area_bbox(bbox)
    dest_pbf.parent.mkdir(parents=True, exist_ok=True)
    west, south, east, north = bbox
    subprocess.run(
        [
            "osmium",
            "extract",
            "--bbox",
            f"{west},{south},{east},{north}",
            "--output",
            str(dest_pbf),
            "--overwrite",
            str(source_pbf),
        ],
        check=True,
    )


def main() -> None:
    fetcher = HttpxFetcher()
    outcome = download_norcal_extract(fetcher)
    print(f"NorCal extract: {outcome.value}")
    clip_to_bbox(DEFAULT_RAW_PBF_PATH, DEFAULT_CLIPPED_PBF_PATH)
    print(f"Wrote {DEFAULT_CLIPPED_PBF_PATH} (sha256={sha256_of(DEFAULT_CLIPPED_PBF_PATH)})")


if __name__ == "__main__":
    main()
