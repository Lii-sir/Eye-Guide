from __future__ import annotations

from eyeguide.domain.models import RoutePlan
from eyeguide.services.navigation.amap_provider import AMapRouteProvider
from eyeguide.services.navigation.demo_provider import DemoRouteProvider
from eyeguide.services.navigation.models import CandidateSearchResult, LocationCandidate, NavigationError, RouteProvider
from eyeguide.services.navigation.osrm_provider import OSRMRouteProvider


class NavigationService:
    def __init__(
        self,
        online_provider: RouteProvider | None = None,
        fallback_provider: RouteProvider | None = None,
    ) -> None:
        self._online_provider = online_provider
        self._fallback_provider = fallback_provider or DemoRouteProvider()

    def build_route(
        self,
        origin: str,
        destination: str,
        prefer_online: bool = True,
        provider: str = "osm",
        amap_api_key: str | None = None,
    ) -> RoutePlan:
        if not prefer_online:
            return self._fallback_provider.build_route(origin, destination)

        online_provider = self._resolve_online_provider(provider, amap_api_key)
        try:
            return online_provider.build_route(origin, destination)
        except NavigationError as exc:
            if self._should_fallback_from_amap(provider, exc):
                fallback_provider = self._resolve_online_provider("osm", amap_api_key)
                try:
                    return fallback_provider.build_route(origin, destination)
                except NavigationError as fallback_exc:
                    detail = self._format_detail(fallback_exc)
                    raise NavigationError(
                        f"高德不可用，已自动回退到 OSM，但 OSM 也失败了：{fallback_exc}",
                        detail=detail,
                    ) from fallback_exc

            detail = self._format_detail(exc)
            if detail:
                raise NavigationError(f"{exc}\n详细信息：{detail}", detail=detail) from exc
            raise
        except Exception as exc:
            raise NavigationError(f"在线路线服务调用失败：{exc}") from exc

    def search_candidates(
        self,
        origin: str,
        destination: str,
        provider: str = "osm",
        amap_api_key: str | None = None,
        limit: int = 5,
    ) -> list[LocationCandidate]:
        return self.search_candidates_debug(
            origin,
            destination,
            provider=provider,
            amap_api_key=amap_api_key,
            limit=limit,
        ).candidates

    def search_candidates_debug(
        self,
        origin: str,
        destination: str,
        provider: str = "osm",
        amap_api_key: str | None = None,
        limit: int = 5,
    ) -> CandidateSearchResult:
        online_provider = self._resolve_online_provider(provider, amap_api_key)
        try:
            return self._search_with_provider_debug(online_provider, origin, destination, provider, limit)
        except NavigationError as exc:
            if self._should_fallback_from_amap(provider, exc):
                fallback_provider = self._resolve_online_provider("osm", amap_api_key)
                result = self._search_with_provider_debug(
                    fallback_provider,
                    origin,
                    destination,
                    "osm",
                    limit,
                )
                return CandidateSearchResult(
                    candidates=result.candidates,
                    debug_lines=[
                        "[候选搜索回退] 高德候选搜索失败，已自动回退到 OSM。",
                        f"[候选搜索回退详情] {exc}",
                        *result.debug_lines,
                    ],
                    provider_used=result.provider_used,
                )
            raise

    def build_demo_route(self, origin: str, destination: str) -> RoutePlan:
        return self._fallback_provider.build_route(origin, destination)

    def _search_with_provider(
        self,
        provider_impl: RouteProvider,
        origin: str,
        destination: str,
        provider_name: str,
        limit: int,
    ) -> list[LocationCandidate]:
        search_method = getattr(provider_impl, "search_candidates", None)
        if callable(search_method):
            try:
                return search_method(destination, nearby_origin=origin, limit=limit)
            except TypeError:
                return search_method(destination, limit=limit)
        raise NavigationError(f"当前导航提供方不支持候选搜索：{provider_name}")

    def _search_with_provider_debug(
        self,
        provider_impl: RouteProvider,
        origin: str,
        destination: str,
        provider_name: str,
        limit: int,
    ) -> CandidateSearchResult:
        debug_method = getattr(provider_impl, "search_candidates_debug", None)
        if callable(debug_method):
            try:
                return debug_method(destination, nearby_origin=origin, limit=limit)
            except TypeError:
                return debug_method(destination, limit=limit)

        candidates = self._search_with_provider(provider_impl, origin, destination, provider_name, limit)
        return CandidateSearchResult(
            candidates=candidates,
            debug_lines=[f"[候选搜索] {provider_name} 未提供细粒度调试信息。"],
            provider_used=provider_name,
        )

    def _resolve_online_provider(self, provider: str, amap_api_key: str | None) -> RouteProvider:
        normalized = (provider or "osm").strip().lower()
        if normalized in {"osm", "osrm"}:
            if self._online_provider is not None:
                return self._online_provider
            return OSRMRouteProvider(amap_api_key=amap_api_key)
        if normalized in {"amap", "gaode", "高德"}:
            return AMapRouteProvider(api_key=amap_api_key)
        raise NavigationError(f"不支持的导航提供方：{provider}")

    def _should_fallback_from_amap(self, provider: str, exc: NavigationError) -> bool:
        normalized = (provider or "").strip().lower()
        if normalized not in {"amap", "gaode", "高德"}:
            return False
        haystack = f"{exc}\n{exc.detail or ''}".upper()
        return (
            "INVALID_USER_KEY" in haystack
            or "USERKEY" in haystack
            or "10001" in haystack
            or "OVER_DIRECTION_RANGE" in haystack
            or "20803" in haystack
        )

    def _format_detail(self, exc: NavigationError) -> str:
        return str(exc.detail or "").strip()
