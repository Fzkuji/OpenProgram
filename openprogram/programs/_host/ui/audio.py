"""Recordings the host makes for an application (capability ``audio.record``).

A window asks for one with ``openprogramApp.audio.start``; the recording
then belongs to the host, not to the window, so closing the window or
switching tabs does not stop it, only ``stop``, the time limit or
disabling the application does. One instance records one thing at a time.

Two tracks share one clock that starts with the recording:

``mic``
    The microphone, recorded by the App's page with the browser's echo
    cancellation (the sandboxed window has no microphone of its own) and
    posted here in self-contained chunks of about 30 seconds.
``system``
    The computer's sound, recorded here in the worker
    (``host._platform.system_sound``, ScreenCaptureKit on macOS) in the
    same cadence. Where it cannot be recorded the recording goes on with
    the microphone and its status says why.

Every chunk is written under the instance's files
(``<folder>/<track>/<seq>.<ext>``, ``storage.files``) and handed, one at a
time and in order of arrival, to the operation the manifest names as
``recording.ingest`` with ``{key, track, seq, t_ms, path}``. When the
recording stops and every chunk is handed over, ``recording.finish`` (if
declared) runs with ``{key, recording}``, the recording's final status.

Every change signals the topic ``application-audio`` under the instance
id, so the window reads the status again.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import re
import threading
import time
import uuid
from typing import Any, Callable

from openprogram.host._platform.changes import publish, signal

from . import catalog, state

_log = logging.getLogger(__name__)

TRACKS = ("mic", "system")
#: The longest a recording runs; a window may ask for less.
MAX_SECONDS = 4 * 3600
MAX_CHUNK_BYTES = 16 * 1024 * 1024
MAX_SEQ = 100_000
SUFFIXES = {"webm", "ogg", "mp4", "wav"}
KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
#: How long one ingest or finish operation may take before the next goes on.
OPERATION_TIMEOUT_SECONDS = 60 * 60

#: A recording of an instance changed (key: the instance id).
publish("application-audio")


class RecordingError(ValueError):
    """A recording cannot start, take a chunk, or does not exist."""


#: ``run(instance_id, operation, input, request_key)`` → the finished run; set by the server.
_runner: Callable[[str, str, dict, str], dict] | None = None


def attach(service, loop: asyncio.AbstractEventLoop) -> None:
    """Hand operations to the worker's application service on its event loop."""
    global _runner

    def run(instance_id: str, operation: str, value: dict, request_key: str) -> dict:
        submitted = asyncio.run_coroutine_threadsafe(
            service.submit(instance_id, operation, value, request_key), loop).result(timeout=120)
        deadline = time.monotonic() + OPERATION_TIMEOUT_SECONDS
        run = submitted
        while run.get("status") not in {"completed", "failed", "cancelled", "interrupted"}:
            if time.monotonic() > deadline:
                break
            time.sleep(0.2)
            run = service.describe(submitted["id"])
        return run

    _runner = run


def detach() -> None:
    global _runner
    _runner = None


def _system_source():
    from openprogram.host._platform import system_sound
    return system_sound.default_source()


class Recording:
    """One recording of one instance."""

    def __init__(self, instance: dict, definition: dict, key: str, folder: str, tracks: tuple[str, ...],
                 max_seconds: int, *, system_source: Callable[[], Any] | None = None):
        self.id = uuid.uuid4().hex
        self.instance_id = instance["id"]
        self.app_id = definition["id"]
        self.key = key
        self.folder = folder
        self.tracks = tracks
        self.ingest = definition["recording"]["ingest"]
        self.finish_operation = definition["recording"].get("finish", "")
        self.max_seconds = max_seconds
        self.started_at = time.time()
        self._mono = time.monotonic()
        self.state = "recording"
        self.end_reason = ""
        self.duration_ms = 0
        self.received = {track: 0 for track in tracks}
        self.delivered = 0
        self.failed = 0
        self.errors: list[str] = []
        self.system_audio = ""
        self.system_audio_reason = ""
        self.system_audio_message = ""
        self._system_source = system_source or _system_source
        self._system = None
        self._seen: set[tuple[str, int]] = set()
        self._lock = threading.RLock()
        self._queue: queue.Queue = queue.Queue()
        self._timer: threading.Timer | None = None
        self._worker: threading.Thread | None = None
        self._stopping = False
        self.settled = threading.Event()

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._mono) * 1000)

    # ------------------------------------------------------------------ life cycle
    def begin(self) -> None:
        for track in self.tracks:
            (state.files_root(self.instance_id) / self.folder / track).mkdir(parents=True, exist_ok=True)
        self._worker = threading.Thread(target=self._work, name=f"application-audio-{self.app_id}", daemon=True)
        self._worker.start()
        if "system" in self.tracks:
            self._start_system()
        self._timer = threading.Timer(self.max_seconds, lambda: self.stop("time_limit"))
        self._timer.daemon = True
        self._timer.start()
        self._changed()

    def _start_system(self) -> None:
        from openprogram.host._platform import system_sound
        try:
            source = self._system_source()
        except Exception as exc:  # noqa: BLE001 — the microphone records regardless
            self._note_system("unavailable", "error", str(exc) or type(exc).__name__)
            return
        self._system = system_sound.SystemAudio(self._take_system, source, self.elapsed_ms)
        self._system.start()
        status = self._system.status()
        self._note_system(status["system_audio"], status["system_audio_reason"], status["system_audio_message"])

    def _note_system(self, value: str, reason: str, message: str) -> None:
        with self._lock:
            self.system_audio, self.system_audio_reason, self.system_audio_message = value, reason, message

    def _take_system(self, seq: int, t_ms: int, data: bytes) -> None:
        try:
            self.take("system", seq, t_ms, data, "wav")
        except RecordingError:
            if self.state == "recording":
                raise
            # Otherwise the recording stopped while the chunk was on its way.

    def stop(self, reason: str = "done") -> None:
        """Stop taking audio; what arrived is still handed over, then ``finish`` runs."""
        with self._lock:
            if self.state != "recording" or self._stopping:
                return
            self._stopping = True
        # The computer's sound: its last partial chunk goes in while the recording still takes chunks.
        if self._system is not None:
            self._system.stop()
        with self._lock:
            self.state = "processing"
            self.end_reason = reason
            self.duration_ms = self.elapsed_ms()
            if self._timer is not None:
                self._timer.cancel()
        self._queue.put(None)
        self._changed()

    def cancel(self) -> None:
        """Stop without handing over anything more (the application was disabled)."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self.state = "cancelled"
        if self._system is not None:
            self._system.cancel()
        self._queue.put(None)
        self._changed()

    # ------------------------------------------------------------------ chunks
    def take(self, track: str, seq: int, t_ms: int, data: bytes, suffix: str = "webm") -> dict:
        """Store one chunk and queue it for the application. A repeated chunk is ignored."""
        if track not in self.tracks:
            raise RecordingError(f"Unknown track: {track}")
        if not 0 <= int(seq) < MAX_SEQ or int(t_ms) < 0:
            raise RecordingError("Bad chunk number or offset.")
        if not data:
            raise RecordingError("The chunk is empty.")
        if len(data) > MAX_CHUNK_BYTES:
            raise RecordingError("The chunk is too large.")
        suffix = suffix if suffix in SUFFIXES else "webm"
        with self._lock:
            if self.state != "recording":
                raise RecordingError("The recording has stopped.")
            if (track, int(seq)) in self._seen:
                return self.status()
            self._seen.add((track, int(seq)))
            relative = f"{self.folder}/{track}/{int(seq):05d}.{suffix}"
            (state.files_root(self.instance_id) / relative).write_bytes(data)
            self.received[track] += 1
        self._queue.put((track, int(seq), int(t_ms), relative))
        self._changed()
        return self.status()

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                break
            if self.state == "cancelled":
                return
            track, seq, t_ms, relative = job
            outcome = self._run(self.ingest, {"key": self.key, "track": track, "seq": seq, "t_ms": t_ms,
                                              "path": relative}, f"{self.id}:{track}:{seq}")
            with self._lock:
                if outcome:
                    self.failed += 1
                    self.errors = (self.errors + [outcome])[-5:]
                else:
                    self.delivered += 1
            self._changed()
        if self.state != "cancelled" and self.finish_operation:
            outcome = self._run(self.finish_operation, {"key": self.key, "recording": self.status()},
                                f"{self.id}:finish")
            if outcome:
                with self._lock:
                    self.errors = (self.errors + [outcome])[-5:]
        with self._lock:
            if self.state != "cancelled":
                self.state = "done"
        self.settled.set()
        self._changed()

    def _run(self, operation: str, value: dict, request_key: str) -> str:
        """Run one operation of the application; ``""`` when it completed, else why not."""
        if _runner is None:
            return "the worker's application service is not running"
        try:
            run = _runner(self.instance_id, operation, value, request_key)
        except Exception as exc:  # noqa: BLE001 — one chunk must not stop the recording
            _log.warning("application %s %s did not run: %s", self.app_id, operation, exc)
            return str(exc) or type(exc).__name__
        if run.get("status") == "completed":
            return ""
        return str(run.get("error") or run.get("status") or "the operation did not finish")

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "key": self.key,
                "state": self.state,
                "tracks": list(self.tracks),
                "started_at": self.started_at,
                "elapsed_ms": self.duration_ms if self.state != "recording" else self.elapsed_ms(),
                "duration_ms": self.duration_ms if self.state != "recording" else self.elapsed_ms(),
                "end_reason": self.end_reason,
                "max_seconds": self.max_seconds,
                "received": dict(self.received),
                "delivered": self.delivered,
                "failed": self.failed,
                "errors": list(self.errors),
                "system_audio": self.system_audio,
                "system_audio_reason": self.system_audio_reason,
                "system_audio_message": self.system_audio_message,
            }

    def _changed(self) -> None:
        signal("application-audio", self.instance_id)


_lock = threading.Lock()
#: instance id → its latest recording (kept after it ends, until the next one starts).
_recordings: dict[str, Recording] = {}


def _definition(instance_id: str) -> tuple[dict, dict]:
    instance = state.get_instance(instance_id)
    definition = catalog.get(instance["app_id"])
    if "audio.record" not in definition["capabilities"]:
        raise RecordingError("application does not declare audio.record")
    return instance, definition


def start(instance_id: str, key: Any, *, tracks: Any = TRACKS, max_seconds: Any = MAX_SECONDS,
          system_source: Callable[[], Any] | None = None) -> dict:
    """Start recording for ``instance_id`` under the application's ``key`` (its own name for it)."""
    instance, definition = _definition(instance_id)
    if not isinstance(key, str) or not KEY.match(key):
        raise RecordingError("invalid recording key")
    wanted = tuple(track for track in TRACKS if track in (tracks or ()))
    if not wanted:
        raise RecordingError("a recording needs at least one track (mic, system)")
    try:
        seconds = max(1, min(MAX_SECONDS, int(max_seconds)))
    except (TypeError, ValueError):
        raise RecordingError("max_seconds must be a number") from None
    with _lock:
        current = _recordings.get(instance_id)
        if current is not None and current.state == "recording":
            raise RecordingError("This application is already recording.")
        recording = Recording(instance, definition, key, f"recordings/{key}", wanted, seconds,
                              system_source=system_source)
        _recordings[instance_id] = recording
    recording.begin()
    return recording.status()


def _current(instance_id: str, recording_id: str) -> Recording:
    _definition(instance_id)
    with _lock:
        recording = _recordings.get(instance_id)
    if recording is None or recording.id != recording_id:
        raise FileNotFoundError("no such recording")
    return recording


def take(instance_id: str, recording_id: str, track: str, seq: int, t_ms: int, data: bytes, suffix: str) -> dict:
    return _current(instance_id, recording_id).take(track, seq, t_ms, data, suffix)


def stop(instance_id: str, recording_id: str, reason: str = "done") -> dict:
    recording = _current(instance_id, recording_id)
    recording.stop(reason)
    return recording.status()


def status(instance_id: str) -> dict | None:
    """The instance's latest recording, or ``None`` (none since the worker started)."""
    _definition(instance_id)
    with _lock:
        recording = _recordings.get(instance_id)
    return recording.status() if recording is not None else None


def cancel_application(app_id: str) -> None:
    """Stop every recording of ``app_id`` without running its operations (it is being disabled)."""
    with _lock:
        live = [r for r in _recordings.values() if r.app_id == app_id and r.state in ("recording", "processing")]
    for recording in live:
        recording.cancel()


def stop_all(reason: str = "stopped") -> None:
    """The worker stops: end every recording; what it recorded stays in the application's files."""
    with _lock:
        live = [r for r in _recordings.values() if r.state == "recording"]
    for recording in live:
        recording.cancel()


def support() -> dict:
    """Whether the computer's sound can be recorded here, and why not; asks the OS for nothing."""
    from openprogram.host._platform import system_sound
    return system_sound.support()


__all__ = ["MAX_SECONDS", "Recording", "RecordingError", "TRACKS", "attach", "cancel_application", "detach",
           "start", "status", "stop", "stop_all", "support", "take"]
