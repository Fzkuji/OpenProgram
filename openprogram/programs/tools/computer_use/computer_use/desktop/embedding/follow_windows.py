"""Windows window events: WinEvent hooks on each followed window's process.

One thread owns the hooks and pumps their messages (an out-of-context hook
calls back on the thread that set it, from its message loop). Windows are
added and removed through a queue the thread reads when it is posted
``WM_APP``; ``WM_QUIT`` ends it.
"""
from __future__ import annotations

import ctypes
import queue
import threading
from typing import Any

from .follow import Report

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MINIMIZESTART = 0x0016
EVENT_SYSTEM_MINIMIZEEND = 0x0017
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_HIDE = 0x8003
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
EVENT_OBJECT_NAMECHANGE = 0x800C
# (first, last) event of each hook set on a process.
EVENT_RANGES = (
    (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND),
    (EVENT_SYSTEM_MINIMIZESTART, EVENT_SYSTEM_MINIMIZEEND),
    (EVENT_OBJECT_DESTROY, EVENT_OBJECT_HIDE),
    (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_NAMECHANGE),
)
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
OBJID_WINDOW = 0
CHILDID_SELF = 0
WM_QUIT = 0x0012
WM_APP = 0x8000


def window_event(event: int, hwnd: int, id_object: int, id_child: int, followed) -> int | None:
    """The followed window a WinEvent is about, or None: only events on a
    window itself (not its caret, cursor or a control inside it) count."""
    if not hwnd or hwnd not in followed:
        return None
    if event == EVENT_SYSTEM_FOREGROUND or (id_object == OBJID_WINDOW and id_child == CHILDID_SELF):
        return hwnd
    return None


_WINEVENTPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_long, ctypes.c_long, ctypes.c_uint, ctypes.c_uint)


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_uint), ("pt_x", ctypes.c_long),
                ("pt_y", ctypes.c_long), ("private", ctypes.c_uint)]


class WinWindowEvents:
    def __init__(self, report: Report) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.SetWinEventHook.restype = ctypes.c_void_p
        user32.SetWinEventHook.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, _WINEVENTPROC,
                                           ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
        user32.UnhookWinEvent.argtypes = [ctypes.c_void_p]
        user32.GetMessageW.argtypes = [ctypes.POINTER(_MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
        user32.PostThreadMessageW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.TranslateMessage.argtypes = [ctypes.POINTER(_MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(_MSG)]
        self._user32 = user32
        self._kernel32 = kernel32
        self._report = report
        self._proc = _WINEVENTPROC(self._on_event)
        self._windows: dict[int, int] = {}       # hwnd -> pid
        self._hooks: dict[int, list[Any]] = {}    # pid -> hook handles
        self._commands: queue.Queue = queue.Queue()
        self._thread_id = 0
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="embedded-window-events", daemon=True)
        self._thread.start()
        if not ready.wait(5) or not self._thread_id:
            raise RuntimeError("The window event thread did not start.")

    def _run(self, ready: threading.Event) -> None:
        message = _MSG()
        # The first PeekMessage-like call makes the thread's message queue.
        self._user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        self._thread_id = self._kernel32.GetCurrentThreadId()
        ready.set()
        while self._user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_APP and not message.hwnd:
                self._apply()
                continue
            self._user32.TranslateMessage(ctypes.byref(message))
            self._user32.DispatchMessageW(ctypes.byref(message))
        for hooks in self._hooks.values():
            for hook in hooks:
                self._user32.UnhookWinEvent(hook)
        self._hooks.clear()

    def _apply(self) -> None:
        while True:
            try:
                kind, window_id, pid, done = self._commands.get_nowait()
            except queue.Empty:
                return
            ok = True
            if kind == "add":
                self._windows[window_id] = pid
                if pid not in self._hooks:
                    hooks = [self._user32.SetWinEventHook(first, last, None, self._proc, pid, 0,
                                                          WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
                             for first, last in EVENT_RANGES]
                    hooks = [hook for hook in hooks if hook]
                    if hooks:
                        self._hooks[pid] = hooks
                    else:
                        self._windows.pop(window_id, None)
                        ok = False
            else:
                pid = self._windows.pop(window_id, None)
                if pid is not None and pid not in self._windows.values():
                    for hook in self._hooks.pop(pid, []):
                        self._user32.UnhookWinEvent(hook)
            if done is not None:
                done[1].append(ok)
                done[0].set()

    def _post(self, kind: str, window_id: int, pid: int, wait: bool) -> bool:
        done: tuple[threading.Event, list[bool]] | None = (threading.Event(), []) if wait else None
        self._commands.put((kind, window_id, pid, done))
        if not self._user32.PostThreadMessageW(self._thread_id, WM_APP, 0, 0):
            return False
        if done is None:
            return True
        return done[0].wait(2) and bool(done[1] and done[1][0])

    def _on_event(self, _hook, event, hwnd, id_object, id_child, _thread, _time) -> None:
        window_id = window_event(int(event), int(hwnd or 0), int(id_object), int(id_child), self._windows)
        if window_id is not None:
            self._report(window_id)

    def add(self, window_id: int, pid: int) -> bool:
        return pid > 0 and self._post("add", window_id, pid, wait=True)

    def remove(self, window_id: int) -> None:
        self._post("remove", window_id, 0, wait=False)

    def close(self) -> None:
        self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if threading.current_thread() is not self._thread:
            self._thread.join(2)


__all__ = ["EVENT_RANGES", "WinWindowEvents", "window_event"]
