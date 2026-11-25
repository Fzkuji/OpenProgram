"""Record the sound one application plays, in the background.

ScreenCaptureKit captures an application's audio without capturing its
pixels for anyone and without touching the output device: the user keeps
hearing what they hear, and nothing else on the computer is recorded.
The stream (``openprogram.host._platform.screen_capture_audio``) asks for
16 kHz mono float samples, which is what speech recognition wants, and is
turned into a 16-bit WAV when it stops.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Any

from openprogram.host._platform import screen_capture_audio as _sck
from openprogram.host._platform.screen_capture_audio import SAMPLE_RATE, wav

DEFAULT_SECONDS = 60.0
MAX_SECONDS = 300.0
# Below this peak the recording is treated as silence (about -80 dBFS).
SILENCE_PEAK = 1e-4


def seconds(value: Any) -> float:
    """The recording limit: 60 seconds by default, at most 300."""
    if value in (None, ""):
        return DEFAULT_SECONDS
    try:
        limit = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_duration must be a number of seconds") from exc
    if not 0 < limit <= MAX_SECONDS:
        raise ValueError(f"max_duration must be between 0 and {MAX_SECONDS:g} seconds")
    return limit


@dataclass
class Recording:
    """One running capture of one application's sound."""
    app: str
    pid: int
    limit: float
    started: float = field(default_factory=time.monotonic)
    stream: Any = None
    timer: threading.Timer | None = None
    chunks: list[bytes] = field(default_factory=list)
    frames: int = 0
    stopped: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def take(self, data: bytes) -> None:
        """Keep samples up to the limit; later ones are dropped."""
        with self.lock:
            room = int(self.limit * SAMPLE_RATE) - self.frames
            if self.stopped or room <= 0:
                return
            data = data[: room * 4]
            self.chunks.append(data)
            self.frames += len(data) // 4

    @property
    def seconds(self) -> float:
        return self.frames / SAMPLE_RATE


def start(pid: int, app: str, limit: float) -> Recording:
    """Begin capturing what ``pid`` plays, for at most ``limit`` seconds."""
    sc, _cm, _appkit = _sck.frameworks()
    content = _sck.shareable_content()
    target = next((a for a in content.applications() if int(a.processID()) == pid), None)
    if target is None:
        raise RuntimeError(f"{app} has no audio ScreenCaptureKit can capture; it needs a window")
    displays = list(content.displays())
    if not displays:
        raise RuntimeError("no display to attach the audio capture to")
    content_filter = sc.SCContentFilter.alloc().initWithDisplay_includingApplications_exceptingWindows_(
        displays[0], [target], [])
    recording = Recording(app=app, pid=pid, limit=limit)
    recording.stream = _sck.open_audio(content_filter, recording.take)
    recording.started = time.monotonic()
    # Release the capture at the limit even if stop is never called.
    recording.timer = threading.Timer(limit + 0.5, lambda: halt(recording))
    recording.timer.daemon = True
    recording.timer.start()
    return recording


def halt(recording: Recording) -> None:
    """Stop the stream; samples taken so far are kept."""
    with recording.lock:
        if recording.stopped:
            return
        recording.stopped = True
    if recording.timer is not None:
        recording.timer.cancel()
    stream, recording.stream = recording.stream, None
    if stream is not None:
        stream.stop()


def finish(recording: Recording) -> tuple[bytes, float]:
    """Stop and return the WAV and its peak level."""
    halt(recording)
    with recording.lock:
        samples = b"".join(recording.chunks)
        recording.chunks = []
    return wav(samples)


__all__ = ["DEFAULT_SECONDS", "MAX_SECONDS", "Recording", "SAMPLE_RATE", "SILENCE_PEAK", "finish", "halt",
           "seconds", "start", "wav"]
