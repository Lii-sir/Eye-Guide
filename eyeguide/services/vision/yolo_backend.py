from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover
    YOLO = None


@dataclass
class BoxPrediction:
    label: str
    confidence: float
    box: Tuple[int, int, int, int]


class YoloDetector:
    def __init__(self, model_name: str = "yolov8n.pt") -> None:
        self._model = self._load_model(model_name)

    @property
    def is_available(self) -> bool:
        return self._model is not None

    def _load_model(self, model_name: str):
        if YOLO is None:
            return None
        try:
            return YOLO(model_name)
        except Exception:
            return None

    def predict(self, frame: np.ndarray) -> List[BoxPrediction]:
        if self._model is None:
            return []

        try:
            result = self._model.predict(
                source=frame,
                conf=0.35,
                verbose=False,
                imgsz=640,
                classes=None,
            )[0]
        except Exception:
            return []

        predictions: List[BoxPrediction] = []
        names = result.names
        for box in result.boxes:
            cls_idx = int(box.cls[0].item())
            confidence = float(box.conf[0].item())
            label = names.get(cls_idx, str(cls_idx))
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            predictions.append(BoxPrediction(label=label, confidence=confidence, box=(x1, y1, x2, y2)))
        return predictions

