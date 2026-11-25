"""``SendInput`` for the steps window messages cannot deliver, with the window brought forward for the step and focus handed back afterwards."""
from __future__ import annotations

import ctypes
import time

from .win32 import _EXTENDED_VKS, _STATE, _thread
from . import win32


class _Forward:
    """Bring a window to the front for real input, then give focus, the cursor and the window's place back."""

    def __init__(self, hwnd: int) -> None:
        self.hwnd = hwnd
        self.previous = 0
        self.cursor = None
        self.above = 0
        self.guard = None

    def __enter__(self):
        from ctypes import wintypes
        from openprogram.host._platform import front
        user32 = win32._user32()
        # The front guard would give the foreground back in the middle of the input.
        self.guard = front.forward()
        self.guard.__enter__()
        self.previous = int(user32.GetForegroundWindow() or 0)
        # The window standing directly over this one: where it goes back to afterwards.
        self.above = int(user32.GetWindow(self.hwnd, 3) or 0)  # GW_HWNDPREV
        cursor = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        self.cursor = (cursor.x, cursor.y)
        if self.previous != self.hwnd:
            self.restore(self.hwnd)
            time.sleep(0.08)
        return self

    def __exit__(self, *exc) -> None:
        user32 = win32._user32()
        time.sleep(0.05)
        if self.cursor:
            user32.SetCursorPos(*self.cursor)
        try:
            if self.previous and self.previous != self.hwnd:
                self.restore(self.previous)
                if self.above and self.above != self.hwnd and user32.IsWindow(self.above):
                    # Under the window it stood under, without activating: SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE.
                    user32.SetWindowPos(self.hwnd, self.above, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        finally:
            if self.guard is not None:
                self.guard.__exit__(None, None, None)
                self.guard = None

    @staticmethod
    def restore(hwnd: int) -> None:
        """Windows lets a process take the foreground only from the thread that has it, so join that thread's
        input queue for the call."""
        user32, kernel32 = win32._user32(), _STATE["kernel32"]
        front = user32.GetForegroundWindow()
        own, other = kernel32.GetCurrentThreadId(), _thread(front) if front else 0
        attached = bool(other and other != own and user32.AttachThreadInput(own, other, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(own, other, False)


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        class _MI(ctypes.Structure):
            _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]

        class _KI(ctypes.Structure):
            _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]

        _fields_ = [("mi", _MI), ("ki", _KI), ("padding", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def _send(inputs: list[_INPUT]) -> None:
    array = (_INPUT * len(inputs))(*inputs)
    win32._user32().SendInput(len(inputs), array, ctypes.sizeof(_INPUT))


def _send_key(vk: int, up: bool) -> None:
    event = _INPUT(type=1)
    event.ki.wVk = vk
    event.ki.dwFlags = (0x2 if up else 0) | (0x1 if vk in _EXTENDED_VKS else 0)
    _send([event])


def _send_unicode(unit: int) -> None:
    down, up = _INPUT(type=1), _INPUT(type=1)
    down.ki.wScan = up.ki.wScan = unit
    down.ki.dwFlags, up.ki.dwFlags = 0x4, 0x4 | 0x2  # KEYEVENTF_UNICODE
    _send([down, up])


def _send_mouse(flags: int) -> None:
    event = _INPUT(type=0)
    event.mi.dwFlags = flags
    _send([event])
