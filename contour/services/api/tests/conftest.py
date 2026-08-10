"""Shared pytest fixtures for the API test suite."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from contour.api import FIXTURE_RESOURCE, app

_SCHEMA_PATH = Path(__file__).parent / "schema" / "route_response.schema.json"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def response_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text())  # type: ignore[no-any-return]


@pytest.fixture
def fixture_data() -> dict[str, Any]:
    """The committed `/v1/route` fixture, read from the same package-data
    resource `api.py` serves — one copy, so tests and the endpoint can
    never drift apart (fix round 1 of task-3-report.md)."""
    return json.loads(FIXTURE_RESOURCE.read_text())  # type: ignore[no-any-return]
