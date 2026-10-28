from __future__ import annotations

from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel, model_validator


ProtocolVersion = Literal["2.0"]
SequenceNumber = Annotated[int, Field(ge=0, le=9_007_199_254_740_991, strict=True)]
ElapsedMilliseconds = Annotated[float, Field(ge=0)]
Direction = Literal["left", "center", "right", "unknown"]
ClientStopReason = Literal["user_stop", "app_background", "screen_locked", "client_error"]
SessionState = Literal["created", "active", "stopping", "ended", "interrupted"]
EndReason = Literal[
    "user_stop", "app_background", "screen_locked", "client_error", "connection_lost",
    "auth_expired", "device_revoked", "server_shutdown", "process_restart", "start_timeout",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class UserView(ContractModel):
    user_id: UUID
    state: Literal["active", "disabled"]


class RegisterDevice(ContractModel):
    installation_id: UUID
    label: str = Field(min_length=1, max_length=64)
    app_version: str = Field(min_length=1, max_length=32)
    platform: Literal["android"] = "android"


class DeviceView(ContractModel):
    device_id: UUID
    user_id: UUID
    state: Literal["active", "revoked"]


class SettingsValues(ContractModel):
    speech_enabled: bool
    speech_rate: float = Field(ge=0.5, le=2.0)
    reminder_detail: Literal["normal", "detailed"]


class SettingsUpdate(ContractModel):
    expected_revision: int = Field(ge=0, strict=True)
    values: SettingsValues


class SettingsView(ContractModel):
    schema_version: Literal[1] = 1
    revision: int = Field(ge=0, strict=True)
    values: SettingsValues


class CreateSession(ContractModel):
    device_id: UUID
    protocol_version: ProtocolVersion


class SessionSummary(ContractModel):
    frames_received: int = Field(ge=0, strict=True)
    frames_processed: int = Field(ge=0, strict=True)
    frames_dropped: int = Field(ge=0, strict=True)


class SessionView(ContractModel):
    session_id: UUID
    device_id: UUID
    state: SessionState
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    end_reason: EndReason | None = None
    summary: SessionSummary | None = None

    @model_validator(mode="after")
    def validate_terminal_state(self) -> SessionView:
        terminal = self.state in ("ended", "interrupted")
        if terminal and (self.ended_at is None or self.end_reason is None):
            raise ValueError("terminal sessions require ended_at and end_reason")
        if not terminal and (self.ended_at is not None or self.end_reason is not None):
            raise ValueError("running sessions cannot have terminal fields")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at precedes started_at")
        return self


class SessionPage(ContractModel):
    items: list[SessionView] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, max_length=256)


class StopSession(ContractModel):
    reason: ClientStopReason


ErrorCode = Literal[
    "AUTH_REQUIRED", "AUTH_EXPIRED", "FORBIDDEN", "DEVICE_REVOKED", "NOT_FOUND",
    "SESSION_CLOSED", "CONNECTION_CONFLICT", "UNSUPPORTED_VERSION", "BAD_MESSAGE",
    "BAD_FRAME", "FRAME_TOO_LARGE", "BUSY", "REVISION_CONFLICT", "IDEMPOTENCY_CONFLICT", "MODEL_UNAVAILABLE",
    "INTERNAL_ERROR",
]


class ApiError(ContractModel):
    code: ErrorCode
    message: str = Field(min_length=1, max_length=256)
    retryable: bool
    request_id: UUID


class ErrorResponse(ContractModel):
    error: ApiError


class MessageEnvelope(ContractModel):
    protocol_version: ProtocolVersion
    session_id: UUID
    connection_id: UUID
    seq: SequenceNumber


class FrameMessage(MessageEnvelope):
    type: Literal["sensor.frame"]
    frame_id: UUID
    captured_elapsed_ms: ElapsedMilliseconds
    width: int = Field(gt=0, le=8192, strict=True)
    height: int = Field(gt=0, le=8192, strict=True)
    image_jpeg_b64: str = Field(min_length=4, max_length=4_000_000)


class PingMessage(MessageEnvelope):
    type: Literal["ping"]
    nonce: UUID


class StopMessage(MessageEnvelope):
    type: Literal["session.stop"]
    reason: ClientStopReason


class SessionLimits(ContractModel):
    max_frame_bytes: int = Field(gt=0, le=3_000_000, strict=True)
    max_width: int = Field(gt=0, le=8192, strict=True)
    max_height: int = Field(gt=0, le=8192, strict=True)
    max_pixels: int = Field(gt=0, strict=True)
    upload_fps_limit: float = Field(gt=0, le=60)
    result_ttl_ms: float = Field(gt=0, le=60_000)
    heartbeat_interval_ms: float = Field(gt=0, le=60_000)
    idle_timeout_ms: float = Field(gt=0, le=300_000)

    @model_validator(mode="after")
    def validate_heartbeat(self) -> SessionLimits:
        if self.idle_timeout_ms <= self.heartbeat_interval_ms:
            raise ValueError("idle timeout must exceed heartbeat interval")
        return self


class Capabilities(ContractModel):
    backend: Literal["fake", "real"]
    obstacles: bool
    depth: bool
    blind_road: bool


class ReadyMessage(MessageEnvelope):
    type: Literal["session.ready"]
    seq: Literal[0] = 0
    limits: SessionLimits
    capabilities: Capabilities


class FrameAccepted(MessageEnvelope):
    type: Literal["frame.accepted"]
    frame_id: UUID
    replaced_frame_id: UUID | None = None


class FrameDropped(MessageEnvelope):
    type: Literal["frame.dropped"]
    frame_id: UUID
    reason: Literal["replaced_by_newer_frame", "expired_in_queue", "session_stopped"]
    replacement_frame_id: UUID | None = None

    @model_validator(mode="after")
    def validate_replacement(self) -> FrameDropped:
        if (self.reason == "replaced_by_newer_frame") != (self.replacement_frame_id is not None):
            raise ValueError("replacement id must match the drop reason")
        return self


NormalizedCoordinate = Annotated[float, Field(ge=0, le=1)]


class Detection(ContractModel):
    label: str = Field(min_length=1, max_length=64)
    confidence: float = Field(ge=0, le=1)
    bbox: tuple[NormalizedCoordinate, NormalizedCoordinate, NormalizedCoordinate, NormalizedCoordinate]
    distance_meters: float | None = Field(default=None, gt=0)
    direction: Direction

    @model_validator(mode="after")
    def validate_bbox(self) -> Detection:
        left, top, right, bottom = self.bbox
        if left >= right or top >= bottom:
            raise ValueError("bbox must have positive area")
        return self


class BlindRoad(ContractModel):
    model_config = ConfigDict(json_schema_extra={
        "if": {"properties": {"status": {"enum": ["not_detected", "unavailable"]}}},
        "then": {"properties": {"direction": {"const": "unknown"}, "blocked": {"type": "null"}}},
    })

    status: Literal["detected", "not_detected", "unavailable"]
    direction: Direction
    blocked: bool | None

    @model_validator(mode="after")
    def validate_unavailable(self) -> BlindRoad:
        if self.status != "detected" and (self.direction != "unknown" or self.blocked is not None):
            raise ValueError("a missing blind road has no direction or occupancy estimate")
        return self


class SpeechDirective(ContractModel):
    code: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=256)
    priority: Literal["normal", "urgent"]
    dedup_key: str = Field(min_length=1, max_length=128)


class FrameResult(MessageEnvelope):
    model_config = ConfigDict(json_schema_extra={
        "allOf": [
            {
                "if": {"properties": {"status": {"enum": ["expired", "unavailable"]}}},
                "then": {"properties": {"speech": {"type": "null"}}},
            },
            {
                "if": {"properties": {"status": {"const": "unavailable"}}},
                "then": {"properties": {"hazard_level": {"const": "unknown"}}},
            },
        ],
    })

    type: Literal["frame.result"]
    frame_id: UUID
    captured_elapsed_ms: ElapsedMilliseconds
    status: Literal["ok", "expired", "unavailable"]
    server_processing_ms: ElapsedMilliseconds
    hazard_level: Literal["unknown", "none", "low", "medium", "high"]
    detections: list[Detection] = Field(max_length=100)
    blind_road: BlindRoad
    speech: SpeechDirective | None = None

    @model_validator(mode="after")
    def validate_speech(self) -> FrameResult:
        if self.status != "ok" and self.speech is not None:
            raise ValueError("invalid or expired results cannot request speech")
        if self.status == "unavailable" and self.hazard_level != "unknown":
            raise ValueError("unavailable analysis cannot claim a hazard assessment")
        return self


class PongMessage(MessageEnvelope):
    type: Literal["pong"]
    nonce: UUID
    server_time: AwareDatetime


class StoppedMessage(MessageEnvelope):
    type: Literal["session.stopped"]
    state: Literal["stopping", "ended"]


class ErrorMessage(MessageEnvelope):
    type: Literal["error"]
    error: ApiError


class ClientMessage(RootModel[Annotated[
    Union[FrameMessage, PingMessage, StopMessage], Field(discriminator="type")
]]):
    pass


class ServerMessage(RootModel[Annotated[
    Union[ReadyMessage, FrameAccepted, FrameDropped, FrameResult, PongMessage, StoppedMessage, ErrorMessage],
    Field(discriminator="type"),
]]):
    pass
