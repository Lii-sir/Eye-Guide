from __future__ import annotations

from eyeguide.domain.models import GeoPoint, RoutePlan, RouteStep


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
