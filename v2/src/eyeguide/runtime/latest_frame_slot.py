from __future__ import annotations

import asyncio

from eyeguide.domain.session import FrameJob


class LatestFrameSlot:
    def __init__(self) -> None:
        self._pending: FrameJob | None = None
        self._event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def put(self, job: FrameJob) -> FrameJob | None:
        async with self._lock:
            replaced = self._pending
            self._pending = job
            return replaced

    def wake(self) -> None:
        self._event.set()

    async def take(self) -> FrameJob | None:
        await self._event.wait()
        async with self._lock:
            job = self._pending
            self._pending = None
            self._event.clear()
            return job

    async def close(self) -> None:
        async with self._lock:
            self._pending = None
            self._event.set()

    @property
    def pending_count(self) -> int:
        return int(self._pending is not None)
