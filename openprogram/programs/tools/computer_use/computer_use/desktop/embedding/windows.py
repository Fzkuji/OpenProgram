"""Windows: the window becomes a child of the App window.

``SetParent`` makes another process's top-level window a child of the App's
window: it moves with it, is clipped to it and takes input there, drawn by
its own process. Its frame styles are swapped for ``WS_CHILD`` while it is
inside and restored, with its place, when it is given back. A window that
belongs to an elevated process refuses (User Interface Privilege Isolation),
and that is reported as it is.

Coordinates are device pixels in the App window's client area.

A view that only shows the window *watches* it: the window stays where it
is, and the page's pointer, wheel and key events are posted to it in the
background with ``PostMessage``, the way the computer_use backend clicks
without taking the front: mouse messages go to the deepest child under the
point in its client coordinates, the wheel in screen coordinates, keys to
the thread's focus window. Posted keys do not change the keyboard state, so
a printable key goes as its character (``WM_CHAR``) and Ctrl with a letter
as its control character, the message ``TranslateMessage`` would have made;
shortcuts that read the keyboard state stay out of reach in the background.
"""
from __future__ import annotations

import ctypes
import os
import queue
import threading
from types import SimpleNamespace
from typing import Any, Callable

from . import EmbedError
from .keycodes import WINDOWS_SCAN, windows_key
from ..windows.win32 import (MK_CONTROL, MK_LBUTTON, MK_MBUTTON, MK_RBUTTON, MK_SHIFT, WHEEL_DELTA, WM_CHAR,
    WM_KEYDOWN, WM_KEYUP, WM_LBUTTONDBLCLK, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP,
    WM_MOUSEHWHEEL, WM_MOUSEMOVE, WM_MOUSEWHEEL, WM_RBUTTONDOWN, WM_RBUTTONUP, PIXELS_PER_NOTCH)
from ..windows.computer import WindowsComputer

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_DLGMODALFRAME = 0x00000001
WS_EX_WINDOWEDGE = 0x00000100
WS_EX_CLIENTEDGE = 0x00000200
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_NOZORDER = 0x0004
SW_HIDE = 0
SW_SHOWNA = 8
SW_RESTORE = 9
GW_OWNER = 4
HWND_TOP = 0
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_RBUTTONDBLCLK, WM_MBUTTONDBLCLK = 0x0206, 0x0209
VK_MENU, VK_F10 = 0x12, 0x79
_QUEUE_LIMIT = 2000
# DOM button numbers: 0 primary, 1 auxiliary (middle), 2 secondary.
_BUTTONS = {0: (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK, MK_LBUTTON),
            1: (WM_MBUTTONDOWN, WM_MBUTTONUP, WM_MBUTTONDBLCLK, MK_MBUTTON),
            2: (WM_RBUTTONDOWN, WM_RBUTTONUP, WM_RBUTTONDBLCLK, MK_RBUTTON)}

_FRAME_STYLES = WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
_FRAME_EX_STYLES = WS_EX_DLGMODALFRAME | WS_EX_WINDOWEDGE | WS_EX_CLIENTEDGE | WS_EX_APPWINDOW
_MIN_SIZE = (120, 80)


def child_style(style: int) -> int:
    """The window style a top-level window gets while it is a child."""
    return (style & ~_FRAME_STYLES & 0xFFFFFFFF) | WS_CHILD


def child_ex_style(ex_style: int) -> int:
    return ex_style & ~_FRAME_EX_STYLES & 0xFFFFFFFF


# ---------------------------------------------------------------------- posted input
Message = tuple[int, int, int, int]  # (window, message, wParam, lParam)


def mouse_lparam(x: int, y: int) -> int:
    """A point packed as mouse messages carry it: signed 16-bit x low, y high."""
    return WindowsComputer._lparam(int(x), int(y))


def key_lparam(scan: int, *, extended: bool, up: bool, alt: bool = False) -> int:
    """The lParam of WM_(SYS)KEYDOWN/UP: repeat 1, scan code, extended, context and transition bits."""
    value = 1 | ((scan & 0xFF) << 16) | ((1 << 24) if extended else 0) | ((1 << 29) if alt else 0)
    return value | ((1 << 30) | (1 << 31) if up else 0)


def wheel_wparam(delta: int, keys: int = 0) -> int:
    """WM_MOUSEWHEEL's wParam: the signed delta in the high word, held keys and buttons in the low."""
    return ((int(delta) & 0xFFFF) << 16) | (keys & 0xFFFF)


def wheel_delta(pixels: float) -> int:
    """Wheel units for a scroll of ``pixels``: one notch (120) per PIXELS_PER_NOTCH pixels."""
    return max(-32768, min(32767, int(round(float(pixels) * WHEEL_DELTA / PIXELS_PER_NOTCH))))


def _mods(event: dict[str, Any]) -> set[str]:
    names = event.get("mods")
    return {str(n) for n in names} if isinstance(names, (list, tuple)) else set()


def _mouse_keys(mods: set[str], buttons: int = 0) -> int:
    keys = (MK_SHIFT if "shift" in mods else 0) | (MK_CONTROL if "ctrl" in mods else 0)
    for bit, flag in ((1, MK_LBUTTON), (2, MK_RBUTTON), (4, MK_MBUTTON)):
        if buttons & bit:
            keys |= flag
    return keys


def _units(text: str) -> list[int]:
    raw = text.replace("\r\n", "\n").replace("\n", "\r").encode("utf-16-le")
    return [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]


def event_messages(event: dict[str, Any], *, origin: tuple[int, int],
                   locate: Callable[[int, int], tuple[int, int, int]],
                   client_of: Callable[[int, int, int], tuple[int, int]],
                   focus: Callable[[], int], scan: Callable[[int], int],
                   held: dict[str, Any]) -> list[Message]:
    """The messages one page event becomes.

    ``origin`` is the window rectangle's top-left on screen; the event's x, y
    are from there. ``locate`` finds the deepest child under a screen point
    and the point in its client coordinates, ``client_of`` converts a screen
    point to one window's client coordinates, ``focus`` names the window
    with keyboard focus and ``scan`` gives a virtual key's scan code.
    ``held`` keeps the window a press went to, which takes the pointer until
    the release, as mouse capture would.
    """
    kind = event.get("type")
    mods = _mods(event)
    if kind in ("move", "down", "up", "wheel"):
        try:
            sx = int(round(origin[0] + float(event.get("x", 0))))
            sy = int(round(origin[1] + float(event.get("y", 0))))
        except (TypeError, ValueError):
            return []
        captured = held.get("capture")
        if kind == "wheel":
            target, _, _ = locate(sx, sy)
            keys, out = _mouse_keys(mods), []
            vertical = wheel_delta(-float(event.get("dy", 0) or 0))
            horizontal = wheel_delta(float(event.get("dx", 0) or 0))
            if vertical:
                out.append((target, WM_MOUSEWHEEL, wheel_wparam(vertical, keys), mouse_lparam(sx, sy)))
            if horizontal:
                out.append((target, WM_MOUSEHWHEEL, wheel_wparam(horizontal, keys), mouse_lparam(sx, sy)))
            return out
        if kind == "move":
            if captured:
                cx, cy = client_of(captured, sx, sy)
                target = captured
            else:
                target, cx, cy = locate(sx, sy)
            return [(target, WM_MOUSEMOVE, _mouse_keys(mods, int(event.get("buttons", 0) or 0)),
                     mouse_lparam(cx, cy))]
        down_msg, up_msg, double_msg, flag = _BUTTONS.get(int(event.get("button", 0) or 0), _BUTTONS[0])
        if kind == "down":
            target, cx, cy = locate(sx, sy)
            held["capture"] = target
            clicks = int(event.get("clicks", 1) or 1)
            message = double_msg if clicks >= 2 and clicks % 2 == 0 else down_msg
            keys = _mouse_keys(mods)
            return [(target, WM_MOUSEMOVE, keys, mouse_lparam(cx, cy)),
                    (target, message, keys | flag, mouse_lparam(cx, cy))]
        held.pop("capture", None)
        if captured:
            cx, cy = client_of(captured, sx, sy)
            target = captured
        else:
            target, cx, cy = locate(sx, sy)
        return [(target, up_msg, _mouse_keys(mods), mouse_lparam(cx, cy))]
    if kind == "text":
        target = focus()
        return [(target, WM_CHAR, unit, 1) for unit in _units(str(event.get("text") or "")[:4000])]
    if kind != "key":
        return []
    down = bool(event.get("down"))
    key = str(event.get("key") or "")
    code = str(event.get("code") or "")
    alt_gr = {"ctrl", "alt"} <= mods
    menu_alt = "alt" in mods and not alt_gr
    if len(key) == 1 and not menu_alt and ("ctrl" not in mods or alt_gr) and "meta" not in mods:
        # A character: what TranslateMessage would make of the key, which a
        # posted key would not get right without the keyboard state.
        return [(focus(), WM_CHAR, unit, 1) for unit in _units(key)] if down else []
    found = windows_key(code)
    if found is None:
        return []
    vk, extended = found
    if "ctrl" in mods and not menu_alt and 0x41 <= vk <= 0x5A:
        # Ctrl with a letter: its control character, which edit controls act on.
        return [(focus(), WM_CHAR, vk - 0x40, 1)] if down else []
    system = menu_alt or vk in (VK_MENU, VK_F10)
    message = (WM_SYSKEYDOWN if down else WM_SYSKEYUP) if system else (WM_KEYDOWN if down else WM_KEYUP)
    code_scan = WINDOWS_SCAN.get(code)
    lparam = key_lparam(code_scan if code_scan is not None else scan(vk), extended=extended, up=not down,
                        alt=menu_alt)
    return [(focus(), message, vk, lparam)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _api():
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
    except OSError:
        dwmapi = None
    handle = ctypes.c_void_p
    user32.SetParent.restype, user32.SetParent.argtypes = handle, [handle, handle]
    user32.GetParent.restype, user32.GetParent.argtypes = handle, [handle]
    user32.GetWindow.restype, user32.GetWindow.argtypes = handle, [handle, ctypes.c_uint]
    user32.IsWindow.argtypes = [handle]
    user32.IsWindowVisible.argtypes = [handle]
    user32.IsIconic.argtypes = [handle]
    user32.IsZoomed.argtypes = [handle]
    user32.ShowWindow.argtypes = [handle, ctypes.c_int]
    user32.GetWindowRect.argtypes = [handle, ctypes.POINTER(_RECT)]
    user32.SetWindowPos.argtypes = [handle, handle, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_uint]
    user32.GetWindowTextLengthW.argtypes = [handle]
    user32.GetWindowTextW.argtypes = [handle, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [handle, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [handle, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [handle, ctypes.c_int, ctypes.c_ssize_t]
    kernel32.OpenProcess.restype = handle
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [handle, wintypes.DWORD, ctypes.c_wchar_p,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [handle]
    return user32, kernel32, dwmapi


class WindowsEmbedder:
    mode = "native"
    # A placed window is part of the App window and takes input itself; a
    # watched one gets the page's events posted.
    place_input = False
    view_input = True

    def __init__(self) -> None:
        self.attached: dict[int, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._user32, self._kernel32, self._dwm = _api()
        self._events: queue.Queue = queue.Queue(maxsize=_QUEUE_LIMIT)
        self._pump: threading.Thread | None = None

    # ------------------------------------------------------------------ helpers
    def _title(self, hwnd: int) -> str:
        length = self._user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def _pid(self, hwnd: int) -> int:
        from ctypes import wintypes
        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def _image(self, pid: int) -> str:
        from ctypes import wintypes
        process = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if self._kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                return buffer.value
            return ""
        finally:
            self._kernel32.CloseHandle(process)

    def _cloaked(self, hwnd: int) -> bool:
        if self._dwm is None:
            return False
        value = ctypes.c_int(0)
        result = self._dwm.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_CLOAKED, ctypes.byref(value),
                                                 ctypes.sizeof(value))
        return result == 0 and value.value != 0

    def _rect(self, hwnd: int) -> tuple[int, int, int, int]:
        rect = _RECT()
        self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

    # ------------------------------------------------------------------ listing
    def list_windows(self, exclude_pids: set[int]) -> list[dict[str, Any]]:
        excluded = set(exclude_pids) | {os.getpid()}
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def collect(hwnd, _param):
            found.append(int(hwnd or 0))
            return True

        self._user32.EnumWindows(callback_type(collect), None)
        out = []
        for hwnd in found:
            if not hwnd or not self._user32.IsWindowVisible(hwnd) or self._user32.GetWindow(hwnd, GW_OWNER):
                continue
            ex_style = self._user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
            if ex_style & WS_EX_TOOLWINDOW or self._cloaked(hwnd):
                continue
            title = self._title(hwnd)
            x, y, width, height = self._rect(hwnd)
            pid = self._pid(hwnd)
            if (not title or title == "Program Manager" or pid in excluded
                    or width < _MIN_SIZE[0] or height < _MIN_SIZE[1]):
                continue
            image = self._image(pid)
            name = os.path.splitext(os.path.basename(image))[0] if image else ""
            if name.lower().startswith("openprogram"):
                continue
            out.append({"id": hwnd, "pid": pid, "app": name or title, "title": title, "icon": "",
                        "bounds": [x, y, width, height]})
        return out

    # ------------------------------------------------------------------ attach
    def attach(self, window_id: int, host: dict[str, Any]) -> dict[str, Any]:
        host_handle = int(host.get("handle") or 0)
        if not host_handle or not self._user32.IsWindow(host_handle):
            raise EmbedError("The App's window could not be found.")
        if not self._user32.IsWindow(window_id):
            raise EmbedError("That window is closed.")
        if window_id == host_handle:
            raise EmbedError("The App cannot embed its own window.")
        with self._lock:
            record = self.attached.get(window_id)
            if record is None or record.get("watched"):
                if self._user32.IsIconic(window_id) or self._user32.IsZoomed(window_id):
                    self._user32.ShowWindow(window_id, SW_RESTORE)
                style = self._user32.GetWindowLongPtrW(window_id, GWL_STYLE) & 0xFFFFFFFF
                ex_style = self._user32.GetWindowLongPtrW(window_id, GWL_EXSTYLE) & 0xFFFFFFFF
                record = {"pid": self._pid(window_id), "style": style, "ex_style": ex_style,
                          "rect": self._rect(window_id), "host": host_handle}
                self._user32.SetWindowLongPtrW(window_id, GWL_STYLE, child_style(style))
                self._user32.SetWindowLongPtrW(window_id, GWL_EXSTYLE, child_ex_style(ex_style))
                ctypes.set_last_error(0)
                previous = self._user32.SetParent(window_id, host_handle)
                if not previous and ctypes.get_last_error():
                    error = ctypes.get_last_error()
                    self._user32.SetWindowLongPtrW(window_id, GWL_STYLE, style)
                    self._user32.SetWindowLongPtrW(window_id, GWL_EXSTYLE, ex_style)
                    raise EmbedError("Windows refused to move this window into the App"
                                     + (" (it belongs to a program running as administrator)." if error == 5
                                        else f" (error {error})."))
                self.attached[window_id] = record
            self._place(window_id, host)
            return self._describe(window_id, record)

    def watch(self, window_id: int) -> dict[str, Any]:
        """Record the window for input and state without moving, sizing or reparenting it."""
        if not self._user32.IsWindow(window_id):
            raise EmbedError("That window is closed.")
        _desktop_user32()  # per-monitor DPI aware, so rectangles are device pixels
        with self._lock:
            record = self.attached.get(window_id)
            if record is None:
                record = {"pid": self._pid(window_id), "title": self._title(window_id), "watched": True}
                self.attached[window_id] = record
            return self._describe(window_id, record)

    def _place(self, window_id: int, host: dict[str, Any]) -> None:
        if not host.get("visible", True):
            self._user32.ShowWindow(window_id, SW_HIDE)
            return
        x, y, width, height = (int(round(v)) for v in host["rect"])
        self._user32.SetWindowPos(window_id, HWND_TOP, x, y, max(1, width), max(1, height),
                                  SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW)

    def _describe(self, window_id: int, record: dict[str, Any]) -> dict[str, Any]:
        watched = bool(record.get("watched"))
        return {"mode": self.mode, "id": window_id, "pid": record["pid"], "app": "",
                "title": self._title(window_id), "frame": list(self._rect(window_id)), "open": True,
                "placed": window_id in self.attached and not watched, "input": watched}

    def layout(self, window_id: int, host: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            record = self.attached.get(window_id)
            if record is None or record.get("watched"):
                raise EmbedError("That window is not embedded.")
            if not self._user32.IsWindow(window_id):
                self.attached.pop(window_id, None)
                return {"id": window_id, "open": False}
            self._place(window_id, host)
            return self._describe(window_id, record)

    def state(self, window_id: int) -> dict[str, Any]:
        if not self._user32.IsWindow(window_id):
            with self._lock:
                self.attached.pop(window_id, None)
            return {"id": window_id, "open": False}
        record = self.attached.get(window_id) or {"pid": self._pid(window_id)}
        return self._describe(window_id, record)

    def detach(self, window_id: int) -> None:
        with self._lock:
            record = self.attached.pop(window_id, None)
        if record is None or record.get("watched") or not self._user32.IsWindow(window_id):
            return
        self._user32.ShowWindow(window_id, SW_HIDE)
        self._user32.SetParent(window_id, None)
        self._user32.SetWindowLongPtrW(window_id, GWL_STYLE, record["style"])
        self._user32.SetWindowLongPtrW(window_id, GWL_EXSTYLE, record["ex_style"])
        x, y, width, height = record["rect"]
        self._user32.SetWindowPos(window_id, HWND_TOP, x, y, width, height, SWP_FRAMECHANGED | SWP_SHOWWINDOW)

    # ------------------------------------------------------------------ input
    def input(self, window_id: int, events: list[dict[str, Any]]) -> None:
        """Post a view's events to the window in the background, in order."""
        if window_id not in self.attached:
            raise EmbedError("That window is not embedded.")
        for event in events:
            try:
                self._events.put_nowait((window_id, event))
            except queue.Full:
                if event.get("type") not in ("move", "wheel"):
                    raise EmbedError("The window is not taking input.")
        with self._lock:
            if self._pump is None or not self._pump.is_alive():
                self._pump = threading.Thread(target=self._run, name="embedded-window-input", daemon=True)
                self._pump.start()

    def _run(self) -> None:
        while True:
            try:
                window_id, event = self._events.get(timeout=30)
            except queue.Empty:
                with self._lock:
                    if self._events.empty():
                        self._pump = None
                        return
                continue
            record = self.attached.get(window_id)
            if record is None:
                continue
            try:
                self._post(window_id, record, event)
            except Exception:
                # One event that cannot be posted must not stop the rest.
                pass

    def _post(self, window_id: int, record: dict[str, Any], event: dict[str, Any]) -> None:
        user32 = _desktop_user32()
        computer = _desktop_backend()
        if event.get("type") == "down" or "origin" not in record:
            # The window may have moved since the last press.
            record["origin"] = self._rect(window_id)[:2]
        held = record.setdefault("held", {})

        def client_of(hwnd: int, sx: int, sy: int) -> tuple[int, int]:
            from ctypes import wintypes
            point = wintypes.POINT(sx, sy)
            user32.ScreenToClient(hwnd, ctypes.byref(point))
            return point.x, point.y

        messages = event_messages(
            event, origin=record["origin"], held=held,
            locate=lambda sx, sy: computer._target_child(window_id, sx, sy),
            client_of=client_of,
            focus=lambda: computer._focus_hwnd(SimpleNamespace(window_id=window_id)),
            scan=lambda vk: int(user32.MapVirtualKeyW(vk, 0)))
        for hwnd, message, wparam, lparam in messages:
            user32.PostMessageW(hwnd, message, wparam, lparam)


def _desktop_user32():
    from ..windows.win32 import _user32
    return _user32()


def _desktop_backend():
    from ..windows.computer import backend
    return backend()


__all__ = ["WindowsEmbedder", "child_ex_style", "child_style", "event_messages", "key_lparam", "mouse_lparam",
           "wheel_delta", "wheel_wparam"]
