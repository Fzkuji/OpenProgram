"""The native app window an Agent operates, for the conversation's preview.

A GUI step that acted on a macOS window returns that window's identity as
``target`` (window_id, pid, app_name) together with the screenshot it
observed. The worker keeps the latest frame per conversation and tells the
App, so the chat preview follows desktop apps the way it follows web pages.
Frames live in memory only: they are what the Agent saw, not a record.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

_log = logging.getLogger(__name__)

_MAX_FRAME_BYTES = 8 * 1024 * 1024
_FRAME_KEYS = ("screenshot_artifact", "img_path")
_lock = threading.Lock()
_latest: dict[str, dict[str, Any]] = {}


def operated_window(output: Any) -> dict[str, Any] | None:
    """The window a function result says it acted on, if it names one."""
    if not isinstance(output, Mapping):
        return None
    target = output.get("target")
    if not isinstance(target, Mapping):
        return None
    window_id, pid, app_name = target.get("window_id"), target.get("pid"), target.get("app_name")
    if type(window_id) is not int or type(pid) is not int or not isinstance(app_name, str):
        return None
    return {"window_id": window_id, "pid": pid, "app_name": app_name[:120]}


def _frame_path(value: Any, depth: int = 0) -> Path | None:
    if depth > 3:
        return None
    if isinstance(value, Mapping):
        for key in _FRAME_KEYS:
            path = value.get(key)
            if isinstance(path, str) and path.lower().endswith(".png"):
                candidate = Path(path)
                if candidate.is_file():
                    return candidate
        for item in value.values():
            found = _frame_path(item, depth + 1)
            if found is not None:
                return found
    return None


def _read_frame(output: Mapping[str, Any]) -> bytes | None:
    path = _frame_path(output)
    if path is None:
        return None
    try:
        if path.stat().st_size > _MAX_FRAME_BYTES:
            return None
        # Read now: the harness may delete its capture after the step.
        return path.read_bytes()
    except OSError:
        return None


def _owner() -> tuple[str | None, str | None]:
    try:
        from openprogram.host.processes import current_owner
        session_id, execution_id, _ = current_owner()
        return session_id, execution_id
    except Exception:
        from openprogram.state import execution_facts
        return execution_facts.current_session_id(), execution_facts.current_execution_id()


_forward: Callable[[dict[str, Any]], None] | None = None


def forward_to_parent(sink: Callable[[dict[str, Any]], None] | None) -> None:
    """In an agentic subprocess, hand windows to the worker instead of keeping them."""
    global _forward
    _forward = sink


def note_operated_window(output: Any) -> None:
    """Record the window a finished function acted on and tell the App."""
    window = operated_window(output)
    if window is None:
        return
    session_id, execution_id = _owner()
    observed = {**window, "execution_id": execution_id or "", "frame": _read_frame(output)}
    if _forward is not None:
        _forward(observed)
        return
    if session_id:
        record_operated_window(observed, session_id=session_id)


def record_operated_window(
    observed: Mapping[str, Any], *, session_id: str, execution_id: str | None = None,
) -> None:
    """Keep the latest window of a conversation and announce it.

    The caller names the conversation; a subprocess's own claim is not used.
    """
    window = operated_window({"target": observed})
    if window is None or not session_id:
        return
    frame = observed.get("frame")
    frame = frame if isinstance(frame, bytes) and len(frame) <= _MAX_FRAME_BYTES else None
    with _lock:
        previous = _latest.get(session_id) or {}
        same_window = previous.get("window_id") == window["window_id"]
        entry = {
            **window,
            "session_id": session_id,
            "execution_id": str(execution_id or observed.get("execution_id") or ""),
            "sequence": int(previous.get("sequence") or 0) + 1,
            "at": time.time(),
            # A step without a capture keeps the last frame of the same window.
            "frame": frame if frame is not None else (previous.get("frame") if same_window else None),
        }
        _latest[session_id] = entry
    try:
        from openprogram.host import runtime_facts
        runtime_facts.broadcast({"type": "native.window", "data": public_window(entry)})
    except Exception:
        _log.debug("native window broadcast failed", exc_info=True)


_action_sequence = 0


def note_action_point(app_name: str, point: tuple[float, float], action: str) -> None:
    """Tell the App where on screen an Agent step lands, for its agent cursor.

    ``point`` is in screen points with the origin at the top left of the main
    display, the space both macOS accessibility frames and Electron use.
    """
    global _action_sequence
    x, y = float(point[0]), float(point[1])
    with _lock:
        _action_sequence += 1
        sequence = _action_sequence
    session_id, execution_id = _owner()
    observed = {"kind": "action", "x": x, "y": y, "action": str(action)[:40],
                "app_name": str(app_name)[:120], "sequence": sequence, "execution_id": execution_id or ""}
    if _forward is not None:
        _forward(observed)
        return
    if session_id:
        record_action_point(observed, session_id=session_id)


def control_owner() -> tuple[str | None, str | None]:
    """The conversation and run of the calling step, for reports made off its thread."""
    return _owner()


def note_control(observed: Mapping[str, Any], *, owner: Any = None) -> None:
    """Tell the App where the window an Agent controls is, or that control ended."""
    session_id = owner[0] if isinstance(owner, tuple) and owner else None
    observed = {**observed, "kind": "control"}
    if _forward is not None:
        _forward(observed)
        return
    if session_id:
        record_control(observed, session_id=session_id)


def _rect(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        rect = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    return rect if all(math.isfinite(v) for v in rect) and rect[2] >= 0 and rect[3] >= 0 else None


def record_control(observed: Mapping[str, Any], *, session_id: str) -> None:
    """Announce a controlled window's place to the conversation's App windows."""
    window_id = observed.get("window_id")
    if type(window_id) is not int or not session_id:
        return
    occluders = observed.get("occluders")
    data = {
        "session_id": session_id, "window_id": window_id, "active": observed.get("active") is True,
        "app_name": str(observed.get("app_name") or "")[:120],
        "frame": _rect(observed.get("frame")), "buttons": _rect(observed.get("buttons")),
        "occluders": [r for r in (_rect(o) for o in occluders[:32]) if r] if isinstance(occluders, list) else [],
        "at": time.time(),
    }
    try:
        from openprogram.host import runtime_facts
        runtime_facts.broadcast({"type": "native.control", "data": data})
    except Exception:
        _log.debug("native control broadcast failed", exc_info=True)


def record_action_point(observed: Mapping[str, Any], *, session_id: str) -> None:
    """Announce one step's screen point to the conversation's App windows."""
    try:
        x, y, sequence = float(observed["x"]), float(observed["y"]), int(observed["sequence"])
    except (KeyError, TypeError, ValueError):
        return
    if not session_id:
        return
    data = {"session_id": session_id, "x": x, "y": y, "sequence": sequence, "at": time.time(),
            "action": str(observed.get("action") or "")[:40], "app_name": str(observed.get("app_name") or "")[:120]}
    try:
        from openprogram.host import runtime_facts
        runtime_facts.broadcast({"type": "native.action", "data": data})
    except Exception:
        _log.debug("native action broadcast failed", exc_info=True)


def public_window(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: entry[key]
        for key in ("session_id", "execution_id", "window_id", "pid", "app_name", "sequence", "at")
    } | {"has_frame": entry.get("frame") is not None}


def latest_window(session_id: str) -> dict[str, Any] | None:
    with _lock:
        entry = _latest.get(session_id)
        return dict(entry) if entry else None


__all__ = [
    "control_owner", "forward_to_parent", "latest_window", "note_action_point", "note_control",
    "note_operated_window", "operated_window", "public_window", "record_action_point", "record_control",
    "record_operated_window",
]
