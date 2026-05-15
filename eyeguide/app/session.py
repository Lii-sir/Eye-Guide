from __future__ import annotations

from queue import Queue
from threading import Event, Lock, Thread
from typing import Optional, Union
import time
import math

import cv2

from eyeguide.core.config import AppConfig
from eyeguide.core.events import AppEvent
from eyeguide.domain.models import GeoPoint, GpsFix, Mode, RoutePlan
from eyeguide.services.location import GpsService
from eyeguide.services.speech import SpeechEngine
from eyeguide.services.vision import SceneAnalyzer


class SessionController:
    def __init__(self, event_queue: Queue[AppEvent], config: AppConfig | None = None) -> None:
        self._config = config or AppConfig()
        self._event_queue = event_queue
        self._speech = SpeechEngine(self._config.speech)
        self._vision = SceneAnalyzer()
        self._gps = GpsService(event_queue, self._config.gps)
        self._stop_event = Event()
        self._thread: Optional[Thread] = None
        self._route_lock = Lock()
        self._route_plan: Optional[RoutePlan] = None
        self._route_index = 0
        self._last_auto_advance_at = 0.0
        self._last_route_fix: Optional[GpsFix] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        mode: Mode,
        source: Union[int, str],
        route_plan: Optional[RoutePlan] = None,
    ) -> None:
        if self.is_running:
            raise RuntimeError("已有处理会话正在运行。")

        self._stop_event.clear()
        self._route_plan = route_plan
        self._route_index = 0
        self._last_auto_advance_at = 0.0
        self._last_route_fix = None
        self._thread = Thread(target=self._run_loop, args=(mode, source), daemon=True)
        self._thread.start()

        self._emit("status", f"{mode.value}已启动，检测后端：{self._vision.backend_name}")
        if route_plan is not None:
            overview = (
                f"已生成路线，总距离约 {int(route_plan.distance_meters)} 米，"
                f"预计 {int(route_plan.duration_seconds // 60) or 1} 分钟。"
            )
            self._emit("log", overview)
            self._emit("log", "当前为步行导航模式，提示会根据实时位置自动推进。")
            self._announce_route_step(repeat=False)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        cv2.destroyAllWindows()
        self._emit("status", "处理会话已停止。")

    def shutdown(self) -> None:
        self.stop()
        self._gps.stop()
        self._speech.stop()

    @property
    def gps_service(self) -> GpsService:
        return self._gps

    def next_route_step(self) -> None:
        with self._route_lock:
            if self._route_plan is None:
                return
            if self._route_index < len(self._route_plan.steps) - 1:
                self._route_index += 1
        self._announce_route_step(repeat=False)

    def repeat_route_step(self) -> None:
        self._announce_route_step(repeat=True)

    def _announce_route_step(self, repeat: bool) -> None:
        with self._route_lock:
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
        )

    def _run_loop(self, mode: Mode, source: Union[int, str]) -> None:
        capture_source = self._config.vision.default_camera_index if source == 0 else source
        cap = cv2.VideoCapture(capture_source)
        if not cap.isOpened():
            self._emit("error", "无法打开摄像头或视频源。")
            self._stop_event.set()
            return

        self._speech.speak("视觉感知已启动，按 Q 键退出。", priority=4, cooldown_seconds=1.0)
        self._emit(
            "log",
            f"{mode.value}处理循环已开始。快捷键：Q 退出，N 下一条导航，R 重复导航。",
        )

        last_status_emit = 0.0
        try:
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    self._emit("log", "视频流结束或读取失败。")
                    break

                analysis = self._vision.analyze(frame)
                self._draw_overlays(frame, analysis.overlays)

                self._sync_route_with_location()
                route_line = self._current_route_text()
                if route_line:
                    self._draw_route_banner(frame, route_line)

                for event in analysis.events:
                    self._speech.speak(
                        event.message,
                        priority=event.priority,
                        dedupe_key=event.category,
                        cooldown_seconds=event.cooldown_seconds,
                    )

                if time.time() - last_status_emit > self._config.vision.status_emit_interval_seconds:
                    self._emit("hazard", analysis.hazard_summary or "环境相对安全")
                    last_status_emit = time.time()

                cv2.imshow(self._config.vision.window_name, frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    self._emit("log", "收到退出指令。")
                    break
                if key == ord("n"):
                    self.next_route_step()
                if key == ord("r"):
                    self.repeat_route_step()
        finally:
            cap.release()
            cv2.destroyAllWindows()
            self._stop_event.set()
            self._emit("status", "会话结束。")

    def _current_route_text(self) -> Optional[str]:
        with self._route_lock:
            if self._route_plan is None or not self._route_plan.steps:
                return None
            step = self._route_plan.steps[self._route_index]
            return f"导航 {self._route_index + 1}/{len(self._route_plan.steps)}: {step.instruction}"

    def _sync_route_with_location(self) -> None:
        with self._route_lock:
            if self._route_plan is None or self._route_index >= len(self._route_plan.steps):
                return
            current_step = self._route_plan.steps[self._route_index]

        fix = self._gps.latest_fix
        if fix is None:
            return
        if fix.accuracy_meters is not None and fix.accuracy_meters > self._config.navigation.poor_accuracy_threshold_meters:
            self._emit("gps_status", f"当前位置精度偏低（约 {int(fix.accuracy_meters)} 米），暂不自动推进")
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
        if now - self._last_auto_advance_at < self._config.navigation.auto_advance_cooldown_seconds:
            return

        self._last_auto_advance_at = now
        self._advance_route_due_to_position(distance)

    def _advance_route_due_to_position(self, distance: float) -> None:
        with self._route_lock:
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
        self._speech.speak(text, priority=3, dedupe_key=f"auto-route:{index}", cooldown_seconds=2.0)

    def _distance_meters(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _draw_overlays(self, frame, overlays: list[str]) -> None:
        for idx, line in enumerate(overlays):
            cv2.putText(
                frame,
                line,
                (20, 35 + idx * 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
            )

    def _draw_route_banner(self, frame, text: str) -> None:
        height, width = frame.shape[:2]
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, height - 78), (width, height), (0, 0, 0), -1)
        frame[:] = cv2.addWeighted(overlay, 0.45, frame, 0.55, 0)
        cv2.putText(
            frame,
            text[:72],
            (20, height - 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.66,
            (255, 255, 255),
            2,
        )

    def _emit(self, kind: str, value: str) -> None:
        self._event_queue.put(AppEvent(kind=kind, value=value))
