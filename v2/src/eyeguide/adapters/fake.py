from __future__ import annotations

import time

from eyeguide.domain.session import VisionResult


class SystemClock:
    def wall_ms(self) -> float:
        return time.time() * 1000.0

    def monotonic(self) -> float:
        return time.monotonic()


class FakeVisionPipeline:
    def analyze(self, image_bytes: bytes) -> VisionResult:
        return VisionResult(hazard_level="none", hazard_summary="Fake 环境相对安全")
