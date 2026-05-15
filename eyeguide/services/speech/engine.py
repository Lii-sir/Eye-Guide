from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, PriorityQueue
from threading import Event, Thread
from typing import Dict, Optional
import itertools
import time

from eyeguide.core.config import SpeechConfig

try:
    import pyttsx3
except ImportError:  # pragma: no cover
    pyttsx3 = None


@dataclass
class SpeechMessage:
    text: str
    priority: int
    dedupe_key: str
    cooldown_seconds: float


class SpeechEngine:
    def __init__(self, config: SpeechConfig | None = None) -> None:
        self._config = config or SpeechConfig()
        self._queue: PriorityQueue[tuple[int, int, SpeechMessage]] = PriorityQueue()
        self._counter = itertools.count()
        self._stop_event = Event()
        self._thread = Thread(target=self._run, daemon=True)
        self._last_spoken: Dict[str, float] = {}
        self._engine = self._build_engine()
        self._thread.start()

    def _build_engine(self):
        if pyttsx3 is None:
            return None

        engine = pyttsx3.init()
        engine.setProperty("rate", self._config.rate)
        return engine

    def speak(
        self,
        text: str,
        priority: int = 5,
        dedupe_key: Optional[str] = None,
        cooldown_seconds: float = 4.0,
    ) -> None:
        key = dedupe_key or text
        now = time.time()
        if now - self._last_spoken.get(key, 0.0) < cooldown_seconds:
            return

        self._queue.put(
            (
                priority,
                next(self._counter),
                SpeechMessage(text, priority, key, cooldown_seconds),
            )
        )

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                _, _, message = self._queue.get(timeout=0.2)
            except Empty:
                continue

            self._last_spoken[message.dedupe_key] = time.time()
            if self._engine is None:
                print(f"[TTS] {message.text}")
                continue

            self._engine.say(message.text)
            self._engine.runAndWait()

