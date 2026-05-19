from __future__ import annotations

import math
import time
from threading import Lock
from typing import Callable, Optional

from eyeguide.core.config import NavigationConfig
from eyeguide.domain.models import GpsFix, RoutePlan
from eyeguide.services.speech import SpeechEngine


class RouteGuidance:
    def __init__(
        self,
        config: NavigationConfig,
        speech: SpeechEngine,
        emit: Callable[[str, str], None],
    ) -> None:
        self._config = config
        self._speech = speech
        self._emit = emit
        self._lock = Lock()
        self._route_plan: Optional[RoutePlan] = None
        self._route_index = 0
        self._last_auto_advance_at = 0.0
        self._last_route_fix: Optional[GpsFix] = None

    def set_route(self, route_plan: Optional[RoutePlan]) -> None:
        with self._lock:
            self._route_plan = route_plan
            self._route_index = 0
            self._last_auto_advance_at = 0.0
            self._last_route_fix = None

    def has_route(self) -> bool:
        with self._lock:
            return self._route_plan is not None

    def current_route_text(self) -> Optional[str]:
        with self._lock:
            if self._route_plan is None or not self._route_plan.steps:
                return None
            step = self._route_plan.steps[self._route_index]
            return f"导航 {self._route_index + 1}/{len(self._route_plan.steps)}: {step.instruction}"

    def next_step(self) -> None:
        with self._lock:
            if self._route_plan is None:
                return
            if self._route_index < len(self._route_plan.steps) - 1:
                self._route_index += 1
        self._announce_current(repeat=False)

    def announce_current(self) -> None:
        self._announce_current(repeat=False)

    def repeat_step(self) -> None:
        self._announce_current(repeat=True)

    def sync_with_location(self, fix: Optional[GpsFix]) -> None:
        with self._lock:
            if self._route_plan is None or self._route_index >= len(self._route_plan.steps):
                return
            current_step = self._route_plan.steps[self._route_index]

        if fix is None:
            return
        if fix.accuracy_meters is not None and fix.accuracy_meters > self._config.poor_accuracy_threshold_meters:
            self._emit("gps_status", f"当前定位精度较低（约 {int(fix.accuracy_meters)} 米），暂不自动推进。")
            return

        self._last_route_fix = fix
        target = current_step.target
        if target is None:
            return

        distance = self._distance_meters(
            fix.latitude,
            fix.longitude,
            target.latitude,
            target.longitude,
        )
        if distance > current_step.arrival_radius_meters:
            return

        now = time.time()
        if now - self._last_auto_advance_at < self._config.auto_advance_cooldown_seconds:
            return

        self._last_auto_advance_at = now
        self._advance_due_to_position(distance)

    def _announce_current(self, repeat: bool) -> None:
        with self._lock:
            if self._route_plan is None or not self._route_plan.steps:
                return
            step = self._route_plan.steps[self._route_index]
            index = self._route_index + 1
            total = len(self._route_plan.steps)

        prefix = "重复播报" if repeat else "导航提示"
        text = f"{prefix}，第 {index} 条，共 {total} 条。{step.instruction}"
        self._emit("route", step.instruction)
        self._emit("log", text)
        self._speech.speak(
            text,
            priority=3,
            dedupe_key=f"route:{index}:{repeat}",
            cooldown_seconds=1.0,
            channel="navigation",
            replace_pending=True,
        )

    def _advance_due_to_position(self, distance: float) -> None:
        with self._lock:
            if self._route_plan is None:
                return
            if self._route_index >= len(self._route_plan.steps) - 1:
                self._emit("route", "已接近最后一步")
                self._speech.speak("前方已接近目的地，请留意入口。", priority=2, cooldown_seconds=5.0)
                return
            self._route_index += 1
            next_step = self._route_plan.steps[self._route_index]
            index = self._route_index + 1
            total = len(self._route_plan.steps)

        text = f"已到达当前节点，自动切换到第 {index} 条，共 {total} 条。{next_step.instruction}"
        self._emit("route", next_step.instruction)
        self._emit("log", text)
        self._speech.speak(
            text,
            priority=3,
            dedupe_key=f"auto-route:{index}",
            cooldown_seconds=2.0,
            channel="navigation",
            replace_pending=True,
        )

    def _distance_meters(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))
