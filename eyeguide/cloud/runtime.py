from __future__ import annotations

import base64
import itertools
import math
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock
from typing import Any, Callable, Optional

import cv2
import numpy as np

from eyeguide.app.route_guidance import RouteGuidance
from eyeguide.core.config import NavigationConfig, SpeechConfig, VisionConfig
from eyeguide.domain.models import DetectionEvent, GpsFix, RoutePlan
from eyeguide.services.navigation import NavigationError, NavigationService
from eyeguide.services.navigation.formatters import format_gps_origin
from eyeguide.services.navigation.models import CandidateSearchResult
from eyeguide.services.speech import SpeechEngine
from eyeguide.services.vision import SceneAnalyzer
from eyeguide.services.vision.rendering import UnicodeFrameRenderer

try:
    import pythoncom
    from win32com.client import Dispatch
except ImportError:  # pragma: no cover
    pythoncom = None
    Dispatch = None


_AUDIO_CACHE_DIR = Path(__file__).resolve().parents[2] / ".tmp" / "cloud_tts"
_AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_CACHE_DIR = _AUDIO_CACHE_DIR


@dataclass(slots=True)
class PendingRouteRequest:
    origin: str
    destination: str
    provider: str
    amap_api_key: Optional[str]
    prefer_online: bool
    use_demo_fallback: bool
    display_name: Optional[str] = None


@dataclass(slots=True)
class PendingSpeechPlayback:
    message_id: str
    done_event: Event
    completed: bool = False


class CloudAudioRenderer:
    def __init__(self, config: SpeechConfig | None = None) -> None:
        self._config = config or SpeechConfig()

    def render_audio_url(self, message_id: str, text: str) -> str | None:
        normalized = str(text or "").strip()
        normalized_id = str(message_id or "").strip()
        if not normalized or not normalized_id or pythoncom is None or Dispatch is None:
            return None

        com_initialized = False
        stream = None
        speaker = None
        original_stream = None
        output_path = _AUDIO_CACHE_DIR / f"{normalized_id}.wav"
        try:
            self._prune_cache()
            pythoncom.CoInitialize()
            com_initialized = True
            speaker = Dispatch("SAPI.SpVoice")
            speaker.Rate = max(-5, min(5, round((self._config.rate - 180) / 15)))
            stream = Dispatch("SAPI.SpFileStream")
            stream.Open(str(output_path), 3, False)
            original_stream = speaker.AudioOutputStream
            speaker.AudioOutputStream = stream
            speaker.Speak(normalized)
            stream.Close()
            speaker.AudioOutputStream = original_stream
            if not output_path.exists() or output_path.stat().st_size <= 0:
                return None
            return f"/audio/{output_path.name}"
        except Exception:
            return None
        finally:
            try:
                if stream is not None:
                    stream.Close()
            except Exception:
                pass
            try:
                if speaker is not None and original_stream is not None:
                    speaker.AudioOutputStream = original_stream
            except Exception:
                pass
            if com_initialized:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def _prune_cache(self, *, max_age_seconds: float = 900.0) -> None:
        current_ts = time.time()
        for path in _AUDIO_CACHE_DIR.glob("*.wav"):
            try:
                if current_ts - path.stat().st_mtime >= max_age_seconds:
                    path.unlink(missing_ok=True)
            except Exception:
                continue


class _CloudSpeechBackend:
    def __init__(self, relay: "CloudSpeechRelay") -> None:
        self._relay = relay
        self._voice_name = "mobile-client"
        self._output_name = "mobile-client"

    @property
    def name(self) -> str:
        return "cloud-relay"

    @property
    def voice_name(self) -> str:
        return self._voice_name

    @property
    def output_name(self) -> str:
        return self._output_name

    def speak(
        self,
        text: str,
        interrupt: bool = False,
        should_cancel: Callable[[], bool] | None = None,
    ) -> bool:
        return self._relay.enqueue_and_wait(text, interrupt=interrupt, should_cancel=should_cancel)

    def stop(self) -> None:
        self._relay.stop_current_playback()

    def close(self) -> None:
        return None


class CloudSpeechRelay(SpeechEngine):
    def __init__(self, config: SpeechConfig | None = None) -> None:
        self._outbound: Queue[dict[str, Any]] = Queue()
        self._audio_renderer = CloudAudioRenderer(config)
        self._message_counter = itertools.count(1)
        self._pending_lock = Lock()
        self._pending_playbacks: dict[str, PendingSpeechPlayback] = {}
        self._current_message_id: str | None = None
        super().__init__(config)

    def _build_backend(self):
        return _CloudSpeechBackend(self)

    def enqueue_and_wait(
        self,
        text: str,
        *,
        interrupt: bool,
        should_cancel: Callable[[], bool] | None,
    ) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False

        message_id = f"tts_{next(self._message_counter)}"
        pending = PendingSpeechPlayback(message_id=message_id, done_event=Event())
        with self._pending_lock:
            self._pending_playbacks[message_id] = pending
            self._current_message_id = message_id

        self._enqueue_message(
            {
                "kind": "speak",
                "message_id": message_id,
                "text": normalized,
                "interrupt": bool(interrupt),
            }
        )

        try:
            while True:
                if pending.done_event.wait(0.05):
                    return pending.completed
                if should_cancel is not None and should_cancel():
                    self._finish_playback(message_id, completed=False)
                    return False
        finally:
            with self._pending_lock:
                self._pending_playbacks.pop(message_id, None)
                if self._current_message_id == message_id:
                    self._current_message_id = None

    def _enqueue_message(self, message: dict[str, Any]) -> None:
        payload = dict(message)
        audio_url = self._audio_renderer.render_audio_url(
            str(payload.get("message_id", "") or ""),
            str(payload.get("text", "") or ""),
        )
        if audio_url:
            payload["audio_url"] = audio_url
        self._outbound.put(payload)

    def stop_current_playback(self) -> None:
        with self._pending_lock:
            message_id = self._current_message_id
        if not message_id:
            return
        self._outbound.put({"kind": "control", "command": "stop", "message_id": message_id})
        self._finish_playback(message_id, completed=False)

    def acknowledge_playback(self, message_id: str, *, completed: bool) -> None:
        self._finish_playback(message_id, completed=completed)

    def _finish_playback(self, message_id: str, *, completed: bool) -> None:
        with self._pending_lock:
            pending = self._pending_playbacks.get(message_id)
            if pending is None:
                return
            pending.completed = completed
            pending.done_event.set()
            if self._current_message_id == message_id:
                self._current_message_id = None

    def drain_messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        while True:
            try:
                messages.append(self._outbound.get_nowait())
            except Empty:
                break
        return messages


class CloudInferenceRuntime:
    def __init__(
        self,
        navigation_service: NavigationService | None = None,
        navigation_config: NavigationConfig | None = None,
        vision_config: VisionConfig | None = None,
        speech_config: SpeechConfig | None = None,
    ) -> None:
        self._navigation_service = navigation_service or NavigationService()
        self._navigation_config = navigation_config or NavigationConfig()
        self._vision_config = vision_config or VisionConfig()
        self._speech_config = speech_config or SpeechConfig()
        self._speech = CloudSpeechRelay(self._speech_config)
        self._renderer = UnicodeFrameRenderer()
        self._route_guidance = RouteGuidance(self._navigation_config, self._speech, self._emit)
        self._route_plan: Optional[RoutePlan] = None
        self._last_fix: Optional[GpsFix] = None
        self._pending_route: Optional[PendingRouteRequest] = None
        self._scene_analyzer: Optional[SceneAnalyzer] = None
        self._preferred_provider = "osm"
        self._amap_api_key: Optional[str] = None
        self._last_hazard_summary = "暂无实时信息"
        self._last_route_text = "暂无导航指令"
        self._last_gps_status = "未开始定位"
        self._mode = "tracking"

    def start_session(self, payload: dict) -> dict:
        requested_mode = str(payload.get("mode", "explore") or "explore").strip().lower()
        if requested_mode in {"navigation", "route", "nav"}:
            self._mode = "navigation"
        else:
            self._mode = "explore"
        self._speech.speak("视觉感知已启动，按 Q 键退出。", priority=4, cooldown_seconds=1.0)
        return self._system_payload(include_tts=True)

    def configure_route(self, payload: dict) -> tuple[dict | None, tuple[str, str] | None]:
        destination = str(payload.get("destination", "") or "").strip()
        if not destination:
            return None, ("BAD_PAYLOAD", "destination is required")

        origin = str(payload.get("origin", "@gps") or "").strip() or "@gps"
        provider = str(payload.get("provider", self._preferred_provider) or self._preferred_provider).strip().lower()
        amap_api_key_raw = payload.get("amap_api_key")
        amap_api_key = str(amap_api_key_raw).strip() if isinstance(amap_api_key_raw, str) else self._amap_api_key
        prefer_online = _as_bool(payload.get("prefer_online"), default=True)
        use_demo_fallback = _as_bool(payload.get("use_demo_fallback"), default=True)
        display_name_raw = payload.get("display_name")
        display_name = str(display_name_raw).strip() if isinstance(display_name_raw, str) else None

        self._preferred_provider = provider
        self._amap_api_key = amap_api_key or self._amap_api_key
        self._mode = "navigation"

        request = PendingRouteRequest(
            origin=origin,
            destination=destination,
            provider=provider,
            amap_api_key=amap_api_key or None,
            prefer_online=prefer_online,
            use_demo_fallback=use_demo_fallback,
            display_name=display_name or None,
        )

        if self._is_origin_from_gps(origin):
            if self._last_fix is None:
                self._pending_route = request
                self._route_plan = None
                self._route_guidance.set_route(None)
                self._last_route_text = "暂无导航指令"
                return (
                    {
                        "status": "pending_origin",
                        "message": "等待手机提供首个 GPS 定位后再生成路线。",
                        "destination": display_name or destination,
                        "provider": provider,
                    },
                    None,
                )
            request.origin = format_gps_origin(self._last_fix)

        response, error = self._build_route(request)
        if error is None:
            self._pending_route = None
        return response, error

    def search_route_candidates(self, payload: dict) -> tuple[dict | None, tuple[str, str] | None]:
        destination = str(payload.get("destination", "") or "").strip()
        if not destination:
            return None, ("BAD_PAYLOAD", "destination is required")

        origin = str(payload.get("origin", "@gps") or "").strip() or "@gps"
        provider = str(payload.get("provider", self._preferred_provider) or self._preferred_provider).strip().lower()
        amap_api_key_raw = payload.get("amap_api_key")
        amap_api_key = str(amap_api_key_raw).strip() if isinstance(amap_api_key_raw, str) else self._amap_api_key
        limit_raw = payload.get("limit", 5)
        limit = min(8, max(1, int(limit_raw))) if isinstance(limit_raw, (int, float)) else 5

        self._preferred_provider = provider
        self._amap_api_key = amap_api_key or self._amap_api_key

        if self._is_origin_from_gps(origin) and self._last_fix is not None:
            origin = format_gps_origin(self._last_fix)
        elif self._is_origin_from_gps(origin):
            origin = ""

        try:
            result = self._navigation_service.search_candidates_debug(
                origin,
                destination,
                provider=provider,
                amap_api_key=amap_api_key,
                limit=limit,
            )
        except NavigationError as exc:
            return (
                {
                    "status": "error",
                    "message": str(exc),
                    "candidates": [],
                    "debug_lines": [str(exc)],
                },
                None,
            )

        return self._candidate_payload(result), None

    def update_settings(self, payload: dict) -> tuple[dict | None, tuple[str, str] | None]:
        provider_raw = payload.get("provider")
        if isinstance(provider_raw, str) and provider_raw.strip():
            self._preferred_provider = provider_raw.strip().lower()

        amap_api_key_raw = payload.get("amap_api_key")
        if isinstance(amap_api_key_raw, str):
            self._amap_api_key = amap_api_key_raw.strip() or None

        dedup_mode_raw = payload.get("object_speech_dedup_mode")
        if dedup_mode_raw is not None:
            normalized_mode = str(dedup_mode_raw).strip().lower()
            if normalized_mode not in {"simple", "detailed"}:
                return None, ("BAD_PAYLOAD", "object_speech_dedup_mode must be simple or detailed")
            if normalized_mode != self._vision_config.object_speech_dedup_mode.strip().lower():
                self._vision_config = replace(self._vision_config, object_speech_dedup_mode=normalized_mode)
                self._scene_analyzer = None

        return (
            {
                "status": "ok",
                "provider": self._preferred_provider,
                "object_speech_dedup_mode": self._vision_config.object_speech_dedup_mode,
                "message": f"普通物体播报去重已切换为：{_dedup_mode_label(self._vision_config.object_speech_dedup_mode)}",
            },
            None,
        )

    def test_speech(self, payload: dict) -> dict:
        text = str(payload.get("text", "") or "").strip() or "这是一条语音测试。如果你能听到这句话，说明播报已经恢复。"
        self._speech.speak(text, priority=1, dedupe_key="ui:test-speech", cooldown_seconds=0.0)
        return self._system_payload(include_tts=True)

    def handle_speech_ack(self, payload: dict) -> None:
        message_id = str(payload.get("message_id", "") or "").strip()
        status = str(payload.get("status", "") or "").strip().lower()
        if not message_id:
            return
        self._speech.acknowledge_playback(message_id, completed=status in {"ended", "completed", "ok"})

    def current_state_payload(self) -> dict:
        return self._system_payload(include_tts=True)

    def next_route_step(self) -> dict:
        self._route_guidance.next_step()
        return self._system_payload(include_tts=True)

    def repeat_route_step(self) -> dict:
        self._route_guidance.repeat_step()
        return self._system_payload(include_tts=True)

    def infer_from_fix(self, fix: GpsFix) -> dict:
        self._last_fix = fix
        self._last_gps_status = "定位正常"

        if self._pending_route is not None:
            request = self._pending_route
            request.origin = format_gps_origin(fix)
            response, error = self._build_route(request)
            if error is None:
                self._pending_route = None
            else:
                self._pending_route = None
                self._speech.speak(f"路线生成失败：{error[1]}", priority=2, cooldown_seconds=0.0)

        return self._system_payload(include_tts=True)

    def infer_from_frame(self, frame: np.ndarray) -> dict:
        analyzer = self._get_scene_analyzer()
        normalized = self._normalize_frame(frame)
        analysis = analyzer.analyze(normalized)

        self._route_guidance.sync_with_location(self._last_fix)
        route_line = self._route_guidance.current_route_text()
        if route_line:
            self._renderer.draw_bottom_banner(normalized, route_line)
        self._renderer.draw_panel(normalized, analysis.overlays)

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

        self._last_hazard_summary = analysis.hazard_summary or "环境相对安全"
        route = self._current_route_payload()
        top_event = analysis.events[0] if analysis.events else None

        return {
            "mode": "navigation+vision" if route["active"] else "vision",
            "hazard_level": self._hazard_level_from_analysis(analysis.events),
            "location": self._current_location_payload(),
            "route": route,
            "vision": {
                "hazard_summary": analysis.hazard_summary,
                "event_count": len(analysis.events),
                "top_event": None if top_event is None else top_event.message,
                "blind_road_detected": analysis.blind_road_detected,
                "blind_road_blocked": analysis.blind_road_blocked,
                "blind_road_direction": analysis.blind_road_direction,
                "boxes": [
                    {
                        "label": box.label,
                        "confidence": round(box.confidence, 3),
                        "track_id": box.track_id,
                        "distance_meters": box.distance_meters,
                        "relative_direction": box.relative_direction,
                        "box": [int(box.box[0]), int(box.box[1]), int(box.box[2]), int(box.box[3])],
                    }
                    for box in analysis.boxes[:20]
                ],
                "overlays": analysis.overlays[:8],
                "frame_shape": {"height": int(normalized.shape[0]), "width": int(normalized.shape[1])},
                "rendered_image_b64": self._encode_rendered_frame(normalized),
            },
            "tts": None,
            "tts_items": self._speech.drain_messages(),
            "nav_hint": route["instruction"] if route["active"] else self._last_hazard_summary,
        }

    def clear_route(self) -> None:
        self._route_plan = None
        self._pending_route = None
        self._route_guidance.set_route(None)
        self._last_route_text = "暂无导航指令"

    def _build_route(self, request: PendingRouteRequest) -> tuple[dict | None, tuple[str, str] | None]:
        try:
            plan = self._navigation_service.build_route(
                request.origin,
                request.destination,
                prefer_online=request.prefer_online,
                provider=request.provider,
                amap_api_key=request.amap_api_key,
            )
            fallback_used = False
            error_text = None
        except NavigationError as exc:
            if not request.use_demo_fallback:
                return None, ("ROUTE_BUILD_FAILED", str(exc))
            plan = self._navigation_service.build_demo_route(request.origin, request.destination)
            fallback_used = True
            error_text = str(exc)

        if request.display_name:
            plan.destination = request.display_name
            plan.resolved_destination_address = request.display_name

        self._route_plan = plan
        self._route_guidance.set_route(plan)
        self._route_guidance.announce_current()
        first_instruction = plan.steps[0].instruction if plan.steps else ""
        return (
            {
                "status": "ok",
                "provider_used": plan.source,
                "route_steps": len(plan.steps),
                "distance_meters": round(plan.distance_meters, 1),
                "duration_seconds": round(plan.duration_seconds, 1),
                "instruction": first_instruction,
                "fallback_used": fallback_used,
                "fallback_reason": error_text,
                "destination": plan.destination,
            },
            None,
        )

    def _current_route_payload(self) -> dict:
        if self._route_plan is None or not self._route_plan.steps:
            return {
                "active": False,
                "provider": None,
                "step_index": 0,
                "step_total": 0,
                "instruction": "",
                "distance_to_step_m": None,
                "arrived": False,
                "destination": None,
            }

        route_text = self._route_guidance.current_route_text()
        step_index = 1
        step_total = len(self._route_plan.steps)
        instruction = self._route_plan.steps[0].instruction
        if route_text:
            step_index, step_total, instruction = self._parse_route_text(route_text, step_total)

        return {
            "active": True,
            "provider": self._route_plan.source,
            "step_index": step_index,
            "step_total": step_total,
            "instruction": instruction,
            "distance_to_step_m": self._distance_to_current_step(),
            "arrived": False,
            "destination": self._route_plan.destination,
        }

    def _distance_to_current_step(self) -> float | None:
        if self._route_plan is None or not self._route_plan.steps or self._last_fix is None:
            return None
        route_text = self._route_guidance.current_route_text()
        current_index = 0
        if route_text:
            current_index, _, _ = self._parse_route_text(route_text, len(self._route_plan.steps))
            current_index = max(0, current_index - 1)
        step = self._route_plan.steps[min(current_index, len(self._route_plan.steps) - 1)]
        if step.target is None:
            return None
        return round(
            self._distance_meters(
                self._last_fix.latitude,
                self._last_fix.longitude,
                step.target.latitude,
                step.target.longitude,
            ),
            1,
        )

    def _current_location_payload(self) -> dict | None:
        if self._last_fix is None:
            return None
        return {
            "lat": self._last_fix.latitude,
            "lon": self._last_fix.longitude,
            "accuracy_m": self._last_fix.accuracy_meters,
            "speed_kmh": round(self._last_fix.speed_mps * 3.6, 2),
            "heading_deg": self._last_fix.heading_degrees,
            "source": self._last_fix.source,
        }

    def _system_payload(self, include_tts: bool = False) -> dict:
        route = self._current_route_payload()
        tts_items = self._speech.drain_messages() if include_tts else []
        return {
            "mode": self._mode,
            "hazard_level": "low",
            "location": self._current_location_payload(),
            "route": route,
            "vision": None,
            "tts": tts_items[0]["text"] if tts_items else None,
            "tts_items": tts_items,
            "nav_hint": route["instruction"] if route["active"] else self._last_hazard_summary,
        }

    def _candidate_payload(self, result: CandidateSearchResult) -> dict:
        return {
            "status": "ok",
            "provider_used": result.provider_used,
            "candidates": [
                {
                    "display_name": item.display_name,
                    "route_value": item.route_value,
                    "provider": item.provider,
                    "score": item.score,
                }
                for item in result.candidates
            ],
            "debug_lines": result.debug_lines,
        }

    def _get_scene_analyzer(self) -> SceneAnalyzer:
        if self._scene_analyzer is None:
            self._scene_analyzer = SceneAnalyzer(self._vision_config)
        return self._scene_analyzer

    def _normalize_frame(self, frame: np.ndarray) -> np.ndarray:
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("frame must be BGR image")
        height, width = frame.shape[:2]
        max_width = 960
        if width <= max_width:
            return frame
        ratio = max_width / float(width)
        return cv2.resize(frame, (max_width, max(1, int(height * ratio))), interpolation=cv2.INTER_AREA)

    def _encode_rendered_frame(self, frame: np.ndarray) -> str | None:
        ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
        if not ok:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")

    def _hazard_level_from_analysis(self, events: list[DetectionEvent]) -> str:
        if not events:
            return "low"
        best_priority = min(event.priority for event in events)
        if best_priority <= 1:
            return "high"
        if best_priority <= 3:
            return "medium"
        return "low"

    def _emit(self, kind: str, value: str) -> None:
        if kind == "route":
            self._last_route_text = value
        elif kind == "gps_status":
            self._last_gps_status = value
        elif kind == "hazard":
            self._last_hazard_summary = value

    def _parse_route_text(self, route_text: str, default_total: int) -> tuple[int, int, str]:
        marker = ":"
        prefix, _, instruction = route_text.partition(marker)
        step_index = 1
        step_total = default_total
        if " " in prefix:
            _, _, progress = prefix.partition(" ")
            left, _, right = progress.partition("/")
            if left.isdigit():
                step_index = int(left)
            if right.isdigit():
                step_total = int(right)
        return step_index, step_total, instruction.strip()

    def _distance_meters(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _is_origin_from_gps(self, origin: str) -> bool:
        return origin.strip().lower() in {"@gps", "@current", "current_gps", "auto", "phone"}


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _dedup_mode_label(value: str) -> str:
    return "简单播报" if value.strip().lower() == "simple" else "详细播报"
