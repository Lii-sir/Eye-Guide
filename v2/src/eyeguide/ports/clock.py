from __future__ import annotations

from typing import Protocol


class Clock(Protocol):
    def wall_ms(self) -> float: ...

    def monotonic(self) -> float: ...
