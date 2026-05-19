from __future__ import annotations

from eyeguide.services.navigation.amap_provider import AMapRouteProvider
from eyeguide.services.navigation.demo_provider import DemoRouteProvider
from eyeguide.services.navigation.models import (
    CandidateSearchResult,
    LocationCandidate,
    NavigationError,
    RouteProvider,
)
from eyeguide.services.navigation.osrm_provider import OSRMRouteProvider

__all__ = [
    "AMapRouteProvider",
    "CandidateSearchResult",
    "DemoRouteProvider",
    "LocationCandidate",
    "NavigationError",
    "OSRMRouteProvider",
    "RouteProvider",
]
