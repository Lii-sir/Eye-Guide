from __future__ import annotations

from eyeguide.domain.models import RoutePlan
from eyeguide.services.navigation.providers import (
    DemoRouteProvider,
    NavigationError,
    OSRMRouteProvider,
    RouteProvider,
)


class NavigationService:
    def __init__(
        self,
        online_provider: RouteProvider | None = None,
        fallback_provider: RouteProvider | None = None,
    ) -> None:
        self._online_provider = online_provider or OSRMRouteProvider()
        self._fallback_provider = fallback_provider or DemoRouteProvider()

    def build_route(self, origin: str, destination: str, prefer_online: bool = True) -> RoutePlan:
        if not prefer_online:
            return self._fallback_provider.build_route(origin, destination)

        try:
            return self._online_provider.build_route(origin, destination)
        except NavigationError as exc:
            if exc.detail:
                raise NavigationError(f"{exc}\n详细信息：{exc.detail}", detail=exc.detail) from exc
            raise
        except Exception as exc:
            raise NavigationError(f"在线路线服务调用失败：{exc}") from exc

    def build_demo_route(self, origin: str, destination: str) -> RoutePlan:
        return self._fallback_provider.build_route(origin, destination)
