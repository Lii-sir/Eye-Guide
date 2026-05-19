from __future__ import annotations

import html
import math
import os
import re

import requests

from eyeguide.domain.models import GeoPoint, RoutePlan, RouteStep
from eyeguide.services.navigation.models import (
    CandidateSearchResult,
    LocationCandidate,
    NavigationError,
    debug_text,
)


class OSRMRouteProvider:
    search_url = "https://nominatim.openstreetmap.org/search"
    route_url = "https://router.project-osrm.org/route/v1/foot"
    amap_geocode_url = "https://restapi.amap.com/v3/geocode/geo"

    def __init__(self, amap_api_key: str | None = None) -> None:
        self._amap_api_key = (amap_api_key or os.getenv("AMAP_WEB_API_KEY") or os.getenv("AMAP_KEY") or "").strip()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "EyeGuidePrototype/0.1 (desktop navigation demo)"})

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

        steps: list[RouteStep] = []
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
            debug_lines.append(f"[候选搜索][OSM] 高德参考坐标={reference_point[1]:.6f},{reference_point[0]:.6f}")

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
                    f"[候选搜索][OSM][raw {raw_index}/rank {rank}] 跳过：坐标无效 name={debug_text(display_name)}"
                )
                continue
            key = f"{latitude:.6f},{longitude:.6f}"
            if key in seen:
                debug_lines.append(
                    f"[候选搜索][OSM][raw {raw_index}/rank {rank}] 去重：coord={key} score={score} name={debug_text(display_name)}"
                )
                continue
            if len(results) >= limit:
                debug_lines.append(
                    f"[候选搜索][OSM][raw {raw_index}/rank {rank}] 丢弃：超过候选上限 {limit} "
                    f"score={score} coord={key} name={debug_text(display_name)}"
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
                f"[候选搜索][OSM][raw {raw_index}/rank {rank}] 采纳：score={score} coord={key} "
                f"class={item.get('class', '')}/{item.get('type', '')} name={debug_text(display_name)}"
            )

        debug_lines.append(f"[候选搜索][OSM] 最终候选数={len(results)}")
        for index, candidate in enumerate(results, start=1):
            debug_lines.append(
                f"[候选搜索][OSM][候选 {index}] score={candidate.score} route_value={candidate.route_value} "
                f"name={debug_text(candidate.display_name)}"
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
        best = max(payload, key=lambda item: self._score_geocode_candidate(query, item, reference_point))
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
        return LocationCandidate(display_name=raw, route_value=f"{latitude},{longitude}", provider="osrm", score=9999)

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

        try:
            score -= abs(int(item.get("place_rank")) - 30)
        except (TypeError, ValueError):
            pass

        try:
            score += int(float(item.get("importance")) * 20)
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
        return re.sub(r"[\s\-_(),，。、《》（）]+", "", lowered)

    def _haversine_distance_meters(self, point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
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

    def _build_instruction(self, maneuver_type: str, modifier: str, road_name: str, distance: float) -> str:
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
