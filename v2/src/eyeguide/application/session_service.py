from __future__ import annotations

from dataclasses import dataclass

from eyeguide.domain.session import FrameJob
from eyeguide.ports.clock import Clock
from eyeguide.runtime.session_runtime import SessionRuntime


@dataclass(frozen=True, slots=True)
class AcceptedFrame:
    job: FrameJob
    replaced: FrameJob | None
    dropped_pending_frames: int


class SessionService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def submit_frame(
        self,
        runtime: SessionRuntime,
        frame_id: str,
        seq: int,
        client_ts_ms: float,
        image_bytes: bytes,
    ) -> AcceptedFrame:
        job = FrameJob(frame_id, seq, client_ts_ms, image_bytes, self._clock.wall_ms())
        replaced = await runtime.offer(job)
        return AcceptedFrame(job, replaced, runtime.dropped_pending_frames)
