from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(slots=True)
class Envelope:
    type: str
    device_id: str
    seq: int
    ts: float
    payload: dict[str, Any]
    session_id: Optional[str] = None


@dataclass(slots=True)
class ValidationErrorDetail:
    code: str
    message: str


def parse_envelope(data: dict[str, Any]) -> tuple[Envelope | None, ValidationErrorDetail | None]:
    if not isinstance(data, dict):
        return None, ValidationErrorDetail(code="BAD_REQUEST", message="payload must be an object")

    required_keys = ("type", "device_id", "seq", "ts")
    missing = [key for key in required_keys if key not in data]
    if missing:
        return None, ValidationErrorDetail(
            code="MISSING_FIELDS",
            message=f"missing required fields: {', '.join(missing)}",
        )

    msg_type = data.get("type")
    device_id = data.get("device_id")
    seq = data.get("seq")
    ts = data.get("ts")
    payload = data.get("payload", {})

    if not isinstance(msg_type, str) or not msg_type.strip():
        return None, ValidationErrorDetail(code="BAD_TYPE", message="type must be non-empty string")
    if not isinstance(device_id, str) or not device_id.strip():
        return None, ValidationErrorDetail(code="BAD_DEVICE_ID", message="device_id must be non-empty string")
    if not isinstance(seq, int) or seq < 0:
        return None, ValidationErrorDetail(code="BAD_SEQ", message="seq must be integer >= 0")
    if not isinstance(ts, (int, float)):
        return None, ValidationErrorDetail(code="BAD_TS", message="ts must be number")
    if not isinstance(payload, dict):
        return None, ValidationErrorDetail(code="BAD_PAYLOAD", message="payload must be object")

    return (
        Envelope(
            type=msg_type.strip(),
            session_id=(data.get("session_id") or None),
            device_id=device_id.strip(),
            seq=seq,
            ts=float(ts),
            payload=payload,
        ),
        None,
    )

