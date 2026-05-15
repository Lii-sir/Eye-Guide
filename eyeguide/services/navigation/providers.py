from __future__ import annotations

from typing import List, Protocol
import html

import requests

from eyeguide.domain.models import GeoPoint, RoutePlan, RouteStep


class NavigationError(Exception):
    """Raised when route generation fails."""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class RouteProvider(Protocol):
    def build_route(self, origin: str, destination: str) -> RoutePlan:
        ...


class DemoRouteProvider:
    def build_route(self, origin: str, destination: str) -> RoutePlan:
        steps = [
            RouteStep("请从当前位置直行约 80 米。", 80, target=GeoPoint(0.0, 0.0)),
            RouteStep("前方路口右转，注意观察来往车辆。", 30, target=GeoPoint(0.0, 0.0)),
            RouteStep("继续沿人行道直行，留意路边障碍物。", 120, target=GeoPoint(0.0, 0.0)),
            RouteStep("目的地就在前方附近，请减速确认入口。", 20, target=GeoPoint(0.0, 0.0)),
        ]
        return RoutePlan(
            origin=origin or "当前位置",
            destination=destination,
            steps=steps,
            source="demo",
            travel_mode="walking",
            distance_meters=250,
            duration_seconds=240,
        )


class OSRMRouteProvider:
    search_url = "https://nominatim.openstreetmap.org/search"
    route_url = "https://router.project-osrm.org/route/v1/walking"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": "EyeGuidePrototype/0.1 (desktop navigation demo)"}
        )

    def build_route(self, origin: str, destination: str) -> RoutePlan:
        if not origin.strip() or not destination.strip():
            raise NavigationError("路线导航模式需要填写起点和终点。")

        origin_lon, origin_lat = self._resolve_origin(origin)
        destination_lon, destination_lat = self._geocode(destination)
        try:
            response = self._session.get(
                f"{self.route_url}/{origin_lon},{origin_lat};{destination_lon},{destination_lat}",
                params={"overview": "false", "steps": "true", "alternatives": "false"},
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.Timeout as exc:
            raise NavigationError("路线服务请求超时。", detail=str(exc)) from exc
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "未知"
            response_text = ""
            if exc.response is not None:
                response_text = exc.response.text[:200].strip()
            raise NavigationError(
                f"路线服务返回错误状态：{status_code}。",
                detail=response_text or str(exc),
            ) from exc
        except requests.RequestException as exc:
            raise NavigationError("路线服务网络请求失败。", detail=str(exc)) from exc
        except ValueError as exc:
            raise NavigationError("路线服务返回了无法解析的数据。", detail=str(exc)) from exc

        routes = payload.get("routes", [])
        if not routes:
            raise NavigationError("没有获取到可用步行路线。")

        route = routes[0]
        legs = route.get("legs", [])
        if not legs:
            raise NavigationError("路线结果缺少分段信息。")

        steps: List[RouteStep] = []
        for step in legs[0].get("steps", []):
            maneuver = step.get("maneuver", {})
            name = step.get("name", "").strip()
            instruction = self._build_instruction(
                maneuver_type=maneuver.get("type", ""),
                modifier=maneuver.get("modifier", ""),
                road_name=name,
                distance=float(step.get("distance", 0.0)),
            )
            steps.append(
                RouteStep(
                    instruction=instruction,
                    distance_meters=float(step.get("distance", 0.0)),
                    road_name=name,
                    target=self._step_target(maneuver.get("location")),
                    arrival_radius_meters=25.0,
                )
            )

        if not steps:
            raise NavigationError("路线步骤为空。")

        return RoutePlan(
            origin=origin,
            destination=destination,
            steps=steps,
            source="osrm",
            travel_mode="walking",
            distance_meters=float(route.get("distance", 0.0)),
            duration_seconds=float(route.get("duration", 0.0)),
        )

    def _resolve_origin(self, origin: str) -> tuple[float, float]:
        raw = origin.strip()
        if "," in raw:
            parts = [item.strip() for item in raw.split(",", 1)]
            try:
                latitude = float(parts[0])
                longitude = float(parts[1])
                return longitude, latitude
            except ValueError:
                pass
        return self._geocode(origin)

    def _geocode(self, query: str) -> tuple[float, float]:
        try:
            response = self._session.get(
                self.search_url,
                params={"q": query, "format": "jsonv2", "limit": 1},
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.Timeout as exc:
            raise NavigationError(f"地点解析超时：{query}", detail=str(exc)) from exc
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "未知"
            response_text = ""
            if exc.response is not None:
                response_text = exc.response.text[:200].strip()
            raise NavigationError(
                f"地点解析服务返回错误状态：{status_code}",
                detail=f"query={query}; response={response_text or exc}",
            ) from exc
        except requests.RequestException as exc:
            raise NavigationError(f"地点解析网络请求失败：{query}", detail=str(exc)) from exc
        except ValueError as exc:
            raise NavigationError(f"地点解析结果无法解析：{query}", detail=str(exc)) from exc

        if not payload:
            raise NavigationError(f"找不到地点：{query}")

        first = payload[0]
        return float(first["lon"]), float(first["lat"])

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
