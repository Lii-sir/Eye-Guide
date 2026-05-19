from __future__ import annotations

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


class AMapRouteProvider:
    geocode_url = "https://restapi.amap.com/v3/geocode/geo"
    regeo_url = "https://restapi.amap.com/v3/geocode/regeo"
    foot_url = "https://restapi.amap.com/v3/direction/walking"
    input_tips_url = "https://restapi.amap.com/v3/assistant/inputtips"
    place_text_url = "https://restapi.amap.com/v3/place/text"
    place_around_url = "https://restapi.amap.com/v3/place/around"

    class SearchContext:
        def __init__(self, city: str = "", district: str = "", adcode: str = "", formatted_address: str = "") -> None:
            self.city = city
            self.district = district
            self.adcode = adcode
            self.formatted_address = formatted_address

    class CandidatePoint:
        def __init__(self, longitude: float, latitude: float, resolved_address: str, score: int) -> None:
            self.longitude = longitude
            self.latitude = latitude
            self.resolved_address = resolved_address
            self.score = score

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = (api_key or os.getenv("AMAP_WEB_API_KEY") or os.getenv("AMAP_KEY") or "").strip()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "EyeGuidePrototype/0.1 (desktop foot navigation)"})
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
        steps: list[RouteStep] = []
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
            return CandidateSearchResult(candidates=[coordinate_candidate], debug_lines=debug_lines, provider_used="amap")

        nearby_point, search_context = self._origin_context_from_text(nearby_origin or "")
        debug_lines.append(
            "[候选搜索][高德] origin_context="
            f"city={getattr(search_context, 'city', '') or '<none>'}, "
            f"district={getattr(search_context, 'district', '') or '<none>'}, "
            f"adcode={getattr(search_context, 'adcode', '') or '<none>'}"
        )

        candidates: list[LocationCandidate] = []

        def append_candidate(source: str, display_name: str, route_value: str, score: int) -> None:
            candidates.append(
                LocationCandidate(
                    display_name=display_name,
                    route_value=route_value,
                    provider="amap",
                    score=score,
                )
            )
            debug_lines.append(
                f"[候选搜索][高德][{source}] 命中：score={score} route_value={route_value} name={debug_text(display_name)}"
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
                    f"[候选搜索][高德] 去重：score={candidate.score} route_value={candidate.route_value} "
                    f"name={debug_text(candidate.display_name)}"
                )
                continue
            seen.add(key)
            if len(results) >= limit:
                debug_lines.append(
                    f"[候选搜索][高德] 丢弃：超过候选上限 {limit} score={candidate.score} "
                    f"route_value={candidate.route_value} name={debug_text(candidate.display_name)}"
                )
                continue
            results.append(candidate)
        debug_lines.append(f"[候选搜索][高德] 原始候选数={len(candidates)} 去重后候选数={len(results)}")
        for index, candidate in enumerate(results, start=1):
            debug_lines.append(
                f"[候选搜索][高德][候选 {index}] score={candidate.score} route_value={candidate.route_value} "
                f"name={debug_text(candidate.display_name)}"
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
        parts = [part for part in re.split(r"[\s,，。《》()/（）]+", raw) if part]
        cjk_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", raw)
        tokens = {self._normalize_search_text(part) for part in parts + cjk_chunks}
        return {token for token in tokens if token}

    def _normalize_search_text(self, text: str) -> str:
        lowered = str(text or "").strip().lower()
        return re.sub(r"[\s\-_(),，。、《》（）]+", "", lowered)

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
        matches = re.findall(r"[\u4e00-\u9fff]{2,}(?:省|市|区|县|州|盟|镇|乡|街道)?", raw)
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
        return LocationCandidate(display_name=raw, route_value=f"{latitude},{longitude}", provider="amap", score=9999)

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
