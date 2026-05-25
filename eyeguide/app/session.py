"""
会话控制模块：管理应用的主处理流程。

该模块实现了应用的核心处理管道，包括视觉感知、语音输出、GPS 定位和路线导航的协调。
SessionController 是应用运行时的主控制器，负责启动、停止和管理整个处理会话。
"""

from __future__ import annotations

import os
import time
from queue import Queue
from threading import Event, Lock, Thread
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
    """
    应用会话控制器。

    该类管理应用从启动到停止的整个生命周期。它协调各个功能模块（视觉、语音、GPS 等）的工作，
    实现实时的感知、处理和反馈循环。处理流程运行在独立的后台线程中，确保 UI 响应性。

    主要职责：
    - 初始化和管理各功能服务（视觉、语音、GPS、路由）
    - 启动和停止处理会话
    - 协调多个服务的交互和数据流动
    - 处理视频帧、检测事件、GPS 位置和路线导航

    属性：
        _config: 应用全局配置对象
        _event_queue: 事件队列，用于向 UI 发送异步事件
        _speech: 语音合成引擎
        _vision: 视觉场景分析器
        _renderer: 视频帧渲染器（用于调试和显示）
        _gps: GPS 定位服务
        _route_guidance: 路线导航控制器
        _stop_event: 停止信号事件
        _thread: 处理循环所在的后台线程
        _capture_lock: 摄像头对象的互斥锁
        _active_capture: 当前活跃的视频捕获对象
    """

    def __init__(self, event_queue: Queue[AppEvent], config: AppConfig | None = None) -> None:
        """
        初始化会话控制器。

        参数：
            event_queue: 事件队列，用于向 UI 发送事件
            config: 应用配置对象，如为 None 则使用默认配置
        """
        self._config = config or AppConfig()
        self._event_queue = event_queue
        self._speech = SpeechEngine(self._config.speech)
        self._vision = SceneAnalyzer(self._config.vision)
        self._renderer = UnicodeFrameRenderer()
        self._gps = GpsService(event_queue, self._config.gps)
        self._route_guidance = RouteGuidance(self._config.navigation, self._speech, self._emit)
        self._stop_event = Event()
        self._thread: Optional[Thread] = None
        self._capture_lock = Lock()
        self._active_capture: Optional[cv2.VideoCapture] = None

    @property
    def is_running(self) -> bool:
        """
        检查会话是否正在运行。

        返回值：
            bool: 如果处理循环线程正在运行，返回 True；否则返回 False
        """
        return self._thread is not None and self._thread.is_alive()

    @property
    def gps_service(self) -> GpsService:
        """
        获取 GPS 服务实例。

        返回值：
            GpsService: GPS 定位服务对象
        """
        return self._gps

    def start(
        self,
        mode: Mode,
        source: Union[int, str],
        route_plan: Optional[RoutePlan] = None,
    ) -> None:
        """
        启动处理会话。

        该方法启动处理循环，使应用开始从指定的视频源进行实时视觉处理。
        如果已有会话正在运行，会抛出异常。

        参数：
            mode: 应用工作模式（EXPLORE、NAVIGATION 或 SIMULATION）
            source: 视频源，可以是摄像头索引（整数）或视频文件路径（字符串）
            route_plan: 路线计划对象，仅在导航模式下使用，为 None 表示不使用路线

        异常：
            RuntimeError: 如果已有处理会话正在运行
        """
        if self.is_running:
            raise RuntimeError("已有处理会话正在运行。")

        self._speech.cancel_all()
        self._stop_event.clear()
        self._vision = SceneAnalyzer(self._config.vision)
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
        """
        停止处理会话。

        该方法请求停止处理循环、取消待播放的语音、释放摄像头资源，并等待后台线程退出。
        如果线程未能在规定时间内退出，会记录警告信息但不会强制杀死线程。
        """
        self._stop_event.set()
        self._speech.cancel_all()
        self._release_active_capture()

        thread = self._thread
        if thread is not None:
            thread.join(timeout=3.0)
            if thread.is_alive():
                self._emit("status", "会话正在停止，请稍后再试。")
                self._emit("log", "会话线程仍在退出中，已请求停止摄像头与语音。")
                return

        self._thread = None
        self._route_guidance.set_route(None)
        self._emit("route", "暂无导航指令")
        cv2.destroyAllWindows()
        self._emit("status", "处理会话已停止。")

    def shutdown(self) -> None:
        """
        完全关闭应用。

        该方法会停止处理会话、关闭 GPS 服务和语音引擎，进行完整的资源清理。
        """
        self.stop()
        self._gps.stop()
        self._speech.stop()

    def next_route_step(self) -> None:
        """
        手动推进到下一条路线。

        该方法用于 UI 快捷键触发的手动导航推进。
        """
        self._route_guidance.next_step()

    def repeat_route_step(self) -> None:
        """
        重复播放当前路线步骤。

        该方法用于 UI 快捷键触发的导航重复播放。
        """
        self._route_guidance.repeat_step()

    def preview_speech(self, text: str) -> None:
        """
        播放预览语音（用于测试）。

        参数：
            text: 要播放的文本
        """
        self._speech.speak(text, priority=1, dedupe_key="ui:test-speech", cooldown_seconds=0.0)

    def _run_loop(self, mode: Mode, source: Union[int, str]) -> None:
        """
        主处理循环（运行在后台线程中）。

        该方法实现了应用的主要业务逻辑，包括：
        1. 打开视频源（摄像头或视频文件）
        2. 循环读取视频帧
        3. 对每一帧进行视觉分析
        4. 根据分析结果生成语音提示和视觉反馈
        5. 监听 GPS 位置并同步导航进度
        6. 处理用户输入（快捷键）
        7. 妥善清理资源

        参数：
            mode: 应用工作模式
            source: 视频源
        """
        capture_source = self._config.vision.default_camera_index if source == 0 else source
        cap = self._open_capture(capture_source)
        if cap is None:
            self._emit("error", "无法打开摄像头或视频源。")
            self._stop_event.set()
            return
        self._set_active_capture(cap)

        self._speech.speak("视觉感知已启动，按 Q 键退出。", priority=4, cooldown_seconds=1.0)
        self._emit("log", f"{mode.value}处理循环已开始。快捷键：Q 退出，N 下一条导航，R 重复导航。")

        last_status_emit = 0.0
        try:
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    # 尝试重新打开视频源
                    reopened = self._open_capture(capture_source)
                    if reopened is not None:
                        cap.release()
                        cap = reopened
                        self._set_active_capture(cap)
                        self._emit("log", "Camera stream dropped; reconnecting.")
                        continue
                    self._emit("log", "视频流结束或读取失败。")
                    break

                # 分析当前帧
                analysis = self._vision.analyze(frame)
                self._renderer.draw_panel(frame, analysis.overlays)

                # 同步路线导航与 GPS 位置
                self._route_guidance.sync_with_location(self._gps.latest_fix)
                route_line = self._route_guidance.current_route_text()
                if route_line:
                    self._renderer.draw_bottom_banner(frame, route_line)

                # 处理视觉检测事件
                if analysis.events:
                    event = analysis.events[0]
                    is_blind_road_event = event.channel == "blind_road"
                    self._speech.speak(
                        event.message,
                        priority=event.priority,
                        dedupe_key=event.dedupe_key or event.category,
                        cooldown_seconds=event.cooldown_seconds,
                        channel=event.channel,
                        persistent_dedupe=event.persistent_dedupe,
                        replace_pending=event.channel in {"vision", "blind_road"},
                        interrupt=True
                        if is_blind_road_event
                        else (False if event.channel == "vision" else event.priority <= 1),
                    )

                # 定期发送状态更新
                if time.time() - last_status_emit > self._config.vision.status_emit_interval_seconds:
                    self._emit("hazard", analysis.hazard_summary or "环境相对安全")
                    last_status_emit = time.time()

                # 显示视频窗口并处理快捷键
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
            # 清理资源
            cap.release()
            self._clear_active_capture(cap)
            cv2.destroyAllWindows()
            self._stop_event.set()
            self._speech.cancel_all()
            self._emit("status", "会话结束。")

    def _open_capture(self, source: Union[int, str]) -> Optional[cv2.VideoCapture]:
        """
        打开视频捕获对象。

        该方法会尝试打开视频源。对于摄像头（整数索引），会尝试多个后端以确保兼容性；
        对于视频文件路径，直接打开。

        参数：
            source: 视频源（摄像头索引或文件路径）

        返回值：
            cv2.VideoCapture | None: 成功打开的视频捕获对象；如果打开失败则返回 None
        """
        if isinstance(source, int):
            # 对于摄像头，在 Windows 上优先尝试特定的后端
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

        # 对于文件路径，直接打开
        cap = cv2.VideoCapture(source)
        if cap.isOpened():
            return cap
        cap.release()
        return None

    def _emit(self, kind: str, value: str) -> None:
        """
        向事件队列发送一个事件。

        参数：
            kind: 事件类型
            value: 事件值
        """
        self._event_queue.put(AppEvent(kind=kind, value=value))

    def _set_active_capture(self, cap: cv2.VideoCapture) -> None:
        """
        设置当前活跃的视频捕获对象（线程安全）。

        参数：
            cap: 视频捕获对象
        """
        with self._capture_lock:
            self._active_capture = cap

    def _clear_active_capture(self, cap: cv2.VideoCapture) -> None:
        """
        清除活跃的视频捕获对象（线程安全）。

        参数：
            cap: 要清除的视频捕获对象
        """
        with self._capture_lock:
            if self._active_capture is cap:
                self._active_capture = None

    def _release_active_capture(self) -> None:
        """
        释放当前活跃的视频捕获对象的资源（线程安全）。
        """
        with self._capture_lock:
            cap = self._active_capture
            self._active_capture = None
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
