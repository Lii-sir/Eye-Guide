from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from eyeguide.domain.models import GpsFix


@dataclass(slots=True)
class GpsSensorPayload:
    lat: float
    lon: float
    accuracy: Optional[float] = None
    speed_mps: float = 0.0
    heading_deg: Optional[float] = None
    altitude: Optional[float] = None


MAX_FRAME_BYTES = 3_000_000


def parse_gps_payload(payload: dict) -> tuple[GpsSensorPayload | None, str | None]:
    lat = payload.get("lat")
    lon = payload.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None, "lat/lon must be numbers"
    if not (-90.0 <= float(lat) <= 90.0 and -180.0 <= float(lon) <= 180.0):
        return None, "lat/lon out of range"

    speed_mps = payload.get("speed_mps", 0.0)
    if not isinstance(speed_mps, (int, float)):
        return None, "speed_mps must be a number"

    accuracy = payload.get("accuracy")
    if accuracy is not None and not isinstance(accuracy, (int, float)):
        return None, "accuracy must be a number"

    heading_deg = payload.get("heading_deg")
    if heading_deg is not None and not isinstance(heading_deg, (int, float)):
        return None, "heading_deg must be a number"

    altitude = payload.get("altitude")
    if altitude is not None and not isinstance(altitude, (int, float)):
        return None, "altitude must be a number"

    return (
        GpsSensorPayload(
            lat=float(lat),
            lon=float(lon),
            accuracy=None if accuracy is None else float(accuracy),
            speed_mps=float(speed_mps),
            heading_deg=None if heading_deg is None else float(heading_deg),
            altitude=None if altitude is None else float(altitude),
        ),
        None,
    )


def build_fix(sensor: GpsSensorPayload, ts: float) -> GpsFix:
    return GpsFix(
        latitude=sensor.lat,
        longitude=sensor.lon,
        speed_mps=sensor.speed_mps,
        accuracy_meters=sensor.accuracy,
        heading_degrees=sensor.heading_deg,
        altitude_meters=sensor.altitude,
        source="phone-web",
        timestamp=ts,
    )


def parse_frame_payload(payload: dict) -> tuple[np.ndarray | None, str | None]:
    encoded = payload.get("image_b64") or payload.get("image")
    if not isinstance(encoded, str) or not encoded.strip():
        return None, "image_b64 is required"

    raw = encoded.strip()
    if raw.startswith("data:"):
        parts = raw.split(",", 1)
        if len(parts) != 2:
            return None, "invalid data URL"
        raw = parts[1]

    try:
        image_bytes = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None, "image_b64 is not valid base64"

    if not image_bytes:
        return None, "decoded image is empty"
    if len(image_bytes) > MAX_FRAME_BYTES:
        return None, f"image too large: {len(image_bytes)} bytes"

    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if frame is None:
        return None, "failed to decode image frame"
    return frame, None
