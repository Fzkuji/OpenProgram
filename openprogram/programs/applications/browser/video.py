"""Video recording of one pane of the App, for ``computer_use_agent(target="browser")``.

The App records itself: the window that shows the conversation captures its
own page as a stream, crops it to the pane and writes an H.264 MP4 (see
``apps/web/lib/desktop/recording/pane-recorder.ts``). This module names the
file, sends the three commands over the desktop link and words the results.
It keeps no state: the window holds the recording, so a stop finds it
whichever process asks.

- ``panes``: the panes on screen in that window.
- ``start_video_recording(pane, max_duration)``: returns at once; the Agent
  acts while it records.
- ``stop_video_recording(discard)``: the file, its length and its size.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

ACTIONS = ("panes", "start_video_recording", "stop_video_recording")

DEFAULT_SECONDS = 60.0
MAX_SECONDS = 600.0
KEEP_RECORDINGS = 20

_NO_APP = ("Video recording needs the OpenProgram desktop App with a window open; "
           "open it and call again.")


def seconds(value: Any) -> float:
    """The recording limit: 60 seconds by default, at most 600."""
    if value in (None, ""):
        return DEFAULT_SECONDS
    try:
        limit = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_duration must be a number of seconds") from exc
    if not 0 < limit <= MAX_SECONDS:
        raise ValueError(f"max_duration must be between 0 and {MAX_SECONDS:g} seconds")
    return limit


def folder() -> Path:
    from openprogram.state.paths import get_state_dir

    return get_state_dir() / "computer-use" / "video"


def new_path(pane: str) -> Path:
    """A fresh file name for a recording of ``pane``; older recordings beyond the newest are removed."""
    root = folder()
    root.mkdir(parents=True, exist_ok=True)
    for old in sorted(root.glob("*.mp4"), key=lambda p: p.stat().st_mtime)[:-(KEEP_RECORDINGS - 1)]:
        old.unlink(missing_ok=True)
    stem = "".join(c if c.isalnum() else "-" for c in pane).strip("-").lower()[:40] or "pane"
    path = root / f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}.mp4"
    suffix = 1
    while path.exists():
        suffix += 1
        path = root / f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}-{suffix}.mp4"
    return path


def _request(command: dict, session: str, timeout: float) -> dict:
    from openprogram.host.resources import desktop_link

    return desktop_link.request({**command, "session_id": session}, timeout)


def _failure(reply: dict) -> dict:
    """A window's refusal as it stands; no answer at all means no App window is connected."""
    if reply.get("reason_code") and reply.get("error"):
        result = {"ok": False, "reason_code": reply["reason_code"], "error": reply["error"]}
        if reply.get("panes"):
            result["error"] += ". Panes: " + _pane_list(reply["panes"])
        return result
    return {"ok": False, "reason_code": "desktop_app_unavailable", "error": _NO_APP}


def _pane_list(panes: list[dict]) -> str:
    return "; ".join(
        f"{pane.get('id')} ({pane.get('kind')}{', ' + pane['title'] if pane.get('title') else ''}, "
        f"{pane.get('width')}×{pane.get('height')})" for pane in panes) or "none"


def run(action: str, args: dict[str, Any], session: str) -> dict[str, Any]:
    """One recording action; a failure is a result with ``ok: False``."""
    if action == "panes":
        reply = _request({"op": "record_panes"}, session, 5.0)
        if not reply.get("ok"):
            return _failure(reply)
        panes = reply.get("panes") or []
        lines = [f"- {pane.get('id')}: {pane.get('kind')}"
                 f"{' · ' + pane['title'] if pane.get('title') else ''} · {pane.get('width')}×{pane.get('height')}"
                 for pane in panes]
        return {"ok": True, "panes": panes,
                "done": "Panes on screen (pass an id as pane; \"conversation\" is this conversation's own pane, "
                        "\"window\" the whole window):\n" + ("\n".join(lines) or "(none)")}

    if action == "start_video_recording":
        try:
            limit = seconds(args.get("max_duration"))
        except ValueError as exc:
            return {"ok": False, "reason_code": "invalid_argument", "error": str(exc)}
        pane = str(args.get("pane") or "").strip()
        if not pane:
            return {"ok": False, "reason_code": "invalid_argument",
                    "error": "pane is required: \"conversation\", \"window\", or an id from panes"}
        path = new_path(pane)
        reply = _request({"op": "record_start", "pane": pane, "max_duration": limit, "path": str(path)},
                         session, 15.0)
        if not reply.get("ok"):
            return _failure(reply)
        recording = reply.get("recording") or {}
        return {"ok": True, "pane": pane, "width": recording.get("width"), "height": recording.get("height"),
                "max_duration": limit,
                "done": f"Recording {pane}, {recording.get('width')}×{recording.get('height')}, for up to "
                        f"{limit:g} seconds. Act as usual, then call stop_video_recording."}

    discard = bool(args.get("discard"))
    reply = _request({"op": "record_stop", "discard": discard}, session, 20.0)
    if not reply.get("ok"):
        return _failure(reply)
    recording = reply.get("recording") or {}
    if recording.get("discarded"):
        return {"ok": True, "discarded": True, "done": "The recording was discarded; nothing was saved."}
    path = str(recording.get("path") or "")
    if not path or not Path(path).is_file():
        return {"ok": False, "reason_code": "recording_missing",
                "error": f"the App reported the recording at {path or '(no path)'}, but no such file is on this computer"}
    ended = str(recording.get("ended") or "stopped")
    why = "" if ended == "stopped" else f" It ended before this call: {ended}."
    return {"ok": True, "path": path, "seconds": recording.get("seconds"), "width": recording.get("width"),
            "height": recording.get("height"), "fps": recording.get("fps"), "bytes": recording.get("bytes"),
            "ended": ended,
            "done": f"Recorded {recording.get('seconds')} seconds of {recording.get('pane')}, "
                    f"{recording.get('width')}×{recording.get('height')} at up to {recording.get('fps')} fps, "
                    f"saved to {path}.{why}"}
