import asyncio
import threading
import time
import unittest

from eyeguide.adapters.fake import FakeVisionPipeline, SystemClock
from eyeguide.domain.session import FrameJob, SessionState, VisionResult, is_stale
from eyeguide.runtime.latest_frame_slot import LatestFrameSlot
from eyeguide.runtime.session_manager import SessionManager


class DomainTests(unittest.TestCase):
    def test_stale_boundary(self) -> None:
        self.assertFalse(is_stale(1000.0, 1500.0))
        self.assertTrue(is_stale(1000.0, 1500.01))


class BlockingPipeline:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def analyze(self, image_bytes: bytes) -> VisionResult:
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError("test pipeline was not released")
        return VisionResult("none", "released")


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_latest_slot_replaces_only_pending_frame(self) -> None:
        slot = LatestFrameSlot()
        first = FrameJob("first", 1, 1000.0, b"a", 1000.0)
        second = FrameJob("second", 2, 1001.0, b"b", 1001.0)
        self.assertIsNone(await slot.put(first))
        self.assertEqual(await slot.put(second), first)
        self.assertEqual(slot.pending_count, 1)
        slot.wake()
        self.assertEqual(await slot.take(), second)
        self.assertEqual(slot.pending_count, 0)

    async def test_two_sessions_keep_results_separate(self) -> None:
        manager = SessionManager(FakeVisionPipeline, SystemClock())
        first_messages: list[dict] = []
        second_messages: list[dict] = []
        first_received = asyncio.Event()
        second_received = asyncio.Event()

        async def send_first(message: dict) -> None:
            first_messages.append(message)
            first_received.set()

        async def send_second(message: dict) -> None:
            second_messages.append(message)
            second_received.set()

        first = manager.create("first-session", send_first)
        second = manager.create("second-session", send_second)
        now_ms = time.time() * 1000.0
        await first.offer(FrameJob("first-frame", 1, now_ms, b"a", now_ms))
        await second.offer(FrameJob("second-frame", 2, now_ms, b"b", now_ms))
        first.wake()
        second.wake()
        await asyncio.wait_for(asyncio.gather(first_received.wait(), second_received.wait()), 5)
        self.assertEqual(first_messages[0]["payload"]["frame_id"], "first-frame")
        self.assertEqual(second_messages[0]["payload"]["frame_id"], "second-frame")
        self.assertEqual(manager.active_count, 2)
        self.assertTrue(await first.stop())
        self.assertTrue(await second.stop())
        self.assertEqual(manager.active_count, 0)

    async def test_stop_waits_for_running_pipeline_and_suppresses_late_result(self) -> None:
        pipeline = BlockingPipeline()
        manager = SessionManager(lambda: pipeline, SystemClock())
        messages: list[dict] = []

        async def send(message: dict) -> None:
            messages.append(message)

        runtime = manager.create("session", send)
        now_ms = time.time() * 1000.0
        await runtime.offer(FrameJob("late-frame", 1, now_ms, b"a", now_ms))
        runtime.wake()
        self.assertTrue(await asyncio.to_thread(pipeline.started.wait, 5))
        self.assertFalse(await runtime.stop(timeout=0.01))
        self.assertEqual(runtime.state, SessionState.STOPPING)
        self.assertEqual(manager.active_count, 1)
        pipeline.release.set()
        self.assertTrue(await runtime.stop(timeout=5))
        self.assertEqual(runtime.state, SessionState.STOPPED)
        self.assertEqual(manager.active_count, 0)
        self.assertEqual(messages, [])
