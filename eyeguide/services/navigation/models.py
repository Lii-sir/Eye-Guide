from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from eyeguide.domain.models import RoutePlan


class NavigationError(Exception):
    """Raised when route generation fails."""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


@dataclass(frozen=True)
class LocationCandidate:
    display_name: str
    route_value: str
    provider: str
    score: int = 0


@dataclass(frozen=True)
class CandidateSearchResult:
    candidates: list[LocationCandidate]
    debug_lines: list[str]
    provider_used: str


class RouteProvider(Protocol):
    def build_route(self, origin: str, destination: str) -> RoutePlan:
        ...


def debug_text(value: str, limit: int = 120) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."
