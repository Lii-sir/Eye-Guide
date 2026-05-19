from __future__ import annotations

import numpy as np

from eyeguide.domain.models import DetectionBox, DetectionEvent, FrameAnalysis
from eyeguide.services.vision.depth_backend import DepthAnythingV2MetricEstimator, DepthEstimate
from eyeguide.services.vision.heuristics import HeuristicVisionDetector
from eyeguide.services.vision.rendering import UnicodeFrameRenderer
from eyeguide.services.vision.tracking import ObjectTracker
from eyeguide.services.vision.yolo_backend import BoxPrediction, YoloDetector


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


class SceneAnalyzer:
    def __init__(self) -> None:
        self._frame_index = 0
        self._yolo = YoloDetector()
        self._depth = DepthAnythingV2MetricEstimator()
        self._heuristics = HeuristicVisionDetector()
        self._tracker = ObjectTracker()
        self._renderer = UnicodeFrameRenderer()

    @property
    def backend_name(self) -> str:
        if self._yolo.is_available and self._depth.is_available:
            return f"YOLO + Depth Anything V2 ({self._yolo.device})"
        if self._yolo.is_available:
            return f"YOLO only ({self._yolo.device})"
        return "OpenCV heuristic + tracker"

    @property
    def backend_detail(self) -> str:
        if self._yolo.is_available and self._depth.is_available:
            return f"YOLO device={self._yolo.device}; depth={self._depth.model_name}"
        if self._yolo.is_available:
            return self._depth.load_error or "Depth backend unavailable"
        return self._yolo.load_error or "YOLO unavailable"

    def analyze(self, frame: np.ndarray) -> FrameAnalysis:
        self._frame_index += 1
        analysis = FrameAnalysis()
        height, width = frame.shape[:2]

        predictions = self._yolo.track(frame)
        if predictions:
            depth_estimate = self._depth.estimate(frame)
            analysis.boxes.extend(self._predictions_to_boxes(depth_estimate, predictions, width))
            self._collect_yolo_events(frame, analysis.boxes, analysis)
        else:
            self._heuristics.collect_people_events(frame, analysis, self._frame_index)
            self._heuristics.collect_ground_obstacle_events(frame, analysis)
            analysis.boxes = self._tracker.update(analysis.boxes)

        self._heuristics.collect_step_events(frame, analysis)
        self._draw_detection_boxes(frame, analysis.boxes)

        if analysis.events:
            analysis.events.sort(key=lambda item: item.priority)
            analysis.hazard_summary = analysis.events[0].message
        else:
            analysis.hazard_summary = "环境相对安全"

        analysis.overlays = [
            f"检测后端: {self.backend_name}",
            f"追踪目标: {len(analysis.boxes)}",
            f"状态: {analysis.hazard_summary}",
            *analysis.overlays,
        ]
        return analysis

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

            distance_text, priority = self._classify_distance(distance_meters, bottom_ratio, area_ratio)
            label = HAZARD_LABELS[box.label]
            direction_text = box.relative_direction or self._relative_direction(box.box, width)
            distance_band = "near"
            if distance_meters <= 0.8:
                distance_band = "very-near"
            elif distance_meters <= 1.4:
                distance_band = "close"
            analysis.events.append(
                DetectionEvent(
                    message=f"注意，{direction_text}，{label}，{distance_meters:.1f}米",
                    category=f"object:{box.label}:{distance_band}:{direction_text}",
                    dedupe_key=f"object:{box.label}:{direction_text}:{distance_band}",
                    channel="vision",
                    priority=priority,
                    cooldown_seconds=5.5,
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
