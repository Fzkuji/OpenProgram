"""One recording at a time: its files, its time limit and its life cycle.

The capture backend reports raw events through :meth:`Recording.add`;
this module groups typing, decides when a screenshot is taken, writes
``events.jsonl`` and ``session.json`` and stops at the time limit.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from openprogram.host._platform.filesystem import replace_file

MAX_SECONDS = 600
WARN_SECONDS = 30
MAX_IMAGES = 60
SCREENSHOT_GAP = 0.6
TYPING_PAUSE = 1.5
PURPOSES = ("workflow", "context")


class RecordingError(RuntimeError):
    """A recording cannot start or does not exist.

    ``capability`` names the system access grant that is missing, when
    that is the reason (see :mod:`openprogram.host.system_access`).
    """

    def __init__(self, message: str, capability: str = "") -> None:
        super().__init__(message)
        self.capability = capability


def recordings_root() -> Path:
    from openprogram.state.paths import get_state_dir
    return Path(get_state_dir()) / "recordings"


@dataclass
class _Typing:
    """Characters typed into one element, not yet written."""
    first_t: int
    last: float
    event: dict
    chars: list[str] = field(default_factory=list)
    secure: bool = False
    value: str | None = None


class Recording:
    """A recording in progress or finished."""

    def __init__(self, directory: Path, purpose: str, capture_factory: Callable[["Recording"], Any] | None):
        self.id = directory.name
        self.directory = directory
        self.purpose = purpose
        self.started = time.time()
        self._mono = time.monotonic()
        self.state = "recording"
        self.end_reason = ""
        self.error = ""
        self._lock = threading.RLock()
        self._file = None
        self._typing: _Typing | None = None
        self._images = 0
        self._steps = 0
        self._last_shot = 0.0
        self._timer: threading.Timer | None = None
        self._flusher: threading.Timer | None = None
        self._capture = capture_factory(self) if capture_factory else None

    # ------------------------------------------------------------------ life cycle
    def begin(self) -> None:
        (self.directory / "images").mkdir(parents=True, exist_ok=True)
        self._file = open(self.directory / "events.jsonl", "a", encoding="utf-8")
        self._write_meta()
        self._timer = threading.Timer(MAX_SECONDS, self._time_limit)
        self._timer.daemon = True
        self._timer.start()
        if self._capture is not None:
            self._capture.start()

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._mono) * 1000)

    def _time_limit(self) -> None:
        self.finish("time_limit")

    def finish(self, reason: str = "done") -> None:
        with self._lock:
            if self.state != "recording":
                return
            self.state = "stopping"
        if self._capture is not None:
            try:
                self._capture.stop()
            except Exception as exc:  # the files are still worth keeping
                self.error = str(exc)
        with self._lock:
            self._flush_typing()
            for timer in (self._timer, self._flusher):
                if timer is not None:
                    timer.cancel()
            self.state = "finished"
            self.end_reason = reason
            self.duration_ms = self.elapsed_ms()
            if self._file is not None:
                self._file.close()
                self._file = None
            self._write_meta()

    def discard(self) -> None:
        self.finish("discarded")
        shutil.rmtree(self.directory, ignore_errors=True)

    def _write_meta(self) -> None:
        import platform
        meta = {
            "id": self.id,
            "purpose": self.purpose,
            "started_at": self.started,
            "ended_at": time.time() if self.state == "finished" else None,
            "duration_ms": getattr(self, "duration_ms", None),
            "end_reason": self.end_reason,
            "error": self.error,
            "steps": self._steps,
            "images": self._images,
            "platform": platform.system().lower(),
            "max_seconds": MAX_SECONDS,
        }
        temp = self.directory / "session.json.tmp"
        temp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_file(temp, self.directory / "session.json")

    def status(self) -> dict:
        with self._lock:
            elapsed = self.elapsed_ms() if self.state == "recording" else getattr(self, "duration_ms", 0)
            return {
                "id": self.id,
                "state": self.state,
                "purpose": self.purpose,
                "elapsed_ms": elapsed,
                "max_seconds": MAX_SECONDS,
                "warn_seconds": WARN_SECONDS,
                "steps": self._steps + (1 if self._typing else 0),
                "images": self._images,
                "end_reason": self.end_reason,
                "error": self.error,
            }

    # ------------------------------------------------------------------ events
    def _write(self, event: dict) -> None:
        if self._file is None:
            return
        self._file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._file.flush()
        if event.get("type") not in {"say", "note"}:
            self._steps += 1

    def add(self, event: dict, *, screenshot: Callable[[], bytes | None] | None = None) -> None:
        """Record one event from the capture backend.

        ``type`` events carry one character in ``char`` (or ``backspace``)
        and are grouped per element. ``screenshot`` is called when this
        event should carry an image.
        """
        with self._lock:
            if self.state not in {"recording", "stopping"} or self._file is None:
                return
            event = dict(event)
            event.setdefault("t", self.elapsed_ms())
            if event.get("type") == "type":
                self._add_char(event)
                return
            self._flush_typing()
            if screenshot is not None and self._wants_screenshot(event):
                data = None
                try:
                    data = screenshot()
                except Exception:
                    data = None
                if data:
                    self._images += 1
                    name = f"images/{self._images:04d}.jpg"
                    (self.directory / name).write_bytes(data)
                    event["image"] = name
                    self._last_shot = time.monotonic()
            self._write(event)

    def add_narration(self, t: int | None, text: str) -> None:
        text = " ".join(str(text or "").split())
        if not text:
            return
        with self._lock:
            if self._file is None:
                return
            stamp = self.elapsed_ms() if t is None else max(0, int(t))
            self._write({"t": stamp, "type": "say", "text": text[:2000]})

    def set_typing_value(self, element: dict, value: str) -> None:
        """The text field's contents after typing paused, as read from the field."""
        with self._lock:
            typing = self._typing
            if typing is not None and _same_element(typing.event.get("element"), element):
                typing.value = value

    def _wants_screenshot(self, event: dict) -> bool:
        if self._images >= MAX_IMAGES:
            return False
        kind = event.get("type")
        if kind == "key" and event.get("keys") != "return":
            return False
        if kind not in {"click", "key", "switch"}:
            return False
        return time.monotonic() - self._last_shot >= SCREENSHOT_GAP

    def _add_char(self, event: dict) -> None:
        element = event.get("element") or {}
        typing = self._typing
        same = typing is not None and _same_element(typing.event.get("element"), element) and \
            (typing.event.get("app") or {}) == (event.get("app") or {})
        if not same:
            self._flush_typing()
            base = {k: v for k, v in event.items() if k not in {"char", "backspace", "secure", "t"}}
            typing = self._typing = _Typing(first_t=int(event["t"]), last=time.monotonic(), event=base)
        typing.last = time.monotonic()
        if event.get("secure"):
            typing.secure = True
        elif event.get("backspace"):
            if typing.chars:
                typing.chars.pop()
        elif event.get("char"):
            typing.chars.append(str(event["char"]))
        self._schedule_flush()

    def _schedule_flush(self) -> None:
        if self._flusher is not None:
            self._flusher.cancel()
        self._flusher = threading.Timer(TYPING_PAUSE, self._flush_if_idle)
        self._flusher.daemon = True
        self._flusher.start()

    def _flush_if_idle(self) -> None:
        with self._lock:
            if self._typing is not None and time.monotonic() - self._typing.last >= TYPING_PAUSE - 0.05:
                self._flush_typing()

    def _flush_typing(self) -> None:
        typing, self._typing = self._typing, None
        if typing is None:
            return
        event = dict(typing.event, t=typing.first_t, type="type")
        if typing.secure:
            event["secure"] = True
        else:
            text = "".join(typing.chars)
            if not text and not typing.value:
                return
            event["text"] = text
            if typing.value is not None and typing.value != text:
                event["value"] = typing.value[:2000]
        self._write(event)


def _same_element(a: dict | None, b: dict | None) -> bool:
    """One element across keystrokes: its value changes as the user types."""
    def key(element):
        return {k: v for k, v in (element or {}).items() if k != "value"}
    return key(a) == key(b)


_lock = threading.Lock()
_recordings: dict[str, Recording] = {}


def support(system: str | None = None) -> dict:
    """Whether a demonstration can be recorded on this computer, and why not."""
    import platform
    if (system or platform.system()) != "Darwin":
        return {"available": False, "reason": "Recording is available on macOS only."}
    return {"available": True, "reason": ""}


def _default_capture(recording: Recording):
    state = support()
    if not state["available"]:
        raise RecordingError(state["reason"])
    from .macos import Capture
    return Capture(recording)


def start(purpose: str = "workflow", *, capture_factory: Callable[[Recording], Any] | None = _default_capture,
          root: Path | None = None) -> Recording:
    if purpose not in PURPOSES:
        raise RecordingError(f"Unknown recording purpose: {purpose}")
    with _lock:
        active = [r for r in _recordings.values() if r.state == "recording"]
        if active:
            raise RecordingError("Another recording is in progress.")
        directory = (root or recordings_root()) / uuid.uuid4().hex[:12]
        directory.mkdir(parents=True)
        try:
            recording = Recording(directory, purpose, capture_factory)
            recording.begin()
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        _recordings[recording.id] = recording
        return recording


def get(recording_id: str, *, root: Path | None = None) -> Recording | None:
    with _lock:
        return _recordings.get(recording_id)


def directory_of(recording_id: str, *, root: Path | None = None) -> Path:
    """The directory of a finished or running recording, by id."""
    if not recording_id or not all(c in "0123456789abcdef" for c in recording_id):
        raise RecordingError("Unknown recording.")
    directory = (root or recordings_root()) / recording_id
    if not (directory / "session.json").is_file():
        raise RecordingError("Unknown recording.")
    return directory


def active() -> Recording | None:
    with _lock:
        return next((r for r in _recordings.values() if r.state == "recording"), None)


__all__ = ["MAX_SECONDS", "PURPOSES", "Recording", "RecordingError", "active", "directory_of", "get",
           "recordings_root", "start"]
