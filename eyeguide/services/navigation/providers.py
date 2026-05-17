from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol
import html
import math
import os
import re

import requests

from eyeguide.domain.models import GeoPoint, RoutePlan, RouteStep


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


def _debug_text(value: str, limit: int = 120) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


class RouteProvider(Protocol):
    def build_route(self, origin: str, destination: str) -> RoutePlan:
        ...


class DemoRouteProvider:
    def build_route(self, origin: str, destination: str) -> RoutePlan:
        steps = [
            RouteStep("请从当前位置直行约 80 米。", 80, target=GeoPoint(0.0, 0.0)),
            RouteStep("前方路口右转，注意来往车辆。", 30, target=GeoPoint(0.0, 0.0)),
            RouteStep("继续沿人行道前行，留意路边障碍物。", 120, target=GeoPoint(0.0, 0.0)),
            RouteStep("目的地就在前方附近，请减速确认入口。", 20, target=GeoPoint(0.0, 0.0)),
        ]
        return RoutePlan(
            origin=origin or "当前位置",
            destination=destination,
            steps=steps,
            source="demo",
            travel_mode="foot",
            resolved_origin_address=origin or "当前位置",
            resolved_destination_address=destination,
            distance_meters=250,
            duration_seconds=240,
        )


class OSRMRouteProvider:
    search_url = "https://nominatim.openstreetmap.org/search"
    route_url = "https://router.project-osrm.org/route/v1/foot"
    amap_geocode_url = "https://restapi.amap.com/v3/geocode/geo"

    def __init__(self, amap_api_key: str | None = None) -> None:
        self._amap_api_key = (amap_api_key or os.getenv("AMAP_WEB_API_KEY") or os.getenv("AMAP_KEY") or "").strip()
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": "EyeGuidePrototype/0.1 (desktop navigation demo)"}
        )

    def build_route(self, origin: str, destination: str) -> RoutePlan:
        if not origin.strip() or not destination.strip():
            raise NavigationError("路线导航模式需要填写起点和终点。")

        origin_lon, origin_lat, origin_address = self._resolve_point(origin)
        destination_lon, destination_lat, destination_address = self._resolve_point(destination)
        try:
            response = self._session.get(
                f"{self.route_url}/{origin_lon},{origin_lat};{destination_lon},{destination_lat}",
                params={"overview": "false", "steps": "true", "alternatives": "false"},
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.Timeout as exc:
            raise NavigationError("OSRM 路线请求超时。", detail=str(exc)) from exc
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "未知"
            response_text = exc.response.text[:200].strip() if exc.response is not None else ""
            raise NavigationError(
                f"OSRM 路线服务返回错误状态：{status_code}",
                detail=response_text or str(exc),
            ) from exc
        except requests.RequestException as exc:
            raise NavigationError("OSRM 路线网络请求失败。", detail=str(exc)) from exc
        except ValueError as exc:
            raise NavigationError("OSRM 路线结果无法解析。", detail=str(exc)) from exc

        routes = payload.get("routes", [])
        if not routes:
            raise NavigationError("OSRM 没有返回可用步行路线。")

        route = routes[0]
        legs = route.get("legs", [])
        if not legs:
            raise NavigationError("OSRM 路线结果缺少分段信息。")

        steps: List[RouteStep] = []
        for step in legs[0].get("steps", []):
            maneuver = step.get("maneuver", {})
            road_name = str(step.get("name", "") or "").strip()
            steps.append(
                RouteStep(
                    instruction=self._build_instruction(
                        maneuver_type=str(maneuver.get("type", "") or ""),
                        modifier=str(maneuver.get("modifier", "") or ""),
                        road_name=road_name,
                        distance=float(step.get("distance", 0.0)),
                    ),
                    distance_meters=float(step.get("distance", 0.0)),
                    road_name=road_name,
                    target=self._step_target(maneuver.get("location")),
                    arrival_radius_meters=25.0,
                )
            )

        if not steps:
            raise NavigationError("OSRM 路线步骤为空。")

        return RoutePlan(
            origin=origin,
            destination=destination,
            steps=steps,
            source="osrm",
            travel_mode="foot",
            resolved_origin_address=origin_address,
            resolved_destination_address=destination_address,
            distance_meters=float(route.get("distance", 0.0)),
            duration_seconds=float(route.get("duration", 0.0)),
        )

    def search_candidates(
        self,
        query: str,
        nearby_origin: str | None = None,
        limit: int = 5,
    ) -> list[LocationCandidate]:
        return self.search_candidates_debug(query, nearby_origin=nearby_origin, limit=limit).candidates

    def search_candidates_debug(
        self,
        query: str,
        nearby_origin: str | None = None,
        limit: int = 5,
    ) -> CandidateSearchResult:
        raw = query.strip()
        debug_lines = [
            f"[候选搜索][OSM] query={raw or '<empty>'}",
            f"[候选搜索][OSM] nearby_origin={nearby_origin or '<none>'}",
            f"[候选搜索][OSM] final_limit={limit}",
        ]
        if not raw:
            debug_lines.append("[候选搜索][OSM] 输入为空，未执行搜索。")
            return CandidateSearchResult(candidates=[], debug_lines=debug_lines, provider_used="osrm")

        coordinate_candidate = self._coordinate_candidate(raw)
        if coordinate_candidate is not None:
            debug_lines.append(
                f"[候选搜索][OSM] 输入被识别为坐标，直接采用 route_value={coordinate_candidate.route_value}"
            )
            return CandidateSearchResult(
                candidates=[coordinate_candidate],
                debug_lines=debug_lines,
                provider_used="osrm",
            )

        fetch_limit = max(limit * 2, 10)
        payload = self._fetch_search_payload(raw, limit=fetch_limit)
        debug_lines.append(f"[候选搜索][OSM] Nominatim 原始结果数={len(payload)} fetch_limit={fetch_limit}")
        reference_point = self._geocode_with_amap(raw)
        if reference_point is None:
            debug_lines.append("[候选搜索][OSM] 高德参考坐标=<none>")
        else:
            debug_lines.append(
                f"[候选搜索][OSM] 高德参考坐标={reference_point[1]:.6f},{reference_point[0]:.6f}"
            )

        scored_items: list[tuple[int, int, dict]] = []
        for raw_index, item in enumerate(payload, start=1):
            score = self._score_geocode_candidate(raw, item, reference_point)
            scored_items.append((score, raw_index, item))
        ranked = sorted(scored_items, key=lambda item: item[0], reverse=True)

        results: list[LocationCandidate] = []
        seen: set[str] = set()
        for rank, (score, raw_index, item) in enumerate(ranked, start=1):
            display_name = str(item.get("display_name", "") or "").strip()
            if not display_name:
                debug_lines.append(f"[候选搜索][OSM][raw {raw_index}/rank {rank}] 跳过：display_name 为空")
                continue
            try:
                longitude = float(item["lon"])
                latitude = float(item["lat"])
            except (KeyError, TypeError, ValueError):
                debug_lines.append(
                    f"[候选搜索][OSM][raw {raw_index}/rank {rank}] 跳过：坐标无效 name={_debug_text(display_name)}"
                )
                continue
            key = f"{latitude:.6f},{longitude:.6f}"
            if key in seen:
                debug_lines.append(
                    f"[候选搜索][OSM][raw {raw_index}/rank {rank}] 去重：coord={key} "
                    f"score={score} name={_debug_text(display_name)}"
                )
                continue
            if len(results) >= limit:
                debug_lines.append(
                    f"[候选搜索][OSM][raw {raw_index}/rank {rank}] 丢弃：超过候选上限 {limit} "
                    f"score={score} coord={key} name={_debug_text(display_name)}"
                )
                continue
            results.append(
                LocationCandidate(
                    display_name=display_name,
                    route_value=f"{latitude},{longitude}",
                    provider="osrm",
                    score=score,
                )
            )
            seen.add(key)
            debug_lines.append(
                f"[候选搜索][OSM][raw {raw_index}/rank {rank}] 采纳：score={score} "
                f"coord={key} class={item.get('class', '')}/{item.get('type', '')} "
                f"name={_debug_text(display_name)}"
            )

        debug_lines.append(f"[候选搜索][OSM] 最终候选数={len(results)}")
        for index, candidate in enumerate(results, start=1):
            debug_lines.append(
                f"[候选搜索][OSM][候选 {index}] score={candidate.score} "
                f"route_value={candidate.route_value} name={_debug_text(candidate.display_name)}"
            )
        return CandidateSearchResult(candidates=results, debug_lines=debug_lines, provider_used="osrm")

    def _resolve_point(self, value: str) -> tuple[float, float, str]:
        raw = value.strip()
        if "," in raw:
            parts = [item.strip() for item in raw.split(",", 1)]
            try:
                latitude = float(parts[0])
                longitude = float(parts[1])
                return longitude, latitude, raw
            except ValueError:
                pass
        longitude, latitude, resolved_address = self._geocode(raw)
        return longitude, latitude, resolved_address or raw

    def _fetch_search_payload(self, query: str, limit: int = 8) -> list[dict]:
        try:
            response = self._session.get(
                self.search_url,
                params={
                    "q": query,
                    "format": "jsonv2",
                    "limit": limit,
                    "addressdetails": 1,
                    "namedetails": 1,
                    "accept-language": "zh-CN,zh;q=0.9,en;q=0.6",
                },
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.Timeout as exc:
            raise NavigationError(f"地点解析超时：{query}", detail=str(exc)) from exc
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "未知"
            response_text = exc.response.text[:200].strip() if exc.response is not None else ""
            raise NavigationError(
                f"地点解析服务返回错误状态：{status_code}",
                detail=f"query={query}; response={response_text or exc}",
            ) from exc
        except requests.RequestException as exc:
            raise NavigationError(f"地点解析网络请求失败：{query}", detail=str(exc)) from exc
        except ValueError as exc:
            raise NavigationError(f"地点解析结果无法解析：{query}", detail=str(exc)) from exc
        return payload

    def _geocode(self, query: str) -> tuple[float, float, str]:
        payload = self._fetch_search_payload(query, limit=8)
        if not payload:
            raise NavigationError(f"找不到地点：{query}")

        reference_point = self._geocode_with_amap(query)
        best = max(
            payload,
            key=lambda item: self._score_geocode_candidate(query, item, reference_point),
        )
        return float(best["lon"]), float(best["lat"]), str(best.get("display_name", "") or "")

    def _coordinate_candidate(self, raw: str) -> LocationCandidate | None:
        if "," not in raw:
            return None
        parts = [item.strip() for item in raw.split(",", 1)]
        try:
            latitude = float(parts[0])
            longitude = float(parts[1])
        except ValueError:
            return None
        return LocationCandidate(
            display_name=raw,
            route_value=f"{latitude},{longitude}",
            provider="osrm",
            score=9999,
        )

    def _score_geocode_candidate(
        self,
        query: str,
        item: dict,
        reference_point: tuple[float, float] | None,
    ) -> int:
        score = 0
        normalized_query = self._normalize_search_text(query)
        display_name = str(item.get("display_name", "") or "")
        normalized_display = self._normalize_search_text(display_name)

        if normalized_query and normalized_display == normalized_query:
            score += 450
        elif normalized_query and normalized_query in normalized_display:
            score += 260

        names_to_check = [str(item.get("name", "") or ""), display_name]
        namedetails = item.get("namedetails")
        if isinstance(namedetails, dict):
            names_to_check.extend(str(value or "") for value in namedetails.values())
        address = item.get("address")
        if isinstance(address, dict):
            names_to_check.extend(str(value or "") for value in address.values())

        for name in names_to_check:
            normalized_name = self._normalize_search_text(name)
            if not normalized_name:
                continue
            if normalized_name == normalized_query:
                score += 700
                break
            if normalized_query and normalized_query in normalized_name:
                score += 320
                break

        item_class = str(item.get("class", "") or "")
        item_type = str(item.get("type", "") or "")
        if item_class in {"amenity", "building"}:
            score += 20
        if item_type in {"university", "college", "school"}:
            score += 120

        place_rank = item.get("place_rank")
        try:
            score -= abs(int(place_rank) - 30)
        except (TypeError, ValueError):
            pass

        importance = item.get("importance")
        try:
            score += int(float(importance) * 20)
        except (TypeError, ValueError):
            pass

        if reference_point is not None:
            try:
                candidate_point = (float(item["lon"]), float(item["lat"]))
            except (KeyError, TypeError, ValueError):
                candidate_point = None
            if candidate_point is not None:
                distance_m = self._haversine_distance_meters(reference_point, candidate_point)
                if distance_m <= 500:
                    score += 600
                elif distance_m <= 2000:
                    score += 180
                elif distance_m >= 50000:
                    score -= 600
                elif distance_m >= 10000:
                    score -= 260

        return score

    def _geocode_with_amap(self, query: str) -> tuple[float, float] | None:
        if not self._amap_api_key:
            return None
        try:
            response = self._session.get(
                self.amap_geocode_url,
                params={"key": self._amap_api_key, "address": query, "output": "JSON"},
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            return None

        if str(payload.get("status", "")) != "1":
            return None
        geocodes = payload.get("geocodes", [])
        if not geocodes:
            return None
        location = str(geocodes[0].get("location", "") or "")
        try:
            longitude, latitude = [float(item) for item in location.split(",", 1)]
        except (TypeError, ValueError):
            return None
        return longitude, latitude

    def _normalize_search_text(self, text: str) -> str:
        lowered = str(text or "").strip().lower()
        return re.sub(r"[\s\-_(),，。·/（）]+", "", lowered)

    def _haversine_distance_meters(
        self,
        point_a: tuple[float, float],
        point_b: tuple[float, float],
    ) -> float:
        lon1, lat1 = point_a
        lon2, lat2 = point_b
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        half_chord = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
        )
        return 6371000.0 * 2 * math.atan2(math.sqrt(half_chord), math.sqrt(max(1 - half_chord, 0.0)))

    def _build_instruction(
        self,
        maneuver_type: str,
        modifier: str,
        road_name: str,
        distance: float,
    ) -> str:
        direction_map = {
            "left": "左转",
            "right": "右转",
            "straight": "直行",
            "slight left": "向左前方",
            "slight right": "向右前方",
            "sharp left": "向左急转",
            "sharp right": "向右急转",
            "uturn": "掉头",
        }
        action_map = {
            "depart": "出发后",
            "turn": "",
            "continue": "",
            "arrive": "即将到达目的地",
            "end of road": "到路口后",
            "fork": "前方岔路口",
            "new name": "继续前进",
            "merge": "并入前方道路",
            "roundabout": "进入环岛",
            "notification": "请注意",
        }

        action = action_map.get(maneuver_type, "请继续")
        direction = direction_map.get(modifier, "前进")
        distance_text = f"约 {int(distance)} 米" if distance >= 1 else "很近"
        road_text = html.unescape(road_name)

        if maneuver_type == "arrive":
            return f"{action}。"
        if road_text:
            return f"{action}{direction}，沿 {road_text} 前进 {distance_text}。"
        return f"{action}{direction}，前进 {distance_text}。"

    def _step_target(self, raw_location) -> GeoPoint | None:
        if not isinstance(raw_location, (list, tuple)) or len(raw_location) < 2:
            return None
        try:
            longitude = float(raw_location[0])
            latitude = float(raw_location[1])
        except (TypeError, ValueError):
            return None
        return GeoPoint(latitude=latitude, longitude=longitude)


class AMapRouteProvider:
    geocode_url = "https://restapi.amap.com/v3/geocode/geo"
    regeo_url = "https://restapi.amap.com/v3/geocode/regeo"
    foot_url = "https://restapi.amap.com/v3/direction/walking"
    input_tips_url = "https://restapi.amap.com/v3/assistant/inputtips"
    place_text_url = "https://restapi.amap.com/v3/place/text"
    place_around_url = "https://restapi.amap.com/v3/place/around"

    @dataclass
    class SearchContext:
        city: str = ""
        district: str = ""
        adcode: str = ""
        formatted_address: str = ""

    @dataclass
    class CandidatePoint:
        longitude: float
        latitude: float
        resolved_address: str
        score: int

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = (api_key or os.getenv("AMAP_WEB_API_KEY") or os.getenv("AMAP_KEY") or "").strip()
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": "EyeGuidePrototype/0.1 (desktop foot navigation)"}
        )
        self._last_resolved_address = ""

    def build_route(self, origin: str, destination: str) -> RoutePlan:
        if not self._api_key:
            raise NavigationError("高德导航需要 Web 服务 Key。")
        if not origin.strip() or not destination.strip():
            raise NavigationError("路线导航模式需要填写起点和终点。")

        self._last_resolved_address = ""
        origin_lon, origin_lat = self._resolve_point(origin, prefer_nearby_bias=False)
        origin_point = GeoPoint(latitude=origin_lat, longitude=origin_lon)
        origin_context = self._reverse_geocode_context(origin_point)
        origin_resolved_address = origin_context.formatted_address or self._last_resolved_address or origin.strip()

        self._last_resolved_address = ""
        destination_lon, destination_lat = self._resolve_point(
            destination,
            nearby_point=origin_point,
            search_context=origin_context,
            prefer_nearby_bias=False,
        )
        destination_resolved_address = self._last_resolved_address or destination.strip()
        if destination_resolved_address == destination.strip():
            destination_point = GeoPoint(latitude=destination_lat, longitude=destination_lon)
            destination_context = self._reverse_geocode_context(destination_point)
            destination_resolved_address = destination_context.formatted_address or destination_resolved_address

        payload = self._request_json(
            self.foot_url,
            params={
                "key": self._api_key,
                "origin": f"{origin_lon},{origin_lat}",
                "destination": f"{destination_lon},{destination_lat}",
                "output": "JSON",
            },
            action_name="高德步行路线规划",
        )

        route = payload.get("route", {})
        paths = route.get("paths", [])
        if not paths:
            raise NavigationError("高德没有返回可用步行路线。", detail=str(payload)[:300])

        path = paths[0]
        steps: List[RouteStep] = []
        for step in path.get("steps", []):
            instruction = str(step.get("instruction", "") or "").strip()
            if not instruction:
                instruction = self._build_instruction(step)
            steps.append(
                RouteStep(
                    instruction=instruction,
                    distance_meters=self._safe_float(step.get("distance")),
                    road_name=str(step.get("road", "") or "").strip(),
                    target=self._polyline_target(str(step.get("polyline", "") or "")),
                    arrival_radius_meters=25.0,
                )
            )

        if not steps:
            raise NavigationError("高德路线步骤为空。", detail=str(path)[:300])

        return RoutePlan(
            origin=origin,
            destination=destination,
            steps=steps,
            source="amap",
            travel_mode="foot",
            resolved_origin_address=origin_resolved_address,
            resolved_destination_address=destination_resolved_address,
            distance_meters=self._safe_float(path.get("distance")),
            duration_seconds=self._safe_float(path.get("duration")),
        )

    def search_candidates(
        self,
        query: str,
        nearby_origin: str | None = None,
        limit: int = 5,
    ) -> list[LocationCandidate]:
        return self.search_candidates_debug(query, nearby_origin=nearby_origin, limit=limit).candidates

    def search_candidates_debug(
        self,
        query: str,
        nearby_origin: str | None = None,
        limit: int = 5,
    ) -> CandidateSearchResult:
        raw = query.strip()
        debug_lines = [
            f"[候选搜索][高德] query={raw or '<empty>'}",
            f"[候选搜索][高德] nearby_origin={nearby_origin or '<none>'}",
            f"[候选搜索][高德] final_limit={limit}",
        ]
        if not raw:
            debug_lines.append("[候选搜索][高德] 输入为空，未执行搜索。")
            return CandidateSearchResult(candidates=[], debug_lines=debug_lines, provider_used="amap")

        if not self._api_key:
            debug_lines.append("[候选搜索][高德] 未配置 Web API Key，无法执行高德候选搜索。")
            return CandidateSearchResult(candidates=[], debug_lines=debug_lines, provider_used="amap")

        coordinate_candidate = self._coordinate_candidate(raw)
        if coordinate_candidate is not None:
            debug_lines.append(
                f"[候选搜索][高德] 输入被识别为坐标，直接采用 route_value={coordinate_candidate.route_value}"
            )
            return CandidateSearchResult(
                candidates=[coordinate_candidate],
                debug_lines=debug_lines,
                provider_used="amap",
            )

        nearby_point, search_context = self._origin_context_from_text(nearby_origin or "")
        debug_lines.append(
            "[候选搜索][高德] origin_context="
            f"city={getattr(search_context, 'city', '') or '<none>'}, "
            f"district={getattr(search_context, 'district', '') or '<none>'}, "
            f"adcode={getattr(search_context, 'adcode', '') or '<none>'}"
        )
        candidates: list[LocationCandidate] = []

        def append_candidate(
            source: str,
            display_name: str,
            route_value: str,
            score: int,
        ) -> None:
            candidates.append(
                LocationCandidate(
                    display_name=display_name,
                    route_value=route_value,
                    provider="amap",
                    score=score,
                )
            )
            debug_lines.append(
                f"[候选搜索][高德][{source}] 命中：score={score} route_value={route_value} "
                f"name={_debug_text(display_name)}"
            )

        exact_result = self._try_global_exact_match(raw)
        if exact_result is not None:
            longitude, latitude, resolved_address = exact_result
            append_candidate("global_exact", resolved_address, f"{latitude},{longitude}", 2400)
        else:
            debug_lines.append("[候选搜索][高德][global_exact] 未命中")

        global_geocode = self._try_geocode(raw, None)
        if global_geocode is not None:
            longitude, latitude, resolved_address = global_geocode
            score = 2000 if self._is_high_confidence_match(raw, resolved_address) else 1200
            append_candidate("global_geocode", resolved_address, f"{latitude},{longitude}", score)
        else:
            debug_lines.append("[候选搜索][高德][global_geocode] 未命中")

        candidate_sources = [
            ("global_poi", self._search_best_poi(raw, None, None, city_limit=False)),
            ("global_tip", self._search_best_tip(raw, None, None, city_limit=False)),
            ("local_poi", self._search_best_poi(raw, nearby_point, search_context, city_limit=True)),
            ("local_tip", self._search_best_tip(raw, nearby_point, search_context, city_limit=True)),
        ]
        for source, candidate_point in candidate_sources:
            if candidate_point is None:
                debug_lines.append(f"[候选搜索][高德][{source}] 未命中")
                continue
            append_candidate(
                source,
                candidate_point.resolved_address,
                f"{candidate_point.latitude},{candidate_point.longitude}",
                candidate_point.score,
            )

        ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
        results: list[LocationCandidate] = []
        seen: set[str] = set()
        for candidate in ranked:
            key = self._normalize_search_text(candidate.display_name)
            if key in seen:
                debug_lines.append(
                    f"[候选搜索][高德] 去重：score={candidate.score} "
                    f"route_value={candidate.route_value} name={_debug_text(candidate.display_name)}"
                )
                continue
            seen.add(key)
            if len(results) >= limit:
                debug_lines.append(
                    f"[候选搜索][高德] 丢弃：超过候选上限 {limit} "
                    f"score={candidate.score} route_value={candidate.route_value} "
                    f"name={_debug_text(candidate.display_name)}"
                )
                continue
            results.append(candidate)
        debug_lines.append(f"[候选搜索][高德] 原始候选数={len(candidates)} 去重后候选数={len(results)}")
        for index, candidate in enumerate(results, start=1):
            debug_lines.append(
                f"[候选搜索][高德][候选 {index}] score={candidate.score} "
                f"route_value={candidate.route_value} name={_debug_text(candidate.display_name)}"
            )
        return CandidateSearchResult(candidates=results, debug_lines=debug_lines, provider_used="amap")

    def _resolve_point(
        self,
        value: str,
        nearby_point: GeoPoint | None = None,
        search_context: SearchContext | None = None,
        prefer_nearby_bias: bool = False,
    ) -> tuple[float, float]:
        raw = value.strip()
        if "," in raw:
            parts = [item.strip() for item in raw.split(",", 1)]
            try:
                latitude = float(parts[0])
                longitude = float(parts[1])
                self._last_resolved_address = raw
                return longitude, latitude
            except ValueError:
                pass

        global_geocode_result = self._try_geocode(raw, None)
        if global_geocode_result is not None and self._is_high_confidence_match(raw, global_geocode_result[2]):
            longitude, latitude, resolved_address = global_geocode_result
            self._last_resolved_address = resolved_address
            return longitude, latitude

        exact_result = self._try_global_exact_match(raw)
        if exact_result is not None:
            longitude, latitude, resolved_address = exact_result
            self._last_resolved_address = resolved_address
            return longitude, latitude

        query_conflicts_with_context = self._query_conflicts_with_context(raw, search_context)

        geocode_result = None
        if not query_conflicts_with_context:
            geocode_result = self._try_geocode(raw, search_context)
            if geocode_result is not None and self._is_high_confidence_match(raw, geocode_result[2]):
                longitude, latitude, resolved_address = geocode_result
                self._last_resolved_address = resolved_address
                return longitude, latitude

        best_poi = self._search_best_poi(raw, nearby_point, search_context, city_limit=False)
        if best_poi is not None and self._is_high_confidence_match(raw, best_poi.resolved_address):
            self._last_resolved_address = best_poi.resolved_address
            return best_poi.longitude, best_poi.latitude

        if global_geocode_result is not None:
            longitude, latitude, resolved_address = global_geocode_result
            self._last_resolved_address = resolved_address
            return longitude, latitude

        if geocode_result is not None:
            longitude, latitude, resolved_address = geocode_result
            self._last_resolved_address = resolved_address
            return longitude, latitude

        best_tip = self._search_best_tip(raw, nearby_point, search_context, city_limit=False)
        if best_tip is not None and self._is_high_confidence_match(raw, best_tip.resolved_address):
            self._last_resolved_address = best_tip.resolved_address
            return best_tip.longitude, best_tip.latitude

        local_poi = None if query_conflicts_with_context else self._search_best_poi(raw, nearby_point, search_context, city_limit=True)
        if local_poi is not None and prefer_nearby_bias:
            self._last_resolved_address = local_poi.resolved_address
            return local_poi.longitude, local_poi.latitude

        local_tip = None if query_conflicts_with_context else self._search_best_tip(raw, nearby_point, search_context, city_limit=True)
        if local_tip is not None and prefer_nearby_bias:
            self._last_resolved_address = local_tip.resolved_address
            return local_tip.longitude, local_tip.latitude

        if best_poi is not None:
            self._last_resolved_address = best_poi.resolved_address
            return best_poi.longitude, best_poi.latitude
        if best_tip is not None:
            self._last_resolved_address = best_tip.resolved_address
            return best_tip.longitude, best_tip.latitude

        raise NavigationError(f"高德无法识别地点：{raw}")

    def _try_global_exact_match(self, keyword: str) -> tuple[float, float, str] | None:
        poi = self._search_best_poi(keyword, None, None, city_limit=False)
        if poi is not None and self._is_strict_exact_match(keyword, poi.resolved_address):
            return poi.longitude, poi.latitude, poi.resolved_address

        tip = self._search_best_tip(keyword, None, None, city_limit=False)
        if tip is not None and self._is_strict_exact_match(keyword, tip.resolved_address):
            return tip.longitude, tip.latitude, tip.resolved_address
        return None

    def _try_geocode(
        self,
        address: str,
        search_context: SearchContext | None = None,
    ) -> tuple[float, float, str] | None:
        params = {"key": self._api_key, "address": address, "output": "JSON"}
        if search_context is not None:
            city_hint = search_context.adcode or search_context.city
            if city_hint:
                params["city"] = city_hint

        try:
            payload = self._request_json(self.geocode_url, params=params, action_name=f"高德地点解析：{address}")
        except NavigationError:
            return None

        geocodes = payload.get("geocodes", [])
        if not geocodes:
            return None

        matched = geocodes[0]
        location = str(matched.get("location", "") or "")
        try:
            longitude, latitude = [float(item) for item in location.split(",", 1)]
        except (TypeError, ValueError):
            return None
        return longitude, latitude, self._format_resolved_address(matched)

    def _reverse_geocode_context(self, point: GeoPoint) -> SearchContext:
        payload = self._request_json(
            self.regeo_url,
            params={
                "key": self._api_key,
                "location": f"{point.longitude},{point.latitude}",
                "extensions": "base",
                "output": "JSON",
            },
            action_name="高德逆地理编码",
        )
        regeocode = payload.get("regeocode", {})
        component = regeocode.get("addressComponent", {})
        city_value = component.get("city", "")
        if isinstance(city_value, list):
            city_value = city_value[0] if city_value else ""
        return self.SearchContext(
            city=str(city_value or ""),
            district=str(component.get("district", "") or ""),
            adcode=str(component.get("adcode", "") or ""),
            formatted_address=str(regeocode.get("formatted_address", "") or ""),
        )

    def _request_json(self, url: str, *, params: dict[str, str], action_name: str) -> dict:
        try:
            response = self._session.get(url, params=params, timeout=12)
            response.raise_for_status()
            payload = response.json()
        except requests.Timeout as exc:
            raise NavigationError(f"{action_name}超时。", detail=str(exc)) from exc
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "未知"
            response_text = exc.response.text[:200].strip() if exc.response is not None else ""
            raise NavigationError(
                f"{action_name}返回错误状态：{status_code}",
                detail=response_text or str(exc),
            ) from exc
        except requests.RequestException as exc:
            raise NavigationError(f"{action_name}网络请求失败。", detail=str(exc)) from exc
        except ValueError as exc:
            raise NavigationError(f"{action_name}返回了无法解析的数据。", detail=str(exc)) from exc

        if str(payload.get("status", "")) != "1":
            raise NavigationError(
                f"{action_name}失败：{payload.get('info', '未知错误')}",
                detail=f"infocode={payload.get('infocode', '')}; payload={str(payload)[:300]}",
            )
        return payload

    def _search_best_tip(
        self,
        keyword: str,
        nearby_point: GeoPoint | None,
        search_context: SearchContext | None,
        *,
        city_limit: bool,
    ) -> CandidatePoint | None:
        params = {
            "key": self._api_key,
            "keywords": keyword,
            "datatype": "all",
            "output": "JSON",
        }
        if nearby_point is not None:
            params["location"] = f"{nearby_point.longitude},{nearby_point.latitude}"
        if search_context is not None:
            city_hint = search_context.adcode or search_context.city
            if city_hint:
                params["city"] = city_hint
                if city_limit:
                    params["citylimit"] = "true"

        try:
            payload = self._request_json(self.input_tips_url, params=params, action_name=f"高德输入提示：{keyword}")
        except NavigationError:
            return None

        return self._pick_best_candidate(keyword, payload.get("tips", []), nearby_point, formatter=self._format_tip_address)

    def _search_best_poi(
        self,
        keyword: str,
        nearby_point: GeoPoint | None,
        search_context: SearchContext | None,
        *,
        city_limit: bool,
    ) -> CandidatePoint | None:
        if nearby_point is not None:
            params = {
                "key": self._api_key,
                "location": f"{nearby_point.longitude},{nearby_point.latitude}",
                "keywords": keyword,
                "radius": "5000",
                "sortrule": "distance",
                "offset": "15",
                "page": "1",
                "extensions": "base",
                "output": "JSON",
            }
            url = self.place_around_url
            action_name = f"高德周边 POI 搜索：{keyword}"
        else:
            params = {
                "key": self._api_key,
                "keywords": keyword,
                "offset": "15",
                "page": "1",
                "extensions": "base",
                "output": "JSON",
            }
            url = self.place_text_url
            action_name = f"高德关键词 POI 搜索：{keyword}"
            if search_context is not None:
                city_hint = search_context.adcode or search_context.city
                if city_hint:
                    params["city"] = city_hint
                    if city_limit:
                        params["citylimit"] = "true"

        try:
            payload = self._request_json(url, params=params, action_name=action_name)
        except NavigationError:
            return None

        return self._pick_best_candidate(keyword, payload.get("pois", []), nearby_point, formatter=self._format_poi_address)

    def _pick_best_candidate(
        self,
        keyword: str,
        items: list,
        nearby_point: GeoPoint | None,
        *,
        formatter,
    ) -> CandidatePoint | None:
        best: AMapRouteProvider.CandidatePoint | None = None
        for item in items:
            location = str(item.get("location", "") or "").strip()
            point = self._parse_location_text(location)
            if point is None:
                continue
            resolved_address = formatter(item)
            score = self._score_amap_candidate(keyword, resolved_address, point, nearby_point)
            candidate = self.CandidatePoint(
                longitude=point.longitude,
                latitude=point.latitude,
                resolved_address=resolved_address,
                score=score,
            )
            if best is None or candidate.score > best.score:
                best = candidate
        return best

    def _score_amap_candidate(
        self,
        keyword: str,
        resolved_address: str,
        point: GeoPoint,
        nearby_point: GeoPoint | None,
    ) -> int:
        score = 0
        normalized_keyword = self._normalize_search_text(keyword)
        normalized_address = self._normalize_search_text(resolved_address)

        if normalized_keyword and normalized_address == normalized_keyword:
            score += 1200
        elif normalized_keyword and normalized_keyword in normalized_address:
            score += 800

        if self._is_strict_exact_match(keyword, resolved_address):
            score += 900
        elif self._is_high_confidence_match(keyword, resolved_address):
            score += 450

        keyword_tokens = self._split_meaningful_tokens(keyword)
        address_tokens = self._split_meaningful_tokens(resolved_address)
        score += len(keyword_tokens & address_tokens) * 120

        if nearby_point is not None:
            distance_m = self._distance_between_points(nearby_point, point)
            if distance_m <= 500:
                score += 60
            elif distance_m <= 3000:
                score += 30
            elif distance_m >= 50000:
                score -= 80

        return score

    def _is_strict_exact_match(self, keyword: str, resolved_address: str) -> bool:
        normalized_keyword = self._normalize_search_text(keyword)
        normalized_address = self._normalize_search_text(resolved_address)
        if not normalized_keyword:
            return False
        return normalized_keyword == normalized_address or normalized_address.endswith(normalized_keyword)

    def _is_high_confidence_match(self, keyword: str, resolved_address: str) -> bool:
        normalized_keyword = self._normalize_search_text(keyword)
        normalized_address = self._normalize_search_text(resolved_address)
        if not normalized_keyword or not normalized_address:
            return False
        if normalized_keyword == normalized_address or normalized_keyword in normalized_address:
            return True
        keyword_tokens = self._split_meaningful_tokens(keyword)
        address_tokens = self._split_meaningful_tokens(resolved_address)
        return bool(keyword_tokens) and keyword_tokens.issubset(address_tokens)

    def _split_meaningful_tokens(self, text: str) -> set[str]:
        raw = str(text or "").strip()
        parts = [part for part in re.split(r"[\s,，、\-_()/（）]+", raw) if part]
        cjk_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", raw)
        tokens = {self._normalize_search_text(part) for part in parts + cjk_chunks}
        return {token for token in tokens if token}

    def _normalize_search_text(self, text: str) -> str:
        lowered = str(text or "").strip().lower()
        return re.sub(r"[\s\-_(),，。·/（）]+", "", lowered)

    def _query_conflicts_with_context(self, query: str, search_context: SearchContext | None) -> bool:
        if search_context is None:
            return False
        region_tokens = self._extract_region_tokens(query)
        if not region_tokens:
            return False
        context_text = f"{search_context.city}{search_context.district}{search_context.formatted_address}"
        normalized_context = self._normalize_search_text(context_text)
        return any(token not in normalized_context for token in region_tokens)

    def _extract_region_tokens(self, text: str) -> list[str]:
        raw = str(text or "")
        matches = re.findall(r"[\u4e00-\u9fff]{2,}(?:省|市|区|县|旗|镇|乡)", raw)
        return [self._normalize_search_text(match) for match in matches if match]

    def _origin_context_from_text(self, origin_text: str) -> tuple[GeoPoint | None, SearchContext | None]:
        raw = str(origin_text or "").strip()
        if "," not in raw:
            return None, None
        parts = [item.strip() for item in raw.split(",", 1)]
        try:
            latitude = float(parts[0])
            longitude = float(parts[1])
        except ValueError:
            return None, None
        point = GeoPoint(latitude=latitude, longitude=longitude)
        try:
            return point, self._reverse_geocode_context(point)
        except NavigationError:
            return point, None

    def _coordinate_candidate(self, raw: str) -> LocationCandidate | None:
        if "," not in raw:
            return None
        parts = [item.strip() for item in raw.split(",", 1)]
        try:
            latitude = float(parts[0])
            longitude = float(parts[1])
        except ValueError:
            return None
        return LocationCandidate(
            display_name=raw,
            route_value=f"{latitude},{longitude}",
            provider="amap",
            score=9999,
        )

    def _distance_between_points(self, point_a: GeoPoint, point_b: GeoPoint) -> float:
        lon1, lat1 = point_a.longitude, point_a.latitude
        lon2, lat2 = point_b.longitude, point_b.latitude
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        half_chord = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
        )
        return 6371000.0 * 2 * math.atan2(math.sqrt(half_chord), math.sqrt(max(1 - half_chord, 0.0)))

    def _polyline_target(self, polyline: str) -> GeoPoint | None:
        if not polyline:
            return None
        return self._parse_location_text(polyline.split(";")[-1])

    def _parse_location_text(self, text: str) -> GeoPoint | None:
        if not text or "," not in text:
            return None
        try:
            longitude, latitude = [float(item) for item in text.split(",", 1)]
        except (TypeError, ValueError):
            return None
        return GeoPoint(latitude=latitude, longitude=longitude)

    def _build_instruction(self, step: dict) -> str:
        action = str(step.get("action") or step.get("assistant_action") or "前进").strip()
        road = str(step.get("road", "") or "").strip()
        distance = int(self._safe_float(step.get("distance")))
        if road:
            return f"{action}，沿 {road} 步行约 {distance} 米。"
        return f"{action}，步行约 {distance} 米。"

    def _safe_float(self, value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _format_resolved_address(self, item: dict) -> str:
        formatted_address = str(item.get("formatted_address", "") or "").strip()
        if formatted_address:
            return formatted_address
        name = str(item.get("name", "") or "").strip()
        address = str(item.get("address", "") or "").strip()
        district = str(item.get("district", "") or "").strip()
        return " ".join(part for part in [district, address, name] if part)

    def _format_tip_address(self, item: dict) -> str:
        name = str(item.get("name", "") or "").strip()
        district = str(item.get("district", "") or "").strip()
        adname = str(item.get("adname", "") or "").strip()
        tip_address = str(item.get("address", "") or "").strip()
        return " ".join(part for part in [district, adname, tip_address, name] if part)

    def _format_poi_address(self, item: dict) -> str:
        name = str(item.get("name", "") or "").strip()
        address = str(item.get("address", "") or "").strip()
        district = str(item.get("adname", "") or "").strip()
        return " ".join(part for part in [district, address, name] if part)
