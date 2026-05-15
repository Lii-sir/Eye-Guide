from __future__ import annotations

from typing import Optional

from eyeguide.domain.models import GpsFix


def parse_nmea_sentence(sentence: str) -> Optional[dict[str, object]]:
    if not sentence.startswith("$"):
        return None

    payload = sentence.split("*", 1)[0]
    parts = payload.split(",")
    if len(parts) < 2:
        return None

    return {"type": parts[0][3:], "parts": parts}


def _coord_to_decimal(raw_value: str, direction: str) -> Optional[float]:
    if not raw_value or not direction:
        return None

    degree_width = 2 if direction in {"N", "S"} else 3 if direction in {"E", "W"} else 0
    if degree_width == 0:
        return None

    try:
        degrees = float(raw_value[:degree_width])
        minutes = float(raw_value[degree_width:])
    except ValueError:
        return None

    decimal = degrees + minutes / 60.0
    if direction in {"S", "W"}:
        decimal = -decimal
    return decimal


def _knots_to_mps(value: str) -> float:
    try:
        return float(value) * 0.514444
    except ValueError:
        return 0.0


def build_fix_from_rmc(parts: list[str], previous_fix: GpsFix | None = None) -> Optional[GpsFix]:
    if len(parts) < 9 or parts[2] != "A":
        return None

    latitude = _coord_to_decimal(parts[3], parts[4])
    longitude = _coord_to_decimal(parts[5], parts[6])
    if latitude is None or longitude is None:
        return None

    try:
        heading = float(parts[8]) if parts[8] else None
    except ValueError:
        heading = None

    return GpsFix(
        latitude=latitude,
        longitude=longitude,
        speed_mps=_knots_to_mps(parts[7]),
        heading_degrees=heading,
        altitude_meters=previous_fix.altitude_meters if previous_fix else None,
        satellites=previous_fix.satellites if previous_fix else None,
        source="nmea-rmc",
    )


def build_fix_from_gll(parts: list[str], previous_fix: GpsFix | None = None) -> Optional[GpsFix]:
    if len(parts) < 7 or parts[6] != "A":
        return None

    latitude = _coord_to_decimal(parts[1], parts[2])
    longitude = _coord_to_decimal(parts[3], parts[4])
    if latitude is None or longitude is None:
        return None

    return GpsFix(
        latitude=latitude,
        longitude=longitude,
        speed_mps=previous_fix.speed_mps if previous_fix else 0.0,
        heading_degrees=previous_fix.heading_degrees if previous_fix else None,
        altitude_meters=previous_fix.altitude_meters if previous_fix else None,
        satellites=previous_fix.satellites if previous_fix else None,
        source="nmea-gll",
    )


def update_fix_from_gga(parts: list[str], previous_fix: GpsFix | None = None) -> Optional[GpsFix]:
    if len(parts) < 10:
        return previous_fix

    try:
        fix_quality = int(parts[6]) if parts[6] else 0
    except ValueError:
        fix_quality = 0
    if fix_quality <= 0:
        return previous_fix

    latitude = _coord_to_decimal(parts[2], parts[3])
    longitude = _coord_to_decimal(parts[4], parts[5])
    if latitude is None or longitude is None:
        return previous_fix

    try:
        satellites = int(parts[7]) if parts[7] else None
    except ValueError:
        satellites = None

    try:
        altitude = float(parts[9]) if parts[9] else None
    except ValueError:
        altitude = None

    return GpsFix(
        latitude=latitude,
        longitude=longitude,
        speed_mps=previous_fix.speed_mps if previous_fix else 0.0,
        heading_degrees=previous_fix.heading_degrees if previous_fix else None,
        altitude_meters=altitude,
        satellites=satellites,
        source="nmea-gga",
    )
