"""Linux (X11): the window becomes a child of the App window.

``XReparentWindow`` moves a top-level window of another client into the
App's window, the way a window manager puts a window into its frame: the
window manager lets go of it, and it moves, clips and takes input as part
of the App window. Giving it back reparents it to the root window at the
place it had, where the window manager takes it again.

Wayland has no way for one client to place another's surface; there, and
when the App itself runs as a Wayland window, embedding is reported as
unavailable. Coordinates are pixels in the App window.

A view that only shows the window watches it: the window stays where it
is, and the page shows its capture without forwarding input.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import threading
import time
from typing import Any

from . import EmbedError

_MIN_SIZE = (120, 80)
_ANY_PROPERTY_TYPE = 0
_SUCCESS = 0
_NORMAL_TYPES = {"_NET_WM_WINDOW_TYPE_NORMAL", "_NET_WM_WINDOW_TYPE_DIALOG", ""}


class _XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int), ("height", ctypes.c_int),
        ("border_width", ctypes.c_int), ("depth", ctypes.c_int), ("visual", ctypes.c_void_p),
        ("root", ctypes.c_ulong), ("class", ctypes.c_int), ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int), ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_int), ("map_state", ctypes.c_int), ("all_event_masks", ctypes.c_long),
        ("your_event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int), ("screen", ctypes.c_void_p),
    ]


_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


def unavailable_reason(env: dict[str, str] | None = None) -> str:
    """Why this session cannot embed windows, or ``""`` when it can."""
    env = os.environ if env is None else env
    if not env.get("DISPLAY"):
        if env.get("WAYLAND_DISPLAY") or env.get("XDG_SESSION_TYPE") == "wayland":
            return "Wayland does not let one application place another's window. Use an X11 session to embed windows."
        return "No X display is available to embed windows on."
    if ctypes.util.find_library("X11") is None:
        return "libX11 is not installed."
    return ""


class X11Embedder:
    mode = "native"
    place_input = False
    view_input = False

    def __init__(self) -> None:
        reason = unavailable_reason()
        if reason:
            raise EmbedError(reason)
        x11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11"))
        x11.XInitThreads()
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XGetAtomName.restype = ctypes.c_void_p
        x11.XGetAtomName.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long, ctypes.c_int,
            ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
        x11.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XWindowAttributes)]
        x11.XTranslateCoordinates.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
        x11.XMoveResizeWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_uint, ctypes.c_uint]
        x11.XReparentWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
        for name in ("XMapWindow", "XMapRaised", "XUnmapWindow"):
            getattr(x11, name).argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
        x11.XWithdrawWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
        x11.XQueryTree.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint)]
        x11.XFree.argtypes = [ctypes.c_void_p]
        x11.XSetErrorHandler.restype = ctypes.c_void_p
        x11.XSetErrorHandler.argtypes = [_ERROR_HANDLER]
        self._x = x11
        self._display = x11.XOpenDisplay(None)
        if not self._display:
            raise EmbedError("The X display could not be opened.")
        self._root = x11.XDefaultRootWindow(self._display)
        self._errors = 0
        # Kept on the instance: the library calls it for as long as the display is open.
        self._on_error = _ERROR_HANDLER(self._record_error)
        x11.XSetErrorHandler(self._on_error)
        self._lock = threading.RLock()
        self.attached: dict[int, dict[str, Any]] = {}

    def _record_error(self, _display, _event) -> int:
        self._errors += 1
        return 0

    # ------------------------------------------------------------------ properties
    def _atom(self, name: str) -> int:
        return self._x.XInternAtom(self._display, name.encode(), 0)

    def _property(self, window: int, name: str, kind: str = "") -> tuple[int, int, bytes]:
        """(format, item count, raw bytes) of a window property; (0, 0, b"") when absent."""
        actual_type, actual_format = ctypes.c_ulong(), ctypes.c_int()
        count, remaining, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        status = self._x.XGetWindowProperty(
            self._display, window, self._atom(name), 0, 1 << 16, 0,
            self._atom(kind) if kind else _ANY_PROPERTY_TYPE, ctypes.byref(actual_type), ctypes.byref(actual_format),
            ctypes.byref(count), ctypes.byref(remaining), ctypes.byref(data))
        if status != _SUCCESS or not data.value:
            return 0, 0, b""
        try:
            fmt, items = actual_format.value, count.value
            # Format 32 items are C longs in the client's memory.
            width = ctypes.sizeof(ctypes.c_long) if fmt == 32 else fmt // 8
            return fmt, items, ctypes.string_at(data.value, items * width)
        finally:
            self._x.XFree(data)

    def _ids(self, window: int, name: str) -> list[int]:
        fmt, count, raw = self._property(window, name)
        if fmt != 32:
            return []
        return list((ctypes.c_ulong * count).from_buffer_copy(raw))

    def _text(self, window: int, name: str, kind: str = "") -> str:
        _, _, raw = self._property(window, name, kind)
        return raw.split(b"\0")[0].decode("utf-8", "replace") if name != "WM_CLASS" else raw.decode(
            "utf-8", "replace")

    def _atom_name(self, atom: int) -> str:
        pointer = self._x.XGetAtomName(self._display, atom)
        if not pointer:
            return ""
        try:
            return ctypes.string_at(pointer).decode()
        finally:
            self._x.XFree(pointer)

    def _geometry(self, window: int) -> tuple[int, int, int, int] | None:
        attributes = _XWindowAttributes()
        if not self._x.XGetWindowAttributes(self._display, window, ctypes.byref(attributes)):
            return None
        x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        self._x.XTranslateCoordinates(self._display, window, self._root, 0, 0, ctypes.byref(x), ctypes.byref(y),
                                      ctypes.byref(child))
        return x.value, y.value, attributes.width, attributes.height

    def _parent(self, window: int) -> int:
        root, parent, children, count = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p(), ctypes.c_uint()
        if not self._x.XQueryTree(self._display, window, ctypes.byref(root), ctypes.byref(parent),
                                  ctypes.byref(children), ctypes.byref(count)):
            return 0
        if children.value:
            self._x.XFree(children)
        return parent.value

    def _withdraw(self, window_id: int) -> None:
        """Take the window from the window manager before moving it.

        A managed window sits in the window manager's frame, and the window
        manager reparents it again on its own schedule. Withdrawing it
        (ICCCM 4.1.4) makes the window manager let go and put it back under
        the root; only then is it ours to move.
        """
        self._x.XWithdrawWindow(self._display, window_id, self._x.XDefaultScreen(self._display))
        self._x.XSync(self._display, 0)
        deadline = time.monotonic() + 1.0
        while self._parent(window_id) not in (self._root, 0) and time.monotonic() < deadline:
            time.sleep(0.01)
            self._x.XSync(self._display, 0)

    def _exists(self, window: int) -> bool:
        attributes = _XWindowAttributes()
        before = self._errors
        ok = bool(self._x.XGetWindowAttributes(self._display, window, ctypes.byref(attributes)))
        return ok and self._errors == before

    # ------------------------------------------------------------------ listing
    def list_windows(self, exclude_pids: set[int]) -> list[dict[str, Any]]:
        excluded = set(exclude_pids) | {os.getpid()}
        out = []
        with self._lock:
            for window in self._ids(self._root, "_NET_CLIENT_LIST"):
                pids = self._ids(window, "_NET_WM_PID")
                pid = int(pids[0]) if pids else 0
                types = {self._atom_name(atom) for atom in self._ids(window, "_NET_WM_WINDOW_TYPE")} or {""}
                if pid in excluded or not types & _NORMAL_TYPES:
                    continue
                geometry = self._geometry(window)
                if geometry is None or geometry[2] < _MIN_SIZE[0] or geometry[3] < _MIN_SIZE[1]:
                    continue
                title = self._text(window, "_NET_WM_NAME", "UTF8_STRING") or self._text(window, "WM_NAME")
                classes = [part for part in self._text(window, "WM_CLASS").split("\0") if part]
                app = classes[-1] if classes else ""
                if app.lower().startswith("openprogram"):
                    continue
                out.append({"id": int(window), "pid": pid, "app": app or title, "title": title, "icon": "",
                            "bounds": list(geometry)})
        return out

    # ------------------------------------------------------------------ attach
    def attach(self, window_id: int, host: dict[str, Any]) -> dict[str, Any]:
        host_window = int(host.get("handle") or 0)
        with self._lock:
            if not host_window or not self._exists(host_window):
                raise EmbedError("The App's window is not an X11 window (it may be running under Wayland).")
            if not self._exists(window_id):
                raise EmbedError("That window is closed.")
            record = self.attached.get(window_id)
            if record is None or record.get("watched"):
                geometry = self._geometry(window_id)
                record = {"geometry": geometry, "host": host_window}
                before = self._errors
                self._withdraw(window_id)
                self._x.XReparentWindow(self._display, window_id, host_window, 0, 0)
                self._x.XSync(self._display, 0)
                if self._errors != before:
                    self._x.XMapWindow(self._display, window_id)
                    self._x.XSync(self._display, 0)
                    raise EmbedError("The X server refused to move this window into the App.")
                self.attached[window_id] = record
            self._place(window_id, host)
            return self._describe(window_id)

    def watch(self, window_id: int) -> dict[str, Any]:
        """Record the window for state without moving, sizing or reparenting it."""
        with self._lock:
            if not self._exists(window_id):
                raise EmbedError("That window is closed.")
            if window_id not in self.attached:
                pids = self._ids(window_id, "_NET_WM_PID")
                self.attached[window_id] = {"watched": True, "pid": int(pids[0]) if pids else 0,
                                            "title": self._text(window_id, "_NET_WM_NAME", "UTF8_STRING")
                                            or self._text(window_id, "WM_NAME")}
            return self._describe(window_id)

    def _place(self, window_id: int, host: dict[str, Any]) -> None:
        if not host.get("visible", True):
            self._x.XUnmapWindow(self._display, window_id)
        else:
            x, y, width, height = (int(round(v)) for v in host["rect"])
            self._x.XMoveResizeWindow(self._display, window_id, x, y, max(1, width), max(1, height))
            self._x.XMapRaised(self._display, window_id)
        self._x.XSync(self._display, 0)

    def _describe(self, window_id: int) -> dict[str, Any]:
        pids = self._ids(window_id, "_NET_WM_PID")
        title = self._text(window_id, "_NET_WM_NAME", "UTF8_STRING") or self._text(window_id, "WM_NAME")
        geometry = self._geometry(window_id)
        record = self.attached.get(window_id) or {}
        return {"mode": self.mode, "id": window_id, "pid": int(pids[0]) if pids else 0, "app": "",
                "title": title, "frame": list(geometry) if geometry else None, "open": True,
                "placed": bool(record) and not record.get("watched"), "input": False}

    def layout(self, window_id: int, host: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if (self.attached.get(window_id) or {"watched": True}).get("watched"):
                raise EmbedError("That window is not embedded.")
            if not self._exists(window_id):
                self.attached.pop(window_id, None)
                return {"id": window_id, "open": False}
            self._place(window_id, host)
            return self._describe(window_id)

    def state(self, window_id: int) -> dict[str, Any]:
        with self._lock:
            if not self._exists(window_id):
                self.attached.pop(window_id, None)
                return {"id": window_id, "open": False}
            return self._describe(window_id)

    def detach(self, window_id: int) -> None:
        with self._lock:
            record = self.attached.pop(window_id, None)
            if record is None or record.get("watched") or not self._exists(window_id):
                return
            x, y, width, height = record.get("geometry") or (0, 0, 800, 600)
            self._x.XUnmapWindow(self._display, window_id)
            self._x.XReparentWindow(self._display, window_id, self._root, x, y)
            self._x.XMoveResizeWindow(self._display, window_id, x, y, max(1, width), max(1, height))
            self._x.XMapWindow(self._display, window_id)
            self._x.XSync(self._display, 0)

    def input(self, window_id: int, events: list[dict[str, Any]]) -> None:
        # The window is part of the App's window and takes input there itself.
        return None


__all__ = ["X11Embedder", "unavailable_reason"]
