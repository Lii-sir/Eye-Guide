from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VisionConfig:
    default_camera_index: int = 0
    window_name: str = "EyeGuide Live View"
    status_emit_interval_seconds: float = 1.0
    parallel_inference_enabled: bool = True
    show_performance_overlay: bool = True
    yolo_device_preference: str = "gpu"
    yolo_infer_interval_frames: int = 4
    depth_device_preference: str = "gpu"
    depth_infer_interval_frames: int = 4
    blind_road_enabled: bool = True
    blind_road_device_preference: str = "cpu"
    blind_road_model_config_path: str = "training/paddleseg/pp_mobileseg_tiny_blind_road_512x512.yml"
    blind_road_weights_dir: str = "training/paddleseg/output/blind_road_pp_mobileseg_tiny/best_model"
    blind_road_paddleseg_root: str = ".tmp/PaddleSeg"
    blind_road_input_size: int = 512
    blind_road_roi_top_ratio: float = 0.42
    blind_road_infer_interval_frames: int = 5


@dataclass(frozen=True)
class SpeechConfig:
    rate: int = 185
    preferred_voice_name: str = "Microsoft Huihui Desktop - Chinese (Simplified)"
    preferred_output_name: str = "EDIFIER Comfo SE"


@dataclass(frozen=True)
class GpsConfig:
    baud_rate: int = 9600
    read_timeout_seconds: float = 1.0
    reconnect_interval_seconds: float = 2.0
    status_emit_interval_seconds: float = 2.0
    windows_fallback_enabled: bool = True
    windows_refresh_interval_seconds: float = 6.0


@dataclass(frozen=True)
class NavigationConfig:
    auto_advance_radius_meters: float = 25.0
    auto_advance_cooldown_seconds: float = 4.0
    poor_accuracy_threshold_meters: float = 60.0


@dataclass(frozen=True)
class AppConfig:
    vision: VisionConfig = VisionConfig()
    speech: SpeechConfig = SpeechConfig()
    gps: GpsConfig = GpsConfig()
    navigation: NavigationConfig = NavigationConfig()
    ui_poll_interval_ms: int = 200
