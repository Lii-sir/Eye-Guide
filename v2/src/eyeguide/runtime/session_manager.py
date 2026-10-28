from __future__ import annotations

from collections.abc import Callable

from eyeguide.ports.clock import Clock
from eyeguide.ports.vision import VisionPipeline
from eyeguide.runtime.session_runtime import SendMessage, SessionRuntime


class SessionManager:
    def __init__(self, pipeline_factory: Callable[[], VisionPipeline], clock: Clock) -> None:
        self._pipeline_factory = pipeline_factory
        self._clock = clock
        self._generation = 0
        self._sessions: dict[int, SessionRuntime] = {}

    def create(self, session_id: str, send: SendMessage) -> SessionRuntime:
        self._generation += 1
        runtime = SessionRuntime(
            session_id,
            self._generation,
            self._pipeline_factory(),
            self._clock,
            send,
            self._on_stopped,
        )
        self._sessions[runtime.generation] = runtime
        return runtime

    def _on_stopped(self, session_id: str, generation: int) -> None:
        runtime = self._sessions.get(generation)
        if runtime is not None and runtime.session_id == session_id:
            del self._sessions[generation]

    @property
    def active_count(self) -> int:
        return len(self._sessions)
