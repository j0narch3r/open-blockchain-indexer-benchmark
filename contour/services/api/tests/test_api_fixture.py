"""Tests for the `/v1/health` and fixture `/v1/route` endpoints (SPEC.md §6).

`test_route_returns_schema_valid_fixture` and `test_route_rejects_out_of_area`
are given verbatim by task-3-brief.md Step 2.
"""

from typing import Any

import jsonschema
import pytest
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


def _routes_by_label(client: TestClient) -> dict[str, dict[str, Any]]:
    r = client.post("/v1/route", json=_VALID_REQUEST)
    routes: list[dict[str, Any]] = r.json()["routes"]
    return {route["label"]: route for route in routes}


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


def test_committed_fixture_file_validates_against_schema(
    fixture_data: dict[str, Any], response_schema: dict[str, Any]
) -> None:
    """The committed fixture itself, read straight off the package-data
    resource `api.py` serves, must be schema valid independent of the
    endpoint plumbing."""
    jsonschema.validate(fixture_data, response_schema)


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
# Fix round 1: three routes, internally consistent
# ---------------------------------------------------------------------------


def test_fixture_has_exactly_three_routes_with_expected_labels(client: TestClient) -> None:
    """SPEC.md §2.2's MVP interaction is three ranked alternatives — a
    one-route fixture can't exercise the card list, ranking labels, or
    comparison sentences later tasks are built against (Appendix B
    ticket M1-04, Task 20's client tests)."""
    r = client.post("/v1/route", json=_VALID_REQUEST)
    assert r.status_code == 200
    routes = r.json()["routes"]
    assert len(routes) == 3
    assert {route["label"] for route in routes} == {"Gentlest", "Balanced", "Fastest"}


def test_gentlest_and_fastest_are_the_extremes(client: TestClient) -> None:
    """Gentlest must have the lowest ascent and greatest distance; Fastest
    the reverse — otherwise the labels lie about the trade-off."""
    routes = _routes_by_label(client)
    gentlest, balanced, fastest = routes["Gentlest"], routes["Balanced"], routes["Fastest"]

    assert gentlest["ascent_m"] < balanced["ascent_m"] < fastest["ascent_m"]
    assert gentlest["distance_m"] > balanced["distance_m"] > fastest["distance_m"]
    assert (
        gentlest["flat_equivalent_m"] < balanced["flat_equivalent_m"] < fastest["flat_equivalent_m"]
    )
    assert gentlest["effort_score"] < balanced["effort_score"] < fastest["effort_score"]


def test_comparison_to_fastest_is_arithmetically_correct(client: TestClient) -> None:
    """Every route's `comparison_to_fastest.delta_*` must equal that route's
    own value minus the route labelled Fastest's value — the assertion that
    catches a hand-edited fixture drifting out of internal consistency."""
    routes = _routes_by_label(client)
    fastest = routes["Fastest"]

    for label, route in routes.items():
        comparison = route["comparison_to_fastest"]
        assert comparison["delta_distance_m"] == route["distance_m"] - fastest["distance_m"], label
        assert comparison["delta_duration_s"] == route["duration_s"] - fastest["duration_s"], label
        assert comparison["delta_ascent_m"] == route["ascent_m"] - fastest["ascent_m"], label

    # The Fastest route's own comparison to itself must be all zeros.
    assert fastest["comparison_to_fastest"] == {
        "delta_distance_m": 0,
        "delta_duration_s": 0,
        "delta_ascent_m": 0,
    }


def test_grade_segments_max_matches_route_max_grade_pct(fixture_data: dict[str, Any]) -> None:
    """`grade_segments` must actually correspond to `max_grade_pct` — the
    steepest listed segment should equal the route's own stated maximum,
    not exceed or wildly undershoot it."""
    for route in fixture_data["routes"]:
        segment_max = max(segment["grade_pct"] for segment in route["grade_segments"])
        assert segment_max == pytest.approx(route["max_grade_pct"], abs=0.05), route["label"]
        for section in route["steep_sections"]:
            assert section["grade_pct"] <= route["max_grade_pct"] + 1e-9, route["label"]


def test_elevation_profile_rise_and_fall_match_ascent_and_descent(
    fixture_data: dict[str, Any],
) -> None:
    """Summing the profile's positive/negative elevation deltas should land
    close to the route's stated `ascent_m`/`descent_m` — a client charting
    this should see something coherent, not noise."""
    for route in fixture_data["routes"]:
        points = route["elevation_profile"]
        rises = sum(
            max(0.0, points[i]["ele_m"] - points[i - 1]["ele_m"]) for i in range(1, len(points))
        )
        falls = sum(
            max(0.0, points[i - 1]["ele_m"] - points[i]["ele_m"]) for i in range(1, len(points))
        )
        assert rises == pytest.approx(route["ascent_m"], abs=0.5), route["label"]
        assert falls == pytest.approx(route["descent_m"], abs=0.5), route["label"]


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
