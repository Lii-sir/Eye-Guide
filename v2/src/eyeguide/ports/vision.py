from __future__ import annotations

from typing import Protocol

from eyeguide.domain.session import VisionResult


class VisionPipeline(Protocol):
    def analyze(self, image_bytes: bytes) -> VisionResult: ...
