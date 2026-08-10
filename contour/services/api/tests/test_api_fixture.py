"""Tests for the `/v1/health` and fixture `/v1/route` endpoints (SPEC.md §6).

`test_route_returns_schema_valid_fixture` and `test_route_rejects_out_of_area`
are given verbatim by task-3-brief.md Step 2.
"""

import json
from pathlib import Path
from typing import Any

import jsonschema
from fastapi.testclient import TestClient

from contour import constants

_VALID_REQUEST: dict[str, Any] = {
    "origin": {"lat": 37.7695, "lon": -122.4290},
    "destination": {"lat": 37.7749, "lon": -122.4194},
    "effort_preference": 3,
    "bike_profile": "commuter",
    "avoid_stairs": True,
    "max_alternatives": 3,
}


# ---------------------------------------------------------------------------
# Step 2 tests, verbatim from the brief
# ---------------------------------------------------------------------------


def test_route_returns_schema_valid_fixture(
    client: TestClient, response_schema: dict[str, Any]
) -> None:
    r = client.post(
        "/v1/route",
        json={
            "origin": {"lat": 37.7695, "lon": -122.4290},
            "destination": {"lat": 37.7749, "lon": -122.4194},
            "effort_preference": 3,
            "bike_profile": "commuter",
            "avoid_stairs": True,
            "max_alternatives": 3,
        },
    )
    assert r.status_code == 200
    jsonschema.validate(r.json(), response_schema)


def test_route_rejects_out_of_area(client: TestClient) -> None:
    r = client.post(
        "/v1/route",
        json={
            "origin": {"lat": 40.0, "lon": -74.0},
            "destination": {"lat": 37.7749, "lon": -122.4194},
            "effort_preference": 3,
            "bike_profile": "commuter",
            "avoid_stairs": True,
            "max_alternatives": 3,
        },
    )
    assert r.status_code == 422
    assert r.json()["code"] == "OUT_OF_SERVICE_AREA"


# ---------------------------------------------------------------------------
# Additional coverage
# ---------------------------------------------------------------------------


def test_committed_fixture_file_validates_against_schema(response_schema: dict[str, Any]) -> None:
    """The committed fixture itself, read straight off disk, must be schema
    valid independent of the endpoint plumbing."""
    fixture_path = Path(__file__).parent / "fixtures" / "route_fixture.json"
    fixture = json.loads(fixture_path.read_text())
    jsonschema.validate(fixture, response_schema)


def test_route_response_emits_class_not_klass(client: TestClient) -> None:
    """Task resolution #2: the wire format must use `"class"`, never the
    Python attribute spelling `"klass"` (`contour.types.GradeSegment`)."""
    r = client.post("/v1/route", json=_VALID_REQUEST)
    assert r.status_code == 200
    body = r.json()
    assert body["routes"], "fixture must contain at least one route"
    for segment in body["routes"][0]["grade_segments"]:
        assert "class" in segment
        assert "klass" not in segment


def test_route_destination_out_of_area_also_rejected(client: TestClient) -> None:
    r = client.post(
        "/v1/route",
        json={
            **_VALID_REQUEST,
            "destination": {"lat": 40.0, "lon": -74.0},
        },
    )
    assert r.status_code == 422
    assert r.json()["code"] == "OUT_OF_SERVICE_AREA"


def test_route_invalid_effort_preference_rejected_with_flat_body(client: TestClient) -> None:
    r = client.post("/v1/route", json={**_VALID_REQUEST, "effort_preference": 9})
    assert r.status_code == 422
    body = r.json()
    # Flat {code, message, detail} — not FastAPI's default {"detail": [...]}.
    assert body["code"] == "INVALID_REQUEST"
    assert isinstance(body["message"], str)
    assert isinstance(body["detail"], dict)


def test_error_body_is_flat_not_fastapi_default_envelope(client: TestClient) -> None:
    r = client.post(
        "/v1/route",
        json={**_VALID_REQUEST, "origin": {"lat": 40.0, "lon": -74.0}},
    )
    body = r.json()
    assert set(body.keys()) == {"code", "message", "detail"}
    assert body["detail"] == {"bbox": list(constants.SF_BBOX)}


# ---------------------------------------------------------------------------
# /v1/health
# ---------------------------------------------------------------------------


def test_health_reports_honest_unwired_dependencies(client: TestClient) -> None:
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "status",
        "engine_reachable",
        "dem_readable",
        "manifest_hash",
        "model_version",
    }
    # Nothing in this task makes the engine or DEM real — must not be faked true.
    assert body["engine_reachable"] is False
    assert body["dem_readable"] is None
    assert body["status"] == "degraded"
    assert body["model_version"] == "effort-v1"
