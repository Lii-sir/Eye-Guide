"""
路线导航管理模块：实现导航流程的控制逻辑。

该模块提供路线管理、自动推进、位置同步等核心导航功能，是导航模式下应用的业务逻辑控制中心。
通过监听 GPS 位置更新，实现自动推进到下一导航步骤的功能。
"""

from __future__ import annotations

import math
import time
from threading import Lock
from typing import Callable, Optional

from eyeguide.core.config import NavigationConfig
from eyeguide.domain.models import GpsFix, RoutePlan
from eyeguide.services.speech import SpeechEngine


class RouteGuidance:
    """
    路线导航控制类。

    该类管理路线的状态和流程，包括设置路线、推进导航步骤、根据 GPS 位置自动推进等功能。
    使用线程锁确保多线程环境下的数据一致性。

    属性：
        _config: 导航配置对象
        _speech: 语音引擎实例，用于播放导航提示
        _emit: 事件发送回调函数
        _lock: 线程锁，保护内部状态
        _route_plan: 当前的路线计划对象
        _route_index: 当前导航步骤的索引
        _last_auto_advance_at: 上次自动推进的时间戳
        _last_route_fix: 上次同步的 GPS 定位数据
    """

    def __init__(
        self,
        config: NavigationConfig,
        speech: SpeechEngine,
        emit: Callable[[str, str], None],
    ) -> None:
        """
        初始化路线导航控制器。

        参数：
            config: 导航配置对象，包含推进距离、冷却时间等参数
            speech: 语音引擎实例，用于播放导航指令
            emit: 事件发送回调函数，用于向应用发送导航相关事件
        """
        self._config = config
        self._speech = speech
        self._emit = emit
        self._lock = Lock()
        self._route_plan: Optional[RoutePlan] = None
        self._route_index = 0
        self._last_auto_advance_at = 0.0
        self._last_route_fix: Optional[GpsFix] = None

    def set_route(self, route_plan: Optional[RoutePlan]) -> None:
        """
        设置新的路线计划。

        该方法会重置导航状态，使之准备好处理新的路线。调用此方法会清除之前的导航进度。

        参数：
            route_plan: 新的路线计划对象，为 None 表示清除当前路线
        """
        with self._lock:
            self._route_plan = route_plan
            self._route_index = 0
            self._last_auto_advance_at = 0.0
            self._last_route_fix = None

    def has_route(self) -> bool:
        """
        检查是否已设置路线。

        返回值：
            bool: 如果当前有活跃的路线计划，返回 True；否则返回 False
        """
        with self._lock:
            return self._route_plan is not None

    def current_route_text(self) -> Optional[str]:
        """
        获取当前导航步骤的显示文本。

        返回值：
            str | None: 格式为 "导航 X/N: 指令内容" 的文本；
                        如果没有路线或步骤为空，返回 None
        """
        with self._lock:
            if self._route_plan is None or not self._route_plan.steps:
                return None
            step = self._route_plan.steps[self._route_index]
            return f"导航 {self._route_index + 1}/{len(self._route_plan.steps)}: {step.instruction}"

    def next_step(self) -> None:
        """
        手动推进到下一导航步骤。

        该方法会推进到下一个导航步骤并播放该步骤的导航指令。如果已是最后一步，则不进行推进。
        """
        with self._lock:
            if self._route_plan is None:
                return
            if self._route_index < len(self._route_plan.steps) - 1:
                self._route_index += 1
        self._announce_current(repeat=False)

    def announce_current(self) -> None:
        """
        重新播放当前导航步骤的指令。

        该方法会播放当前步骤的导航信息，不改变步骤索引。
        """
        self._announce_current(repeat=False)

    def repeat_step(self) -> None:
        """
        重复播放当前导航步骤的指令。

        该方法会以 "重复播报" 的前缀重新播放当前步骤的导航指令。
        """
        self._announce_current(repeat=True)

    def sync_with_location(self, fix: Optional[GpsFix]) -> None:
        """
        根据 GPS 定位数据同步导航进度。

        该方法监听 GPS 位置更新，当用户接近当前导航步骤的目标时，自动推进到下一步骤。
        会检查 GPS 精度、距离阈值和冷却时间，确保自动推进的合理性。

        参数：
            fix: GPS 定位数据，为 None 表示无效定位
        """
        with self._lock:
            if self._route_plan is None or self._route_index >= len(self._route_plan.steps):
                return
            current_step = self._route_plan.steps[self._route_index]

        if fix is None:
            return
        # 检查 GPS 精度是否良好
        if fix.accuracy_meters is not None and fix.accuracy_meters > self._config.poor_accuracy_threshold_meters:
            self._emit("gps_status", f"当前定位精度较低（约 {int(fix.accuracy_meters)} 米），暂不自动推进。")
            return

        self._last_route_fix = fix
        target = current_step.target
        if target is None:
            return

        # 计算当前位置到目标的距离
        distance = self._distance_meters(
            fix.latitude,
            fix.longitude,
            target.latitude,
            target.longitude,
        )
        # 如果未达到目标距离范围，不推进
        if distance > current_step.arrival_radius_meters:
            return

        # 检查是否在冷却期内
        now = time.time()
        if now - self._last_auto_advance_at < self._config.auto_advance_cooldown_seconds:
            return

        self._last_auto_advance_at = now
        self._advance_due_to_position(distance)

    def _announce_current(self, repeat: bool) -> None:
        """
        播放当前导航步骤的指令（内部方法）。

        参数：
            repeat: 是否为重复播放，影响播放前缀的文本
        """
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
        """
        因位置到达而自动推进到下一步骤（内部方法）。

        参数：
            distance: 当前位置到目标的距离（米）
        """
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
        """
        计算两点间的地面距离（使用 Haversine 公式）。

        参数：
            lat1, lon1: 第一个点的纬度、经度
            lat2, lon2: 第二个点的纬度、经度

        返回值：
            float: 两点间的地面距离（米）
        """
        radius = 6371000.0  # 地球平均半径（米）
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))
