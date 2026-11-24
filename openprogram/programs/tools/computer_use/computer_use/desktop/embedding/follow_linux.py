"""X11 window events: structure and property changes on each followed window.

A connection of its own selects ``StructureNotifyMask`` (configure, map,
unmap, reparent, destroy) and ``PropertyChangeMask`` (the window's name and
state) on each followed window, beside whatever the window's own client
selects. One thread waits in ``select`` on that connection and on a pipe
that brings it windows to add and remove; nothing wakes it otherwise.

The embedder's error handler (``linux.py``) is already installed for the
process, so a window that is gone by the time it is selected costs one
ignored ``BadWindow``.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import select
import threading
from collections import deque

from .follow import Report

STRUCTURE_NOTIFY_MASK = 1 << 17
PROPERTY_CHANGE_MASK = 1 << 22
DESTROY_NOTIFY = 17
UNMAP_NOTIFY = 18
MAP_NOTIFY = 19
REPARENT_NOTIFY = 21
CONFIGURE_NOTIFY = 22
PROPERTY_NOTIFY = 28
STRUCTURE_EVENTS = frozenset({DESTROY_NOTIFY, UNMAP_NOTIFY, MAP_NOTIFY, REPARENT_NOTIFY, CONFIGURE_NOTIFY})
PROPERTIES = ("_NET_WM_NAME", "WM_NAME", "_NET_WM_STATE")


class _XAnyEvent(ctypes.Structure):
    # The head every XEvent shares; ``window`` is the window an event was
    # selected on (``event`` in a structure event), and the next field is a
    # PropertyNotify's atom.
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("atom", ctypes.c_ulong)]


class _XEvent(ctypes.Union):
    _fields_ = [("any", _XAnyEvent), ("pad", ctypes.c_long * 24)]


def changed_window(kind: int, window: int, atom: int, followed, properties) -> int | None:
    """The followed window an X event says changed, or None."""
    if window not in followed:
        return None
    if kind in STRUCTURE_EVENTS:
        return window
    if kind == PROPERTY_NOTIFY and atom in properties:
        return window
    return None


class X11WindowEvents:
    def __init__(self, report: Report) -> None:
        name = ctypes.util.find_library("X11")
        if not name or not os.environ.get("DISPLAY"):
            raise RuntimeError("No X display to follow windows on.")
        x11 = ctypes.cdll.LoadLibrary(name)
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XConnectionNumber.argtypes = [ctypes.c_void_p]
        x11.XSelectInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_long]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XPending.argtypes = [ctypes.c_void_p]
        x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XEvent)]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self._x = x11
        self._display = x11.XOpenDisplay(None)
        if not self._display:
            raise RuntimeError("The X display could not be opened.")
        self._properties = frozenset(x11.XInternAtom(self._display, item.encode(), 0) for item in PROPERTIES)
        self._report = report
        self._windows: set[int] = set()
        self._commands: deque[tuple[str, int]] = deque()
        self._closed = False
        self._wake_read, self._wake_write = os.pipe()
        self._thread = threading.Thread(target=self._run, name="embedded-window-events", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        x11, display = self._x, self._display
        connection = x11.XConnectionNumber(display)
        event = _XEvent()
        try:
            while not self._closed:
                readable, _, _ = select.select([connection, self._wake_read], [], [])
                if self._wake_read in readable:
                    os.read(self._wake_read, 512)
                    self._apply()
                while not self._closed and x11.XPending(display) > 0:
                    x11.XNextEvent(display, ctypes.byref(event))
                    window = changed_window(event.any.type, event.any.window, event.any.atom,
                                            self._windows, self._properties)
                    if window is not None:
                        self._report(window)
                        if event.any.type == DESTROY_NOTIFY:
                            self._windows.discard(window)
        finally:
            x11.XCloseDisplay(display)
            os.close(self._wake_read)

    def _apply(self) -> None:
        while self._commands:
            kind, window = self._commands.popleft()
            if kind == "add":
                self._windows.add(window)
                self._x.XSelectInput(self._display, window, STRUCTURE_NOTIFY_MASK | PROPERTY_CHANGE_MASK)
            else:
                self._windows.discard(window)
                self._x.XSelectInput(self._display, window, 0)
        self._x.XFlush(self._display)

    def _send(self, kind: str, window: int) -> None:
        self._commands.append((kind, window))
        os.write(self._wake_write, b"x")

    def add(self, window_id: int, pid: int) -> bool:
        if self._closed:
            return False
        self._send("add", window_id)
        return True

    def remove(self, window_id: int) -> None:
        if not self._closed:
            self._send("remove", window_id)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        os.write(self._wake_write, b"x")
        if threading.current_thread() is not self._thread:
            self._thread.join(2)
        os.close(self._wake_write)


__all__ = ["PROPERTIES", "X11WindowEvents", "changed_window"]
