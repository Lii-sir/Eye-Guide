"""WebSocket 会话控制器。

阶段 B 的核心变化：
1. 使用统一帧协议，把 `frame_id / client_ts / image / gps / imu` 放在一条消息里。
2. 服务端不再“收到一帧就立刻同步推理”，而是改成“单槽队列 + 后台 worker”。
3. 队列容量固定为 1，新帧到达时直接覆盖尚未处理的旧帧，保证最新帧优先。
4. 服务端返回延迟元数据和 `should_drop` 标志，客户端可按 500ms 规则丢弃过期结果。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import math
import time
from dataclasses import dataclass, replace
from typing import Any

import cv2
import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from eyeguide.api.lifecycle import ApiServices
from eyeguide.api.protocol import (
    ClientEnvelope,
    ConfigUpdatePayload,
    FramePayload,
    GpsPayload,
    ImuPayload,
    RouteSearchPayload,
    RouteSetPayload,
    SpeechTestPayload,
    error_message,
    ok_message,
)
from eyeguide.app.route_guidance import RouteGuidance
from eyeguide.domain.models import DetectionEvent, GpsFix, RoutePlan
from eyeguide.services.navigation import NavigationError
from eyeguide.services.navigation.formatters import format_gps_origin
from eyeguide.services.vision import SceneAnalyzer

MAX_FRAME_BYTES = 3_000_000
CLIENT_STALE_RESULT_MS = 500.0


@dataclass(slots=True)
class ImuSnapshot:
    """服务端内部使用的 IMU 快照。"""

    heading_deg: float | None = None
    pitch_deg: float | None = None
    roll_deg: float | None = None
    yaw_deg: float | None = None
    accel_x: float | None = None
    accel_y: float | None = None
    accel_z: float | None = None
    gyro_x: float | None = None
    gyro_y: float | None = None
    gyro_z: float | None = None


@dataclass(slots=True)
class PendingFrameJob:
    """等待被推理 worker 处理的单帧任务。"""

    frame_id: str
    client_ts_ms: float
    seq: int
    frame: np.ndarray
    gps: GpsFix | None
    imu: ImuSnapshot | None  #  imu是服务端内部使用的快照，不直接暴露给协议层，避免协议层过于复杂。快照就是当时的姿态/惯导数据，和这帧图像尽量同步。
    received_wall_ts_ms: float
    received_mono_ts: float


class ApiSession:
    """单个客户端连接对应的会话对象。"""

    def __init__(self, websocket: WebSocket, services: ApiServices) -> None:
        self._websocket = websocket
        self._services = services
        self._vision_config = services.config.vision
        self._analyzer = SceneAnalyzer(self._vision_config)
        self._route_guidance = RouteGuidance(
            services.config.navigation,
            services.speech,
            self._emit,
        )

        self._last_fix: GpsFix | None = None
        self._last_imu: ImuSnapshot | None = None
        self._route_plan: RoutePlan | None = None
        self._last_route_text = "暂无导航指令"
        self._last_hazard_text = "暂无实时信息"
        self._mode = "explore"
        self._default_provider = "osm"
        self._default_amap_api_key: str | None = None

        # 单槽队列实现：
        # - `_latest_frame_job` 永远只保留一帧待处理任务
        # - 新帧到达时直接覆盖旧的待处理帧
        # - worker 空闲后总是拿到“当前最新的一帧”
        self._slot_lock = asyncio.Lock()
        self._slot_event = asyncio.Event()
        self._latest_frame_job: PendingFrameJob | None = None
        self._frame_worker_task: asyncio.Task | None = None
        self._closed = False
        self._dropped_pending_frames = 0

    async def run(self) -> None:
        """接收并处理客户端消息的主循环。"""

        await self._websocket.accept()
        self._frame_worker_task = asyncio.create_task(self._frame_worker_loop(), name="eyeguide-frame-worker")

        await self._send(
            ok_message(
                "session.ready",
                {
                    "message": "EyeGuide API session is ready.",
                    "backend": self._analyzer.backend_name,
                    "frame_queue_mode": "single-slot-lifo",
                    "stale_after_ms": CLIENT_STALE_RESULT_MS,
                    "speech_policy": {
                        "persistent_dedupe_ttl_seconds": self._services.config.speech.persistent_dedupe_ttl_seconds,
                    },
                },
            )
        )

        try:
            while True:
                message = await self._websocket.receive_json()
                await self._handle_message(message)
        except WebSocketDisconnect:
            return
        finally:
            self._closed = True
            self._slot_event.set()
            if self._frame_worker_task is not None:
                self._frame_worker_task.cancel()
                try:
                    await self._frame_worker_task
                except asyncio.CancelledError:
                    pass

    async def _handle_message(self, raw_message: Any) -> None:
        """解析客户端消息并分发到对应处理逻辑。"""

        try:
            envelope = ClientEnvelope.model_validate(raw_message)
        except ValidationError as exc:
            await self._send(error_message("BAD_REQUEST", exc.errors()[0]["msg"]))
            return

        handlers = {
            "hello": self._handle_hello,
            "ping": self._handle_ping,
            "sensor.gps": self._handle_gps,
            "sensor.imu": self._handle_imu,
            "sensor.frame": self._handle_frame,
            "route.search": self._handle_route_search,
            "route.set": self._handle_route_set,
            "route.next": self._handle_route_next,
            "route.repeat": self._handle_route_repeat,
            "route.clear": self._handle_route_clear,
            "speech.test": self._handle_speech_test,
            "config.update": self._handle_config_update,
        }

        handler = handlers.get(envelope.type)
        if handler is None:
            await self._send(
                error_message(
                    "UNSUPPORTED_TYPE",
                    f"unsupported message type: {envelope.type}",
                    seq=envelope.seq,
                )
            )
            return
        await handler(envelope)

    async def _handle_hello(self, envelope: ClientEnvelope) -> None:
        """处理客户端初始化问候消息。"""

        await self._send(
            ok_message(
                "hello.ack",
                {
                    "session_id": envelope.session_id,
                    "backend": self._analyzer.backend_name,
                    "detail": self._analyzer.backend_detail,
                    "state": self._build_state_payload(),
                    "frame_queue_mode": "single-slot-lifo",
                    "stale_after_ms": CLIENT_STALE_RESULT_MS,
                    "speech_policy": {
                        "persistent_dedupe_ttl_seconds": self._services.config.speech.persistent_dedupe_ttl_seconds,
                    },
                },
                seq=envelope.seq,
            )
        )

    async def _handle_ping(self, envelope: ClientEnvelope) -> None:
        """处理客户端心跳。"""

        await self._send(
            ok_message(
                "pong",
                {"server_ts": time.time()},
                seq=envelope.seq,
            )
        )

    async def _handle_gps(self, envelope: ClientEnvelope) -> None:
        """处理独立 GPS 消息。

        虽然阶段 B 推荐把 GPS 放进 `sensor.frame` 里一起发，
        但这里仍然保留独立消息接口，方便低频定位更新和回退兼容。
        """

        try:
            payload = GpsPayload.model_validate(envelope.payload)
        except ValidationError as exc:
            await self._send(error_message("BAD_GPS_PAYLOAD", exc.errors()[0]["msg"], seq=envelope.seq))
            return

        self._last_fix = self._build_fix_from_gps(payload, envelope.ts * 1000.0)
        self._route_guidance.sync_with_location(self._last_fix)

        await self._send(
            ok_message(
                "gps.updated",
                {
                    "location": self._build_location_payload(),
                    "route": self._build_route_payload(),
                },
                seq=envelope.seq,
            )
        )

    async def _handle_imu(self, envelope: ClientEnvelope) -> None:
        """处理独立 IMU 消息。"""

        try:
            payload = ImuPayload.model_validate(envelope.payload)
        except ValidationError as exc:
            await self._send(error_message("BAD_IMU_PAYLOAD", exc.errors()[0]["msg"], seq=envelope.seq))
            return

        self._last_imu = self._build_imu_snapshot(payload)
        await self._send(
            ok_message(
                "imu.updated",
                {"imu": self._build_imu_payload(self._last_imu)},
                seq=envelope.seq,
            )
        )

    async def _handle_frame(self, envelope: ClientEnvelope) -> None:
        """处理统一帧消息。

        注意这里不再直接调用 `analyze()`。
        我们只负责：
        1. 校验并解码图像
        2. 提取同步的 GPS / IMU 快照
        3. 将任务放入“容量为 1 的最新帧缓冲区”
        4. 立即回包告知客户端该帧已被接受或覆盖了旧帧
        """

        try:
            payload = FramePayload.model_validate(envelope.payload)
        except ValidationError as exc:
            await self._send(error_message("BAD_FRAME_PAYLOAD", exc.errors()[0]["msg"], seq=envelope.seq))
            return

        try:
            frame = await asyncio.to_thread(self._decode_frame, payload)
        except ValueError as exc:
            await self._send(error_message("BAD_FRAME_DATA", str(exc), seq=envelope.seq))
            return

        client_ts_ms = self._resolve_client_ts_ms(payload.client_ts, envelope.ts)
        gps_fix = self._build_fix_from_gps(payload.gps, client_ts_ms) if payload.gps is not None else self._last_fix
        imu_snapshot = (
            self._build_imu_snapshot(payload.imu) if payload.imu is not None else self._last_imu
        )

        # 先更新“最近状态”，这样哪怕该帧稍后才被推理，路线与状态面板也已经拿到了最新传感器值。
        self._last_fix = gps_fix
        self._last_imu = imu_snapshot
        if gps_fix is not None:
            self._route_guidance.sync_with_location(gps_fix)

        job = PendingFrameJob(
            frame_id=payload.frame_id,
            client_ts_ms=client_ts_ms,
            seq=envelope.seq,
            frame=frame,
            gps=gps_fix,
            imu=imu_snapshot,
            received_wall_ts_ms=time.time() * 1000.0,
            received_mono_ts=time.perf_counter(),
        )

        replaced_job = await self._enqueue_latest_frame(job)
        await self._send(
            ok_message(
                "frame.accepted",
                {
                    "frame_id": job.frame_id,
                    "client_ts": job.client_ts_ms,
                    "queue_mode": "single-slot-lifo",
                    "replaced_frame_id": None if replaced_job is None else replaced_job.frame_id,
                    "dropped_pending_frames": self._dropped_pending_frames,
                },
                seq=envelope.seq,
            )
        )

        # 如果有一帧尚未被处理就被新帧覆盖，显式通知客户端，
        # 这样客户端不会一直傻等那一帧的推理结果。
        if replaced_job is not None:
            await self._send(
                ok_message(
                    "frame.dropped",
                    {
                        "frame_id": replaced_job.frame_id,
                        "client_ts": replaced_job.client_ts_ms,
                        "reason": "replaced_by_newer_frame",
                        "replacement_frame_id": job.frame_id,
                    },
                    seq=envelope.seq,
                )
            )

    async def _handle_route_search(self, envelope: ClientEnvelope) -> None:
        """处理路线搜索请求。"""

        try:
            payload = RouteSearchPayload.model_validate(envelope.payload)
        except ValidationError as exc:
            await self._send(error_message("BAD_ROUTE_SEARCH", exc.errors()[0]["msg"], seq=envelope.seq))
            return

        provider = payload.provider or self._default_provider
        amap_api_key = payload.amap_api_key or self._default_amap_api_key
        origin = payload.origin
        if self._is_gps_origin(origin) and self._last_fix is not None:
            origin = format_gps_origin(self._last_fix)
        elif self._is_gps_origin(origin):
            origin = ""

        try:
            result = self._services.navigation.search_candidates_debug(
                origin,
                payload.destination,
                provider=provider,
                amap_api_key=amap_api_key,
                limit=payload.limit,
            )
        except NavigationError as exc:
            await self._send(error_message("ROUTE_SEARCH_FAILED", str(exc), seq=envelope.seq))
            return

        await self._send(
            ok_message(
                "route.search.result",
                {
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
                },
                seq=envelope.seq,
            )
        )

    async def _handle_route_set(self, envelope: ClientEnvelope) -> None:
        """处理设置导航路线请求。"""

        try:
            payload = RouteSetPayload.model_validate(envelope.payload)
        except ValidationError as exc:
            await self._send(error_message("BAD_ROUTE_SET", exc.errors()[0]["msg"], seq=envelope.seq))
            return

        provider = payload.provider or self._default_provider
        amap_api_key = payload.amap_api_key or self._default_amap_api_key
        origin = payload.origin
        if self._is_gps_origin(origin):
            if self._last_fix is None:
                await self._send(
                    ok_message(
                        "route.pending",
                        {
                            "message": "尚未收到 GPS，暂时无法生成路线。",
                            "destination": payload.destination,
                        },
                        seq=envelope.seq,
                    )
                )
                return
            origin = format_gps_origin(self._last_fix)

        try:
            route_plan = self._services.navigation.build_route(
                origin,
                payload.destination,
                prefer_online=payload.prefer_online,
                provider=provider,
                amap_api_key=amap_api_key,
            )
        except NavigationError as exc:
            if not payload.use_demo_fallback:
                await self._send(error_message("ROUTE_BUILD_FAILED", str(exc), seq=envelope.seq))
                return
            route_plan = self._services.navigation.build_demo_route(origin, payload.destination)

        self._route_plan = route_plan
        self._route_guidance.set_route(route_plan)
        self._route_guidance.announce_current()
        self._mode = "navigation"

        await self._send(
            ok_message(
                "route.set.result",
                {
                    "route": self._build_route_payload(),
                    "distance_meters": round(route_plan.distance_meters, 1),
                    "duration_seconds": round(route_plan.duration_seconds, 1),
                    "provider_used": route_plan.source,
                    "step_count": len(route_plan.steps),
                },
                seq=envelope.seq,
            )
        )

    async def _handle_route_next(self, envelope: ClientEnvelope) -> None:
        """处理手动切换到下一导航步骤。"""

        self._route_guidance.next_step()
        await self._send(ok_message("route.next.result", {"route": self._build_route_payload()}, seq=envelope.seq))

    async def _handle_route_repeat(self, envelope: ClientEnvelope) -> None:
        """处理重复播报当前导航步骤。"""

        self._route_guidance.repeat_step()
        await self._send(
            ok_message("route.repeat.result", {"route": self._build_route_payload()}, seq=envelope.seq)
        )

    async def _handle_route_clear(self, envelope: ClientEnvelope) -> None:
        """处理清除导航路线请求。"""

        self._route_plan = None
        self._route_guidance.set_route(None)
        self._last_route_text = "暂无导航指令"
        self._mode = "explore"
        await self._send(ok_message("route.clear.result", {"route": self._build_route_payload()}, seq=envelope.seq))

    async def _handle_speech_test(self, envelope: ClientEnvelope) -> None:
        """处理语音测试请求。"""

        try:
            payload = SpeechTestPayload.model_validate(envelope.payload)
        except ValidationError as exc:
            await self._send(error_message("BAD_SPEECH_PAYLOAD", exc.errors()[0]["msg"], seq=envelope.seq))
            return

        text = payload.text or "这是一条语音测试。如果你能听到这句话，说明播报已经恢复。"
        await self._send(ok_message("speech.test.result", {"text": text}, seq=envelope.seq))

    async def _handle_config_update(self, envelope: ClientEnvelope) -> None:
        """处理运行时配置更新请求。"""

        try:
            payload = ConfigUpdatePayload.model_validate(envelope.payload)
        except ValidationError as exc:
            await self._send(error_message("BAD_CONFIG_UPDATE", exc.errors()[0]["msg"], seq=envelope.seq))
            return

        if payload.provider:
            self._default_provider = payload.provider
        if payload.amap_api_key is not None:
            self._default_amap_api_key = payload.amap_api_key or None
        if payload.object_speech_dedup_mode is not None:
            self._vision_config = replace(
                self._vision_config,
                object_speech_dedup_mode=payload.object_speech_dedup_mode,
            )
            self._analyzer = SceneAnalyzer(self._vision_config)

        await self._send(
            ok_message(
                "config.update.result",
                {
                    "provider": self._default_provider,
                    "object_speech_dedup_mode": self._vision_config.object_speech_dedup_mode,
                },
                seq=envelope.seq,
            )
        )

    async def _frame_worker_loop(self) -> None:
        """后台帧推理 worker。

        它只做一件事：只要有新帧，就拿当前缓冲区中的“最新一帧”去推理。
        如果在推理过程中又来了更多帧，它们不会排成队列，只会不断覆盖单槽中的旧帧。
        """

        while not self._closed:
            await self._slot_event.wait()
            if self._closed:
                return

            job = await self._take_latest_frame_job()
            if job is None:
                continue

            try:
                await self._process_frame_job(job)
            except Exception as exc:
                await self._send(
                    error_message(
                        "FRAME_PROCESS_FAILED",
                        f"frame {job.frame_id} failed: {exc}",
                        seq=job.seq,
                    )
                )

    async def _enqueue_latest_frame(self, job: PendingFrameJob) -> PendingFrameJob | None:
        """把新帧写入单槽缓冲区。

        返回值：
        - `None`：表示没有待处理旧帧被覆盖
        - `PendingFrameJob`：表示这个旧帧还没来得及处理，就被新帧替换了
        """

        async with self._slot_lock:
            replaced_job = self._latest_frame_job
            self._latest_frame_job = job
            self._slot_event.set()
            if replaced_job is not None:
                self._dropped_pending_frames += 1
            return replaced_job

    async def _take_latest_frame_job(self) -> PendingFrameJob | None:
        """由 worker 取走当前最新待处理帧。"""

        async with self._slot_lock:
            job = self._latest_frame_job
            self._latest_frame_job = None
            self._slot_event.clear()
            return job

    async def _process_frame_job(self, job: PendingFrameJob) -> None:
        """真正执行单帧推理，并把结果发回客户端。"""

        infer_start_mono = time.perf_counter()
        infer_start_wall_ms = time.time() * 1000.0

        # 这里使用 `asyncio.to_thread`，避免把事件循环阻塞在 CPU/GPU 推理上。
        analysis = await asyncio.to_thread(self._analyzer.analyze, job.frame)

        # 用该帧绑定的 GPS 快照推进路线，保证推理结果和导航状态尽量同步。
        self._last_fix = job.gps
        self._last_imu = job.imu
        if job.gps is not None:
            self._route_guidance.sync_with_location(job.gps)

        route_line = self._route_guidance.current_route_text()
        if route_line:
            self._last_route_text = route_line

        # API / mobile-web 模式下，服务端不直接在电脑本机播报，
        # 而是把结果文本回传给前端，由手机浏览器负责最终发声。
        self._last_hazard_text = analysis.hazard_summary or "环境相对安全"

        infer_end_mono = time.perf_counter()
        server_sent_ts_ms = time.time() * 1000.0
        queue_delay_ms = (infer_start_mono - job.received_mono_ts) * 1000.0
        infer_delay_ms = (infer_end_mono - infer_start_mono) * 1000.0
        result_age_ms = max(0.0, server_sent_ts_ms - job.client_ts_ms)
        should_drop = result_age_ms > CLIENT_STALE_RESULT_MS

        await self._send(
            ok_message(
                "frame.result",
                {
                    "frame_id": job.frame_id,
                    "client_ts": job.client_ts_ms,
                    "server_received_ts": job.received_wall_ts_ms,
                    "server_infer_started_ts": infer_start_wall_ms,
                    "server_sent_ts": server_sent_ts_ms,
                    "queue_mode": "single-slot-lifo",
                    "hazard_level": self._hazard_level_from_events(analysis.events),
                    "hazard_summary": analysis.hazard_summary or "环境相对安全",
                    "top_event": analysis.events[0].message if analysis.events else None,
                    "blind_road_detected": analysis.blind_road_detected,
                    "blind_road_blocked": analysis.blind_road_blocked,
                    "blind_road_direction": analysis.blind_road_direction,
                    "route": self._build_route_payload(),
                    "location": self._build_location_payload(),
                    "imu": self._build_imu_payload(job.imu),
                    "events": [self._serialize_event(event) for event in analysis.events[:5]],
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
                    # 前端绘制检测框时需要知道服务端推理所用图像的尺寸，
                    # 这样才能把返回的像素坐标正确缩放到手机预览层。
                    "frame_shape": {
                        "height": int(job.frame.shape[0]),
                        "width": int(job.frame.shape[1]),
                    },
                    "overlays": analysis.overlays[:8],
                    "backend": self._analyzer.backend_name,
                    "timing": {
                        "queue_delay_ms": round(queue_delay_ms, 2),
                        "infer_delay_ms": round(infer_delay_ms, 2),
                        "result_age_ms": round(result_age_ms, 2),
                        "stale_after_ms": CLIENT_STALE_RESULT_MS,
                        "should_drop": should_drop,
                    },
                },
                seq=job.seq,
            )
        )

    async def _send(self, message: dict[str, Any]) -> None:
        """统一发送 JSON 响应。"""

        await self._websocket.send_json(message)

    def _emit(self, kind: str, value: str) -> None:
        """接收 RouteGuidance 的回调并更新最近状态。"""

        if kind == "route":
            self._last_route_text = value
        elif kind == "hazard":
            self._last_hazard_text = value

    def _decode_frame(self, payload: FramePayload) -> np.ndarray:
        """把客户端上传的 Base64 图像解码成 OpenCV BGR 帧。"""

        raw = payload.image_b64 or payload.image
        if not raw:
            raise ValueError("image_b64 is required")
        if raw.startswith("data:"):
            parts = raw.split(",", 1)
            if len(parts) != 2:
                raise ValueError("invalid data URL")
            raw = parts[1]
        try:
            image_bytes = base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("image_b64 is not valid base64") from exc
        if not image_bytes:
            raise ValueError("decoded image is empty")
        if len(image_bytes) > MAX_FRAME_BYTES:
            raise ValueError(f"image too large: {len(image_bytes)} bytes")
        frame = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("failed to decode image frame")
        return frame

    def _resolve_client_ts_ms(self, client_ts: float | None, envelope_ts_seconds: float) -> float:
        """统一把客户端时间戳转换为毫秒。

        约定：
        - 如果 `payload.client_ts` 存在，则优先使用它
        - 如果缺失，则回退到 envelope.ts，并换算成毫秒
        """

        if client_ts is not None:
            return float(client_ts)
        return float(envelope_ts_seconds) * 1000.0

    def _build_fix_from_gps(self, payload: GpsPayload | None, client_ts_ms: float) -> GpsFix | None:
        """把协议层 GPS 数据转换成领域层 GpsFix。"""

        if payload is None:
            return None
        return GpsFix(
            latitude=payload.lat,
            longitude=payload.lon,
            speed_mps=payload.speed_mps,
            accuracy_meters=payload.accuracy,
            heading_degrees=payload.heading_deg,
            altitude_meters=payload.altitude,
            source="phone-web",
            timestamp=client_ts_ms / 1000.0,
        )

    def _build_imu_snapshot(self, payload: ImuPayload | None) -> ImuSnapshot | None:
        """把协议层 IMU 数据转换成内部快照。"""

        if payload is None:
            return None
        return ImuSnapshot(
            heading_deg=payload.heading_deg,
            pitch_deg=payload.pitch_deg,
            roll_deg=payload.roll_deg,
            yaw_deg=payload.yaw_deg,
            accel_x=payload.accel_x,
            accel_y=payload.accel_y,
            accel_z=payload.accel_z,
            gyro_x=payload.gyro_x,
            gyro_y=payload.gyro_y,
            gyro_z=payload.gyro_z,
        )

    def _build_state_payload(self) -> dict[str, Any]:
        """返回当前会话摘要。"""

        return {
            "mode": self._mode,
            "backend": self._analyzer.backend_name,
            "backend_detail": self._analyzer.backend_detail,
            "route": self._build_route_payload(),
            "location": self._build_location_payload(),
            "imu": self._build_imu_payload(self._last_imu),
            "hazard_summary": self._last_hazard_text,
        }

    def _build_route_payload(self) -> dict[str, Any]:
        """将当前路线状态转换成前端可直接消费的结构。"""

        if self._route_plan is None or not self._route_plan.steps:
            return {
                "active": False,
                "instruction": "",
                "step_index": 0,
                "step_total": 0,
                "provider": None,
                "destination": None,
                "distance_to_step_m": None,
            }

        step_index = 1
        step_total = len(self._route_plan.steps)
        instruction = self._route_plan.steps[0].instruction
        current_text = self._route_guidance.current_route_text()
        if current_text and ":" in current_text:
            prefix, _, suffix = current_text.partition(":")
            instruction = suffix.strip()
            if " " in prefix and "/" in prefix:
                _, _, progress = prefix.partition(" ")
                left, _, right = progress.partition("/")
                if left.isdigit():
                    step_index = int(left)
                if right.isdigit():
                    step_total = int(right)

        return {
            "active": True,
            "instruction": instruction,
            "step_index": step_index,
            "step_total": step_total,
            "provider": self._route_plan.source,
            "destination": self._route_plan.destination,
            "distance_to_step_m": self._distance_to_current_step(step_index - 1),
        }

    def _build_location_payload(self) -> dict[str, Any] | None:
        """将最近一次定位转换成标准输出。"""

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

    def _build_imu_payload(self, imu: ImuSnapshot | None) -> dict[str, Any] | None:
        """把内部 IMU 快照转换成可发送 JSON。"""

        if imu is None:
            return None
        return {
            "heading_deg": imu.heading_deg,
            "pitch_deg": imu.pitch_deg,
            "roll_deg": imu.roll_deg,
            "yaw_deg": imu.yaw_deg,
            "accel_x": imu.accel_x,
            "accel_y": imu.accel_y,
            "accel_z": imu.accel_z,
            "gyro_x": imu.gyro_x,
            "gyro_y": imu.gyro_y,
            "gyro_z": imu.gyro_z,
        }

    def _distance_to_current_step(self, step_index: int) -> float | None:
        """计算当前位置到当前导航步骤目标点的大致距离。"""

        if self._route_plan is None or self._last_fix is None or not self._route_plan.steps:
            return None
        normalized_index = min(max(step_index, 0), len(self._route_plan.steps) - 1)
        step = self._route_plan.steps[normalized_index]
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

    def _distance_meters(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """使用 Haversine 公式估算两点间距离。"""

        radius = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _hazard_level_from_events(self, events: list[DetectionEvent]) -> str:
        """把事件优先级映射成前端更容易展示的风险等级。"""

        if not events:
            return "low"
        best_priority = min(event.priority for event in events)
        if best_priority <= 1:
            return "high"
        if best_priority <= 3:
            return "medium"
        return "low"

    def _serialize_event(self, event: DetectionEvent) -> dict[str, Any]:
        """精简事件对象，避免把内部实现细节直接暴露给前端。"""

        return {
            "message": event.message,
            "category": event.category,
            "channel": event.channel,
            "priority": event.priority,
            "cooldown_seconds": event.cooldown_seconds,
            "persistent_dedupe": event.persistent_dedupe,
            "replace_pending": event.channel in {"vision", "blind_road"},
            "interrupt": True
            if event.channel == "blind_road"
            else (False if event.channel == "vision" else event.priority <= 1),
            "dedupe_key": event.dedupe_key,
            "timestamp": event.timestamp,
        }

    def _is_gps_origin(self, origin: str) -> bool:
        """识别客户端是否希望使用当前定位作为起点。"""

        return origin.strip().lower() in {"@gps", "@current", "current_gps", "auto", "phone"}
