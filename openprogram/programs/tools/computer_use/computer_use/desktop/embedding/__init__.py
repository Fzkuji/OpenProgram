"""Another application's window shown inside an App tab.

Each platform does it the way its window system allows:

* **macOS** (``mode: "stream"``): a window belongs to the process that made
  it and cannot become part of another process's window. The App shows a
  live capture of the window in the tab and sends what the user does there
  back to it; the worker posts those events to the window's process through
  SkyLight, without activating it. The window itself is kept exactly
  behind the tab, at the tab's size, so its menus, pop-ups and input-method
  panels open where the tab shows them.
* **Windows** and **Linux (X11)** (``mode: "native"``): the window becomes a
  child of the App window (``SetParent`` / ``XReparentWindow``), so it
  moves, clips and takes input as part of it. Closing the tab, or the App,
  gives it back as it was.

The App passes a *host*: its own native window handle, its process id and
the tab's rectangle (screen points on macOS; device pixels in the host
window's client area elsewhere) and whether the tab is showing.

A window can also be *watched* (:meth:`Embedder.watch`) for a view that
only shows it: the embedder records it for input and state and leaves it
where it is, at its size, in its own parent.
"""
from __future__ import annotations

import platform
import threading
from typing import Any, Protocol


class EmbedError(Exception):
    """A window cannot be embedded, laid out or given back; the message says why."""


class Embedder(Protocol):
    mode: str
    # Whether the page forwards input for a holder that places the window,
    # and for one that only shows it.
    place_input: bool
    view_input: bool

    def list_windows(self, exclude_pids: set[int]) -> list[dict[str, Any]]: ...
    def attach(self, window_id: int, host: dict[str, Any]) -> dict[str, Any]: ...
    def watch(self, window_id: int) -> dict[str, Any]: ...
    def layout(self, window_id: int, host: dict[str, Any]) -> dict[str, Any]: ...
    def state(self, window_id: int) -> dict[str, Any]: ...
    def detach(self, window_id: int) -> None: ...
    def input(self, window_id: int, events: list[dict[str, Any]]) -> None: ...


def parse_host(value: Any) -> dict[str, Any]:
    """The host a request describes, validated: ``handle``, ``pid``, ``rect`` and ``visible``."""
    if not isinstance(value, dict):
        raise EmbedError("The App did not describe its window.")
    rect = value.get("rect")
    if (not isinstance(rect, (list, tuple)) or len(rect) != 4
            or not all(isinstance(v, (int, float)) and abs(v) < 1e6 for v in rect)):
        raise EmbedError("The tab's rectangle is missing.")
    handle = value.get("handle")
    try:
        handle = int(str(handle)) if handle not in (None, "") else 0
    except ValueError as exc:
        raise EmbedError("The App's window handle is not a number.") from exc
    pid = value.get("pid")
    return {
        "handle": handle,
        "pid": int(pid) if isinstance(pid, int) and pid > 0 else 0,
        "rect": [float(v) for v in rect],
        "visible": value.get("visible") is not False,
    }


_lock = threading.Lock()
_instance: Embedder | None = None


def embedder() -> Embedder:
    """The embedder for this platform; raises :class:`EmbedError` where there is none."""
    global _instance
    with _lock:
        if _instance is None:
            system = platform.system()
            if system == "Darwin":
                from .macos import MacEmbedder
                _instance = MacEmbedder()
            elif system == "Windows":
                from .windows import WindowsEmbedder
                _instance = WindowsEmbedder()
            elif system == "Linux":
                from .linux import X11Embedder
                _instance = X11Embedder()
            else:
                raise EmbedError(f"Embedding windows is not available on {system}.")
        return _instance


def release_all() -> None:
    """Give back every embedded window; called when the worker stops."""
    instance = _instance
    if instance is None:
        return
    for window_id in list(getattr(instance, "attached", {}) or {}):
        try:
            instance.detach(window_id)
        except Exception:
            pass


def embedded_children(tops, *, is_own, pid_of, children_of) -> list[tuple[int, int]]:
    """Windows of other processes embedded in an App window, as (window, its pid).

    ``tops`` are top-level windows; ``is_own(window, pid)`` says whether one
    belongs to an OpenProgram App process; ``children_of`` lists a window's
    direct children. A child of an App window that another process owns is
    an embedded application window, unless that process is the App's too
    (Chromium's GPU process draws into child windows of its own).
    Stateless, so a computer_use backend in any process finds them.
    """
    out: list[tuple[int, int]] = []
    seen: set[int] = set()
    for top in tops:
        parent_pid = pid_of(top)
        if not is_own(top, parent_pid):
            continue
        for child in children_of(top):
            pid = pid_of(child)
            if not pid or pid == parent_pid or child in seen or is_own(child, pid):
                continue
            seen.add(child)
            out.append((child, pid))
    return out


__all__ = ["EmbedError", "Embedder", "embedded_children", "embedder", "parse_host", "release_all"]
