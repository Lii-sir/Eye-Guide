"""
域模型模块：定义 EyeGuide 应用的核心业务数据模型。

该模块包含应用所有主要的数据模型，涵盖应用运行模式、目标检测结果、路线导航信息、
GPS 位置数据等。这些模型是应用各个功能模块间的数据交换格式，也是业务逻辑的基础。

主要数据类：
- Mode: 应用工作模式枚举
- DetectionEvent: 目标检测事件
- DetectionBox: 检测到的对象边界框
- RouteStep: 路线导航中的单个步骤
- RoutePlan: 完整的路线规划
- FrameAnalysis: 视频帧分析结果
- GpsFix: GPS 定位数据
- GeoPoint: 地理坐标点
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple
import time


class Mode(str, Enum):
    """
    应用工作模式枚举。

    定义了应用可以运行的三种主要工作模式，用于指导应用的行为和功能启用。

    模式：
        EXPLORE: 自由探索模式 - 用户可以自由移动和浏览周围环境，应用提供视觉信息和环境感知
        NAVIGATION: 路线导航模式 - 应用引导用户按照预定路线行进，提供转向和到达提示
        SIMULATION: 模拟测试模式 - 用于测试和演示，使用预录数据或模拟数据而非真实传感器数据
    """
    EXPLORE = "自由探索"
    NAVIGATION = "路线导航"
    SIMULATION = "模拟测试"


@dataclass
class DetectionEvent:
    """
    目标检测事件数据类。

    表示视觉系统检测到的对象或场景变化，包含事件的文本描述、分类、优先级和去重策略等元数据。
    这些事件会被转换为语音提示或其他通知方式播放给用户。

    属性：
        message: 检测事件的文本描述，将被转换为语音播放给用户（如 "前方有行人" ）
        category: 检测事件的分类标签，用于组织和管理事件类型（如 "pedestrian", "vehicle" 等）
        dedupe_key: 去重键值，用于识别相同的事件以避免重复播放，为 None 时表示不需要去重
        channel: 事件所属的通道，用于不同来源的事件分类，默认为 "vision"（视觉通道）
        priority: 事件优先级（0-10），值越高优先级越高，默认 5 为中等优先级
        cooldown_seconds: 该事件类型的冷却时间（秒），在冷却期内相同事件不会重复播放
        persistent_dedupe: 是否启用持久去重模式，在长时间内记忆已播放的事件以避免重复
        timestamp: 事件发生的时间戳（UNIX 时间），用于跟踪事件的时间顺序
    """
    message: str
    category: str
    dedupe_key: Optional[str] = None
    channel: str = "vision"
    priority: int = 5
    cooldown_seconds: float = 4.0
    persistent_dedupe: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class DetectionBox:
    """
    检测到的对象边界框数据类。

    表示目标检测模型输出的单个检测结果，包含对象的位置、大小、类别和置信度等信息。

    属性：
        label: 检测对象的类别标签（如 "person", "car", "bicycle" 等）
        box: 对象边界框的坐标，格式为 (x_min, y_min, x_max, y_max)，单位为像素
        confidence: 检测置信度（0.0-1.0），表示模型对该检测结果的确定程度，1.0 表示 100% 确定
        track_id: 目标追踪 ID，用于在连续的视频帧中识别同一对象，为 None 表示未进行追踪
        distance_meters: 估计的对象距离（米），通过深度估计得出，为 None 表示距离无法估计
        relative_direction: 对象相对于用户的方向描述（如 "左前方", "正前方" 等），为 None 表示未计算
    """
    label: str
    box: Tuple[int, int, int, int]
    confidence: float = 0.0
    track_id: Optional[int] = None
    distance_meters: Optional[float] = None
    relative_direction: Optional[str] = None


@dataclass
class RouteStep:
    """
    路线导航中的单个步骤数据类。

    表示导航路线中的一个段落，包含该段落的文本指令、距离、道路名称和目标位置等信息。

    属性：
        instruction: 导航指令文本，包含转向方向和其他导航信息（如 "向左转进入中山路" ）
        distance_meters: 本步骤的距离（米），即行进到下一步骤需要走过的距离
        road_name: 当前所在或将要进入的道路名称
        target: 本步骤的目标位置，为一个 GeoPoint 对象，为 None 表示无特定目标坐标
        arrival_radius_meters: 判定到达该步骤目标的距离范围（米），在 GPS 位置进入该范围时
                             自动推进到下一步骤，默认 25 米
    """
    instruction: str
    distance_meters: float = 0.0
    road_name: str = ""
    target: Optional["GeoPoint"] = None
    arrival_radius_meters: float = 25.0


@dataclass
class RoutePlan:
    """
    完整的路线规划数据类。

    表示一条完整的导航路线，包含起点、终点、路线步骤和其他相关信息。

    属性：
        origin: 起始地点名称或地址
        destination: 目标地点名称或地址
        steps: 路线的步骤列表，每个元素是一个 RouteStep 对象
        source: 路线数据的来源（如 "baidu_maps", "amap" 等），用于标记使用的地图服务
        travel_mode: 出行方式（如 "foot" 步行, "car" 驾车, "bus" 公交等），默认为步行
        resolved_origin_address: 解析后的起始地点完整地址
        resolved_destination_address: 解析后的目标地点完整地址
        distance_meters: 整条路线的总距离（米）
        duration_seconds: 预计路线耗时（秒）
    """
    origin: str
    destination: str
    steps: List[RouteStep]
    source: str
    travel_mode: str = "foot"
    resolved_origin_address: str = ""
    resolved_destination_address: str = ""
    distance_meters: float = 0.0
    duration_seconds: float = 0.0


@dataclass
class FrameAnalysis:
    """
    视频帧分析结果数据类。

    表示对一帧视频进行全面分析后的结果，包含检测事件、边界框、盲道检测结果等多种信息。
    这是视觉处理管道的输出，包含了一帧中发现的所有有价值的信息。

    属性：
        events: 该帧检测到的事件列表，每个元素是一个 DetectionEvent 对象
        boxes: 该帧检测到的对象边界框列表，每个元素是一个 DetectionBox 对象
        overlays: 用于渲染的文本覆盖层列表，包含要在视频预览窗口中显示的调试或信息文本
        hazard_summary: 该帧中检测到的危险情况汇总文本，为 None 表示无明显危险
        blind_road_detected: 是否检测到盲道（人行道）
        blind_road_blocked: 检测到的盲道是否被阻挡
        blind_road_direction: 盲道方向（相对用户的位置），为 None 表示未检测或方向无法确定
    """
    events: List[DetectionEvent] = field(default_factory=list)
    boxes: List[DetectionBox] = field(default_factory=list)
    overlays: List[str] = field(default_factory=list)
    hazard_summary: Optional[str] = None
    blind_road_detected: bool = False
    blind_road_blocked: bool = False
    blind_road_direction: Optional[str] = None


@dataclass
class GpsFix:
    """
    GPS 定位数据类。

    表示一次 GPS 定位结果，包含位置坐标、精度、速度、方向等多维定位信息。
    GPS 接收器会周期性输出 GpsFix 对象，应用基于这些数据进行路线导航和位置追踪。

    属性：
        latitude: 纬度（-90 到 90 度，负数表示南纬）
        longitude: 经度（-180 到 180 度，负数表示西经）
        speed_mps: 运动速度（米/秒），0 表示静止
        accuracy_meters: GPS 定位精度（米），表示实际位置可能的误差范围，为 None 表示未知
        heading_degrees: 运动方向（0-360 度，0 度表示北方），为 None 表示无法确定或静止
        altitude_meters: 海拔高度（米），为 None 表示未获取
        satellites: 用于定位的卫星数量，为 None 表示未知，通常 4 颗及以上卫星才能确定位置
        source: 定位数据源（如 "gps", "aiding", "simulation" 等），默认为 "gps"
        timestamp: 定位数据的时间戳（UNIX 时间）
    """
    latitude: float
    longitude: float
    speed_mps: float = 0.0
    accuracy_meters: Optional[float] = None
    heading_degrees: Optional[float] = None
    altitude_meters: Optional[float] = None
    satellites: Optional[int] = None
    source: str = "gps"
    timestamp: float = field(default_factory=time.time)

    @property
    def is_valid(self) -> bool:
        """
        检查 GPS 定位数据是否有效。

        通过验证经纬度是否在有效范围内来判断定位数据的合法性。

        返回值：
            bool: 如果纬度在 [-90, 90] 范围内且经度在 [-180, 180] 范围内，返回 True；
                  否则返回 False
        """
        return -90.0 <= self.latitude <= 90.0 and -180.0 <= self.longitude <= 180.0


@dataclass
class GeoPoint:
    """
    地理坐标点数据类。

    表示地球表面上的一个点，使用标准的 WGS84 坐标系统。用于标记特定的地理位置，
    如路线中的路由点、目标位置等。

    属性：
        latitude: 纬度（-90 到 90 度）
        longitude: 经度（-180 到 180 度）
    """
    latitude: float
    longitude: float

