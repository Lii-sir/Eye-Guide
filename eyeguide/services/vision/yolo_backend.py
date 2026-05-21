from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import numpy as np

from eyeguide.core.paths import bundled_path, first_existing_path


@dataclass
class BoxPrediction:
    label: str
    confidence: float
    box: Tuple[int, int, int, int]
    track_id: int | None = None


class YoloDetector:
    def __init__(self, model_name: str = "models/YOLO/yolo26n.pt", preferred_device: str = "auto") -> None:
        self._load_error: str | None = None
        self._preferred_device = preferred_device.strip().lower()
        self._device = self._detect_device()
        self._inference_device = "0" if self._device == "gpu" else "cpu"
        self._model = self._load_model(self._resolve_model_path(model_name))

    @property
    def is_available(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def device(self) -> str:
        return self._device

    def _detect_device(self) -> str:
        if self._preferred_device == "cpu":
            return "cpu"
        try:
            import torch

            if self._preferred_device in {"auto", "gpu"} and torch.cuda.is_available():
                return "gpu"
        except Exception:
            pass
        if self._preferred_device == "gpu":
            self._load_error = "YOLO preferred GPU but CUDA is unavailable; falling back to CPU"
        return "cpu"

    def _import_yolo(self):
        try:
            from ultralytics import YOLO
        except Exception as exc:  # pragma: no cover
            self._load_error = str(exc)
            return None
        return YOLO

    def _load_model(self, model_name: str):
        yolo_cls = self._import_yolo()
        if yolo_cls is None:
            return None
        try:
            return yolo_cls(model_name)
        except Exception as exc:
            self._load_error = str(exc)
            return None

    def _resolve_model_path(self, model_name: str) -> str:
        model_path = first_existing_path(
            Path(model_name),
            bundled_path(model_name),
        )
        if model_path is None:
            return model_name
        return str(model_path)

    def predict(self, frame: np.ndarray) -> List[BoxPrediction]:
        return self._infer(frame, track=False)

    def track(self, frame: np.ndarray) -> List[BoxPrediction]:
        return self._infer(frame, track=True)

    def _infer(self, frame: np.ndarray, track: bool) -> List[BoxPrediction]:
        if self._model is None:
            return []

        try:
            if track:
                result = self._model.track(
                    source=frame,
                    persist=True,
                    conf=0.45,
                    iou=0.5,
                    verbose=False,
                    imgsz=256,
                    classes=None,
                    device=self._inference_device,
                    tracker="bytetrack.yaml",
                )[0]
            else:
                result = self._model.predict(
                    source=frame,
                    conf=0.45,
                    verbose=False,
                    imgsz=256,
                    classes=None,
                    device=self._inference_device,
                )[0]
        except Exception:
            return []

        predictions: List[BoxPrediction] = []
        names = result.names
        track_ids = getattr(result.boxes, "id", None)
        for box in result.boxes:
            cls_idx = int(box.cls[0].item())
            confidence = float(box.conf[0].item())
            label = names.get(cls_idx, str(cls_idx))
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            track_id = None
            if track_ids is not None:
                try:
                    track_id = int(track_ids[len(predictions)].item())
                except Exception:
                    track_id = None
            predictions.append(
                BoxPrediction(
                    label=label,
                    confidence=confidence,
                    box=(x1, y1, x2, y2),
                    track_id=track_id,
                )
            )
        return predictions
