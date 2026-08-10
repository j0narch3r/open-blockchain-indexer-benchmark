#!/usr/bin/env python3
"""Shared HTTP fetch machinery for Contour's real-data pipeline scripts.

============================================================================
Written against documentation verified 2026-08-09; NEVER EXECUTED. The
first real run should expect to debug it. Verify tile availability,
checksum behaviour, and GraphHopper graph-build memory before trusting
output. See contour/.research/API_FACTS.md and docs/DECISIONS.md.
============================================================================

Both `clip_osm.py` and `fetch_3dep.py` need the same three things when
talking to a public data host:

1. **Never re-download an unchanged file.** Conditional GET via
   `If-None-Match` (ETag) / `If-Modified-Since`, sidecar-cached from the
   previous response.
2. **Resume a partial download** rather than restart it, via `Range`.
3. **Rate-limit outbound requests** — a fixed minimum interval between
   real network calls (`HttpxFetcher`), so this pipeline is a good citizen
   of Geofabrik's and USGS's free public infrastructure.

All of that logic is collected here, behind a `Fetcher` Protocol, so the
pure control flow (which status code does what) can be unit-tested with a
stub that never touches the network — Global Constraints: "No test may
touch the network."
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

# --- The Fetcher seam -------------------------------------------------------


@dataclass(frozen=True)
class FetchResponse:
    """The whole HTTP response this module needs: status, headers, body.

    Header lookups elsewhere in this module use `.get` case-sensitively on
    the exact names below (`ETag`, `Last-Modified`, `Content-Range`) — real
    HTTP header names are case-insensitive on the wire, so `HttpxFetcher`
    normalizes httpx's response headers (which are already case-insensitive)
    into a plain dict keyed by the canonical capitalization used throughout
    this module. A stub built for tests must do the same if it wants those
    lookups to succeed.
    """

    status: int
    headers: Mapping[str, str]
    content: bytes


class Fetcher(Protocol):
    """Abstraction over an HTTP client. `HttpxFetcher` below is the only
    real implementation and is never imported by a test module — tests
    provide their own stub implementing just this method, per Global
    Constraints ("No test may touch the network; put every fetch behind a
    Fetcher protocol and stub it in tests")."""

    def request(
        self, method: str, url: str, *, headers: Mapping[str, str] | None = None
    ) -> FetchResponse: ...


# --- Real implementation (never imported by tests) --------------------------

RATE_LIMIT_INTERVAL_S: float = 1.0  # minimum seconds between real requests


class HttpxFetcher:
    """The real network fetcher, used only when a human runs `make data`.

    Self-throttles to `min_interval_s` between requests (Global
    Constraints: "Rate-limit outbound downloads. Be a good citizen of free
    public data services.") and streams the whole response into memory —
    acceptable here because this pipeline makes a handful of large-but-
    bounded requests (one OSM extract, 1-4 DEM tiles), not high request
    volume.
    """

    def __init__(self, *, min_interval_s: float = RATE_LIMIT_INTERVAL_S) -> None:
        self._min_interval_s = min_interval_s
        self._last_request_at: float | None = None

    def request(
        self, method: str, url: str, *, headers: Mapping[str, str] | None = None
    ) -> FetchResponse:
        import httpx  # imported lazily: only the real fetcher needs it

        self._throttle()
        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            resp = client.request(method, url, headers=dict(headers or {}))
        self._last_request_at = time.monotonic()
        return FetchResponse(
            status=resp.status_code,
            headers={k: v for k, v in resp.headers.items()},
            content=resp.content,
        )

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self._min_interval_s - elapsed
        if remaining > 0:
            time.sleep(remaining)


# --- Conditional-request cache sidecar --------------------------------------


@dataclass(frozen=True)
class CacheMeta:
    etag: str | None
    last_modified: str | None


def _meta_path_for(dest_path: Path) -> Path:
    return dest_path.with_name(dest_path.name + ".meta.json")


def _partial_path_for(dest_path: Path) -> Path:
    return dest_path.with_name(dest_path.name + ".partial")


def _load_cache_meta(meta_path: Path) -> CacheMeta | None:
    if not meta_path.exists():
        return None
    raw = json.loads(meta_path.read_text())
    return CacheMeta(etag=raw.get("etag"), last_modified=raw.get("last_modified"))


def _write_cache_meta(meta_path: Path, resp: FetchResponse) -> None:
    etag = resp.headers.get("ETag")
    last_modified = resp.headers.get("Last-Modified")
    if etag is None and last_modified is None:
        # Nothing to cache against; leave any prior meta alone rather than
        # overwrite it with an empty, useless record.
        return
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps({"etag": etag, "last_modified": last_modified}))


_CONTENT_RANGE_RE = re.compile(r"bytes\s+\d+-\d+/(\d+)")


def _expected_total_size(resp: FetchResponse) -> int | None:
    """Parse `Content-Range: bytes start-end/total` if present. Returns
    `None` when absent — callers then assume a single 206 response, sent in
    reply to a single `Range: bytes=N-` request, always contains the rest
    of the file (true for a direct HTTP GET against a static file host;
    this pipeline never issues chunked/multi-part range requests)."""
    header = resp.headers.get("Content-Range")
    if header is None:
        return None
    m = _CONTENT_RANGE_RE.search(header)
    return int(m.group(1)) if m else None


# --- The download outcome ----------------------------------------------------


class DownloadOutcome(StrEnum):
    NOT_MODIFIED = "not_modified"
    DOWNLOADED = "downloaded"
    RESUMED = "resumed"
    RESUMED_INCOMPLETE = "resumed_incomplete"


@dataclass(frozen=True)
class DownloadResult:
    outcome: DownloadOutcome
    path: Path
    total_bytes: int


class UnexpectedStatusError(RuntimeError):
    """The fetcher returned a status this downloader has no handling for."""


def conditional_resumable_download(
    fetcher: Fetcher,
    url: str,
    dest_path: Path,
    *,
    meta_path: Path | None = None,
    partial_path: Path | None = None,
) -> DownloadResult:
    """Download `url` to `dest_path`, never touching the network more than
    once per call (a caller loops this for genuinely multi-chunk resumes;
    every test here uses a single-response stub, matching how a direct
    Range GET against a static file host behaves in practice).

    - If `dest_path` already exists, a conditional GET is sent using any
      cached ETag/Last-Modified; a `304` short-circuits with no write.
    - Else if a `.partial` file exists, a `Range: bytes=N-` GET resumes it;
      the new bytes are *appended*, never overwriting what's already on
      disk.
    - A `200` is a normal full download (first time, or the conditional
      check reported the upstream file changed).

    Global Constraints: "Never re-download an unchanged extract... use
    conditional requests (If-Modified-Since / ETag) and resume support."
    """
    meta_path = meta_path or _meta_path_for(dest_path)
    partial_path = partial_path or _partial_path_for(dest_path)

    headers: dict[str, str] = {}
    resuming = False
    existing_partial_size = 0

    if dest_path.exists():
        meta = _load_cache_meta(meta_path)
        if meta is not None:
            if meta.etag:
                headers["If-None-Match"] = meta.etag
            if meta.last_modified:
                headers["If-Modified-Since"] = meta.last_modified
    elif partial_path.exists() and partial_path.stat().st_size > 0:
        existing_partial_size = partial_path.stat().st_size
        headers["Range"] = f"bytes={existing_partial_size}-"
        resuming = True

    resp = fetcher.request("GET", url, headers=headers)

    if resp.status == 304:
        if not dest_path.exists():
            raise UnexpectedStatusError(
                f"{url}: server returned 304 Not Modified but no cached file exists at {dest_path}"
            )
        return DownloadResult(DownloadOutcome.NOT_MODIFIED, dest_path, dest_path.stat().st_size)

    if resp.status == 206:
        if not resuming:
            raise UnexpectedStatusError(
                f"{url}: server returned 206 Partial Content for a request "
                f"that did not send a Range header"
            )
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        with partial_path.open("ab") as f:
            f.write(resp.content)
        _write_cache_meta(meta_path, resp)
        new_size = partial_path.stat().st_size
        total = _expected_total_size(resp)
        if total is None or new_size >= total:
            partial_path.replace(dest_path)
            return DownloadResult(DownloadOutcome.RESUMED, dest_path, new_size)
        return DownloadResult(DownloadOutcome.RESUMED_INCOMPLETE, partial_path, new_size)

    if resp.status == 200:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(resp.content)
        if partial_path.exists():
            partial_path.unlink()
        _write_cache_meta(meta_path, resp)
        return DownloadResult(DownloadOutcome.DOWNLOADED, dest_path, len(resp.content))

    raise UnexpectedStatusError(f"{url}: unexpected HTTP status {resp.status}")


def sha256_of(path: Path) -> str:
    """SHA-256 of a file's raw bytes. Same approach as
    `scripts/make_fixture_dem.py::sha256_of` — kept as a free function here
    (not imported from that script) because that script is fixture-only
    tooling and this module must not depend on it."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
