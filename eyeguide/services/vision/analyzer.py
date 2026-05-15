from __future__ import annotations

import cv2
import numpy as np

from eyeguide.domain.models import DetectionEvent, FrameAnalysis
from eyeguide.services.vision.heuristics import HeuristicVisionDetector
from eyeguide.services.vision.yolo_backend import BoxPrediction, YoloDetector


HAZARD_LABELS = {
    "person": "行人",
    "bicycle": "自行车",
    "motorcycle": "摩托车",
    "car": "汽车",
    "bus": "公交车",
    "truck": "卡车",
    "chair": "椅子",
    "bench": "长椅",
    "suitcase": "行李箱",
    "backpack": "背包",
    "potted plant": "障碍物",
}


class SceneAnalyzer:
    def __init__(self) -> None:
        self._frame_index = 0
        self._yolo = YoloDetector()
        self._heuristics = HeuristicVisionDetector()

    @property
    def backend_name(self) -> str:
        return "YOLO + OpenCV" if self._yolo.is_available else "OpenCV heuristic"

    def analyze(self, frame: np.ndarray) -> FrameAnalysis:
        self._frame_index += 1
        analysis = FrameAnalysis()

        predictions = self._yolo.predict(frame)
        if predictions:
            self._collect_yolo_events(frame, predictions, analysis)
        else:
            self._heuristics.collect_people_events(frame, analysis, self._frame_index)

        self._heuristics.collect_ground_obstacle_events(frame, analysis)
        self._heuristics.collect_step_events(frame, analysis)

        if analysis.events:
            analysis.events.sort(key=lambda item: item.priority)
            analysis.hazard_summary = analysis.events[0].message
        else:
            analysis.hazard_summary = "环境相对安全"

        analysis.overlays.extend(
            [
                f"检测后端: {self.backend_name}",
                f"状态: {analysis.hazard_summary}",
            ]
        )
        return analysis

    def _collect_yolo_events(
        self,
        frame: np.ndarray,
        predictions: list[BoxPrediction],
        analysis: FrameAnalysis,
    ) -> None:
        height, width = frame.shape[:2]
        frame_area = float(height * width)

        for prediction in predictions:
            x1, y1, x2, y2 = prediction.box
            cv2.rectangle(frame, (x1, y1), (x2, y2), (40, 210, 40), 2)
            cv2.putText(
                frame,
                f"{prediction.label} {prediction.confidence:.2f}",
                (x1, max(y1 - 8, 24)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (40, 210, 40),
                2,
            )

            if prediction.label not in HAZARD_LABELS:
                continue

            box_area = (x2 - x1) * (y2 - y1)
            area_ratio = box_area / frame_area
            center_x = (x1 + x2) / 2
            bottom_ratio = y2 / height
            in_path = 0.25 * width <= center_x <= 0.75 * width
            if not in_path:
                continue

            distance_text, priority = self._classify_distance(bottom_ratio, area_ratio)
            analysis.events.append(
                DetectionEvent(
                    message=f"{distance_text}有{HAZARD_LABELS[prediction.label]}，请注意避让。",
                    category=f"object:{prediction.label}",
                    priority=priority,
                    cooldown_seconds=3.5,
                )
            )

    def _classify_distance(self, bottom_ratio: float, area_ratio: float) -> tuple[str, int]:
        if bottom_ratio > 0.72 or area_ratio > 0.08:
            return "非常近", 1
        if bottom_ratio > 0.58 or area_ratio > 0.04:
            return "较近", 2
        return "前方", 4

