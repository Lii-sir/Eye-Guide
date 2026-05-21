from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from eyeguide.core.paths import bundled_path


@dataclass
class DepthEstimate:
    depth_map_meters: np.ndarray


class DepthAnythingV2MetricEstimator:
    def __init__(
        self,
        model_name: str = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf",
        preferred_device: str = "auto",
    ) -> None:
        self._model_name = model_name
        self._preferred_device = preferred_device.strip().lower()
        self._load_error: str | None = None
        self._device = self._detect_device()
        self._processor = None
        self._model = None
        self._load()

    @property
    def is_available(self) -> bool:
        return self._processor is not None and self._model is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def device(self) -> str:
        return self._device

    @property
    def model_name(self) -> str:
        return self._model_name

    def _detect_device(self) -> str:
        if self._preferred_device == "cpu":
            return "cpu"
        try:
            import torch

            if self._preferred_device in {"auto", "gpu"} and torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
        if self._preferred_device == "gpu":
            self._load_error = "Depth preferred GPU but CUDA is unavailable; falling back to CPU"
        return "cpu"

    def _load(self) -> None:
        try:
            import torch
            from transformers import AutoImageProcessor, AutoModelForDepthEstimation

            model_source, local_files_only = self._resolve_model_source()
            self._processor = AutoImageProcessor.from_pretrained(
                model_source,
                local_files_only=local_files_only,
            )
            self._model = AutoModelForDepthEstimation.from_pretrained(
                model_source,
                local_files_only=local_files_only,
            )
            self._model = self._model.to(self._device).eval()
            self._torch = torch
        except Exception as exc:
            self._load_error = str(exc)
            self._processor = None
            self._model = None

    def _resolve_model_source(self) -> tuple[str, bool]:
        bundled_model_dir = bundled_path("models", "depth-anything-v2")
        if bundled_model_dir.exists():
            return str(bundled_model_dir), True

        configured_path = Path(self._model_name)
        if configured_path.exists():
            return str(configured_path), True

        return self._model_name, False

    def estimate(self, frame_bgr: np.ndarray) -> DepthEstimate | None:
        if not self.is_available:
            return None

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        try:
            from PIL import Image

            image = Image.fromarray(rgb)
            inputs = self._processor(images=image, return_tensors="pt")
            inputs = {key: value.to(self._device) for key, value in inputs.items()}

            with self._torch.no_grad():
                outputs = self._model(**inputs)
                predicted_depth = outputs.predicted_depth

            prediction = self._torch.nn.functional.interpolate(
                predicted_depth.unsqueeze(1),
                size=frame_bgr.shape[:2],
                mode="bicubic",
                align_corners=False,
            )
            depth_map = prediction.squeeze().detach().cpu().numpy().astype(np.float32)
            return DepthEstimate(depth_map_meters=depth_map)
        except Exception as exc:
            self._load_error = str(exc)
            return None

    def estimate_box_distance(
        self,
        depth_estimate: DepthEstimate | None,
        box: tuple[int, int, int, int],
    ) -> float | None:
        if depth_estimate is None:
            return None

        x1, y1, x2, y2 = box
        depth_map = depth_estimate.depth_map_meters
        height, width = depth_map.shape[:2]
        x1 = max(0, min(width - 1, x1))
        x2 = max(0, min(width, x2))
        y1 = max(0, min(height - 1, y1))
        y2 = max(0, min(height, y2))
        if x2 <= x1 or y2 <= y1:
            return None

        foot_top = y1 + int((y2 - y1) * 0.6)
        foot_region = depth_map[foot_top:y2, x1:x2]
        if foot_region.size == 0:
            return None

        valid = foot_region[np.isfinite(foot_region)]
        if valid.size == 0:
            return None

        percentile_depth = float(np.percentile(valid, 35))
        return round(max(0.1, min(percentile_depth, 20.0)), 2)
