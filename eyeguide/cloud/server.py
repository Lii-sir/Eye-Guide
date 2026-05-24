from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from eyeguide.cloud.adapter import build_fix, parse_frame_payload, parse_gps_payload
from eyeguide.cloud.runtime import AUDIO_CACHE_DIR, CloudInferenceRuntime
from eyeguide.cloud.schemas import ValidationErrorDetail, parse_envelope

app = FastAPI(title="EyeGuide Cloud Bridge")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOBILE_WEB_DIR = _PROJECT_ROOT / "mobile-web"
if _MOBILE_WEB_DIR.exists():
    app.mount("/mobile", StaticFiles(directory=str(_MOBILE_WEB_DIR), html=True), name="mobile")
if AUDIO_CACHE_DIR.exists():
    app.mount("/audio", StaticFiles(directory=str(AUDIO_CACHE_DIR)), name="audio")


def _error(detail: ValidationErrorDetail | tuple[str, str]) -> dict:
    if isinstance(detail, ValidationErrorDetail):
        code = detail.code
        message = detail.message
    else:
        code, message = detail
    return {"type": "error", "payload": {"code": code, "message": message}}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "ts": time.time()}


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/mobile/")


@app.websocket("/ws/track")
async def ws_track(websocket: WebSocket) -> None:
    await websocket.accept()
    runtime = CloudInferenceRuntime()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(_error(("BAD_JSON", "request must be valid JSON")))
                continue

            envelope, err = parse_envelope(data)
            if err is not None:
                await websocket.send_json(_error(err))
                continue

            if envelope.type == "ping":
                await websocket.send_json({"type": "pong", "payload": {"ts": time.time()}})
                continue

            if envelope.type == "hello":
                await websocket.send_json(
                    {
                        "type": "ack",
                        "payload": {"seq": envelope.seq, "session_id": envelope.session_id},
                    }
                )
                continue

            if envelope.type == "session.start":
                await websocket.send_json({"type": "ack", "payload": {"seq": envelope.seq}})
                await websocket.send_json({"type": "infer.result", "payload": runtime.start_session(envelope.payload)})
                continue

            if envelope.type == "sensor.gps":
                sensor, parse_err = parse_gps_payload(envelope.payload)
                if parse_err is not None or sensor is None:
                    await websocket.send_json(_error(("BAD_PAYLOAD", parse_err or "invalid gps payload")))
                    continue

                fix = build_fix(sensor, envelope.ts)
                result = runtime.infer_from_fix(fix)
                await websocket.send_json({"type": "ack", "payload": {"seq": envelope.seq}})
                await websocket.send_json({"type": "infer.result", "payload": result})
                continue

            if envelope.type == "sensor.orientation":
                continue

            if envelope.type == "sensor.frame":
                frame, frame_err = parse_frame_payload(envelope.payload)
                if frame_err is not None or frame is None:
                    await websocket.send_json(_error(("BAD_PAYLOAD", frame_err or "invalid frame payload")))
                    continue
                try:
                    result = runtime.infer_from_frame(frame)
                except Exception as exc:
                    await websocket.send_json(_error(("INFER_FRAME_FAILED", str(exc))))
                    continue
                await websocket.send_json({"type": "ack", "payload": {"seq": envelope.seq}})
                await websocket.send_json({"type": "infer.result", "payload": result})
                continue

            if envelope.type == "route.set":
                response, route_err = runtime.configure_route(envelope.payload)
                if route_err is not None:
                    await websocket.send_json(_error(route_err))
                    continue
                await websocket.send_json({"type": "ack", "payload": {"seq": envelope.seq}})
                await websocket.send_json({"type": "route.status", "payload": response or {}})
                await websocket.send_json({"type": "infer.result", "payload": runtime.current_state_payload()})
                continue

            if envelope.type == "route.search":
                response, route_err = runtime.search_route_candidates(envelope.payload)
                if route_err is not None:
                    await websocket.send_json(_error(route_err))
                    continue
                await websocket.send_json({"type": "ack", "payload": {"seq": envelope.seq}})
                await websocket.send_json({"type": "route.status", "payload": response or {}})
                continue

            if envelope.type == "route.next":
                await websocket.send_json({"type": "ack", "payload": {"seq": envelope.seq}})
                await websocket.send_json({"type": "infer.result", "payload": runtime.next_route_step()})
                continue

            if envelope.type == "route.repeat":
                await websocket.send_json({"type": "ack", "payload": {"seq": envelope.seq}})
                await websocket.send_json({"type": "infer.result", "payload": runtime.repeat_route_step()})
                continue

            if envelope.type == "route.clear":
                runtime.clear_route()
                await websocket.send_json({"type": "ack", "payload": {"seq": envelope.seq}})
                await websocket.send_json(
                    {"type": "route.status", "payload": {"status": "cleared", "message": "Route cleared."}}
                )
                await websocket.send_json({"type": "infer.result", "payload": runtime.current_state_payload()})
                continue

            if envelope.type == "speech.test":
                await websocket.send_json({"type": "ack", "payload": {"seq": envelope.seq}})
                await websocket.send_json({"type": "infer.result", "payload": runtime.test_speech(envelope.payload)})
                continue

            if envelope.type == "speech.ack":
                runtime.handle_speech_ack(envelope.payload)
                await websocket.send_json({"type": "ack", "payload": {"seq": envelope.seq}})
                continue

            if envelope.type == "config.update":
                response, config_err = runtime.update_settings(envelope.payload)
                if config_err is not None:
                    await websocket.send_json(_error(config_err))
                    continue
                await websocket.send_json({"type": "ack", "payload": {"seq": envelope.seq}})
                await websocket.send_json({"type": "config.status", "payload": response or {}})
                continue

            await websocket.send_json(_error(("UNSUPPORTED_TYPE", f"unsupported type: {envelope.type}")))
    except WebSocketDisconnect:
        return
