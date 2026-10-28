from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SessionState(str, Enum):
    ACTIVE = "active"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class FrameJob:
    frame_id: str
    seq: int
    client_ts_ms: float
    image_bytes: bytes
    received_ts_ms: float


@dataclass(frozen=True, slots=True)
class VisionResult:
    hazard_level: str
    hazard_summary: str


def is_stale(client_ts_ms: float, server_ts_ms: float, stale_after_ms: float = 500.0) -> bool:
    return max(0.0, server_ts_ms - client_ts_ms) > stale_after_ms
