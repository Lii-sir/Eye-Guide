from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, PriorityQueue
from threading import Event, Lock, Thread
from typing import Callable, Dict, Optional
import itertools
import time

from eyeguide.core.config import SpeechConfig

try:
    import pyttsx3
except ImportError:  # pragma: no cover
    pyttsx3 = None

try:
    import pythoncom
    from win32com.client import Dispatch
except ImportError:  # pragma: no cover
    pythoncom = None
    Dispatch = None


@dataclass
class SpeechMessage:
    text: str
    priority: int
    dedupe_key: str
    cooldown_seconds: float
    channel: str = "general"
    interrupt: bool = False
    persistent_dedupe: bool = False
    generation: int = 0


class _SapiSpeechBackend:
    def __init__(
        self,
        rate: int,
        preferred_voice_name: str | None = None,
        preferred_output_name: str | None = None,
    ) -> None:
        if pythoncom is None or Dispatch is None:
            raise RuntimeError("SAPI backend unavailable")

        pythoncom.CoInitialize()
        self._speaker = Dispatch("SAPI.SpVoice")
        self._voice_name = "default"
        self._output_name = "default"
        self._speaker.Rate = max(-5, min(5, round((rate - 180) / 15)))
        self._prefer_chinese_voice(preferred_voice_name)
        self._prefer_audio_output(preferred_output_name)

    @property
    def name(self) -> str:
        return "sapi"

    @property
    def voice_name(self) -> str:
        return self._voice_name

    @property
    def output_name(self) -> str:
        return self._output_name

    def _prefer_chinese_voice(self, preferred_voice_name: str | None) -> None:
        voices = self._speaker.GetVoices()
        preferred_lower = preferred_voice_name.lower() if preferred_voice_name else ""
        for index in range(voices.Count):
            voice = voices.Item(index)
            description = voice.GetDescription().lower()
            voice_id = str(getattr(voice, "Id", "")).lower()
            full_name = voice.GetDescription()
            if preferred_lower and preferred_lower in f"{description} {voice_id}":
                self._speaker.Voice = voice
                self._voice_name = full_name
                return
            if any(token in f"{description} {voice_id}" for token in ("chinese", "huihui", "zh-cn", "xiaoxiao")):
                self._speaker.Voice = voice
                self._voice_name = full_name
                return

    def _prefer_audio_output(self, preferred_output_name: str | None) -> None:
        try:
            outputs = self._speaker.GetAudioOutputs()
        except Exception:
            return

        preferred_tokens = (
            "edifier",
            "headset",
            "headphone",
            "耳机",
            "speaker",
            "扬声器",
        )
        avoid_tokens = (
            "spdif",
            "digital audio",
            "steam streaming",
            "nvidia",
            "microphone",
        )

        candidates = []
        preferred_lower = preferred_output_name.lower() if preferred_output_name else ""
        for index in range(outputs.Count):
            output = outputs.Item(index)
            description = output.GetDescription()
            lowered = description.lower()
            score = 0
            if preferred_lower and preferred_lower in lowered:
                score += 100
            if any(token in lowered for token in preferred_tokens):
                score += 10
            if any(token in lowered for token in avoid_tokens):
                score -= 10
            candidates.append((score, index, description, output))

        if not candidates:
            return

        candidates.sort(key=lambda item: (-item[0], item[1]))
        _, _, description, output = candidates[0]
        try:
            self._speaker.AudioOutput = output
            self._output_name = description
        except Exception:
            pass

    def speak(
        self,
        text: str,
        interrupt: bool = False,
        should_cancel: Callable[[], bool] | None = None,
    ) -> bool:
        flags = 1 | (2 if interrupt else 0)
        self._speaker.Speak(text, flags)
        while True:
            try:
                done = self._speaker.WaitUntilDone(100)
            except Exception:
                return False
            if done:
                return True
            if should_cancel is not None and should_cancel():
                try:
                    self._speaker.Speak("", 3)
                except Exception:
                    pass
                return False
        return True

    def stop(self) -> None:
        try:
            self._speaker.Speak("", 3)
        except Exception:
            pass

    def close(self) -> None:
        try:
            self.stop()
        finally:
            pythoncom.CoUninitialize()


class _Pyttsx3SpeechBackend:
    def __init__(
        self,
        rate: int,
        preferred_voice_name: str | None = None,
        preferred_output_name: str | None = None,
    ) -> None:
        if pyttsx3 is None:
            raise RuntimeError("pyttsx3 backend unavailable")
        self._engine = pyttsx3.init()
        self._voice_name = "default"
        self._engine.setProperty("rate", rate)
        self._prefer_chinese_voice(preferred_voice_name)

    @property
    def name(self) -> str:
        return "pyttsx3"

    @property
    def voice_name(self) -> str:
        return self._voice_name

    def _prefer_chinese_voice(self, preferred_voice_name: str | None) -> None:
        try:
            voices = self._engine.getProperty("voices") or []
        except Exception:
            return

        preferred_lower = preferred_voice_name.lower() if preferred_voice_name else ""
        for voice in voices:
            languages = " ".join(str(item) for item in getattr(voice, "languages", [])).lower()
            voice_name = str(getattr(voice, "name", "")).lower()
            voice_id = str(getattr(voice, "id", "")).lower()
            full_name = str(getattr(voice, "name", "default"))
            if preferred_lower and preferred_lower in f"{languages} {voice_name} {voice_id}":
                try:
                    self._engine.setProperty("voice", voice.id)
                    self._voice_name = full_name
                except Exception:
                    pass
                return
            if any(token in f"{languages} {voice_name} {voice_id}" for token in ("zh", "chinese", "huihui")):
                try:
                    self._engine.setProperty("voice", voice.id)
                    self._voice_name = full_name
                except Exception:
                    pass
                return

    def speak(
        self,
        text: str,
        interrupt: bool = False,
        should_cancel: Callable[[], bool] | None = None,
    ) -> bool:
        self._engine.say(text)
        self._engine.runAndWait()
        return True

    def stop(self) -> None:
        try:
            self._engine.stop()
        except Exception:
            pass

    def close(self) -> None:
        self.stop()


class SpeechEngine:
    def __init__(self, config: SpeechConfig | None = None) -> None:
        self._config = config or SpeechConfig()
        self._queue: PriorityQueue[tuple[int, int, SpeechMessage]] = PriorityQueue()
        self._counter = itertools.count()
        self._stop_event = Event()
        self._cancel_current = Event()
        self._lock = Lock()
        self._generation = 0
        self._last_spoken: Dict[str, float] = {}
        self._last_enqueued: Dict[str, float] = {}
        self._persistent_seen: Dict[str, float] = {}
        self._current_message: SpeechMessage | None = None
        self._backend = None
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def backend_name(self) -> str:
        backend = self._backend
        if backend is None:
            return "console"
        voice_name = getattr(backend, "voice_name", "default")
        output_name = getattr(backend, "output_name", "default")
        return f"{getattr(backend, 'name', 'unknown')}:{voice_name}@{output_name}"

    def _build_backend(self):
        for backend_cls in (_SapiSpeechBackend, _Pyttsx3SpeechBackend):
            try:
                return backend_cls(
                    self._config.rate,
                    self._config.preferred_voice_name,
                    self._config.preferred_output_name,
                )
            except Exception:
                continue
        return None

    def speak(
        self,
        text: str,
        priority: int = 5,
        dedupe_key: Optional[str] = None,
        cooldown_seconds: float = 4.0,
        channel: str = "general",
        replace_pending: bool = False,
        interrupt: bool = False,
        persistent_dedupe: bool = False,
    ) -> bool:
        key = dedupe_key or text
        now = time.time()
        with self._lock:
            self._prune_persistent_dedupe_locked(now)
            if persistent_dedupe and self._is_persistent_dedupe_blocked_locked(key, now):
                return False

            recent_at = max(self._last_spoken.get(key, 0.0), self._last_enqueued.get(key, 0.0))
            if now - recent_at < cooldown_seconds:
                return False

            if replace_pending:
                if (
                    channel == "vision"
                    and self._current_message is not None
                    and self._current_message.channel != "vision"
                    and self._has_pending_channel_locked("vision")
                ):
                    return False
                self._drop_pending_locked(channel)
            if interrupt:
                self._cancel_current.set()
                backend = self._backend
                if backend is not None:
                    try:
                        backend.stop()
                    except Exception:
                        pass

            generation = self._generation
            self._last_enqueued[key] = now
            self._queue.put(
                (
                    priority,
                    next(self._counter),
                    SpeechMessage(
                        text,
                        priority,
                        key,
                        cooldown_seconds,
                        channel,
                        interrupt,
                        persistent_dedupe,
                        generation,
                    ),
                )
            )
        return True

    def cancel_all(self) -> None:
        with self._lock:
            self._generation += 1
            self._cancel_current.set()
            self._current_message = None
            self._clear_queue_locked()
            self._last_spoken.clear()
            self._last_enqueued.clear()
            self._persistent_seen.clear()
            backend = self._backend
            if backend is not None:
                try:
                    backend.stop()
                except Exception:
                    pass

    def stop(self) -> None:
        self.cancel_all()
        self._stop_event.set()
        self._thread.join(timeout=1.0)

    def _clear_queue_locked(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break

    def _drop_pending_locked(self, channel: str) -> None:
        retained: list[tuple[int, int, SpeechMessage]] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except Empty:
                break
            if item[2].channel != channel:
                retained.append(item)
        for item in retained:
            self._queue.put(item)

    def _has_pending_channel_locked(self, channel: str) -> bool:
        retained: list[tuple[int, int, SpeechMessage]] = []
        has_pending = False
        while True:
            try:
                item = self._queue.get_nowait()
            except Empty:
                break
            retained.append(item)
            if item[2].channel == channel:
                has_pending = True
        for item in retained:
            self._queue.put(item)
        return has_pending

    def _persistent_dedupe_ttl_seconds(self) -> float:
        return max(0.0, self._config.persistent_dedupe_ttl_seconds)

    def _is_persistent_dedupe_blocked_locked(self, key: str, now: float) -> bool:
        ttl_seconds = self._persistent_dedupe_ttl_seconds()
        if ttl_seconds <= 0:
            return False
        seen_at = self._persistent_seen.get(key)
        if seen_at is None:
            return False
        if now - seen_at >= ttl_seconds:
            self._persistent_seen.pop(key, None)
            return False
        return True

    def _prune_persistent_dedupe_locked(self, now: float) -> None:
        ttl_seconds = self._persistent_dedupe_ttl_seconds()
        if ttl_seconds <= 0:
            self._persistent_seen.clear()
            return
        expired_keys = [
            key for key, seen_at in self._persistent_seen.items() if now - seen_at >= ttl_seconds
        ]
        for key in expired_keys:
            self._persistent_seen.pop(key, None)

    def _run(self) -> None:
        self._backend = self._build_backend()
        if self._backend is None:
            print("[TTS] backend unavailable; falling back to console output")
        else:
            print(f"[TTS] backend={self.backend_name}")
        try:
            while not self._stop_event.is_set():
                try:
                    _, _, message = self._queue.get(timeout=0.2)
                except Empty:
                    continue

                with self._lock:
                    current_generation = self._generation
                if message.generation != current_generation:
                    continue

                with self._lock:
                    self._cancel_current.clear()
                    self._current_message = message

                if self._backend is None:
                    print(f"[TTS] {message.text}")
                    with self._lock:
                        if self._current_message is message:
                            self._last_spoken[message.dedupe_key] = time.time()
                            if message.persistent_dedupe:
                                self._persistent_seen[message.dedupe_key] = time.time()
                            self._current_message = None
                    continue

                try:
                    completed = self._backend.speak(
                        message.text,
                        interrupt=message.interrupt,
                        should_cancel=lambda: (
                            self._stop_event.is_set()
                            or message.generation != self._generation
                            or self._cancel_current.is_set()
                        ),
                    )
                    with self._lock:
                        if self._current_message is message:
                            if completed:
                                spoken_at = time.time()
                                self._last_spoken[message.dedupe_key] = spoken_at
                                if message.persistent_dedupe:
                                    self._persistent_seen[message.dedupe_key] = spoken_at
                            self._current_message = None
                except Exception as exc:
                    with self._lock:
                        if self._current_message is message:
                            self._current_message = None
                    backend_name = getattr(self._backend, "name", "unknown")
                    print(f"[TTS ERROR:{backend_name}] {exc}")
                    try:
                        self._backend.close()
                    except Exception:
                        pass
                    self._backend = self._build_backend()
                    if self._backend is None:
                        print("[TTS] backend unavailable; falling back to console output")
                        print(f"[TTS] {message.text}")
                    else:
                        print(f"[TTS] backend={self.backend_name}")
        finally:
            if self._backend is not None:
                try:
                    self._backend.close()
                except Exception:
                    pass
