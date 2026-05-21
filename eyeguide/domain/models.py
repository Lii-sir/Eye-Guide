from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple
import time


class Mode(str, Enum):
    EXPLORE = "自由探索"
    NAVIGATION = "路线导航"
    SIMULATION = "模拟测试"


@dataclass
class DetectionEvent:
    message: str
    category: str
    dedupe_key: Optional[str] = None
    channel: str = "vision"
    priority: int = 5
    cooldown_seconds: float = 4.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class DetectionBox:
    label: str
    box: Tuple[int, int, int, int]
    confidence: float = 0.0
    track_id: Optional[int] = None
    distance_meters: Optional[float] = None
    relative_direction: Optional[str] = None


@dataclass
class RouteStep:
    instruction: str
    distance_meters: float = 0.0
    road_name: str = ""
    target: Optional["GeoPoint"] = None
    arrival_radius_meters: float = 25.0


@dataclass
class RoutePlan:
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
    events: List[DetectionEvent] = field(default_factory=list)
    boxes: List[DetectionBox] = field(default_factory=list)
    overlays: List[str] = field(default_factory=list)
    hazard_summary: Optional[str] = None
    blind_road_detected: bool = False
    blind_road_blocked: bool = False
    blind_road_direction: Optional[str] = None


@dataclass
class GpsFix:
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
        return -90.0 <= self.latitude <= 90.0 and -180.0 <= self.longitude <= 180.0


@dataclass
class GeoPoint:
    latitude: float
    longitude: float
