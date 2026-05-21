from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from eyeguide.core.config import VisionConfig
from eyeguide.core.paths import app_root, first_existing_path
from eyeguide.domain.models import DetectionBox, DetectionEvent, FrameAnalysis


BLIND_ROAD_CLASS_ID = 1
MIN_ROAD_AREA_RATIO = 0.015
LOWER_ROAD_SCAN_RATIO = 0.58
ROAD_CENTER_TOLERANCE = 0.08
BLOCKING_LABELS = {
    "bicycle",
    "motorcycle",
    "car",
    "chair",
    "bench",
    "suitcase",
    "backpack",
    "potted plant",
    "ground_obstacle",
}
BLOCKING_LABEL_TEXT = {
    "bicycle": "自行车",
    "motorcycle": "摩托车",
    "car": "汽车",
    "chair": "椅子",
    "bench": "长凳",
    "suitcase": "行李箱",
    "backpack": "背包",
    "potted plant": "盆栽",
    "ground_obstacle": "障碍物",
}


@dataclass(slots=True)
class BlindRoadEstimate:
    mask: np.ndarray
    area_ratio: float
    center_ratio: float | None
    direction_text: str | None
    is_centered: bool
    blocking_box: DetectionBox | None = None


class BlindRoadSegmenter:
    def __init__(self, config: VisionConfig) -> None:
        self._config = config
        self._load_error: str | None = None
        self._device = "cpu"
        self._paddle = None
        self._model = None
        self._last_estimate: BlindRoadEstimate | None = None
        self._last_shape: tuple[int, int] | None = None
        self._mean = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
        self._std = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)
        self._load()

    @property
    def is_available(self) -> bool:
        return self._model is not None and self._paddle is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def device(self) -> str:
        return self._device

    def enrich_analysis(
        self,
        frame: np.ndarray,
        boxes: list[DetectionBox],
        analysis: FrameAnalysis,
        frame_index: int,
        estimate: BlindRoadEstimate | None = None,
    ) -> None:
        estimate = estimate if estimate is not None else self._get_estimate(frame, frame_index)
        if estimate is None:
            analysis.overlays.append("盲道: 不可用")
            return

        if estimate.area_ratio > 0 and estimate.direction_text is not None:
            analysis.blind_road_detected = True
            analysis.blind_road_direction = estimate.direction_text

        estimate.blocking_box = self._find_blocking_box(estimate.mask, boxes)
        analysis.blind_road_blocked = estimate.blocking_box is not None

        self._draw_mask_overlay(frame, estimate.mask)
        self._draw_direction_indicator(frame, estimate)
        self._append_overlay_text(analysis, estimate)
        self._append_events(analysis, estimate)

    def estimate(self, frame: np.ndarray, frame_index: int) -> BlindRoadEstimate | None:
        return self._get_estimate(frame, frame_index)

    def _get_estimate(self, frame: np.ndarray, frame_index: int) -> BlindRoadEstimate | None:
        if not self.is_available:
            return None

        frame_shape = frame.shape[:2]
        if (
            self._last_estimate is not None
            and self._last_shape == frame_shape
            and frame_index % max(1, self._config.blind_road_infer_interval_frames) != 1
        ):
            return BlindRoadEstimate(
                mask=self._last_estimate.mask.copy(),
                area_ratio=self._last_estimate.area_ratio,
                center_ratio=self._last_estimate.center_ratio,
                direction_text=self._last_estimate.direction_text,
                is_centered=self._last_estimate.is_centered,
            )

        estimate = self._infer(frame)
        if estimate is not None:
            self._last_estimate = estimate
            self._last_shape = frame_shape
        return estimate

    def _load(self) -> None:
        if not self._config.blind_road_enabled:
            self._load_error = "Blind road segmentation disabled"
            return

        config_path = self._resolve_config_path()
        model_path = self._resolve_model_path()
        paddleseg_root = self._resolve_paddleseg_root()
        if config_path is None or model_path is None or paddleseg_root is None:
            self._load_error = "Blind road config, weights, or PaddleSeg root not found"
            return

        try:
            if str(paddleseg_root) not in sys.path:
                sys.path.insert(0, str(paddleseg_root))

            paddle = importlib.import_module("paddle")
            cvlibs = importlib.import_module("paddleseg.cvlibs")
            seg_utils = importlib.import_module("paddleseg.utils.utils")

            self._device = self._resolve_device(paddle)
            paddle.set_device(self._device)

            cfg = cvlibs.Config(str(config_path))
            cfg.dic["model"]["backbone"]["pretrained"] = None
            builder = cvlibs.SegBuilder(cfg)
            model = builder.model
            seg_utils.load_entire_model(model, str(model_path))
            model.eval()

            self._paddle = paddle
            self._model = model
        except Exception as exc:  # pragma: no cover
            self._load_error = str(exc)
            self._paddle = None
            self._model = None

    def _resolve_device(self, paddle_module) -> str:
        preference = self._config.blind_road_device_preference.strip().lower()
        if preference == "cpu":
            return "cpu"
        gpu_available = (
            paddle_module.is_compiled_with_cuda()
            and paddle_module.device.cuda.device_count() > 0
        )
        if preference in {"auto", "gpu"} and gpu_available:
            return "gpu"
        if preference == "gpu":
            self._load_error = "Blind road preferred GPU but CUDA is unavailable; falling back to CPU"
        return "cpu"

    def _resolve_config_path(self) -> Path | None:
        return first_existing_path(
            Path(self._config.blind_road_model_config_path),
            app_root() / self._config.blind_road_model_config_path,
        )

    def _resolve_model_path(self) -> Path | None:
        model_dir = first_existing_path(
            Path(self._config.blind_road_weights_dir),
            app_root() / self._config.blind_road_weights_dir,
        )
        if model_dir is None:
            return None
        model_path = Path(model_dir) / "model.pdparams"
        return model_path if model_path.exists() else None

    def _resolve_paddleseg_root(self) -> Path | None:
        return first_existing_path(
            Path(self._config.blind_road_paddleseg_root),
            app_root() / self._config.blind_road_paddleseg_root,
        )

    def _infer(self, frame: np.ndarray) -> BlindRoadEstimate | None:
        assert self._paddle is not None
        assert self._model is not None

        height, width = frame.shape[:2]
        roi_top = self._resolve_roi_top(height)
        roi = frame[roi_top:, :]
        if roi.size == 0:
            roi_top = 0
            roi = frame

        resized = cv2.resize(
            roi,
            (self._config.blind_road_input_size, self._config.blind_road_input_size),
            interpolation=cv2.INTER_LINEAR,
        )
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        rgb = (rgb - self._mean) / self._std
        tensor = self._paddle.to_tensor(rgb.transpose(2, 0, 1)[None, ...], dtype="float32")

        with self._paddle.no_grad():
            output = self._model(tensor)[0]
            pred = self._paddle.argmax(output, axis=1) if len(output.shape) == 4 else output

        roi_mask = pred.cpu().numpy()[0].astype(np.uint8)
        roi_mask = (roi_mask == BLIND_ROAD_CLASS_ID).astype(np.uint8) * 255
        roi_mask = cv2.resize(roi_mask, (width, height - roi_top), interpolation=cv2.INTER_NEAREST)
        roi_mask = self._postprocess_mask(roi_mask)

        mask = np.zeros((height, width), dtype=np.uint8)
        mask[roi_top:, :] = roi_mask

        if not np.any(mask):
            return BlindRoadEstimate(
                mask=mask,
                area_ratio=0.0,
                center_ratio=None,
                direction_text=None,
                is_centered=False,
            )

        area_ratio = float(np.count_nonzero(mask)) / float(mask.size)
        center_ratio = self._estimate_center_ratio(mask)
        if area_ratio < MIN_ROAD_AREA_RATIO or center_ratio is None:
            return BlindRoadEstimate(
                mask=np.zeros_like(mask),
                area_ratio=area_ratio,
                center_ratio=center_ratio,
                direction_text=None,
                is_centered=False,
            )

        direction_text = self._direction_from_center(center_ratio)
        return BlindRoadEstimate(
            mask=mask,
            area_ratio=area_ratio,
            center_ratio=center_ratio,
            direction_text=direction_text,
            is_centered=abs(center_ratio - 0.5) <= ROAD_CENTER_TOLERANCE,
        )

    def _resolve_roi_top(self, frame_height: int) -> int:
        ratio = min(max(self._config.blind_road_roi_top_ratio, 0.0), 0.9)
        return int(round(frame_height * ratio))

    def _postprocess_mask(self, mask: np.ndarray) -> np.ndarray:
        kernel = np.ones((5, 5), np.uint8)
        refined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        refined = cv2.morphologyEx(refined, cv2.MORPH_OPEN, kernel, iterations=1)
        contours, _ = cv2.findContours(refined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return np.zeros_like(mask)

        largest = max(contours, key=cv2.contourArea)
        filled = np.zeros_like(mask)
        cv2.drawContours(filled, [largest], -1, 255, thickness=cv2.FILLED)
        return filled

    def _estimate_center_ratio(self, mask: np.ndarray) -> float | None:
        height, width = mask.shape[:2]
        scan_top = int(height * LOWER_ROAD_SCAN_RATIO)
        scan_region = mask[scan_top:, :]
        points = np.column_stack(np.nonzero(scan_region))
        if points.size == 0:
            return None
        x_mean = float(points[:, 1].mean())
        return x_mean / float(max(width - 1, 1))

    def _direction_from_center(self, center_ratio: float) -> str:
        if center_ratio < 0.22:
            return "左侧"
        if center_ratio < 0.42:
            return "左前方"
        if center_ratio <= 0.58:
            return "正前方"
        if center_ratio <= 0.78:
            return "右前方"
        return "右侧"

    def _find_blocking_box(self, mask: np.ndarray, boxes: list[DetectionBox]) -> DetectionBox | None:
        road_pixels = mask > 0
        road_area = int(np.count_nonzero(road_pixels))
        if road_area == 0:
            return None

        best_box: DetectionBox | None = None
        best_score = 0.0
        height, width = mask.shape[:2]
        for box in boxes:
            if box.label not in BLOCKING_LABELS:
                continue
            if box.distance_meters is not None and box.distance_meters > 3.0:
                continue

            x1, y1, x2, y2 = box.box
            x1 = max(0, min(width - 1, x1))
            x2 = max(0, min(width, x2))
            y1 = max(0, min(height - 1, y1))
            y2 = max(0, min(height, y2))
            if x2 <= x1 or y2 <= y1:
                continue

            overlap_pixels = int(np.count_nonzero(road_pixels[y1:y2, x1:x2]))
            if overlap_pixels == 0:
                continue

            box_area = max(1, (x2 - x1) * (y2 - y1))
            overlap_box_ratio = overlap_pixels / float(box_area)
            overlap_road_ratio = overlap_pixels / float(road_area)
            score = overlap_box_ratio * 0.7 + overlap_road_ratio * 0.3
            if overlap_box_ratio < 0.12 and overlap_road_ratio < 0.02:
                continue
            if score > best_score:
                best_score = score
                best_box = box
        return best_box

    def _append_overlay_text(self, analysis: FrameAnalysis, estimate: BlindRoadEstimate) -> None:
        if estimate.area_ratio <= 0 or estimate.direction_text is None:
            analysis.overlays.append("盲道: 未检测到")
            return

        analysis.overlays.append(f"盲道: {'居中' if estimate.is_centered else estimate.direction_text}")
        if estimate.blocking_box is not None:
            obstacle_text = BLOCKING_LABEL_TEXT.get(
                estimate.blocking_box.label,
                estimate.blocking_box.label,
            )
            analysis.overlays.append(f"盲道占用: {obstacle_text}")

    def _append_events(self, analysis: FrameAnalysis, estimate: BlindRoadEstimate) -> None:
        if estimate.area_ratio <= 0 or estimate.direction_text is None:
            return

        if estimate.blocking_box is not None:
            obstacle_text = BLOCKING_LABEL_TEXT.get(
                estimate.blocking_box.label,
                estimate.blocking_box.label,
            )
            if estimate.blocking_box.distance_meters is not None:
                message = (
                    f"注意，盲道在{estimate.direction_text}，有{obstacle_text}占用，"
                    f"约{estimate.blocking_box.distance_meters:.1f}米"
                )
            else:
                message = f"注意，盲道在{estimate.direction_text}，有{obstacle_text}占用"
            analysis.events.append(
                DetectionEvent(
                    message=message,
                    category=f"blind_road:blocked:{estimate.blocking_box.label}:{estimate.direction_text}",
                    dedupe_key=f"blind_road:blocked:{estimate.blocking_box.label}:{estimate.direction_text}",
                    channel="blind_road",
                    priority=1,
                    cooldown_seconds=3.0,
                )
            )
            return

        analysis.events.append(
            DetectionEvent(
                message=f"注意，盲道在{estimate.direction_text}",
                category=f"blind_road:direction:{estimate.direction_text}",
                dedupe_key=f"blind_road:direction:{estimate.direction_text}",
                channel="blind_road",
                priority=2,
                cooldown_seconds=2.5,
            )
        )

    def _draw_mask_overlay(self, frame: np.ndarray, mask: np.ndarray) -> None:
        if not np.any(mask):
            return

        overlay = frame.copy()
        overlay[mask > 0] = (30, 170, 255)
        frame[:] = cv2.addWeighted(overlay, 0.28, frame, 0.72, 0)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(frame, contours, -1, (0, 215, 255), thickness=2)

    def _draw_direction_indicator(self, frame: np.ndarray, estimate: BlindRoadEstimate) -> None:
        if estimate.center_ratio is None or estimate.area_ratio <= 0:
            return

        height, width = frame.shape[:2]
        center_x = int(round(estimate.center_ratio * (width - 1)))
        line_top = int(height * 0.52)
        line_bottom = height - 12
        color = (40, 220, 40) if estimate.is_centered else (0, 220, 255)
        cv2.line(frame, (center_x, line_top), (center_x, line_bottom), color, 3)
        cv2.circle(frame, (center_x, line_bottom - 4), 7, color, -1)
