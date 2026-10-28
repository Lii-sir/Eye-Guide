from __future__ import annotations

from copy import deepcopy
from uuid import UUID


JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
    "2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/"
    "wAARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJx"
    "FDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/"
    "8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwAooooA/9k="
)


def build_examples() -> dict:
    user_id, device_id, session_id, connection_id, frame_id, next_frame_id, request_id = (
        str(UUID(int=number)) for number in range(1, 8)
    )
    envelope = {
        "protocol_version": "2.0", "session_id": session_id,
        "connection_id": connection_id, "seq": 1,
    }
    settings = {"speech_enabled": True, "speech_rate": 1.0, "reminder_detail": "normal"}
    session = {
        "session_id": session_id, "device_id": device_id, "state": "created",
        "started_at": "2026-10-28T00:00:00Z",
    }
    error = {
        "code": "BUSY", "message": "服务繁忙，请稍后重试", "retryable": True, "request_id": request_id,
    }
    result = {
        **envelope, "type": "frame.result", "frame_id": frame_id,
        "captured_elapsed_ms": 1000.0, "status": "ok", "server_processing_ms": 80.0,
        "hazard_level": "high",
        "detections": [{
            "label": "person", "confidence": 0.9, "bbox": [0.2, 0.1, 0.8, 0.9],
            "distance_meters": None, "direction": "center",
        }],
        "blind_road": {"status": "unavailable", "direction": "unknown", "blocked": None},
        "speech": {"code": "OBSTACLE_AHEAD", "text": "前方有行人", "priority": "urgent", "dedup_key": "obstacle-front"},
    }
    expired = deepcopy(result)
    expired.update(status="expired", speech=None)
    unavailable = deepcopy(result)
    unavailable.update(status="unavailable", speech=None, hazard_level="unknown", detections=[])
    return {
        "http_models": [
            {"model": "UserView", "value": {"user_id": user_id, "state": "active"}},
            {"model": "RegisterDevice", "value": {
                "installation_id": str(UUID(int=8)), "label": "test-android", "app_version": "0.1.0", "platform": "android",
            }},
            {"model": "DeviceView", "value": {"user_id": user_id, "device_id": device_id, "state": "active"}},
            {"model": "SettingsUpdate", "value": {"expected_revision": 0, "values": settings}},
            {"model": "SettingsView", "value": {"schema_version": 1, "revision": 1, "values": settings}},
            {"model": "CreateSession", "value": {"device_id": device_id, "protocol_version": "2.0"}},
            {"model": "SessionView", "value": session},
            {"model": "SessionPage", "value": {"items": [session], "next_cursor": None}},
            {"model": "StopSession", "value": {"reason": "user_stop"}},
            {"model": "ErrorResponse", "value": {"error": error}},
        ],
        "client_messages": [
            {**envelope, "type": "sensor.frame", "frame_id": frame_id, "captured_elapsed_ms": 1000.0,
             "width": 2, "height": 2, "image_jpeg_b64": JPEG_B64},
            {**envelope, "type": "ping", "nonce": request_id, "seq": 2},
            {**envelope, "type": "session.stop", "reason": "user_stop", "seq": 3},
        ],
        "server_messages": [
            {**envelope, "type": "session.ready", "seq": 0,
             "limits": {"max_frame_bytes": 1_000_000, "max_width": 1280, "max_height": 1280,
                        "max_pixels": 921600, "upload_fps_limit": 5, "result_ttl_ms": 1000,
                        "heartbeat_interval_ms": 5000, "idle_timeout_ms": 15000},
             "capabilities": {"backend": "fake", "obstacles": False, "depth": False, "blind_road": False}},
            {**envelope, "type": "frame.accepted", "frame_id": frame_id, "replaced_frame_id": None},
            {**envelope, "type": "frame.dropped", "frame_id": frame_id,
             "reason": "replaced_by_newer_frame", "replacement_frame_id": next_frame_id},
            result, expired, unavailable,
            {**envelope, "type": "pong", "nonce": request_id, "seq": 2, "server_time": "2026-10-28T00:00:00Z"},
            {**envelope, "type": "session.stopped", "state": "ended", "seq": 3},
            {**envelope, "type": "error", "error": error},
        ],
    }
