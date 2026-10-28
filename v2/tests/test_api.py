import json
from pathlib import Path
import threading
import time
import unittest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from eyeguide.entrypoints.api.app import create_app, decode_frame, FramePayload
from eyeguide.adapters.fake import SystemClock
from eyeguide.domain.session import VisionResult
from eyeguide.runtime.session_manager import SessionManager


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "v1"


def fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


class ApiTests(unittest.TestCase):
    def test_local_access_and_frame_size_guard(self) -> None:
        with TestClient(create_app(), client=("192.0.2.10", 50000)) as remote:
            self.assertEqual(remote.get("/healthz").status_code, 403)
            with self.assertRaises(WebSocketDisconnect):
                with remote.websocket_connect("/ws"):
                    pass
        with self.assertRaises(ValueError):
            decode_frame(FramePayload(frame_id="too-large", image_b64="A" * 4_000_100))

    def test_health_and_v1_message_subset(self) -> None:
        with TestClient(create_app()) as client:
            self.assertEqual(client.get("/healthz").json()["backend"], "fake")
            with client.websocket_connect("/ws") as websocket:
                self.assertEqual(websocket.receive_json()["type"], "session.ready")
                for name in ("hello", "ping"):
                    contract = fixture(name)
                    websocket.send_json(contract["request"])
                    response = websocket.receive_json()
                    self.assertEqual(response["type"], contract["response_type"])
                    self.assertEqual(response["seq"], contract["request"]["seq"])
                    self.assertTrue(set(contract["response_fields"]) <= response["payload"].keys())

                contract = fixture("frame")
                request = contract["request"]
                request["ts"] = time.time()
                request["payload"]["client_ts"] = request["ts"] * 1000.0
                websocket.send_json(request)
                accepted = websocket.receive_json()
                result = websocket.receive_json()
                self.assertEqual(accepted["type"], contract["response_type"])
                self.assertTrue(set(contract["response_fields"]) <= accepted["payload"].keys())
                self.assertEqual(result["type"], "frame.result")
                self.assertEqual(result["payload"]["frame_id"], request["payload"]["frame_id"])
                self.assertFalse(result["payload"]["timing"]["should_drop"])
                legacy_result = json.loads((FIXTURE_DIR / "frame_result.json").read_text(encoding="utf-8"))
                self.assertTrue({
                    "frame_id", "client_ts", "hazard_level", "hazard_summary", "timing"
                } <= legacy_result["payload"].keys() & result["payload"].keys())

                request["seq"] += 1
                request["payload"]["frame_id"] = "stale-frame"
                request["payload"]["client_ts"] = 1700000000000.0
                websocket.send_json(request)
                self.assertEqual(websocket.receive_json()["type"], "frame.accepted")
                self.assertTrue(websocket.receive_json()["payload"]["timing"]["should_drop"])

                websocket.send_json({**fixture("ping")["request"], "type": "route.set"})
                error = websocket.receive_json()
                self.assertEqual(error["payload"]["code"], fixture("error")["payload"]["code"])

    def test_two_websockets_have_distinct_results(self) -> None:
        with TestClient(create_app()) as client:
            with client.websocket_connect("/ws") as first, client.websocket_connect("/ws") as second:
                first.receive_json()
                second.receive_json()
                self.assertEqual(client.get("/healthz").json()["active_sessions"], 2)
                for websocket, frame_id in ((first, "first"), (second, "second")):
                    request = fixture("frame")["request"]
                    request["payload"]["frame_id"] = frame_id
                    request["payload"]["client_ts"] = time.time() * 1000.0
                    websocket.send_json(request)
                    self.assertEqual(websocket.receive_json()["type"], "frame.accepted")
                    self.assertEqual(websocket.receive_json()["payload"]["frame_id"], frame_id)
            self.assertEqual(client.get("/healthz").json()["active_sessions"], 0)

    def test_pending_frame_replacement_matches_v1_shape(self) -> None:
        class BlockingPipeline:
            def __init__(self) -> None:
                self.started = threading.Event()
                self.release = threading.Event()

            def analyze(self, image_bytes: bytes) -> VisionResult:
                self.started.set()
                self.release.wait(5)
                return VisionResult("none", "released")

        pipeline = BlockingPipeline()
        manager = SessionManager(lambda: pipeline, SystemClock())
        with TestClient(create_app(manager=manager)) as client:
            with client.websocket_connect("/ws") as websocket:
                websocket.receive_json()
                request = fixture("frame")["request"]
                request["payload"]["client_ts"] = time.time() * 1000.0
                request["payload"]["frame_id"] = "running-frame"
                websocket.send_json(request)
                self.assertEqual(websocket.receive_json()["type"], "frame.accepted")
                self.assertTrue(pipeline.started.wait(5))

                request["payload"]["frame_id"] = "pending-frame"
                websocket.send_json(request)
                self.assertIsNone(websocket.receive_json()["payload"]["replaced_frame_id"])

                request["payload"]["frame_id"] = "newer-frame"
                websocket.send_json(request)
                self.assertEqual(websocket.receive_json()["payload"]["replaced_frame_id"], "pending-frame")
                dropped = websocket.receive_json()
                legacy_dropped = json.loads((FIXTURE_DIR / "frame_dropped.json").read_text(encoding="utf-8"))
                self.assertEqual(dropped["type"], legacy_dropped["type"])
                self.assertEqual(dropped["payload"]["reason"], legacy_dropped["payload"]["reason"])
                self.assertEqual(dropped["payload"]["replacement_frame_id"], "newer-frame")
                pipeline.release.set()
                self.assertEqual(websocket.receive_json()["payload"]["frame_id"], "running-frame")
                self.assertEqual(websocket.receive_json()["payload"]["frame_id"], "newer-frame")
