import sqlite3
import tempfile
from pathlib import Path
import time
import unittest

from fastapi.testclient import TestClient

from eyeguide.adapters.sqlite_store import RevisionConflict, SqliteStore
from eyeguide.entrypoints.api.app import create_app


class SqliteStoreTests(unittest.TestCase):
    def test_settings_revision_and_restart_readback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "eyeguide.sqlite3"
            store = SqliteStore(path)
            store.initialize()
            self.assertEqual(store.read_settings(), {"revision": 0, "payload": {}})
            saved = store.save_settings(0, {"provider": "osm"})
            self.assertEqual(saved["revision"], 1)
            with self.assertRaises(RevisionConflict):
                store.save_settings(0, {"provider": "amap"})
            restarted = SqliteStore(path)
            restarted.initialize()
            self.assertEqual(restarted.read_settings(), saved)

    def test_session_summary_is_idempotent_and_recovers_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "eyeguide.sqlite3"
            store = SqliteStore(path)
            store.initialize()
            store.start_session("complete")
            store.finish_session("complete", 3, 2, 1, "client_disconnect")
            store.finish_session("complete", 99, 99, 99, "duplicate")
            store.start_session("abandoned")
            SqliteStore(path).initialize()
            sessions = {row["id"]: row for row in store.list_sessions()}
            self.assertEqual(sessions["complete"]["frames_received"], 3)
            self.assertEqual(sessions["complete"]["end_reason"], "client_disconnect")
            self.assertEqual(sessions["abandoned"]["state"], "interrupted")


class FailingSettingsStore(SqliteStore):
    def save_settings(self, expected_revision: int, payload: dict) -> dict:
        raise sqlite3.OperationalError("simulated disk error")


class PersistentApiTests(unittest.TestCase):
    def test_settings_and_session_survive_app_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "eyeguide.sqlite3"
            with TestClient(create_app(store=SqliteStore(path))) as client:
                self.assertEqual(client.get("/settings").json()["revision"], 0)
                response = client.put("/settings", json={
                    "expected_revision": 0,
                    "payload": {"provider": "osm", "object_speech_dedup_mode": "detailed"},
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(client.put("/settings", json={
                    "expected_revision": 0, "payload": {"provider": "amap"}
                }).status_code, 409)
                self.assertEqual(client.put("/settings", json={
                    "expected_revision": 1, "payload": {"amap_api_key": "forbidden"}
                }).status_code, 422)
                with client.websocket_connect("/ws") as websocket:
                    self.assertEqual(websocket.receive_json()["type"], "session.ready")
                    websocket.send_json({
                        "type": "sensor.frame",
                        "device_id": "fixture-device",
                        "seq": 1,
                        "ts": time.time(),
                        "payload": {"frame_id": "persisted-summary", "image_b64": "AQID"},
                    })
                    self.assertEqual(websocket.receive_json()["type"], "frame.accepted")
                    self.assertEqual(websocket.receive_json()["type"], "frame.result")
            with TestClient(create_app(store=SqliteStore(path))) as restarted:
                self.assertEqual(restarted.get("/settings").json()["payload"]["object_speech_dedup_mode"], "detailed")
                sessions = restarted.get("/sessions").json()
                self.assertEqual(len(sessions), 1)
                self.assertEqual(sessions[0]["state"], "ended")
                self.assertEqual(sessions[0]["frames_received"], 1)
                self.assertEqual(sessions[0]["frames_processed"], 1)

    def test_settings_failure_is_reported_without_breaking_existing_frame_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FailingSettingsStore(Path(directory) / "eyeguide.sqlite3")
            with TestClient(create_app(store=store)) as client:
                with client.websocket_connect("/ws") as websocket:
                    websocket.receive_json()
                    response = client.put("/settings", json={
                        "expected_revision": 0, "payload": {"provider": "osm"}
                    })
                    self.assertEqual(response.status_code, 503)
                    websocket.send_json({
                        "type": "sensor.frame",
                        "device_id": "fixture-device",
                        "seq": 1,
                        "ts": 1700000000.0,
                        "payload": {"frame_id": "still-live", "image_b64": "AQID"},
                    })
                    self.assertEqual(websocket.receive_json()["type"], "frame.accepted")
                    self.assertEqual(websocket.receive_json()["payload"]["frame_id"], "still-live")
