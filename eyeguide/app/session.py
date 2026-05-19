from __future__ import annotations

import os
import time
from queue import Queue
from threading import Event, Thread
from typing import Optional, Union

import cv2

from eyeguide.app.route_guidance import RouteGuidance
from eyeguide.core.config import AppConfig
from eyeguide.core.events import AppEvent
from eyeguide.domain.models import Mode, RoutePlan
from eyeguide.services.location import GpsService
from eyeguide.services.speech import SpeechEngine
from eyeguide.services.vision import SceneAnalyzer
from eyeguide.services.vision.rendering import UnicodeFrameRenderer


class SessionController:
    def __init__(self, event_queue: Queue[AppEvent], config: AppConfig | None = None) -> None:
        self._config = config or AppConfig()
        self._event_queue = event_queue
        self._speech = SpeechEngine(self._config.speech)
        self._vision = SceneAnalyzer()
        self._renderer = UnicodeFrameRenderer()
        self._gps = GpsService(event_queue, self._config.gps)
        self._route_guidance = RouteGuidance(self._config.navigation, self._speech, self._emit)
        self._stop_event = Event()
        self._thread: Optional[Thread] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def gps_service(self) -> GpsService:
        return self._gps

    def start(
        self,
        mode: Mode,
        source: Union[int, str],
        route_plan: Optional[RoutePlan] = None,
    ) -> None:
        if self.is_running:
            raise RuntimeError("已有处理会话正在运行。")

        self._speech.cancel_all()
        self._stop_event.clear()
        self._vision = SceneAnalyzer()
        self._route_guidance.set_route(route_plan)
        self._thread = Thread(target=self._run_loop, args=(mode, source), daemon=True)
        self._thread.start()

        if "YOLO" not in self._vision.backend_name:
            self._emit("log", f"YOLO fallback reason: {self._vision.backend_detail}")

        self._emit("status", f"{mode.value}已启动，检测后端：{self._vision.backend_name}")
        if route_plan is not None:
            overview = (
                f"已生成路线，总距离约 {int(route_plan.distance_meters)} 米，"
                f"预计 {int(route_plan.duration_seconds // 60) or 1} 分钟。"
            )
            self._emit("log", overview)
            self._emit("log", "当前为步行导航模式，提示会根据实时位置自动推进。")
            self._route_guidance.announce_current()

    def stop(self) -> None:
        self._stop_event.set()
        self._speech.cancel_all()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        cv2.destroyAllWindows()
        self._emit("status", "处理会话已停止。")

    def shutdown(self) -> None:
        self.stop()
        self._gps.stop()
        self._speech.stop()

    def next_route_step(self) -> None:
        self._route_guidance.next_step()

    def repeat_route_step(self) -> None:
        self._route_guidance.repeat_step()

    def preview_speech(self, text: str) -> None:
        self._speech.speak(text, priority=1, dedupe_key="ui:test-speech", cooldown_seconds=0.0)

    def _run_loop(self, mode: Mode, source: Union[int, str]) -> None:
        capture_source = self._config.vision.default_camera_index if source == 0 else source
        cap = self._open_capture(capture_source)
        if cap is None:
            self._emit("error", "无法打开摄像头或视频源。")
            self._stop_event.set()
            return

        self._speech.speak("视觉感知已启动，按 Q 键退出。", priority=4, cooldown_seconds=1.0)
        self._emit("log", f"{mode.value}处理循环已开始。快捷键：Q 退出，N 下一条导航，R 重复导航。")

        last_status_emit = 0.0
        try:
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    reopened = self._open_capture(capture_source)
                    if reopened is not None:
                        cap.release()
                        cap = reopened
                        self._emit("log", "Camera stream dropped; reconnecting.")
                        continue
                    self._emit("log", "视频流结束或读取失败。")
                    break

                analysis = self._vision.analyze(frame)
                self._renderer.draw_panel(frame, analysis.overlays)

                self._route_guidance.sync_with_location(self._gps.latest_fix)
                route_line = self._route_guidance.current_route_text()
                if route_line:
                    self._renderer.draw_bottom_banner(frame, route_line)

                if analysis.events:
                    event = analysis.events[0]
                    # if event.channel == "vision":
                    #     self._emit("log", f"TTS视觉入队：{event.message}")
                    enqueued = self._speech.speak(
                        event.message,
                        priority=event.priority,
                        dedupe_key=event.dedupe_key or event.category,
                        cooldown_seconds=event.cooldown_seconds,
                        channel=event.channel,
                        replace_pending=event.channel == "vision",
                        interrupt=False if event.channel == "vision" else event.priority <= 1,
                    )
                    # if not enqueued and event.channel == "vision":
                    #     self._emit("log", f"TTS视觉跳过：{event.message}")

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
            self._speech.cancel_all()
            self._emit("status", "会话结束。")

    def _open_capture(self, source: Union[int, str]) -> Optional[cv2.VideoCapture]:
        if isinstance(source, int):
            backend_candidates = []
            if os.name == "nt":
                backend_candidates.extend(
                    [
                        getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY),
                        getattr(cv2, "CAP_MSMF", cv2.CAP_ANY),
                    ]
                )
            backend_candidates.append(cv2.CAP_ANY)
            for backend in backend_candidates:
                cap = cv2.VideoCapture(source, backend)
                if cap.isOpened():
                    return cap
                cap.release()
            return None

        cap = cv2.VideoCapture(source)
        if cap.isOpened():
            return cap
        cap.release()
        return None

    def _emit(self, kind: str, value: str) -> None:
        self._event_queue.put(AppEvent(kind=kind, value=value))
