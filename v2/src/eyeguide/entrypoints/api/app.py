from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import math
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from eyeguide.adapters.fake import FakeVisionPipeline, SystemClock
from eyeguide.adapters.sqlite_store import RevisionConflict, SqliteStore
from eyeguide.application.session_service import SessionService
from eyeguide.runtime.session_manager import SessionManager


MAX_FRAME_BYTES = 3_000_000


class ClientEnvelope(BaseModel):
    type: str
    device_id: str = Field(min_length=1)
    seq: int = Field(ge=0)
    ts: float
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class FramePayload(BaseModel):
    frame_id: str = Field(min_length=1)
    client_ts: float | None = None
    image_b64: str | None = None
    image: str | None = None


class SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["osm", "amap"] = "osm"
    object_speech_dedup_mode: Literal["simple", "detailed"] = "simple"


class SettingsUpdate(BaseModel):
    expected_revision: int = Field(ge=0)
    payload: SettingsPayload


def ok_message(message_type: str, payload: dict[str, Any], seq: int | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"type": message_type, "ok": True, "payload": payload}
    if seq is not None:
        message["seq"] = seq
    return message


def error_message(code: str, message: str, seq: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "error", "ok": False, "payload": {"code": code, "message": message}
    }
    if seq is not None:
        body["seq"] = seq
    return body


def is_local_client(host: str | None) -> bool:
    if host == "testclient":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def decode_frame(payload: FramePayload) -> bytes:
    raw = payload.image_b64 or payload.image
    if not raw:
        raise ValueError("image_b64 is required")
    if raw.startswith("data:"):
        parts = raw.split(",", 1)
        if len(parts) != 2:
            raise ValueError("invalid data URL")
        raw = parts[1]
    if len(raw) > MAX_FRAME_BYTES * 4 // 3 + 8:
        raise ValueError("image is too large")
    try:
        image_bytes = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("image_b64 is not valid base64") from exc
    if not image_bytes:
        raise ValueError("decoded image is empty")
    if len(image_bytes) > MAX_FRAME_BYTES:
        raise ValueError("image is too large")
    return image_bytes


def create_app(manager: SessionManager | None = None, store: SqliteStore | None = None) -> FastAPI:
    clock = SystemClock()
    session_manager = manager or SessionManager(FakeVisionPipeline, clock)
    session_service = SessionService(clock)
    finalizers: set[asyncio.Task] = set()
    if store is not None:
        store.initialize()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        yield
        if finalizers:
            await asyncio.wait(finalizers, timeout=5)

    app = FastAPI(title="EyeGuide V2 API", version="0.1.0", lifespan=lifespan)
    app.state.storage_errors = 0

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, Any]:
        if not is_local_client(request.client.host if request.client else None):
            raise HTTPException(403, "local access only")
        return {
            "ok": True,
            "backend": "fake",
            "active_sessions": session_manager.active_count,
            "storage": "sqlite" if store is not None else "disabled",
            "storage_errors": app.state.storage_errors,
        }

    @app.get("/settings")
    async def get_settings(request: Request) -> dict[str, Any]:
        if not is_local_client(request.client.host if request.client else None):
            raise HTTPException(403, "local access only")
        if store is None:
            raise HTTPException(503, "SQLite storage is not configured")
        try:
            return await asyncio.to_thread(store.read_settings)
        except sqlite3.Error as exc:
            raise HTTPException(503, "settings are unavailable") from exc

    @app.put("/settings")
    async def put_settings(update: SettingsUpdate, request: Request) -> dict[str, Any]:
        if not is_local_client(request.client.host if request.client else None):
            raise HTTPException(403, "local access only")
        if store is None:
            raise HTTPException(503, "SQLite storage is not configured")
        try:
            return await asyncio.to_thread(
                store.save_settings, update.expected_revision, update.payload.model_dump()
            )
        except RevisionConflict as exc:
            raise HTTPException(409, "settings revision conflict") from exc
        except sqlite3.Error as exc:
            raise HTTPException(503, "settings were not saved") from exc

    @app.get("/sessions")
    async def get_sessions(request: Request) -> list[dict[str, Any]]:
        if not is_local_client(request.client.host if request.client else None):
            raise HTTPException(403, "local access only")
        if store is None:
            raise HTTPException(503, "SQLite storage is not configured")
        try:
            return await asyncio.to_thread(store.list_sessions)
        except sqlite3.Error as exc:
            raise HTTPException(503, "sessions are unavailable") from exc

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        if not is_local_client(websocket.client.host if websocket.client else None):
            await websocket.close(code=1008)
            return
        session_id = str(uuid.uuid4())
        if store is not None:
            try:
                await asyncio.to_thread(store.start_session, session_id)
            except sqlite3.Error:
                app.state.storage_errors += 1
                await websocket.close(code=1011)
                return
        await websocket.accept()
        send_lock = asyncio.Lock()
        connected = True

        async def send(message: dict[str, Any]) -> None:
            async with send_lock:
                if connected:
                    await websocket.send_json(message)

        runtime = session_manager.create(session_id, send)
        await send(ok_message("session.ready", {
            "message": "EyeGuide V2 Fake session is ready.",
            "backend": "fake",
            "frame_queue_mode": "single-slot-lifo",
            "stale_after_ms": 500.0,
            "speech_policy": {"persistent_dedupe_ttl_seconds": 0},
        }))
        try:
            while True:
                try:
                    envelope = ClientEnvelope.model_validate(await websocket.receive_json())
                except ValidationError:
                    await send(error_message("BAD_REQUEST", "invalid client envelope"))
                    continue
                if not math.isfinite(envelope.ts):
                    await send(error_message("BAD_REQUEST", "ts must be finite", envelope.seq))
                    continue
                if envelope.type == "hello":
                    await send(ok_message("hello.ack", {
                        "session_id": envelope.session_id,
                        "backend": "fake",
                        "detail": "deterministic fake pipeline",
                        "state": {"mode": "fake", "backend": "fake"},
                        "frame_queue_mode": "single-slot-lifo",
                        "stale_after_ms": 500.0,
                        "speech_policy": {"persistent_dedupe_ttl_seconds": 0},
                    }, envelope.seq))
                elif envelope.type == "ping":
                    await send(ok_message("pong", {"server_ts": time.time()}, envelope.seq))
                elif envelope.type == "sensor.frame":
                    try:
                        payload = FramePayload.model_validate(envelope.payload)
                    except ValidationError:
                        await send(error_message("BAD_FRAME_PAYLOAD", "invalid frame payload", envelope.seq))
                        continue
                    client_ts_ms = payload.client_ts if payload.client_ts is not None else envelope.ts * 1000.0
                    if not math.isfinite(client_ts_ms):
                        await send(error_message("BAD_FRAME_PAYLOAD", "client_ts must be finite", envelope.seq))
                        continue
                    try:
                        image_bytes = decode_frame(payload)
                    except ValueError as exc:
                        await send(error_message("BAD_FRAME_DATA", str(exc), envelope.seq))
                        continue
                    accepted = await session_service.submit_frame(
                        runtime, payload.frame_id, envelope.seq, client_ts_ms, image_bytes
                    )
                    await send(ok_message("frame.accepted", {
                        "frame_id": accepted.job.frame_id,
                        "client_ts": accepted.job.client_ts_ms,
                        "queue_mode": "single-slot-lifo",
                        "replaced_frame_id": None if accepted.replaced is None else accepted.replaced.frame_id,
                        "dropped_pending_frames": accepted.dropped_pending_frames,
                    }, envelope.seq))
                    if accepted.replaced is not None:
                        await send(ok_message("frame.dropped", {
                            "frame_id": accepted.replaced.frame_id,
                            "client_ts": accepted.replaced.client_ts_ms,
                            "reason": "replaced_by_newer_frame",
                            "replacement_frame_id": accepted.job.frame_id,
                        }, envelope.seq))
                    runtime.wake()
                elif envelope.type == "session.stop":
                    stopped = await runtime.stop()
                    await send(ok_message("session.stopped", {"stopped": stopped}, envelope.seq))
                    break
                else:
                    await send(error_message(
                        "UNSUPPORTED_TYPE", f"unsupported message type: {envelope.type}", envelope.seq
                    ))
        except WebSocketDisconnect:
            pass
        finally:
            connected = False
            stopped = await runtime.stop()
            if store is not None:
                async def finish_summary() -> None:
                    try:
                        if not stopped:
                            await runtime.wait_stopped()
                        await asyncio.to_thread(
                            store.finish_session,
                            runtime.session_id,
                            runtime.frames_received,
                            runtime.frames_processed,
                            runtime.dropped_pending_frames,
                            "client_disconnect",
                        )
                    except sqlite3.Error:
                        app.state.storage_errors += 1

                finalizer = asyncio.create_task(finish_summary())
                finalizers.add(finalizer)
                finalizer.add_done_callback(finalizers.discard)

    return app


database_path = os.environ.get("EYEGUIDE_V2_DB")
app = create_app(store=SqliteStore(Path(database_path)) if database_path else None)
