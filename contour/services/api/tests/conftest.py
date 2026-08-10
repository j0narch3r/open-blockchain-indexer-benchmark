"""Shared pytest fixtures for the API test suite."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from contour.api import app

_SCHEMA_PATH = Path(__file__).parent / "schema" / "route_response.schema.json"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def response_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text())  # type: ignore[no-any-return]
