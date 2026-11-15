"""The computer's sound for a meeting, recorded on the server.

On macOS the "Others" track comes from ScreenCaptureKit: a stream with a
content filter for the whole display (``initWithDisplay_excludingWindows_``
with no windows excluded) and only an audio output attached, so it hears
everything the computer plays and records no picture. macOS asks for
Screen & System Audio Recording the first time; the worker runs inside
the App's runtime bundle, so the request names the App.

:class:`SystemAudio` is independent of ScreenCaptureKit: a *source*
pushes 16 kHz mono float32 sample buffers into it, and it cuts them into
chunks of ``CHUNK_MS`` (the page's cadence), each a WAV with its sequence
number and its start offset from the meeting start. A thread of its own
hands each chunk to ``deliver`` (the meeting's chunk intake, the same one
``POST /api/meetings/<id>/audio?track=system`` reaches). ``stop`` flushes
the last partial chunk; ``cancel`` drops it.

When the source cannot start, the capture reports ``unavailable`` with a
reason: ``unsupported`` (not macOS, or PyObjC's ScreenCaptureKit is
missing), ``permission`` (macOS refused the capture), or ``error``.
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
from typing import Callable, Protocol

from openprogram.host._platform import screen_capture_audio as _sck

_log = logging.getLogger(__name__)

RATE = _sck.SAMPLE_RATE
CHUNK_MS = 30_000
#: Sample bytes: native float32, one channel.
SAMPLE_BYTES = 4

Sink = Callable[[bytes], None]
#: ``deliver(seq, t_ms, wav)``
Deliver = Callable[[int, int, bytes], None]


class Unavailable(RuntimeError):
    """The computer's sound cannot be recorded; ``reason`` says which way."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


class Source(Protocol):
    """Where the samples come from: pushes float32 buffers into ``sink`` until stopped."""

    def start(self, sink: Sink) -> None: ...

    def stop(self) -> None: ...


_PERMISSION_MARKS = ("-3801", "declined", "tcc", "not authorized", "not permitted", "permission")


def _screen_capture_allowed() -> bool | None:
    """macOS's own answer, without asking; ``None`` when it cannot be read."""
    try:
        import Quartz
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return None


def classify(exc: BaseException, allowed: Callable[[], bool | None] = _screen_capture_allowed) -> str:
    """``permission`` when ScreenCaptureKit refused for lack of the grant, else ``error``."""
    text = str(exc).lower()
    if any(mark in text for mark in _PERMISSION_MARKS):
        return "permission"
    return "permission" if allowed() is False else "error"


_MACOS_ONLY = "The computer's sound is recorded on macOS only."


def support(platform: str | None = None) -> dict:
    """Whether the "Others" track can be recorded here, and why not; asks macOS for nothing.

    A meeting still records the microphone where this is unavailable.
    """
    import importlib.util
    if (sys.platform if platform is None else platform) != "darwin":
        return {"available": False, "reason": _MACOS_ONLY}
    try:
        found = importlib.util.find_spec("ScreenCaptureKit") is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        return {"available": False, "reason": "ScreenCaptureKit for Python (PyObjC) is not installed."}
    return {"available": True, "reason": ""}


class ScreenCaptureSource:
    """Everything the main display plays, through an audio-only ScreenCaptureKit stream."""

    def __init__(self):
        self._stream: _sck.AudioStream | None = None

    def start(self, sink: Sink) -> None:
        if sys.platform != "darwin":
            raise Unavailable("unsupported", _MACOS_ONLY)
        try:
            sc, _cm, _appkit = _sck.frameworks()
        except ImportError as exc:
            raise Unavailable("unsupported", f"ScreenCaptureKit for Python (PyObjC) is not installed: {exc}") from exc
        try:
            content = _sck.shareable_content()
            displays = list(content.displays())
            if not displays:
                raise Unavailable("error", "There is no display to record the computer's sound from.")
            content_filter = sc.SCContentFilter.alloc().initWithDisplay_excludingWindows_(displays[0], [])
            self._stream = _sck.open_audio(content_filter, sink)
        except Unavailable:
            raise
        except Exception as exc:
            raise Unavailable(classify(exc), str(exc) or type(exc).__name__) from exc

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()


def default_source() -> Source:
    """The source a meeting records the computer's sound from."""
    return ScreenCaptureSource()


class SystemAudio:
    """One meeting's "Others" track: samples in, 30-second WAV chunks out."""

    def __init__(self, deliver: Deliver, source: Source, clock_ms: Callable[[], int], *,
                 chunk_ms: int = CHUNK_MS, rate: int = RATE):
        self._deliver = deliver
        self._source = source
        self._clock = clock_ms
        self._rate = rate
        self._chunk_bytes = max(SAMPLE_BYTES, int(rate * chunk_ms / 1000) * SAMPLE_BYTES)
        self._lock = threading.Lock()
        self._parts: list[bytes] = []
        self._size = 0
        self._chunk_start = 0
        self._seq = 0
        self._closed = False
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self.state = ""
        self.reason = ""
        self.message = ""

    # ------------------------------------------------------------------ life cycle
    def start(self) -> None:
        """Start the source; on failure the capture is ``unavailable`` and the meeting goes on."""
        self._thread = threading.Thread(target=self._work, name="meeting-system-audio", daemon=True)
        self._thread.start()
        try:
            self._source.start(self._take)
        except Unavailable as exc:
            self._unavailable(exc.reason, exc.message)
        except Exception as exc:  # the meeting records the microphone regardless
            self._unavailable("error", str(exc) or type(exc).__name__)
        else:
            self.state = "recording"

    def _unavailable(self, reason: str, message: str) -> None:
        _log.info("meeting system audio unavailable (%s): %s", reason, message)
        self.state, self.reason, self.message = "unavailable", reason, message
        with self._lock:
            self._closed = True
        self._queue.put(None)

    def stop(self, timeout: float = 30.0) -> None:
        """Stop the source, hand over the last partial chunk, and wait for delivery."""
        self._halt(flush=True, timeout=timeout)

    def cancel(self) -> None:
        """Stop without handing over anything more."""
        self._halt(flush=False, timeout=5.0)

    def _halt(self, *, flush: bool, timeout: float) -> None:
        if self.state == "recording":
            try:
                self._source.stop()
            except Exception:
                _log.exception("meeting system audio did not stop cleanly")
        with self._lock:
            if not self._closed:
                if flush and self._size:
                    self._cut()
                self._closed = True
                if not flush:
                    self._drain()
                self._queue.put(None)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def status(self) -> dict:
        return {"system_audio": self.state, "system_audio_reason": self.reason,
                "system_audio_message": self.message}

    # ------------------------------------------------------------------ samples
    def _ms(self, size: int) -> int:
        return int(size // SAMPLE_BYTES * 1000 / self._rate)

    def _take(self, data: bytes) -> None:
        """One buffer from the source (any thread); a full chunk is cut and queued."""
        data = data[: len(data) - len(data) % SAMPLE_BYTES]
        if not data:
            return
        with self._lock:
            if self._closed:
                return
            now = self._clock()
            while data:
                if not self._size:
                    # The chunk starts where this buffer's remaining samples began.
                    self._chunk_start = max(0, now - self._ms(len(data)))
                room = self._chunk_bytes - self._size
                piece, data = data[:room], data[room:]
                self._parts.append(piece)
                self._size += len(piece)
                if self._size >= self._chunk_bytes:
                    self._cut()

    def _cut(self) -> None:
        self._queue.put((self._seq, self._chunk_start, b"".join(self._parts)))
        self._seq += 1
        self._parts, self._size = [], 0

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            seq, t_ms, samples = job
            try:
                data, _peak = _sck.wav(samples, self._rate)
                self._deliver(seq, t_ms, data)
            except Exception:
                _log.exception("meeting system audio chunk %s was not taken", seq)


__all__ = ["CHUNK_MS", "RATE", "ScreenCaptureSource", "Source", "SystemAudio", "Unavailable", "classify",
           "default_source"]
