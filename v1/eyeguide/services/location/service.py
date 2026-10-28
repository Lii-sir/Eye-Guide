from __future__ import annotations

from queue import Queue
from threading import Event, Lock, Thread
from typing import Optional
import time

from eyeguide.core.config import GpsConfig
from eyeguide.core.events import AppEvent
from eyeguide.domain.models import GpsFix
from eyeguide.services.location.nmea import (
    build_fix_from_gll,
    build_fix_from_rmc,
    parse_nmea_sentence,
    update_fix_from_gga,
)
from eyeguide.services.location.windows_provider import WindowsLocationProvider

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = None
    list_ports = None


class LocationError(Exception):
    """Raised when GPS access fails."""


class GpsService:
    def __init__(self, event_queue: Queue[AppEvent], config: GpsConfig | None = None) -> None:
        self._event_queue = event_queue
        self._config = config or GpsConfig()
        self._stop_event = Event()
        self._thread: Optional[Thread] = None
        self._fix_lock = Lock()
        self._latest_fix: Optional[GpsFix] = None
        self._port_name: Optional[str] = None
        self._connected = False
        self._last_sentence_at = 0.0
        self._last_message_type = ""
        self._windows_provider = WindowsLocationProvider()
        self._last_windows_attempt_at = 0.0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def latest_fix(self) -> Optional[GpsFix]:
        with self._fix_lock:
            return self._latest_fix

    def list_available_ports(self) -> list[str]:
        if list_ports is None:
            return []
        return [port.device for port in list_ports.comports()]

    def start(self, port_name: str) -> None:
        if serial is None:
            raise LocationError("当前环境缺少 pyserial，无法启用 GPS。")
        if self.is_running:
            raise LocationError("GPS 已经在运行。")
        if not port_name.strip():
            raise LocationError("请选择 GPS 串口。")

        self._port_name = port_name.strip()
        self._stop_event.clear()
        self._thread = Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        self._emit("gps_status", f"GPS 正在连接：{self._port_name}")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._connected = False
        self._emit("gps_status", "GPS 未连接")

    def _read_loop(self) -> None:
        last_emit = 0.0
        while not self._stop_event.is_set():
            try:
                with serial.Serial(
                    self._port_name,
                    self._config.baud_rate,
                    timeout=self._config.read_timeout_seconds,
                ) as device:
                    self._connected = True
                    self._emit("log", f"GPS 已连接到串口 {self._port_name}")
                    self._emit("gps_status", f"GPS 已连接：{self._port_name}，等待有效定位")
                    while not self._stop_event.is_set():
                        raw = device.readline().decode("ascii", errors="ignore").strip()
                        if not raw:
                            self._try_windows_fallback()
                            if time.time() - last_emit > self._config.status_emit_interval_seconds:
                                self._emit_waiting_status()
                                last_emit = time.time()
                            continue
                        self._handle_sentence(raw)
                        if self.latest_fix is None:
                            self._try_windows_fallback()
                        if time.time() - last_emit > self._config.status_emit_interval_seconds:
                            fix = self.latest_fix
                            if fix is not None:
                                self._emit("gps_fix", self._format_fix(fix))
                            else:
                                self._emit_waiting_status()
                            last_emit = time.time()
            except Exception as exc:
                self._connected = False
                self._emit("gps_status", f"GPS 连接异常：{exc}")
                if self._stop_event.wait(self._config.reconnect_interval_seconds):
                    break

    def _handle_sentence(self, sentence: str) -> None:
        parsed = parse_nmea_sentence(sentence)
        if parsed is None:
            return

        message_type = parsed["type"]
        parts = parsed["parts"]
        self._last_sentence_at = time.time()
        self._last_message_type = message_type
        current_fix = self.latest_fix
        next_fix = current_fix

        if message_type == "RMC":
            next_fix = build_fix_from_rmc(parts, current_fix)
        elif message_type == "GGA":
            next_fix = update_fix_from_gga(parts, current_fix)
        elif message_type == "GLL":
            next_fix = build_fix_from_gll(parts, current_fix)

        if next_fix is None or not next_fix.is_valid:
            return

        with self._fix_lock:
            self._latest_fix = next_fix
        if next_fix.source == "windows-location":
            self._emit("gps_status", f"Windows 定位已获取：{next_fix.latitude:.6f}, {next_fix.longitude:.6f}")
        else:
            self._emit("gps_status", f"GPS 已定位：{next_fix.latitude:.6f}, {next_fix.longitude:.6f}")

    def _format_fix(self, fix: GpsFix) -> str:
        speed_kmh = fix.speed_mps * 3.6
        sat_text = f"{fix.satellites} 星" if fix.satellites is not None else "星数未知"
        source_text = "Windows" if fix.source == "windows-location" else "GPS"
        return (
            f"{source_text} 定位，纬度 {fix.latitude:.6f}，经度 {fix.longitude:.6f}，"
            f"速度 {speed_kmh:.1f} km/h，{sat_text}"
        )

    def _emit_waiting_status(self) -> None:
        if not self._connected:
            return
        if self._last_sentence_at == 0.0:
            if self._config.windows_fallback_enabled and self._windows_provider.is_available:
                self._emit("gps_status", f"GPS 已连接：{self._port_name}，尚未收到定位数据，正在尝试 Windows 定位")
            else:
                self._emit("gps_status", f"GPS 已连接：{self._port_name}，尚未收到定位数据")
            return
        self._emit(
            "gps_status",
            f"GPS 已连接：{self._port_name}，已收到 {self._last_message_type} 数据，等待卫星定位",
        )

    def _try_windows_fallback(self) -> None:
        if not self._config.windows_fallback_enabled:
            return
        if not self._windows_provider.is_available:
            return

        current_fix = self.latest_fix
        if current_fix is not None and current_fix.source == "windows-location":
            return
        if current_fix is not None and current_fix.source != "windows-location":
            return

        now = time.time()
        if now - self._last_windows_attempt_at < self._config.windows_refresh_interval_seconds:
            return

        self._last_windows_attempt_at = now
        try:
            fix = self._windows_provider.get_fix()
        except Exception as exc:
            self._emit("log", f"Windows 定位尝试失败：{exc}")
            return

        if fix is None or not fix.is_valid:
            self._emit("log", "Windows 定位暂时不可用。")
            return

        with self._fix_lock:
            self._latest_fix = fix
        self._emit("gps_status", f"Windows 定位已获取：{fix.latitude:.6f}, {fix.longitude:.6f}")
        self._emit("gps_fix", self._format_fix(fix))

    def _emit(self, kind: str, value: str) -> None:
        self._event_queue.put(AppEvent(kind=kind, value=value))
