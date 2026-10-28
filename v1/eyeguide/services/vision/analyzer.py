from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from time import perf_counter

import numpy as np

from eyeguide.core.config import VisionConfig
from eyeguide.domain.models import DetectionBox, DetectionEvent, FrameAnalysis
from eyeguide.services.vision.blind_road_backend import BlindRoadEstimate, BlindRoadSegmenter
from eyeguide.services.vision.depth_backend import DepthAnythingV2MetricEstimator, DepthEstimate
from eyeguide.services.vision.heuristics import HeuristicVisionDetector
from eyeguide.services.vision.rendering import UnicodeFrameRenderer
from eyeguide.services.vision.tracking import ObjectTracker
from eyeguide.services.vision.yolo_backend import BoxPrediction, YoloDetector


_VISION_EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="eyeguide-vision")

HAZARD_LABELS = {
    "person": "行人",
    "bicycle": "自行车",
    "motorcycle": "摩托车",
    "car": "汽车",
    "bus": "公交车",
    "truck": "卡车",
    "chair": "椅子",
    "bench": "长凳",
    "suitcase": "行李箱",
    "backpack": "背包",
    "potted plant": "障碍物",
    "ground_obstacle": "地面障碍物",
}

ANNOUNCE_DISTANCE_METERS = 2.0
MIN_DETECTION_CENTER_RATIO = 0.08
MAX_DETECTION_CENTER_RATIO = 0.92


@dataclass(slots=True)
class InferenceStats:
    yolo_ms: float = 0.0
    depth_ms: float = 0.0
    blind_road_ms: float = 0.0
    total_ms: float = 0.0
    yolo_cached: bool = False
    depth_cached: bool = False
    blind_road_cached: bool = False


class SceneAnalyzer:
    def __init__(self, config: VisionConfig | None = None) -> None:
        self._config = config or VisionConfig()
        self._frame_index = 0
        self._yolo = YoloDetector(preferred_device=self._config.yolo_device_preference)
        self._depth = DepthAnythingV2MetricEstimator(
            preferred_device=self._config.depth_device_preference
        )
        self._blind_road = BlindRoadSegmenter(self._config)
        self._heuristics = HeuristicVisionDetector()
        self._tracker = ObjectTracker()
        self._renderer = UnicodeFrameRenderer()
        self._last_predictions: list[BoxPrediction] = []
        self._last_prediction_shape: tuple[int, int] | None = None
        self._last_depth_estimate: DepthEstimate | None = None
        self._last_depth_shape: tuple[int, int] | None = None

    @property
    def backend_name(self) -> str:
        parts: list[str] = []
        devices: list[str] = []
        if self._yolo.is_available:
            parts.append("YOLO")
            devices.append(self._yolo.device)
        else:
            parts.append("OpenCV heuristic")
        if self._depth.is_available:
            parts.append("Depth Anything V2")
        if self._blind_road.is_available:
            parts.append("BlindRoadSeg")
            devices.append(self._blind_road.device)
        device_text = "+".join(dict.fromkeys(devices)) if devices else "cpu"
        return f"{' + '.join(parts)} ({device_text})"

    @property
    def backend_detail(self) -> str:
        details: list[str] = []
        if self._yolo.is_available:
            details.append(f"YOLO device={self._yolo.device}")
        elif self._yolo.load_error:
            details.append(f"YOLO unavailable: {self._yolo.load_error}")
        if self._depth.is_available:
            details.append(f"depth={self._depth.model_name}")
        elif self._depth.load_error:
            details.append(f"depth unavailable: {self._depth.load_error}")
        if self._blind_road.is_available:
            details.append(f"blind-road device={self._blind_road.device}")
        elif self._blind_road.load_error:
            details.append(f"blind-road unavailable: {self._blind_road.load_error}")
        return "; ".join(details) if details else "Vision backends unavailable"

    def analyze(self, frame: np.ndarray) -> FrameAnalysis:
        total_start = perf_counter()
        self._frame_index += 1
        analysis = FrameAnalysis()
        _, width = frame.shape[:2]
        # 约定：只要 YOLO 后端已经成功加载，就认为“主检测器可用”。
        # 此时整套 OpenCV 启发式只作为兜底方案，不再参与当前帧分析，
        # 以避免出现“YOLO 已经识别到 person，但启发式 step 抢走播报”的冲突。
        use_heuristics_fallback = not self._yolo.is_available

        predictions, depth_estimate, blind_road_estimate, stats = self._run_inference(frame)

        if predictions:
            analysis.boxes.extend(self._predictions_to_boxes(depth_estimate, predictions, width))
            self._collect_yolo_events(frame, analysis.boxes, analysis)
        elif use_heuristics_fallback:
            # 只有在 YOLO 不可用时，才退回到启发式检测。
            # 注意这里的兜底不只是“没人框”，还包含地面障碍等传统视觉规则。
            self._heuristics.collect_people_events(frame, analysis, self._frame_index)
            self._heuristics.collect_ground_obstacle_events(frame, analysis)
            analysis.boxes = self._tracker.update(analysis.boxes)
            self._normalize_heuristic_event_dedupe_keys(analysis)

        self._blind_road.enrich_analysis(
            frame,
            analysis.boxes,
            analysis,
            self._frame_index,
            estimate=blind_road_estimate,
        )
        if use_heuristics_fallback:
            # 台阶检测同样归属于启发式链路。
            # 只要 YOLO 已经启动成功，就不再让 step heuristic 参与播报，
            # 防止误报台阶覆盖更可靠的目标检测结果。
            self._heuristics.collect_step_events(frame, analysis)
        self._apply_speech_policy(analysis)
        self._draw_detection_boxes(frame, analysis.boxes)

        if analysis.events:
            analysis.events.sort(key=lambda item: item.priority)
            analysis.hazard_summary = analysis.events[0].message
        else:
            analysis.hazard_summary = "环境相对安全"

        stats.total_ms = (perf_counter() - total_start) * 1000.0
        analysis.overlays = [
            f"检测后端: {self.backend_name}",
            f"追踪目标: {len(analysis.boxes)}",
            f"状态: {analysis.hazard_summary}",
            *self._build_performance_overlays(stats),
            *analysis.overlays,
        ]
        return analysis

    def _run_inference(
        self,
        frame: np.ndarray,
    ) -> tuple[list[BoxPrediction], DepthEstimate | None, BlindRoadEstimate | None, InferenceStats]:
        if self._config.parallel_inference_enabled:
            return self._run_parallel_inference(frame)
        return self._run_serial_inference(frame)

    def _run_parallel_inference(
        self,
        frame: np.ndarray,
    ) -> tuple[list[BoxPrediction], DepthEstimate | None, BlindRoadEstimate | None, InferenceStats]:
        frame_shape = frame.shape[:2]
        stats = InferenceStats()
        yolo_refresh_due = self._should_refresh(
            frame_shape,
            self._last_prediction_shape,
            bool(self._last_predictions),
            self._config.yolo_infer_interval_frames,
        )
        depth_refresh_due = self._should_refresh(
            frame_shape,
            self._last_depth_shape,
            self._last_depth_estimate is not None,
            self._config.depth_infer_interval_frames,
        )

        yolo_future: Future[tuple[list[BoxPrediction], float]] | None = None
        if self._yolo.is_available and yolo_refresh_due:
            yolo_future = _VISION_EXECUTOR.submit(self._timed_call, self._yolo.track, frame)
        else:
            stats.yolo_cached = bool(self._last_predictions)

        depth_future: Future[tuple[DepthEstimate | None, float]] | None = None
        if self._depth.is_available and depth_refresh_due:
            depth_future = _VISION_EXECUTOR.submit(self._timed_call, self._depth.estimate, frame)
        else:
            stats.depth_cached = self._last_depth_estimate is not None

        blind_road_future: Future[tuple[BlindRoadEstimate | None, float]] = _VISION_EXECUTOR.submit(
            self._timed_call,
            self._blind_road.estimate,
            frame,
            self._frame_index,
        )

        if yolo_future is not None:
            predictions, stats.yolo_ms = yolo_future.result()
            self._last_predictions = list(predictions)
            self._last_prediction_shape = frame_shape
        else:
            predictions = list(self._last_predictions)

        if depth_future is not None:
            depth_estimate, stats.depth_ms = depth_future.result()
            self._last_depth_estimate = depth_estimate
            self._last_depth_shape = frame_shape
        else:
            depth_estimate = self._last_depth_estimate

        blind_road_estimate, stats.blind_road_ms = blind_road_future.result()
        stats.blind_road_cached = (
            blind_road_estimate is not None
            and self._config.blind_road_infer_interval_frames > 1
            and self._frame_index % max(1, self._config.blind_road_infer_interval_frames) != 1
        )

        if not predictions:
            depth_estimate = None
        return predictions, depth_estimate, blind_road_estimate, stats

    def _run_serial_inference(
        self,
        frame: np.ndarray,
    ) -> tuple[list[BoxPrediction], DepthEstimate | None, BlindRoadEstimate | None, InferenceStats]:
        frame_shape = frame.shape[:2]
        stats = InferenceStats()

        yolo_refresh_due = self._should_refresh(
            frame_shape,
            self._last_prediction_shape,
            bool(self._last_predictions),
            self._config.yolo_infer_interval_frames,
        )
        if self._yolo.is_available and yolo_refresh_due:
            predictions, stats.yolo_ms = self._timed_call(self._yolo.track, frame)
            self._last_predictions = list(predictions)
            self._last_prediction_shape = frame_shape
        else:
            predictions = list(self._last_predictions)
            stats.yolo_cached = bool(predictions)

        depth_refresh_due = self._should_refresh(
            frame_shape,
            self._last_depth_shape,
            self._last_depth_estimate is not None,
            self._config.depth_infer_interval_frames,
        )
        if self._depth.is_available and depth_refresh_due:
            depth_estimate, stats.depth_ms = self._timed_call(self._depth.estimate, frame)
            self._last_depth_estimate = depth_estimate
            self._last_depth_shape = frame_shape
        else:
            depth_estimate = self._last_depth_estimate
            stats.depth_cached = depth_estimate is not None

        blind_road_estimate, stats.blind_road_ms = self._timed_call(
            self._blind_road.estimate,
            frame,
            self._frame_index,
        )
        stats.blind_road_cached = (
            blind_road_estimate is not None
            and self._config.blind_road_infer_interval_frames > 1
            and self._frame_index % max(1, self._config.blind_road_infer_interval_frames) != 1
        )

        if not predictions:
            depth_estimate = None
        return predictions, depth_estimate, blind_road_estimate, stats

    def _should_refresh(
        self,
        frame_shape: tuple[int, int],
        last_shape: tuple[int, int] | None,
        has_cached_value: bool,
        interval_frames: int,
    ) -> bool:
        if not has_cached_value or last_shape != frame_shape:
            return True
        if interval_frames <= 1:
            return True
        return self._frame_index % interval_frames == 1

    def _predictions_to_boxes(
        self,
        depth_estimate: DepthEstimate | None,
        predictions: list[BoxPrediction],
        frame_width: int,
    ) -> list[DetectionBox]:
        boxes: list[DetectionBox] = []
        for prediction in predictions:
            distance_meters = self._depth.estimate_box_distance(depth_estimate, prediction.box)
            boxes.append(
                DetectionBox(
                    label=prediction.label,
                    box=prediction.box,
                    confidence=prediction.confidence,
                    track_id=prediction.track_id,
                    distance_meters=distance_meters,
                    relative_direction=self._relative_direction(prediction.box, frame_width),
                )
            )
        return boxes

    def _collect_yolo_events(
        self,
        frame: np.ndarray,
        boxes: list[DetectionBox],
        analysis: FrameAnalysis,
    ) -> None:
        if not boxes:
            return

        height, width = frame.shape[:2]
        frame_area = float(height * width)

        for box in boxes:
            if box.label not in HAZARD_LABELS:
                continue

            x1, y1, x2, y2 = box.box
            box_area = max(1, (x2 - x1) * (y2 - y1))
            area_ratio = box_area / frame_area
            center_x = (x1 + x2) / 2
            bottom_ratio = y2 / float(height)
            center_ratio = center_x / float(width)
            if not (MIN_DETECTION_CENTER_RATIO <= center_ratio <= MAX_DETECTION_CENTER_RATIO):
                continue

            distance_meters = box.distance_meters
            if distance_meters is None or distance_meters > ANNOUNCE_DISTANCE_METERS:
                continue

            _, priority = self._classify_distance(distance_meters, bottom_ratio, area_ratio)
            label = HAZARD_LABELS[box.label]
            direction_text = box.relative_direction or self._relative_direction(box.box, width)
            distance_band = "near"
            if distance_meters <= 0.5:
                distance_band = "very-near"
            elif distance_meters <= 1.4:
                distance_band = "close"

            analysis.events.append(
                DetectionEvent(
                    message=f"注意，{direction_text}，{label}，{distance_meters:.1f}米",
                    category=f"object:{box.label}:{distance_band}:{direction_text}",
                    dedupe_key=self._object_event_dedupe_key(
                        box.label,
                        direction_text,
                        distance_band,
                    ),
                    channel="vision",
                    priority=priority,
                    cooldown_seconds=5.5,
                    persistent_dedupe=True,
                )
            )

    def _draw_detection_boxes(self, frame: np.ndarray, boxes: list[DetectionBox]) -> None:
        palette = {
            "person": (0, 180, 255),
            "ground_obstacle": (0, 80, 255),
        }
        for box in boxes:
            color = palette.get(box.label, (40, 210, 40))
            label = HAZARD_LABELS.get(box.label, box.label)
            if box.relative_direction:
                label = f"{label} {box.relative_direction}"
            if box.distance_meters is not None:
                label = f"{label} {box.distance_meters:.1f}m"
            elif box.confidence > 0:
                label = f"{label} {box.confidence:.2f}"
            self._renderer.draw_box_label(frame, box.box, label, color)

    def _apply_speech_policy(self, analysis: FrameAnalysis) -> None:
        if not analysis.blind_road_detected:
            return
        analysis.events = [
            event for event in analysis.events if event.category.startswith("blind_road:")
        ]

    def _build_performance_overlays(self, stats: InferenceStats) -> list[str]:
        if not self._config.show_performance_overlay:
            return []

        items = [
            self._format_performance_item("YOLO", stats.yolo_ms, stats.yolo_cached, self._yolo.is_available),
            self._format_performance_item("Depth", stats.depth_ms, stats.depth_cached, self._depth.is_available),
            self._format_performance_item(
                "盲道",
                stats.blind_road_ms,
                stats.blind_road_cached,
                self._blind_road.is_available,
            ),
        ]
        items.append(f"总计 {stats.total_ms:.0f}ms")
        return [f"耗时: {' | '.join(items)}"]

    def _format_performance_item(
        self,
        label: str,
        duration_ms: float,
        cached: bool,
        available: bool,
    ) -> str:
        if not available:
            return f"{label} -"
        if cached:
            return f"{label} 缓存"
        return f"{label} {duration_ms:.0f}ms"

    def _normalize_heuristic_event_dedupe_keys(self, analysis: FrameAnalysis) -> None:
        for event in analysis.events:
            if event.category.startswith("person:"):
                direction_text = event.category.split(":", 1)[1]
                event.dedupe_key = self._object_event_dedupe_key(
                    "person",
                    direction_text,
                    "heuristic",
                )
                event.persistent_dedupe = True
                continue

            if event.category == "ground_obstacle:front":
                event.dedupe_key = self._object_event_dedupe_key(
                    "ground_obstacle",
                    "正前方",
                    "heuristic",
                )
                event.persistent_dedupe = True

    def _object_event_dedupe_key(
        self,
        label: str,
        direction_text: str,
        distance_band: str,
    ) -> str:
        mode = self._config.object_speech_dedup_mode.strip().lower()
        if mode == "simple":
            return f"object:{label}:{distance_band}"
        return f"object:{label}:{direction_text}:{distance_band}"

    def _classify_distance(
        self,
        distance_meters: float,
        bottom_ratio: float,
        area_ratio: float,
    ) -> tuple[str, int]:
        if distance_meters <= 0.8 or bottom_ratio > 0.75 or area_ratio > 0.10:
            return "距离非常近", 1
        if distance_meters <= 1.4 or bottom_ratio > 0.62 or area_ratio > 0.05:
            return "距离较近", 2
        return "距离不远", 3

    def _relative_direction(self, box: tuple[int, int, int, int], frame_width: int) -> str:
        x1, _, x2, _ = box
        center_ratio = ((x1 + x2) / 2) / float(max(frame_width, 1))
        if center_ratio < 0.2:
            return "左侧"
        if center_ratio < 0.4:
            return "左前方"
        if center_ratio <= 0.6:
            return "正前方"
        if center_ratio <= 0.8:
            return "右前方"
        return "右侧"

    def _timed_call(self, fn, *args):
        start = perf_counter()
        return fn(*args), (perf_counter() - start) * 1000.0
