"""
配置模块：定义 EyeGuide 应用所有配置参数的数据类。

该模块包含应用程序的主要配置类，涵盖视觉处理、语音合成、GPS 定位和路线导航等功能的配置参数。
所有配置类都是不可变的（frozen=True），确保配置在运行时不被意外修改。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VisionConfig:
    """
    视觉处理配置类。

    该类封装了所有与视觉感知和目标检测相关的配置参数，包括摄像头设置、推理模型参数、
    性能优化选项和盲道检测（辅助功能）配置。

    属性：
        default_camera_index: 默认使用的摄像头索引，0 表示第一个摄像头
        window_name: 实时视图窗口的显示名称
        status_emit_interval_seconds: 状态信息发送间隔（秒），用于 UI 更新
        parallel_inference_enabled: 是否启用并行推理以提高性能
        show_performance_overlay: 是否在视窗上显示性能指标的叠加层
        object_speech_dedup_mode: 目标识别结果的去重模式（如"detailed"表示详细去重）
        yolo_device_preference: YOLO 目标检测模型的设备偏好（"gpu" 或 "cpu"）
        yolo_infer_interval_frames: YOLO 推理的帧间隔（每 N 帧进行一次推理以节省计算）
        depth_device_preference: 深度估计模型的设备偏好
        depth_infer_interval_frames: 深度估计的帧间隔
        blind_road_enabled: 是否启用盲道（人行道）检测功能
        blind_road_device_preference: 盲道检测模型的设备偏好（推荐使用 "cpu"）
        blind_road_model_config_path: 盲道检测模型配置文件的路径
        blind_road_weights_dir: 盲道检测模型权重文件所在目录
        blind_road_paddleseg_root: PaddleSeg 深度学习框架的根目录路径
        blind_road_input_size: 盲道检测模型的输入图像尺寸（像素）
        blind_road_roi_top_ratio: 盲道检测的感兴趣区域（ROI）顶部比例，用于裁剪输入
        blind_road_infer_interval_frames: 盲道检测的帧间隔
    """
    default_camera_index: int = 0
    window_name: str = "EyeGuide Live View"
    status_emit_interval_seconds: float = 1.0
    parallel_inference_enabled: bool = True
    show_performance_overlay: bool = True
    object_speech_dedup_mode: str = "detailed"
    yolo_device_preference: str = "gpu"
    yolo_infer_interval_frames: int = 4
    depth_device_preference: str = "gpu"
    depth_infer_interval_frames: int = 4
    blind_road_enabled: bool = True
    blind_road_device_preference: str = "cpu"
    blind_road_model_config_path: str = "models/blind_road_pp_mobileseg_tiny/pp_mobileseg_tiny_blind_road_512x512.yml"
    blind_road_weights_dir: str = "models/blind_road_pp_mobileseg_tiny"
    blind_road_paddleseg_root: str = ".tmp/PaddleSeg"
    blind_road_input_size: int = 512
    blind_road_roi_top_ratio: float = 0.42
    blind_road_infer_interval_frames: int = 5


@dataclass(frozen=True)
class SpeechConfig:
    """
    语音合成配置类。

    该类配置文本转语音（TTS）引擎的各种参数，控制语音生成的方式和输出设备。

    属性：
        rate: 语音播放速率（每分钟字数），默认 185 表示中等速度
        preferred_voice_name: 首选的语音合成引擎和声音名称，默认为微软汉语语音
        preferred_output_name: 首选的音频输出设备名称（扬声器或耳机）
        persistent_dedupe_ttl_seconds: 持久去重的时间生存期（秒），防止在指定时间内重复播放相同消息
    """
    rate: int = 185
    preferred_voice_name: str = "Microsoft Huihui Desktop - Chinese (Simplified)"
    preferred_output_name: str = "EDIFIER Comfo SE"
    persistent_dedupe_ttl_seconds: float = 20.0


@dataclass(frozen=True)
class GpsConfig:
    """
    GPS 定位配置类。

    该类配置 GPS 接收器的通信参数和工作参数，控制与硬件 GPS 模块的交互。

    属性：
        baud_rate: 串口通信波特率（比特每秒），9600 是标准的 GPS 设备速率
        read_timeout_seconds: 串口读取超时时间（秒），等待 GPS 数据的最长时间
        reconnect_interval_seconds: 连接丢失后重新连接的间隔（秒）
        status_emit_interval_seconds: GPS 状态信息发送到应用的间隔（秒）
        windows_fallback_enabled: 是否在 Windows 系统上启用模拟 GPS 数据的备用模式
        windows_refresh_interval_seconds: Windows 备用模式下数据刷新的间隔（秒）
    """
    baud_rate: int = 9600
    read_timeout_seconds: float = 1.0
    reconnect_interval_seconds: float = 2.0
    status_emit_interval_seconds: float = 2.0
    windows_fallback_enabled: bool = True
    windows_refresh_interval_seconds: float = 6.0


@dataclass(frozen=True)
class NavigationConfig:
    """
    路线导航配置类。

    该类配置路线导航过程中的各种参数，用于自动推进导航步骤和处理 GPS 精度问题。

    属性：
        auto_advance_radius_meters: 自动推进到下一导航步骤的距离阈值（米），
                                   当用户到达该半径范围内时自动推进
        auto_advance_cooldown_seconds: 连续自动推进之间的冷却时间（秒），防止频繁切换
        poor_accuracy_threshold_meters: GPS 精度不佳的判定阈值（米），超过该值则认为精度较低
    """
    auto_advance_radius_meters: float = 25.0
    auto_advance_cooldown_seconds: float = 4.0
    poor_accuracy_threshold_meters: float = 60.0


@dataclass(frozen=True)
class AppConfig:
    """
    应用全局配置类。

    该类聚合了应用所有子模块的配置对象，形成一个完整的配置体系。通过组合各个功能模块的配置，
    提供了对整个应用配置的统一管理和访问接口。

    属性：
        vision: 视觉处理配置对象，包含摄像头和目标检测的参数
        speech: 语音合成配置对象，包含 TTS 引擎的参数
        gps: GPS 定位配置对象，包含串口通信和定位的参数
        navigation: 路线导航配置对象，包含导航策略的参数
        ui_poll_interval_ms: UI 界面轮询更新的间隔（毫秒），控制界面刷新频率
    """
    vision: VisionConfig = VisionConfig()
    speech: SpeechConfig = SpeechConfig()
    gps: GpsConfig = GpsConfig()
    navigation: NavigationConfig = NavigationConfig()
    ui_poll_interval_ms: int = 200

