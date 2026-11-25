"""The windows on screen, front to back, with their frames: what a screenshot snaps to.

macOS gives frames in points, the same units as the App's screen
coordinates. Windows gives them in physical pixels (the listing runs with
per-monitor DPI awareness on its thread) and X11 in pixels; ``pixels`` says
so, and the App turns them into its own coordinates. A Wayland session
does not tell one client where another's windows are, so it lists none and
a screenshot there is dragged by hand.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import threading
from typing import Any

# Smaller windows are tooltips, shadows and helpers, not something to snap to.
MIN_SIZE = 24
MAX_WINDOWS = 200

_lock = threading.Lock()


def visible_windows(exclude_ids: set[int] | None = None) -> dict[str, Any]:
    """``{"pixels": bool, "windows": [{id, pid, app, title, frame: [x, y, w, h]}]}``, front window first."""
    exclude = set(exclude_ids or ())
    system = platform.system()
    if system == "Darwin":
        return {"pixels": False, "windows": _mac(exclude)}
    if system == "Windows":
        return {"pixels": True, "windows": _win(exclude)}
    if system == "Linux" and os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return {"pixels": True, "windows": _x11(exclude)}
    return {"pixels": False, "windows": []}


def _keep(frame: list[float]) -> bool:
    return frame[2] >= MIN_SIZE and frame[3] >= MIN_SIZE


# ---------------------------------------------------------------- macOS

def _mac(exclude: set[int]) -> list[dict[str, Any]]:
    try:
        import Quartz as cg
    except ImportError:
        return []
    options = cg.kCGWindowListOptionOnScreenOnly | cg.kCGWindowListExcludeDesktopElements
    out = []
    for row in cg.CGWindowListCopyWindowInfo(options, cg.kCGNullWindowID) or []:
        wid = int(row.get("kCGWindowNumber", 0))
        # Layer 0 is where application windows live; the menu bar, Dock and overlays are above it.
        if wid in exclude or row.get("kCGWindowLayer") != 0 or float(row.get("kCGWindowAlpha", 1) or 0) <= 0:
            continue
        bounds = row.get("kCGWindowBounds") or {}
        frame = [float(bounds.get(key, 0)) for key in ("X", "Y", "Width", "Height")]
        if not _keep(frame):
            continue
        out.append({"id": wid, "pid": int(row.get("kCGWindowOwnerPID", 0)),
                    "app": str(row.get("kCGWindowOwnerName") or ""), "title": str(row.get("kCGWindowName") or ""),
                    "frame": frame})
        if len(out) >= MAX_WINDOWS:
            break
    return out


# -------------------------------------------------------------- Windows

def _win(exclude: set[int]) -> list[dict[str, Any]]:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    gwl_exstyle, ws_ex_toolwindow, ws_ex_noactivate = -20, 0x80, 0x08000000
    dwmwa_extended_frame_bounds, dwmwa_cloaked = 9, 14
    found: list[dict[str, Any]] = []

    def visit(hwnd, _param):
        handle = int(hwnd or 0)
        if handle in exclude or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        style = user32.GetWindowLongPtrW(hwnd, gwl_exstyle)
        if style & (ws_ex_toolwindow | ws_ex_noactivate):
            return True
        cloaked = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, dwmwa_cloaked, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        rect = wintypes.RECT()
        if dwmapi.DwmGetWindowAttribute(hwnd, dwmwa_extended_frame_bounds, ctypes.byref(rect), ctypes.sizeof(rect)):
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
        frame = [float(rect.left), float(rect.top), float(rect.right - rect.left), float(rect.bottom - rect.top)]
        if not _keep(frame):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append({"id": handle, "pid": int(pid.value), "app": "", "title": title.value, "frame": frame})
        return len(found) < MAX_WINDOWS

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    with _lock:
        # Physical pixels on every monitor, for this thread only.
        previous = None
        set_context = getattr(user32, "SetThreadDpiAwarenessContext", None)
        if set_context is not None:
            set_context.restype = ctypes.c_void_p
            set_context.argtypes = [ctypes.c_void_p]
            previous = set_context(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        try:
            user32.EnumWindows(callback, 0)  # top of the z-order first
        finally:
            if set_context is not None and previous:
                set_context(previous)
    return found


# ------------------------------------------------------------------ X11

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


_IS_VIEWABLE = 2
_XLIB: Any = None


def _xlib() -> Any:
    global _XLIB
    if _XLIB is None:
        name = ctypes.util.find_library("X11")
        if not name:
            return None
        lib = ctypes.cdll.LoadLibrary(name)
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long, ctypes.c_int,
            ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
        lib.XFree.argtypes = [ctypes.c_void_p]
        lib.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XWindowAttributes)]
        lib.XTranslateCoordinates.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
        # A window that closes between listing and reading must not end the process.
        handler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(lambda _d, _e: 0)
        lib.XSetErrorHandler.argtypes = [ctypes.c_void_p]
        lib.XSetErrorHandler(ctypes.cast(handler, ctypes.c_void_p))
        lib._keep_handler = handler
        _XLIB = lib
    return _XLIB


def _property(lib, display, window: int, name: bytes, kind: int, count: int = 4096) -> tuple[int, Any, int]:
    atom = lib.XInternAtom(display, name, 0)
    actual, fmt = ctypes.c_ulong(), ctypes.c_int()
    items, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
    if lib.XGetWindowProperty(display, window, atom, 0, count, 0, kind, ctypes.byref(actual), ctypes.byref(fmt),
                              ctypes.byref(items), ctypes.byref(after), ctypes.byref(data)) != 0 or not data.value:
        return 0, None, 0
    return int(items.value), data, int(fmt.value)


def _cardinals(lib, display, window: int, name: bytes) -> list[int]:
    count, data, fmt = _property(lib, display, window, name, 0)  # AnyPropertyType
    if not data:
        return []
    try:
        # 32-bit items come back as longs.
        values = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong if fmt == 32 else ctypes.c_uint8))
        return [int(values[i]) for i in range(count)]
    finally:
        lib.XFree(data)


def _text(lib, display, window: int, name: bytes) -> str:
    count, data, _ = _property(lib, display, window, name, 0, 1024)
    if not data:
        return ""
    try:
        return ctypes.string_at(data, count).decode("utf-8", "replace")
    finally:
        lib.XFree(data)


def _x11(exclude: set[int]) -> list[dict[str, Any]]:
    lib = _xlib()
    if lib is None:
        return []
    with _lock:
        display = lib.XOpenDisplay(None)
        if not display:
            return []
        try:
            root = lib.XDefaultRootWindow(display)
            stacking = _cardinals(lib, display, root, b"_NET_CLIENT_LIST_STACKING")
            out = []
            for window in reversed(stacking):  # the list is bottom to top
                if window in exclude:
                    continue
                attrs = _XWindowAttributes()
                if not lib.XGetWindowAttributes(display, window, ctypes.byref(attrs)) or attrs.map_state != _IS_VIEWABLE:
                    continue
                x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
                lib.XTranslateCoordinates(display, window, root, 0, 0, ctypes.byref(x), ctypes.byref(y),
                                          ctypes.byref(child))
                # The frame the window manager draws around the client.
                left, right, top, bottom = (_cardinals(lib, display, window, b"_NET_FRAME_EXTENTS") + [0, 0, 0, 0])[:4]
                frame = [float(x.value - left), float(y.value - top),
                         float(attrs.width + left + right), float(attrs.height + top + bottom)]
                if not _keep(frame):
                    continue
                pids = _cardinals(lib, display, window, b"_NET_WM_PID")
                out.append({"id": int(window), "pid": pids[0] if pids else 0, "app": "",
                            "title": _text(lib, display, window, b"_NET_WM_NAME"), "frame": frame})
                if len(out) >= MAX_WINDOWS:
                    break
            return out
        finally:
            lib.XCloseDisplay(display)


__all__ = ["visible_windows"]
