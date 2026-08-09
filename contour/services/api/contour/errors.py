"""Error taxonomy shared by the ranker, elevation, and API layers.

Every route-request failure that should reach the client as a structured
`{code, message, detail}` payload is (or subclasses) `ContourError`. Code
strings are hardcoded per subclass and copied verbatim from Global
Constraints / SPEC.md §6 "Error codes" — never derive them at the call
site, so a typo can't silently mint a new, unhandled code.
"""

from collections.abc import Mapping
from typing import Any


class ContourError(Exception):
    """Base class for every error the API surfaces as a structured code.

    `detail` carries machine-readable context (e.g. `{"min_ascent_m": 64}`)
    for the client copy layer; `message` stays human-readable on its own.
    """

    def __init__(self, code: str, message: str, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail: Mapping[str, Any] = detail if detail is not None else {}

    def __str__(self) -> str:
        return self.message


class OutOfServiceArea(ContourError):
    """Origin and/or destination falls outside `constants.SF_BBOX`."""

    def __init__(self, message: str, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(code="OUT_OF_SERVICE_AREA", message=message, detail=detail)


class NoRouteFound(ContourError):
    """The routing engine returned zero usable candidates."""

    def __init__(self, message: str, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(code="NO_ROUTE_FOUND", message=message, detail=detail)


class OriginUnsnappable(ContourError):
    """Origin or destination could not be snapped onto the road graph."""

    def __init__(self, message: str, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(code="ORIGIN_UNSNAPPABLE", message=message, detail=detail)


class EngineUnavailable(ContourError):
    """The routing engine is unreachable or unhealthy."""

    def __init__(self, message: str, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(code="ENGINE_UNAVAILABLE", message=message, detail=detail)


class InvalidRequest(ContourError):
    """The request failed validation before reaching the ranker."""

    def __init__(self, message: str, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(code="INVALID_REQUEST", message=message, detail=detail)
