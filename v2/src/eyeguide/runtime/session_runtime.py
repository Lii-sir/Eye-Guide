from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from eyeguide.domain.session import FrameJob, SessionState, is_stale
from eyeguide.ports.clock import Clock
from eyeguide.ports.vision import VisionPipeline
from eyeguide.runtime.latest_frame_slot import LatestFrameSlot


SendMessage = Callable[[dict], Awaitable[None]]


class SessionRuntime:
    def __init__(
        self,
        session_id: str,
        generation: int,
        pipeline: VisionPipeline,
        clock: Clock,
        send: SendMessage,
        on_stopped: Callable[[str, int], None],
    ) -> None:
        self.session_id = session_id
        self.generation = generation
        self.state = SessionState.ACTIVE
        self.dropped_pending_frames = 0
        self.frames_received = 0
        self.frames_processed = 0
        self._pipeline = pipeline
        self._clock = clock
        self._send = send
        self._on_stopped = on_stopped
        self._slot = LatestFrameSlot()
        self._task = asyncio.create_task(self._worker(), name=f"v2-session-{generation}")

    @property
    def pending_count(self) -> int:
        return self._slot.pending_count

    async def offer(self, job: FrameJob) -> FrameJob | None:
        if self.state is not SessionState.ACTIVE:
            raise RuntimeError("session is stopping")
        replaced = await self._slot.put(job)
        self.frames_received += 1
        if replaced is not None:
            self.dropped_pending_frames += 1
        return replaced

    def wake(self) -> None:
        self._slot.wake()

    async def stop(self, timeout: float = 0.1) -> bool:
        if self.state is SessionState.ACTIVE:
            self.state = SessionState.STOPPING
            await self._slot.close()
        if self.state is SessionState.STOPPED:
            return True
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    async def wait_stopped(self) -> None:
        await asyncio.shield(self._task)

    async def _worker(self) -> None:
        try:
            while self.state is SessionState.ACTIVE:
                job = await self._slot.take()
                if self.state is not SessionState.ACTIVE:
                    break
                if job is None:
                    continue
                started = self._clock.monotonic()
                try:
                    result = await asyncio.to_thread(self._pipeline.analyze, job.image_bytes)
                except Exception:
                    if self.state is SessionState.ACTIVE:
                        try:
                            await self._send({
                                "type": "error",
                                "ok": False,
                                "payload": {"code": "FRAME_PROCESS_FAILED", "message": "Fake frame processing failed"},
                                "seq": job.seq,
                            })
                        except Exception:
                            break
                    continue
                self.frames_processed += 1
                if self.state is not SessionState.ACTIVE:
                    continue
                sent_ms = self._clock.wall_ms()
                should_drop = is_stale(job.client_ts_ms, sent_ms)
                try:
                    await self._send({
                        "type": "frame.result",
                        "ok": True,
                        "seq": job.seq,
                        "payload": {
                        "frame_id": job.frame_id,
                        "client_ts": job.client_ts_ms,
                        "server_received_ts": job.received_ts_ms,
                        "server_sent_ts": sent_ms,
                        "queue_mode": "single-slot-lifo",
                        "hazard_level": result.hazard_level,
                        "hazard_summary": result.hazard_summary,
                        "events": [],
                        "boxes": [],
                        "overlays": [],
                        "route": None,
                        "location": None,
                        "imu": None,
                        "frame_shape": None,
                        "backend": "fake",
                        "timing": {
                            "infer_delay_ms": round((self._clock.monotonic() - started) * 1000.0, 2),
                            "result_age_ms": round(max(0.0, sent_ms - job.client_ts_ms), 2),
                            "stale_after_ms": 500.0,
                            "should_drop": should_drop,
                        },
                        },
                    })
                except Exception:
                    break
        finally:
            self.state = SessionState.STOPPED
            self._on_stopped(self.session_id, self.generation)
