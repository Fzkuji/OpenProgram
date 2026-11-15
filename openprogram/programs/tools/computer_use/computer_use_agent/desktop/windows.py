"""Windows backend for the ``computer_use`` tool.

It reads one application window as a screenshot plus an indexed UI
Automation tree, and gives the model the same actions as the macOS and
Linux backends.

* Element actions go through UI Automation patterns (Invoke, Toggle,
  Value, RangeValue, SelectionItem, ExpandCollapse, Scroll, Text,
  LegacyIAccessible) and run in the background.
* Pointer input at a coordinate and plain keys are posted as window
  messages to the child window under the point or holding focus, so the
  window is not raised. Chords with modifiers, and windows that read
  raw input instead of window messages (UWP frames, Chromium), get
  ``SendInput`` with the window brought forward for that step; focus
  and the cursor go back afterwards.
* Screenshots use ``PrintWindow`` with full-content rendering, which
  reads covered windows too; OCR uses tesseract when it is installed.
* Recording uses WASAPI process loopback: only the app's own sound
  (its process tree), while the user keeps hearing it.

Coordinates the model gives are pixels of the last screenshot of that
window; the process is per-monitor DPI aware, so they are physical
screen pixels here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ctypes
import json
import os
from pathlib import PureWindowsPath
import platform
import struct
import subprocess
import threading
import time
from typing import Any
import zlib

from . import audio
from . import keys as keymap
from . import ocr
from .base import (HOVER_SECONDS, INCLUDE, MAX_COPY_CHARS, MAX_TEXT_CHARS, OCR_SPARSE_NODES, AudioClip,
                   ComputerUseError, Observation, RefusedApps, Report, refuse_password_field, check_cancelled, click_args, click_words, find_text,
                   menu_key, menu_path, save_recording, scroll_args, seconds_arg, window_node_count)
from .tree import Node, Snapshot, diff, number, rebind, role_name, snapshot

# OpenProgram's own windows, refused where they could show an approval card.
OWN_APPS = frozenset({"openprogram", "ai.openprogram.desktop"})

MAX_NODES = 1500
MAX_DEPTH = 60
MAX_IMAGE_SIDE = 1568
SETTLE_MIN = 0.3
SETTLE_MAX = 1.5
BUSY_MAX = 5.0
QUICK_NODES = 400
WHEEL_DELTA = 120
NOTCHES_PER_PAGE = 5
PIXELS_PER_NOTCH = 40
LAUNCH_SECONDS = 15.0

# UI Automation control type ids and the role names the tree prints.
CONTROL_TYPES = {
    50000: "button", 50001: "calendar", 50002: "check box", 50003: "combo box", 50004: "edit",
    50005: "link", 50006: "image", 50007: "list item", 50008: "list", 50009: "menu", 50010: "menu bar",
    50011: "menu item", 50012: "progress bar", 50013: "radio button", 50014: "scroll bar", 50015: "slider",
    50016: "spinner", 50017: "status bar", 50018: "tab", 50019: "tab item", 50020: "text", 50021: "tool bar",
    50022: "tool tip", 50023: "tree", 50024: "tree item", 50025: "custom", 50026: "group", 50027: "thumb",
    50028: "data grid", 50029: "data item", 50030: "document", 50031: "split button", 50032: "window",
    50033: "pane", 50034: "header", 50035: "header item", 50036: "table", 50037: "title bar",
    50038: "separator", 50039: "semantic zoom", 50040: "app bar",
}
_TRANSPARENT = frozenset({"pane", "group", "custom"})
_TOGGLE_ROLES = frozenset({"check box", "radio button"})
_NOT_TEXT_ROLES = frozenset({"button", "check box", "radio button", "menu item", "menu", "menu bar", "link",
                             "image", "text", "list item", "tab item", "tree item", "slider", "scroll bar",
                             "split button"})
_BUSY_ROLES = frozenset({"progress bar"})

# Pattern ids.
INVOKE, SELECTION, VALUE, RANGE_VALUE, SCROLL, EXPAND_COLLAPSE = 10000, 10001, 10002, 10003, 10004, 10005
WINDOW_PATTERN, SELECTION_ITEM, TEXT, TOGGLE, LEGACY = 10009, 10010, 10014, 10015, 10018

# Window classes that take their input from raw input or a separate core
# window, not from posted mouse and key messages.
FOREGROUND_CLASSES = frozenset({"ApplicationFrameWindow", "Windows.UI.Core.CoreWindow", "Chrome_WidgetWin_1",
                                "Chrome_RenderWidgetHostHWND", "MozillaWindowClass", "UnityWndClass"})

# Window messages.
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x0200, 0x0201, 0x0202, 0x0203
WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0204, 0x0205, 0x0207, 0x0208
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E
WM_KEYDOWN, WM_KEYUP, WM_CHAR, WM_COMMAND, WM_PASTE = 0x0100, 0x0101, 0x0102, 0x0111, 0x0302
MK_LBUTTON, MK_RBUTTON, MK_SHIFT, MK_CONTROL, MK_MBUTTON = 0x1, 0x2, 0x4, 0x8, 0x10
_BUTTON_MESSAGES = {"left": (WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON), "right": (WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON),
                    "middle": (WM_MBUTTONDOWN, WM_MBUTTONUP, MK_MBUTTON)}

# Virtual-key codes for xdotool key names.
VK_NAMED = {
    "return": 0x0D, "enter": 0x0D, "kp_enter": 0x0D, "tab": 0x09, "space": 0x20, "backspace": 0x08,
    "escape": 0x1B, "esc": 0x1B, "delete": 0x2E, "forwarddelete": 0x2E, "insert": 0x2D, "home": 0x24,
    "end": 0x23, "prior": 0x21, "page_up": 0x21, "pageup": 0x21, "next": 0x22, "page_down": 0x22,
    "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "caps_lock": 0x14,
    "capslock": 0x14, "num_lock": 0x90, "volume_up": 0xAF, "volumeup": 0xAF, "volume_down": 0xAE,
    "volumedown": 0xAE, "mute": 0xAD, "menu": 0x5D, "print": 0x2C,
    "kp_0": 0x60, "kp_1": 0x61, "kp_2": 0x62, "kp_3": 0x63, "kp_4": 0x64, "kp_5": 0x65, "kp_6": 0x66,
    "kp_7": 0x67, "kp_8": 0x68, "kp_9": 0x69, "kp_multiply": 0x6A, "kp_add": 0x6B, "kp_subtract": 0x6D,
    "kp_decimal": 0x6E, "kp_divide": 0x6F,
    **{f"f{n}": 0x6F + n for n in range(1, 25)},
}
VK_MODIFIERS = {"shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "option": 0x12, "opt": 0x12,
                "super": 0x5B, "cmd": 0x5B, "command": 0x5B, "meta": 0x5B, "win": 0x5B}
_EXTENDED_VKS = frozenset({0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5D, 0x6F})


def support() -> dict[str, Any]:
    if platform.system() != "Windows":
        return {"available": False, "reason": "the Windows backend runs on Windows only"}
    try:
        _uia()
    except Exception as exc:  # noqa: BLE001 - any COM or import failure means no backend
        return {"available": False, "reason": f"UI Automation is not available ({exc}); install comtypes"}
    return {"available": True, "reason": ""}


# ---------------------------------------------------------------------- pure helpers (tested on any host)
def png_encode(width: int, height: int, bgra: bytes) -> bytes:
    """A PNG of top-down 32-bit BGRA pixels, as GDI returns them."""
    stride = width * 4
    raw = bytearray()
    for row in range(height):
        line = bgra[row * stride:(row + 1) * stride]
        raw.append(0)  # filter: none
        rgb = bytearray(width * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = line[2::4], line[1::4], line[0::4]
        raw += rgb

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")


def key_chord(key: str) -> tuple[list[int], int, str]:
    """``(modifier vks, key vk, text)`` for an xdotool chord; ``text`` is a single printable character."""
    try:
        keymap.parse_chord(key)
    except keymap.KeyNameError as exc:
        if "modifier" in str(exc) or "malformed" in str(exc) or "non-empty" in str(exc):
            raise ComputerUseError(str(exc)) from exc
    raw = key.strip()
    parts = raw.split("+")
    if raw == "+" or raw.endswith("++"):
        parts = raw[:-1].split("+")[:-1] + ["+"]
    mods = []
    for name in parts[:-1]:
        vk = VK_MODIFIERS.get(name.strip().lower())
        if vk is None:
            raise ComputerUseError(f"unknown modifier {name!r} in {key!r}")
        mods.append(vk)
    last = parts[-1].strip() if parts[-1].strip() else parts[-1]
    lower = last.lower()
    if lower in VK_MODIFIERS and len(parts) == 1:
        return [], VK_MODIFIERS[lower], ""
    if lower in VK_NAMED:
        return mods, VK_NAMED[lower], ""
    if len(last) == 1:
        if last.isalpha():
            return mods, ord(last.upper()), last
        if last.isdigit():
            return mods, ord(last), last
        return mods, 0, last  # the layout decides the key (VkKeyScanW)
    shifted = {"plus": "+", "minus": "-", "equal": "=", "comma": ",", "period": ".", "slash": "/",
               "backslash": "\\", "semicolon": ";", "apostrophe": "'", "grave": "`", "bracketleft": "[",
               "bracketright": "]", "exclam": "!", "at": "@", "numbersign": "#", "dollar": "$",
               "percent": "%", "asciicircum": "^", "ampersand": "&", "asterisk": "*", "parenleft": "(",
               "parenright": ")", "underscore": "_", "colon": ":", "quotedbl": '"', "less": "<",
               "greater": ">", "question": "?", "asciitilde": "~", "bar": "|", "braceleft": "{", "braceright": "}"}
    if lower in shifted:
        return mods, 0, shifted[lower]
    raise ComputerUseError(f"unknown key {last!r}")


def cf_html(fragment: str) -> bytes:
    """The clipboard's "HTML Format" payload for an HTML fragment."""
    head = ("Version:0.9\r\nStartHTML:{0:010d}\r\nEndHTML:{1:010d}\r\n"
            "StartFragment:{2:010d}\r\nEndFragment:{3:010d}\r\n")
    prefix = "<html><body><!--StartFragment-->"
    suffix = "<!--EndFragment--></body></html>"
    blank = head.format(0, 0, 0, 0).encode()
    start_html = len(blank)
    start_fragment = start_html + len(prefix.encode())
    end_fragment = start_fragment + len(fragment.encode())
    end_html = end_fragment + len(suffix.encode())
    return (head.format(start_html, end_html, start_fragment, end_fragment) + prefix + fragment + suffix).encode()


def parse_start_apps(text: str) -> list[dict[str, str]]:
    """``Get-StartApps | ConvertTo-Json`` rows as ``{name, app_id}``."""
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    rows = data if isinstance(data, list) else [data]
    out = []
    for row in rows:
        if isinstance(row, dict) and row.get("Name") and row.get("AppID"):
            out.append({"name": str(row["Name"]), "app_id": str(row["AppID"])})
    return sorted(out, key=lambda r: r["name"].lower())


# ---------------------------------------------------------------------- Win32
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {}


# Win32 signatures, so 64-bit handles pass and return intact:
# (dll, function, restype, argtypes). "H" is any handle, "P" any pointer.
_SIGNATURES = [
    ("user32", "GetWindowTextLengthW", "i", "H"), ("user32", "GetWindowTextW", "i", "HPi"),
    ("user32", "GetClassNameW", "i", "HPi"), ("user32", "PostMessageW", "b", "HUWL"),
    ("user32", "SendMessageW", "L", "HUWL"), ("user32", "GetWindowThreadProcessId", "u", "HP"),
    ("user32", "GetForegroundWindow", "H", ""), ("user32", "SetForegroundWindow", "b", "H"),
    ("user32", "BringWindowToTop", "b", "H"), ("user32", "GetWindow", "H", "Hu"),
    ("user32", "SetWindowPos", "b", "HHiiiiu"), ("user32", "IsWindow", "b", "H"),
    ("user32", "GetWindowLongW", "i", "Hi"), ("user32", "IsWindowVisible", "b", "H"),
    ("user32", "IsIconic", "b", "H"), ("user32", "ShowWindow", "b", "Hi"),
    ("user32", "GetWindowRect", "b", "HP"), ("user32", "ScreenToClient", "b", "HP"),
    ("user32", "EnumWindows", "b", "PL"), ("user32", "EnumChildWindows", "b", "HPL"),
    ("user32", "GetDC", "H", "H"), ("user32", "ReleaseDC", "i", "HH"), ("user32", "PrintWindow", "b", "HHu"),
    ("user32", "GetMenu", "H", "H"), ("user32", "GetSubMenu", "H", "Hi"), ("user32", "GetMenuItemCount", "i", "H"),
    ("user32", "GetMenuItemID", "u", "Hi"), ("user32", "GetMenuState", "u", "Huu"),
    ("user32", "GetMenuStringW", "i", "HuPiu"), ("user32", "GetGUIThreadInfo", "b", "uP"),
    ("user32", "AttachThreadInput", "b", "uub"), ("user32", "MapVirtualKeyW", "u", "uu"),
    ("user32", "VkKeyScanW", "s", "w"), ("user32", "SetCursorPos", "b", "ii"), ("user32", "GetCursorPos", "b", "P"),
    ("user32", "SendInput", "u", "uPi"), ("user32", "OpenClipboard", "b", "H"), ("user32", "CloseClipboard", "b", ""),
    ("user32", "EmptyClipboard", "b", ""), ("user32", "GetClipboardData", "H", "u"),
    ("user32", "SetClipboardData", "H", "uH"), ("user32", "RegisterClipboardFormatW", "u", "P"),
    ("gdi32", "CreateCompatibleDC", "H", "H"), ("gdi32", "CreateCompatibleBitmap", "H", "Hii"),
    ("gdi32", "SelectObject", "H", "HH"), ("gdi32", "DeleteObject", "b", "H"), ("gdi32", "DeleteDC", "b", "H"),
    ("gdi32", "SetStretchBltMode", "i", "Hi"), ("gdi32", "StretchBlt", "b", "HiiiiHiiiiu"),
    ("gdi32", "GetDIBits", "i", "HHuuPPu"),
    ("kernel32", "OpenProcess", "H", "ubu"), ("kernel32", "CloseHandle", "b", "H"),
    ("kernel32", "QueryFullProcessImageNameW", "b", "HuPP"), ("kernel32", "GetCurrentThreadId", "u", ""),
    ("kernel32", "GlobalAlloc", "H", "uZ"), ("kernel32", "GlobalLock", "P", "H"), ("kernel32", "GlobalUnlock", "b", "H"),
    ("dwmapi", "DwmGetWindowAttribute", "l", "HuPu"),
]


def _ctype(code: str):
    from ctypes import wintypes
    return {"H": ctypes.c_void_p, "P": ctypes.c_void_p, "i": ctypes.c_int, "u": ctypes.c_uint, "b": wintypes.BOOL,
            "W": wintypes.WPARAM, "L": wintypes.LPARAM, "U": wintypes.UINT, "s": ctypes.c_short, "w": ctypes.c_wchar,
            "l": ctypes.c_long, "Z": ctypes.c_size_t}[code]


def _user32():
    """user32, with gdi32, kernel32 and dwmapi kept in ``_STATE``; the process is made per-monitor DPI aware."""
    with _LOCK:
        if "user32" not in _STATE:
            for name in ("user32", "gdi32", "kernel32", "dwmapi"):
                try:
                    _STATE[name] = ctypes.WinDLL(name, use_last_error=True)
                except OSError:
                    _STATE[name] = None
            for dll, function, restype, argtypes in _SIGNATURES:
                if _STATE[dll] is None:
                    continue
                proc = getattr(_STATE[dll], function)
                proc.restype = _ctype(restype)
                proc.argtypes = [_ctype(code) for code in argtypes]
            from ctypes import wintypes
            child_at = _STATE["user32"].ChildWindowFromPointEx  # takes the POINT by value
            child_at.restype, child_at.argtypes = ctypes.c_void_p, [ctypes.c_void_p, wintypes.POINT, ctypes.c_uint]
            try:
                _STATE["user32"].SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
            except (AttributeError, OSError):
                pass
        return _STATE["user32"]


def _uia():
    """The UI Automation client, with COM initialised on this thread."""
    import comtypes
    import comtypes.client
    comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED) if not getattr(_THREAD, "com", False) else None
    _THREAD.com = True
    with _LOCK:
        if "uia_module" not in _STATE:
            module = comtypes.client.GetModule("UIAutomationCore.dll")
            _STATE["uia_module"] = module
        module = _STATE["uia_module"]
    automation = getattr(_THREAD, "automation", None)
    if automation is None:
        automation = comtypes.client.CreateObject(module.CUIAutomation, interface=module.IUIAutomation)
        _THREAD.automation = automation
        _THREAD.walker = automation.ControlViewWalker
    return automation, module


_THREAD = threading.local()


def _text(hwnd) -> str:
    user32 = _user32()
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _class(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    _user32().GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _pid(hwnd) -> int:
    from ctypes import wintypes
    pid = wintypes.DWORD()
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _thread(hwnd) -> int:
    return int(_user32().GetWindowThreadProcessId(hwnd, None))


def _exe(pid: int) -> str:
    from ctypes import wintypes
    _user32()
    kernel32 = _STATE["kernel32"]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _rect(hwnd) -> tuple[int, int, int, int]:
    """The window's visible frame (left, top, width, height), without the invisible resize borders."""
    from ctypes import wintypes
    _user32()
    rect = wintypes.RECT()
    dwm = _STATE.get("dwmapi")
    if dwm is not None and dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0:
        pass
    else:
        _user32().GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def _top_windows() -> list[int]:
    """Visible, unowned, non-tool top-level windows with a title, front to back."""
    from ctypes import wintypes
    user32 = _user32()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        if user32.GetWindowLongW(hwnd, -20) & 0x80:  # WS_EX_TOOLWINDOW
            return True
        if _text(hwnd):
            found.append(int(hwnd))
        return True

    user32.EnumWindows(visit, 0)
    return found


def _window_pid(hwnd: int) -> int:
    """The app behind a window; a UWP frame belongs to ApplicationFrameHost, its content to the app."""
    pid = _pid(hwnd)
    if _class(hwnd) == "ApplicationFrameWindow":
        from ctypes import wintypes
        user32 = _user32()
        result = [pid]

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(child, _):
            if _class(child) == "Windows.UI.Core.CoreWindow" and _pid(child) != pid:
                result[0] = _pid(child)
                return False
            return True

        user32.EnumChildWindows(hwnd, visit, 0)
        return result[0]
    return pid


@dataclass
class App:
    pid: int
    name: str
    exe: str
    app_id: str = ""

    @property
    def ident(self) -> str:
        return self.app_id or PureWindowsPath(self.exe).stem or self.name


@dataclass
class Win:
    hwnd: int
    title: str
    x: int
    y: int
    width: int
    height: int
    minimized: bool
    window_class: str


@dataclass
class WindowState:
    pid: int
    window_title: str
    window_id: int = 0
    elements: dict[int, Any] = field(default_factory=dict)
    nodes: list[Node] = field(default_factory=list)
    by_index: dict[int, Node] = field(default_factory=dict)
    snapshot: Snapshot | None = None
    origin: tuple[float, float] = (0.0, 0.0)
    size: tuple[float, float] = (0.0, 0.0)
    scale: float = 1.0
    has_screenshot: bool = False
    sparse: bool = False
    text_boxes: list[ocr.TextBox] = field(default_factory=list)
    shapes: list[ocr.Shape] = field(default_factory=list)


class WindowsComputer:
    """One backend per worker; state is kept per (session, pid)."""

    STATELESS = frozenset({"menu", "show_window", "wait"})
    AT_FOCUS = frozenset({"copy", "press_key", "type_text", "paste"})

    def __init__(self) -> None:
        self._states: dict[tuple[str, int], WindowState] = {}
        self._lock = threading.RLock()
        self._recordings: dict[str, Any] = {}
        self._start_apps: tuple[float, list[dict[str, str]]] | None = None

    # ------------------------------------------------------------------ UIA helpers
    @staticmethod
    def _safe(call, default=None):
        try:
            return call()
        except Exception:  # noqa: BLE001 - a vanished element raises COMError
            return default

    def _role(self, el) -> str:
        return CONTROL_TYPES.get(self._safe(lambda: int(el.CurrentControlType), 0), "") or \
            (self._safe(lambda: str(el.CurrentLocalizedControlType), "") or "custom")

    def _name(self, el) -> str:
        return self._safe(lambda: str(el.CurrentName or ""), "") or ""

    def _pattern(self, el, pattern_id: int):
        _, module = _uia()
        interfaces = {
            INVOKE: module.IUIAutomationInvokePattern, VALUE: module.IUIAutomationValuePattern,
            RANGE_VALUE: module.IUIAutomationRangeValuePattern, SCROLL: module.IUIAutomationScrollPattern,
            EXPAND_COLLAPSE: module.IUIAutomationExpandCollapsePattern,
            SELECTION_ITEM: module.IUIAutomationSelectionItemPattern, TEXT: module.IUIAutomationTextPattern,
            TOGGLE: module.IUIAutomationTogglePattern, LEGACY: module.IUIAutomationLegacyIAccessiblePattern,
            WINDOW_PATTERN: module.IUIAutomationWindowPattern,
        }
        raw = self._safe(lambda: el.GetCurrentPattern(pattern_id))
        if not raw:
            return None
        return self._safe(lambda: raw.QueryInterface(interfaces[pattern_id]))

    def _children(self, el) -> list[Any]:
        _uia()
        walker = _THREAD.walker
        out = []
        child = self._safe(lambda: walker.GetFirstChildElement(el))
        while child and len(out) < 2000:
            out.append(child)
            child = self._safe(lambda c=child: walker.GetNextSiblingElement(c))
        return out

    def _bounds(self, el) -> tuple[float, float, float, float] | None:
        rect = self._safe(lambda: el.CurrentBoundingRectangle)
        if rect is None or rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return float(rect.left), float(rect.top), float(rect.right - rect.left), float(rect.bottom - rect.top)

    def _value_text(self, el) -> str | None:
        pattern = self._pattern(el, VALUE)
        if pattern is not None:
            return self._safe(lambda: str(pattern.CurrentValue or ""), "")
        text = self._pattern(el, TEXT)
        if text is not None:
            return self._safe(lambda: str(text.DocumentRange.GetText(-1) or ""), "")
        return None

    def _actions(self, el) -> list[str]:
        out = []
        for pattern_id, name in ((EXPAND_COLLAPSE, "expand"), (EXPAND_COLLAPSE, "collapse"),
                                 (SCROLL, "scroll"), (SELECTION_ITEM, "select")):
            if self._pattern(el, pattern_id) is not None and name not in out:
                out.append(name)
        legacy = self._pattern(el, LEGACY)
        default = self._safe(lambda: str(legacy.CurrentDefaultAction or ""), "") if legacy is not None else ""
        if default and default.lower() not in ("press", "click", "invoke", "jump"):
            out.append(default)
        return out

    # ------------------------------------------------------------------ apps
    def _apps_by_pid(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for hwnd in _top_windows():
            out.setdefault(_window_pid(hwnd), []).append(hwnd)
        return out

    def _installed(self) -> list[dict[str, str]]:
        """Start menu apps, desktop and Store alike, from Get-StartApps; cached for a minute."""
        if self._start_apps and time.monotonic() - self._start_apps[0] < 60:
            return self._start_apps[1]
        try:
            done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                   "Get-StartApps | ConvertTo-Json -Compress"], capture_output=True, text=True,
                                  timeout=20, creationflags=0x08000000)  # CREATE_NO_WINDOW
            rows = parse_start_apps(done.stdout)
        except (OSError, subprocess.TimeoutExpired):
            rows = []
        self._start_apps = (time.monotonic(), rows)
        return rows

    def _running(self) -> list[App]:
        apps = []
        own = os.getpid()
        installed = self._installed()
        for pid in self._apps_by_pid():
            if pid == own:
                continue
            exe = _exe(pid)
            stem = PureWindowsPath(exe).stem
            row = next((r for r in installed if r["app_id"].lower().endswith(PureWindowsPath(exe).name.lower())
                        or r["name"].lower() == stem.lower()), None)
            apps.append(App(pid=pid, name=(row or {}).get("name") or stem or str(pid), exe=exe,
                            app_id=(row or {}).get("app_id", "")))
        return sorted(apps, key=lambda a: a.name.lower())

    @staticmethod
    def _keys(app: App | dict) -> tuple[str, ...]:
        if isinstance(app, App):
            return (PureWindowsPath(app.exe).stem, app.name, app.app_id)
        return (str(app.get("name", "")), str(app.get("app_id", "")))

    @classmethod
    def _refused(cls, app: App | dict) -> bool:
        return RefusedApps(OWN_APPS).hits(*cls._keys(app))

    def _running_match(self, query: str) -> App | None:
        wanted = str(query or "").strip().lower()
        if not wanted:
            return None
        running = self._running()
        for app in running:
            if wanted in (app.name.lower(), PureWindowsPath(app.exe).stem.lower(), PureWindowsPath(app.exe).name.lower(),
                          app.app_id.lower(), str(app.pid), app.exe.lower()):
                return app
        return next((app for app in running if wanted in app.name.lower()), None)

    def _installed_match(self, query: str) -> dict[str, str] | None:
        wanted = str(query or "").strip().lower()
        rows = self._installed()
        for row in rows:
            if wanted in (row["name"].lower(), row["app_id"].lower()):
                return row
        return next((row for row in rows if wanted and wanted in row["name"].lower()), None)

    def resolve_app(self, query: str, *, launch: bool = True) -> App:
        app = self._running_match(query)
        from openprogram.host._platform import front

        if app is None and launch:
            # An application that starts may take the front; the guard is told which process is the new one.
            with front.starting():
                app = self._launch(query)
                if app is not None:
                    front.note(app.pid)
        if app is None:
            raise ComputerUseError(f"{query!r} is not running; list_apps shows the running and installed apps")
        RefusedApps(OWN_APPS).refuse(app.name, *self._keys(app))
        front.note(app.pid)
        return app

    def _launch(self, query: str) -> App | None:
        row = self._installed_match(query)
        if row is None:
            return None
        RefusedApps(OWN_APPS).refuse(row["name"], *self._keys(row))
        before = set(self._apps_by_pid())
        # Starting through the shell's AppsFolder works for Store and desktop apps alike.
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{row['app_id']}"])
        deadline = time.monotonic() + LAUNCH_SECONDS
        while time.monotonic() < deadline:
            check_cancelled()
            time.sleep(0.4)
            for app in self._running():
                if app.pid not in before and (app.name == row["name"] or app.app_id == row["app_id"]):
                    return app
        raise ComputerUseError(f"{row['name']} was launched but no window of it appeared within "
                               f"{LAUNCH_SECONDS:g} seconds")

    def list_apps(self) -> str:
        running = self._running()
        by_pid = self._apps_by_pid()
        lines = ["Running applications:"]
        for app in running:
            count = len(by_pid.get(app.pid, []))
            state = [f"{count} window" + ("" if count == 1 else "s")]
            if self._refused(app):
                state.append("not controllable")
            lines.append(f"- {app.name} ({app.ident}, pid {app.pid}; {', '.join(state)})")
        if len(lines) == 1:
            lines.append("- none with a window")
        names = {app.name.lower() for app in running}
        installed = [row for row in self._installed() if row["name"].lower() not in names and not self._refused(row)]
        if installed:
            lines += ["", "Installed, not running (get_app_state launches them):"]
            lines += [f"- {row['name']} ({row['app_id']})" for row in installed]
        return "\n".join(lines)

    def app_info(self, query: str) -> dict[str, Any]:
        info: dict[str, Any] = {"query": query, "name": query, "bundle_id": "", "icon": "",
                                "installed": False, "running": False, "refused": False}
        if not isinstance(query, str) or not query.strip():
            return info
        app = self._running_match(query)
        row = self._installed_match(app.name if app else query)
        if app is None and row is None:
            return info
        info.update(installed=row is not None, running=app is not None,
                    name=(row or {}).get("name") or (app.name if app else query),
                    bundle_id=(app.ident if app else row["app_id"]))
        info["refused"] = self._refused(app) if app else self._refused(row)
        return info

    def running_apps(self) -> list[dict[str, Any]]:
        return [{"query": app.ident, "name": app.name, "bundle_id": app.ident, "icon": "", "installed": bool(app.app_id),
                 "running": True, "refused": False} for app in self._running() if not self._refused(app)]

    # ------------------------------------------------------------------ windows
    def _windows(self, pid: int) -> list[Win]:
        user32 = _user32()
        out = []
        for hwnd in self._apps_by_pid().get(pid, []):
            x, y, w, h = _rect(hwnd)
            out.append(Win(hwnd, _text(hwnd), x, y, w, h, bool(user32.IsIconic(hwnd)), _class(hwnd)))
        return out

    def _pick(self, app: App, which=None, window_id=None, prefer_id: int = 0) -> Win | None:
        windows = self._windows(app.pid)
        if window_id not in (None, "", 0):
            try:
                wanted = int(window_id)
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("window_id must be an integer from list_windows") from exc
            for win in windows:
                if win.hwnd == wanted:
                    return win
            raise ComputerUseError(f"window_id {wanted} is not an open window of this app; call list_windows")
        if which not in (None, ""):
            if isinstance(which, int) or str(which).strip().isdigit():
                index = int(which)
                if 0 <= index < len(windows):
                    return windows[index]
                raise ComputerUseError(f"window {index} does not exist; the app has {len(windows)} window(s)")
            needle = str(which).strip().lower()
            for win in windows:
                if needle in win.title.lower():
                    return win
            raise ComputerUseError(f"no window title contains {which!r}")
        for win in windows:
            if win.hwnd == prefer_id:
                return win
        return windows[0] if windows else None

    def list_windows(self, query: str) -> str:
        app = self.resolve_app(query, launch=False)
        front = int(_user32().GetForegroundWindow() or 0)
        lines = [f"Windows of {app.name} (pid {app.pid}):"]
        windows = self._windows(app.pid)
        for position, win in enumerate(windows):
            notes = [n for n, on in (("focused", win.hwnd == front), ("minimized", win.minimized)) if on]
            lines.append(f'- {position}: "{win.title}" window_id {win.hwnd}, {win.width}x{win.height} at '
                         f'({win.x}, {win.y})' + (f" [{', '.join(notes)}]" if notes else ""))
        if not windows:
            lines.append("- none open")
        return "\n".join(lines)

    # ------------------------------------------------------------------ screenshot
    def _capture(self, win: Win, region=None) -> tuple[bytes | None, float]:
        """PNG of the window through PrintWindow (covered windows included), scaled to MAX_IMAGE_SIDE."""
        if win.minimized:
            return None, 1.0
        from ctypes import wintypes
        user32, gdi32 = _user32(), _STATE["gdi32"]
        outer = wintypes.RECT()
        user32.GetWindowRect(win.hwnd, ctypes.byref(outer))
        full_w, full_h = outer.right - outer.left, outer.bottom - outer.top
        if full_w <= 0 or full_h <= 0:
            return None, 1.0
        screen = user32.GetDC(0)
        whole_dc = gdi32.CreateCompatibleDC(screen)
        whole = gdi32.CreateCompatibleBitmap(screen, full_w, full_h)
        gdi32.SelectObject(whole_dc, whole)
        try:
            if not user32.PrintWindow(win.hwnd, whole_dc, 2):  # PW_RENDERFULLCONTENT
                return None, 1.0
            # The visible frame inside the outer rectangle (the rest is the invisible resize border).
            ox, oy = win.x - outer.left, win.y - outer.top
            rx, ry, rw, rh = region or (0, 0, win.width, win.height)
            rx, ry, rw, rh = int(ox + rx), int(oy + ry), int(rw), int(rh)
            scale = 1.0
            if region is None and max(rw, rh) > MAX_IMAGE_SIDE:
                scale = MAX_IMAGE_SIDE / max(rw, rh)
            out_w, out_h = max(1, int(rw * scale)), max(1, int(rh * scale))
            out_dc = gdi32.CreateCompatibleDC(screen)
            out = gdi32.CreateCompatibleBitmap(screen, out_w, out_h)
            previous = gdi32.SelectObject(out_dc, out)
            try:
                gdi32.SetStretchBltMode(out_dc, 4)  # HALFTONE
                gdi32.StretchBlt(out_dc, 0, 0, out_w, out_h, whole_dc, rx, ry, rw, rh, 0x00CC0020)  # SRCCOPY
                gdi32.SelectObject(out_dc, previous)  # GetDIBits reads a bitmap no DC holds

                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

                header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), out_w, -out_h, 1, 32, 0, 0, 0, 0, 0, 0)
                pixels = ctypes.create_string_buffer(out_w * out_h * 4)
                if not gdi32.GetDIBits(screen, out, 0, out_h, pixels, ctypes.byref(header), 0):
                    return None, 1.0
                return png_encode(out_w, out_h, pixels.raw), scale
            finally:
                gdi32.DeleteObject(out)
                gdi32.DeleteDC(out_dc)
        finally:
            gdi32.DeleteObject(whole)
            gdi32.DeleteDC(whole_dc)
            user32.ReleaseDC(0, screen)

    # ------------------------------------------------------------------ tree
    def _node(self, el, depth: int) -> Node:
        role = self._role(el)
        label = self._name(el)
        flags: list[str] = []
        value = ""
        secure = bool(self._safe(lambda: el.CurrentIsPassword, False))
        if secure:
            flags.append("secure")
        toggle = self._pattern(el, TOGGLE)
        if toggle is not None and not secure:
            state = self._safe(lambda: int(toggle.CurrentToggleState), 0)
            flags.append({1: "checked", 2: "mixed"}.get(state, "unchecked"))
        elif not secure:
            ranged = self._pattern(el, RANGE_VALUE)
            if ranged is not None:
                current = self._safe(lambda: float(ranged.CurrentValue))
                value = f"{current:g}" if current is not None else ""
            else:
                text = self._value_text(el)
                if text and text != label:
                    value = text[:400]
        if not self._safe(lambda: el.CurrentIsEnabled, True):
            flags.append("disabled")
        if self._safe(lambda: el.CurrentHasKeyboardFocus, False):
            flags.append("focused")
        item = self._pattern(el, SELECTION_ITEM)
        if item is not None and self._safe(lambda: item.CurrentIsSelected, False):
            flags.append("selected")
        expand = self._pattern(el, EXPAND_COLLAPSE)
        if expand is not None and self._safe(lambda: int(expand.CurrentExpandCollapseState), 0) == 1:
            flags.append("expanded")
        settable = self._pattern(el, VALUE)
        if not secure and (settable is not None and not self._safe(lambda: settable.CurrentIsReadOnly, True)
                           or self._pattern(el, RANGE_VALUE) is not None):
            flags.append("settable")
        placeholder = self._safe(lambda: str(el.CurrentHelpText or ""), "") if role == "edit" and not value else ""
        return Node(role=role, depth=depth, label=label, value=value, placeholder=placeholder or "",
                    flags=flags, actions=self._actions(el))

    def _read_tree(self, root) -> tuple[list[Node], list[Any], bool]:
        nodes: list[Node] = []
        elements: list[Any] = []
        if root is None:
            return nodes, elements, True
        stack = [(root, 0)]
        while stack:
            check_cancelled()
            if len(nodes) >= MAX_NODES:
                return nodes, elements, False
            el, depth = stack.pop()
            if depth and self._safe(lambda el=el: el.CurrentIsOffscreen, False) and self._role(el) != "menu item":
                continue
            node = self._node(el, depth)
            transparent = node.role in _TRANSPARENT and not node.label and not node.value and not node.actions
            child_depth = depth
            if not transparent or not depth:
                node.index = len(nodes)
                nodes.append(node)
                elements.append(el)
                child_depth = depth + 1
            if child_depth > MAX_DEPTH:
                continue
            stack.extend((child, child_depth) for child in reversed(self._children(el)))
        return nodes, elements, True

    def get_state(self, session: str, query: str, *, window=None, window_id=None, full=False,
                  as_diff=True, include: str = "all") -> Observation:
        include = str(include or "all").lower()
        if include not in INCLUDE:
            raise ComputerUseError("include must be all, tree or screenshot")
        with self._lock:
            automation, _ = _uia()
            app = self.resolve_app(query)
            key = (session, app.pid)
            previous = self._states.get(key)
            win = self._pick(app, window, window_id, previous.window_id if previous else 0)
            title, wid = (win.title, win.hwnd) if win else ("", 0)
            same_window = bool(previous) and previous.window_id == wid
            if include == "screenshot" and not same_window:
                raise ComputerUseError("Read this window with include=all first; a screenshot-only read keeps the "
                                       "tree of the window the model already has.")
            image, scale = (None, 1.0)
            if include != "tree" and win is not None:
                image, scale = self._capture(win)
            lines = [f"App: {app.name} ({app.ident}, pid {app.pid})"]
            if win is None:
                lines.append("Window: none open.")
            else:
                note = " (minimized)" if win.minimized else ""
                lines.append(f'Window: "{title}"{note}, window_id {wid}, {win.width}x{win.height} pixels')
                if image is not None:
                    w, h = int(round(win.width * scale)), int(round(win.height * scale))
                    lines.append(f"Screenshot: {w}x{h} pixels; x,y coordinates in actions are pixels of this screenshot.")
                elif include != "tree":
                    lines.append("Screenshot: unavailable (the window is minimized; show_window restores it). "
                                 "Use element indices.")
            others = [w for w in self._windows(app.pid) if w.hwnd != wid]
            if others:
                lines.append("Other windows: " + "; ".join(f'"{w.title}"' for w in others[:8])
                             + ". list_windows shows their ids; pass window=<title> or window_id to read one.")
            frame = (win.x, win.y, win.width, win.height) if win else None
            if include == "screenshot":
                previous.origin = (frame[0], frame[1]) if frame else previous.origin
                previous.size = (frame[2], frame[3]) if frame else previous.size
                previous.scale, previous.has_screenshot = scale, image is not None
                previous.text_boxes = _read_text(image) if previous.sparse else []
                lines += ["", "Tree not read (include=screenshot); element indices are those of the last tree."]
                lines.extend(_text_section(previous.text_boxes))
                return Observation(text="\n".join(lines), image=image, app=app.name)
            root = self._safe(lambda: automation.ElementFromHandle(wid)) if wid else None
            nodes, elements, complete = self._read_tree(root)
            prior = previous.snapshot if same_window else None
            fresh = number(nodes, prior)
            body, is_diff = diff(prior if (as_diff and not full) else None, nodes)
            state = WindowState(
                pid=app.pid, window_title=title, window_id=wid,
                elements={node.index: element for node, element in zip(nodes, elements)},
                nodes=nodes, by_index={node.index: node for node in nodes}, snapshot=snapshot(nodes, fresh),
                origin=(frame[0], frame[1]) if frame else (0.0, 0.0),
                size=(frame[2], frame[3]) if frame else (0.0, 0.0), scale=scale, has_screenshot=image is not None)
            if include == "tree" and same_window and previous.has_screenshot:
                state.scale, state.has_screenshot = previous.scale, True
            state.sparse = window_node_count(nodes) < OCR_SPARSE_NODES
            if state.sparse:
                state.text_boxes = _read_text(image)
            self._states[key] = state
            if not complete:
                lines.append(f"The tree was cut at {MAX_NODES} elements.")
            lines.append("")
            lines.append("Accessibility tree changes since the last state:" if is_diff else "Accessibility tree:")
            lines.append(body or "(empty)")
            lines.extend(_text_section(state.text_boxes))
            return Observation(text="\n".join(lines), image=image, app=app.name)

    def _state_for(self, session: str, query: str) -> tuple[App, WindowState]:
        app = self.resolve_app(query, launch=False)
        state = self._states.get((session, app.pid))
        if state is None:
            raise ComputerUseError(f"call get_app_state for {app.name} first")
        return app, state

    def _element(self, state: WindowState, index):
        try:
            index = int(index)
        except (TypeError, ValueError) as exc:
            raise ComputerUseError("element_index must be an integer from the last state") from exc
        el = state.elements.get(index)
        if el is None:
            raise ComputerUseError(f"element {index} is not in the last state; call get_app_state")
        if self._safe(lambda: el.CurrentControlType) is None:
            el = self._rebind(state, index)
            if el is None:
                raise ComputerUseError(f"element {index} no longer exists; call get_app_state for a fresh tree")
        return el

    def _rebind(self, state: WindowState, index: int):
        """The element now behind ``index`` when the app replaced the one read earlier (see :func:`tree.rebind`)."""
        try:
            if not state.window_id:
                return None
            automation, _ = _uia()
            root = self._safe(lambda: automation.ElementFromHandle(state.window_id))
            if root is None:
                return None
            nodes, elements, _ = self._read_tree(root)
            return rebind(state, index, nodes, elements)
        except Exception:
            # The step then fails as it did before: the model reads the window again.
            return None

    def _node_at(self, state: WindowState, index) -> Node:
        return state.by_index[int(index)]

    def _center(self, el) -> tuple[float, float]:
        frame = self._bounds(el)
        if frame is None:
            raise ComputerUseError("this element has no position on screen; act on it by its patterns instead")
        return frame[0] + frame[2] / 2, frame[1] + frame[3] / 2

    def _point(self, state: WindowState, x, y) -> tuple[float, float]:
        if x is None or y is None:
            raise ComputerUseError("give element_index, or x and y in pixels of the last screenshot")
        if not state.has_screenshot:
            raise ComputerUseError("this window has no screenshot to take coordinates from; call get_app_state")
        try:
            px, py = float(x), float(y)
        except (TypeError, ValueError) as exc:
            raise ComputerUseError("x and y must be numbers") from exc
        w, h = state.size[0] * state.scale, state.size[1] * state.scale
        if not (0 <= px < w and 0 <= py < h):
            raise ComputerUseError(f"({px:g}, {py:g}) is outside the {int(w)}x{int(h)} screenshot")
        return state.origin[0] + px / state.scale, state.origin[1] + py / state.scale

    def _focused(self, state: WindowState | None):
        automation, _ = _uia()
        el = self._safe(lambda: automation.GetFocusedElement())
        if el is not None and state is not None and self._safe(lambda: int(el.CurrentProcessId), 0) != state.pid:
            return None
        return el

    def _refuse_secure(self, el) -> None:
        if el is not None and self._safe(lambda: el.CurrentIsPassword, False):
            refuse_password_field("typing into this password field")

    # ------------------------------------------------------------------ input delivery
    def _foreground_only(self, state: WindowState) -> bool:
        return _class(state.window_id) in FOREGROUND_CLASSES

    def _target_child(self, hwnd: int, sx: float, sy: float) -> tuple[int, int, int]:
        """The deepest visible child under a screen point, and the point in its client coordinates."""
        from ctypes import wintypes
        user32 = _user32()
        current = hwnd
        for _ in range(32):
            point = wintypes.POINT(int(sx), int(sy))
            user32.ScreenToClient(current, ctypes.byref(point))
            child = user32.ChildWindowFromPointEx(current, point, 0x1 | 0x2 | 0x4)  # skip invisible/disabled/transparent
            if not child or int(child) == current:
                return current, point.x, point.y
            current = int(child)
        point = wintypes.POINT(int(sx), int(sy))
        user32.ScreenToClient(current, ctypes.byref(point))
        return current, point.x, point.y

    @staticmethod
    def _lparam(x: int, y: int) -> int:
        return ((y & 0xFFFF) << 16) | (x & 0xFFFF)

    def _post_click(self, state, point, button: str, count: int, mods: list[int], hold: float) -> None:
        user32 = _user32()
        target, cx, cy = self._target_child(state.window_id, *point)
        down, up, flag = _BUTTON_MESSAGES[button]
        extra = (MK_SHIFT if 0x10 in mods else 0) | (MK_CONTROL if 0x11 in mods else 0)
        lparam = self._lparam(cx, cy)
        user32.PostMessageW(target, WM_MOUSEMOVE, extra, lparam)
        for n in range(count):
            message = WM_LBUTTONDBLCLK if button == "left" and n == 1 else down
            user32.PostMessageW(target, message, flag | extra, lparam)
            if hold:
                time.sleep(hold)
            user32.PostMessageW(target, up, extra, lparam)
            time.sleep(0.03)

    def _send_input_click(self, state, point, button: str, count: int, mods: list[int], hold: float) -> None:
        with self._forward(state):
            user32 = _user32()
            user32.SetCursorPos(int(point[0]), int(point[1]))
            flags = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}[button]
            for vk in mods:
                _send_key(vk, False)
            try:
                for _ in range(count):
                    _send_mouse(flags[0])
                    if hold:
                        time.sleep(hold)
                    _send_mouse(flags[1])
                    time.sleep(0.05)
            finally:
                for vk in reversed(mods):
                    _send_key(vk, True)

    def _forward(self, state: WindowState):
        return _Forward(state.window_id)

    # ------------------------------------------------------------------ actions
    def act(self, session: str, action: str, args: dict[str, Any]) -> str:
        with self._lock:
            _uia()
            handler = getattr(self, f"_do_{action}", None)
            if handler is None:
                raise ComputerUseError(f"unknown action {action!r}")
            if action in self.STATELESS or (action in self.AT_FOCUS and args.get("element_index") is None):
                app = self.resolve_app(args.get("app") or "", launch=False)
                return handler(app, self._states.get((session, app.pid)), args)
            app, state = self._state_for(session, args.get("app") or "")
            return handler(app, state, args)

    def _ensure_window(self, app: App) -> WindowState:
        win = self._pick(app)
        if win is None:
            raise ComputerUseError(f"{app.name} has no open window")
        return WindowState(pid=app.pid, window_title=win.title, window_id=win.hwnd, origin=(win.x, win.y),
                           size=(win.width, win.height))

    def _do_click(self, app, state, args) -> str:
        button, count = click_args(args)
        mods = [VK_MODIFIERS[m.strip().lower()] for m in str(args.get("key") or "").replace(",", "+").split("+")
                if m.strip() and m.strip().lower() in VK_MODIFIERS]
        unknown = [m for m in str(args.get("key") or "").replace(",", "+").split("+")
                   if m.strip() and m.strip().lower() not in VK_MODIFIERS]
        if unknown:
            raise ComputerUseError(f"unknown modifier {unknown[0]!r}")
        hold = seconds_arg(args.get("duration"), "duration")
        index = args.get("element_index")
        if index is not None:
            el = self._element(state, index)
            node = self._node_at(state, index)
            if count == 1 and not mods and not hold and button == "left":
                for pattern_id, call in ((INVOKE, "Invoke"), (TOGGLE, "Toggle"), (SELECTION_ITEM, "Select"),
                                         (EXPAND_COLLAPSE, None), (LEGACY, "DoDefaultAction")):
                    pattern = self._pattern(el, pattern_id)
                    if pattern is None:
                        continue
                    if call is None:
                        state_now = self._safe(lambda: int(pattern.CurrentExpandCollapseState), 0)
                        call = "Collapse" if state_now == 1 else "Expand"
                    if self._safe(lambda: getattr(pattern, call)() or True, False):
                        return f"pressed {node.body()}"
                if node.role == "edit" and self._safe(lambda: el.SetFocus() or True, False):
                    return f"focused {node.body()}"
            point, what = self._center(el), node.body()
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"at ({args.get('x')}, {args.get('y')})"
        if self._foreground_only(state):
            self._send_input_click(state, point, button, count, mods, hold)
            tail = " (this window takes real input only: it came forward for the click, then focus went back)"
        else:
            self._post_click(state, point, button, count, mods, hold)
            tail = ""
        return f"{click_words(button, count)} {what}" + (f" (held {hold:g}s)" if hold else "") + tail

    def _do_hover(self, app, state, args) -> str:
        index = args.get("element_index")
        if index is not None:
            point, what = self._center(self._element(state, index)), self._node_at(state, index).body()
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"({args.get('x')}, {args.get('y')})"
        rest = seconds_arg(args.get("duration"), "duration") or HOVER_SECONDS
        target, cx, cy = self._target_child(state.window_id, *point)
        deadline = time.monotonic() + rest
        while time.monotonic() < deadline:
            _user32().PostMessageW(target, WM_MOUSEMOVE, 0, self._lparam(cx, cy))
            time.sleep(0.1)
        return f"rested the pointer over {what} for {rest:g}s"

    def _do_wait(self, app, state, args) -> str:
        rest = seconds_arg(args.get("duration"), "duration") or 1.0
        deadline = time.monotonic() + rest
        while time.monotonic() < deadline:
            check_cancelled()
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return f"waited {rest:g}s"

    def _do_drag(self, app, state, args) -> str:
        start = self._point(state, args.get("x"), args.get("y"))
        end = self._point(state, args.get("to_x"), args.get("to_y"))
        if self._foreground_only(state):
            with self._forward(state):
                user32 = _user32()
                user32.SetCursorPos(int(start[0]), int(start[1]))
                _send_mouse(0x2)
                for step in range(1, 13):
                    t = step / 12
                    user32.SetCursorPos(int(start[0] + (end[0] - start[0]) * t), int(start[1] + (end[1] - start[1]) * t))
                    time.sleep(0.015)
                _send_mouse(0x4)
        else:
            user32 = _user32()
            target, sx, sy = self._target_child(state.window_id, *start)
            user32.PostMessageW(target, WM_LBUTTONDOWN, MK_LBUTTON, self._lparam(sx, sy))
            for step in range(1, 13):
                t = step / 12
                px, py = start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t
                # Mouse messages after a press go to the window that took the press.
                from ctypes import wintypes
                pt = wintypes.POINT(int(px), int(py))
                user32.ScreenToClient(target, ctypes.byref(pt))
                user32.PostMessageW(target, WM_MOUSEMOVE, MK_LBUTTON, self._lparam(pt.x, pt.y))
                time.sleep(0.015)
            user32.PostMessageW(target, WM_LBUTTONUP, 0, self._lparam(pt.x, pt.y))
        return f"dragged from ({args.get('x')}, {args.get('y')}) to ({args.get('to_x')}, {args.get('to_y')})"

    def _scrollable(self, el):
        _uia()
        current = el
        for _ in range(12):
            if current is None:
                return None
            pattern = self._pattern(current, SCROLL)
            if pattern is not None:
                return pattern
            current = self._safe(lambda c=current: _THREAD.walker.GetParentElement(c))
        return None

    def _do_scroll(self, app, state, args) -> str:
        direction, pages, pixels = scroll_args(args)
        amount = f"{pixels:g} pixels" if pixels else f"{pages:g} page(s)"
        index = args.get("element_index")
        if index is not None and not pixels:
            el = self._element(state, index)
            node = self._node_at(state, index)
            pattern = self._scrollable(el)
            if pattern is not None:
                large_decrement, no_amount, large_increment = 0, 2, 3
                step = large_decrement if direction in ("up", "left") else large_increment
                for _ in range(max(1, int(round(pages)))):
                    horizontal = step if direction in ("left", "right") else no_amount
                    vertical = step if direction in ("up", "down") else no_amount
                    if not self._safe(lambda: pattern.Scroll(horizontal, vertical) or True, False):
                        break
                else:
                    return f"scrolled {direction} {pages:g} page(s) in {node.body()}"
            point, what = self._center(el), f"over {node.body()}"
        elif index is not None:
            el = self._element(state, index)
            point, what = self._center(el), f"over {self._node_at(state, index).body()}"
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"at ({args.get('x')}, {args.get('y')})"
        notches = max(1, int(round(pixels / PIXELS_PER_NOTCH))) if pixels else max(1, int(round(pages * NOTCHES_PER_PAGE)))
        sign = 1 if direction in ("up", "right") else -1
        message = WM_MOUSEWHEEL if direction in ("up", "down") else WM_MOUSEHWHEEL
        target, _, _ = self._target_child(state.window_id, *point)
        user32 = _user32()
        for _ in range(notches):
            wparam = ((sign * WHEEL_DELTA) & 0xFFFF) << 16
            user32.PostMessageW(target, message, wparam, self._lparam(int(point[0]), int(point[1])))
            time.sleep(0.02)
        return f"scrolled {direction} {amount} {what}"

    def _focus_hwnd(self, state: WindowState) -> int:
        """The child window holding keyboard focus in the target's thread."""
        from ctypes import wintypes

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                        ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND),
                        ("hwndMenuOwner", wintypes.HWND), ("hwndMoveSize", wintypes.HWND),
                        ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]

        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        if _user32().GetGUIThreadInfo(_thread(state.window_id), ctypes.byref(info)) and info.hwndFocus:
            return int(info.hwndFocus)
        return state.window_id

    def _post_key(self, hwnd: int, vk: int, *, up: bool) -> None:
        scan = _user32().MapVirtualKeyW(vk, 0)
        lparam = 1 | (scan << 16) | ((1 << 24) if vk in _EXTENDED_VKS else 0)
        if up:
            lparam |= (1 << 30) | (1 << 31)
        _user32().PostMessageW(hwnd, WM_KEYUP if up else WM_KEYDOWN, vk, lparam)

    def _post_text(self, hwnd: int, text: str) -> None:
        user32 = _user32()
        for unit in struct.unpack(f"<{len(text.encode('utf-16-le')) // 2}H", text.encode("utf-16-le")):
            if unit == 0x0A:
                self._post_key(hwnd, 0x0D, up=False)
                user32.PostMessageW(hwnd, WM_CHAR, 0x0D, 1)
                self._post_key(hwnd, 0x0D, up=True)
            else:
                user32.PostMessageW(hwnd, WM_CHAR, unit, 1)
            time.sleep(0.004)

    def _do_press_key(self, app, state, args) -> str:
        key = str(args.get("key") or "").strip()
        if not key:
            raise ComputerUseError("key is required")
        mods, vk, char = key_chord(key)
        chord = bool(mods)  # a character's own Shift travels in WM_CHAR, so only named modifiers need the keyboard
        if not vk and char:
            scanned = _user32().VkKeyScanW(char)
            if scanned == -1:
                raise ComputerUseError(f"{char!r} has no key on this keyboard layout")
            vk = scanned & 0xFF
            mods = mods + [vk_mod for bit, vk_mod in ((0x100, 0x10), (0x200, 0x11), (0x400, 0x12))
                           if scanned & bit and vk_mod not in mods]
        hold = seconds_arg(args.get("duration"), "duration")
        if args.get("element_index") is not None:
            el = self._element(state, args["element_index"])
            self._refuse_secure(el)
            self._safe(lambda: el.SetFocus())
        else:
            self._refuse_secure(self._focused(state))
        state = state or self._ensure_window(app)
        if not chord and not self._foreground_only(state):
            target = self._focus_hwnd(state)
            self._post_key(target, vk, up=False)
            if char:
                _user32().PostMessageW(target, WM_CHAR, ord(char), 1)
            if hold:
                time.sleep(hold)
            self._post_key(target, vk, up=True)
            return f"pressed {key}"
        # Shortcuts read the keyboard state, which posted messages do not change.
        with self._forward(state):
            for mod in mods:
                _send_key(mod, False)
            try:
                _send_key(vk, False)
                if hold:
                    time.sleep(hold)
                _send_key(vk, True)
            finally:
                for mod in reversed(mods):
                    _send_key(mod, True)
        return f"pressed {key} (a shortcut needs the real keyboard: the window came forward, then focus went back)"

    def _do_type_text(self, app, state, args) -> str:
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise ComputerUseError("text is required")
        if len(text) > MAX_TEXT_CHARS:
            raise ComputerUseError(f"text is longer than {MAX_TEXT_CHARS} characters; use paste")
        if args.get("element_index") is not None:
            el = self._element(state, args["element_index"])
            node = self._node_at(state, args["element_index"])
            if node.role in _NOT_TEXT_ROLES:
                raise ComputerUseError(
                    f"element {args['element_index']} is a {role_name(node.role)}, not a text field: typed text "
                    "would land in whatever field has focus. Click it, use set_value, or pick the text field.")
            self._refuse_secure(el)
            self._safe(lambda: el.SetFocus())
        else:
            self._refuse_secure(self._focused(state))
        state = state or self._ensure_window(app)
        if self._foreground_only(state):
            with self._forward(state):
                for unit in struct.unpack(f"<{len(text.encode('utf-16-le')) // 2}H", text.encode("utf-16-le")):
                    if unit == 0x0A:
                        _send_key(0x0D, False)
                        _send_key(0x0D, True)
                    else:
                        _send_unicode(unit)
            return f"typed {len(text)} character(s) (this window takes real input only: it came forward, then focus went back)"
        self._post_text(self._focus_hwnd(state), text)
        return f"typed {len(text)} character(s)"

    def _do_set_value(self, app, state, args) -> str:
        index = args.get("element_index")
        el = self._element(state, index)
        node = self._node_at(state, index)
        self._refuse_secure(el)
        value = args.get("value")
        if value is None:
            raise ComputerUseError("value is required")
        toggle = self._pattern(el, TOGGLE)
        if toggle is not None:
            wanted = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "on", "yes", "checked")
            for _ in range(3):
                if (self._safe(lambda: int(toggle.CurrentToggleState), 0) == 1) == wanted:
                    return f"set {node.body()} to {wanted}"
                toggle.Toggle()
            raise ComputerUseError(f"element {index} did not take the state {wanted}")
        ranged = self._pattern(el, RANGE_VALUE)
        if ranged is not None and not isinstance(value, bool):
            try:
                number_value = float(value)
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("this element takes a number") from exc
            if not self._safe(lambda: ranged.SetValue(number_value) or True, False):
                raise ComputerUseError("the app refused the value")
            return f"set {node.body()} to {number_value:g}"
        pattern = self._pattern(el, VALUE)
        if pattern is not None and not self._safe(lambda: pattern.CurrentIsReadOnly, True):
            if not self._safe(lambda: pattern.SetValue(str(value)) or True, False):
                raise ComputerUseError("the app refused the text")
            return f"set {node.body()} to {str(value)!r}"
        legacy = self._pattern(el, LEGACY)
        if legacy is not None and self._safe(lambda: legacy.SetValue(str(value)) or True, False):
            return f"set {node.body()} to {str(value)!r}"
        raise ComputerUseError(f"element {index} has no settable value; use click or type_text")

    def _do_perform_secondary_action(self, app, state, args) -> str:
        index = args.get("element_index")
        el = self._element(state, index)
        node = self._node_at(state, index)
        wanted = str(args.get("action_name") or "").strip().lower()
        if not wanted:
            raise ComputerUseError("action_name is required")
        calls = {"expand": (EXPAND_COLLAPSE, "Expand"), "collapse": (EXPAND_COLLAPSE, "Collapse"),
                 "select": (SELECTION_ITEM, "Select"), "invoke": (INVOKE, "Invoke"), "toggle": (TOGGLE, "Toggle")}
        if wanted in calls:
            pattern = self._pattern(el, calls[wanted][0])
            if pattern is not None and self._safe(lambda: getattr(pattern, calls[wanted][1])() or True, False):
                return f"performed {wanted} on {node.body()}"
        legacy = self._pattern(el, LEGACY)
        default = self._safe(lambda: str(legacy.CurrentDefaultAction or ""), "") if legacy is not None else ""
        if default and default.lower() == wanted and self._safe(lambda: legacy.DoDefaultAction() or True, False):
            return f"performed {default} on {node.body()}"
        raise ComputerUseError(f"element {index} does not expose {wanted!r}; its actions are: "
                               + (", ".join(self._actions(el)) or "none"))

    def _do_select_text(self, app, state, args) -> str:
        index = args.get("element_index")
        el = self._element(state, index)
        node = self._node_at(state, index)
        pattern = self._pattern(el, TEXT)
        if pattern is None:
            raise ComputerUseError(f"element {index} has no text to select in")
        whole = self._safe(lambda: str(pattern.DocumentRange.GetText(-1) or ""), "")
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise ComputerUseError("text is required")
        start = find_text(whole, text, args.get("prefix") or "", args.get("suffix") or "")
        if start is None:
            raise ComputerUseError(f"{text!r} was not found in element {index} with that prefix and suffix")
        kind = str(args.get("selection_type") or "text")
        if kind not in ("text", "cursor_before", "cursor_after"):
            raise ComputerUseError("selection_type must be text, cursor_before or cursor_after")
        # Move a range from the document start by characters, then shrink it to the match.
        found = pattern.DocumentRange.Clone()
        unit_character, endpoint_start, endpoint_end = 0, 0, 1
        found.MoveEndpointByRange(endpoint_end, found, endpoint_start)
        found.MoveEndpointByUnit(endpoint_end, unit_character, start + len(text))
        found.MoveEndpointByUnit(endpoint_start, unit_character, start)
        if kind == "cursor_before":
            found.MoveEndpointByRange(endpoint_end, found, endpoint_start)
        elif kind == "cursor_after":
            found.MoveEndpointByRange(endpoint_start, found, endpoint_end)
        if not self._safe(lambda: found.Select() or True, False):
            raise ComputerUseError("the app refused the selection")
        where = {"text": "selected", "cursor_before": "placed the cursor before", "cursor_after": "placed the cursor after"}[kind]
        return f"{where} {text!r} in {node.body()}"

    def _do_paste(self, app, state, args) -> str:
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise ComputerUseError("text is required")
        kind = str(args.get("format") or "text").lower()
        kind = "md" if kind == "markdown" else kind
        if kind not in ("text", "md", "html"):
            raise ComputerUseError("format must be text, md or html")
        if args.get("element_index") is not None:
            el = self._element(state, args["element_index"])
            self._refuse_secure(el)
            self._safe(lambda: el.SetFocus())
        else:
            self._refuse_secure(self._focused(state))
        state = state or self._ensure_window(app)
        html = None
        if kind == "md":
            from markdown_it import MarkdownIt
            html = MarkdownIt("commonmark").enable("table").enable("strikethrough").render(text)
        elif kind == "html":
            html = text
        plain = text if kind != "html" else _strip_tags(text)
        saved = _clipboard_text()
        _set_clipboard(plain, html)
        try:
            target = self._focus_hwnd(state)
            if not self._foreground_only(state) and _class(target).lower() in ("edit", "richedit20w", "richedit50w",
                                                                                "richeditd2dpt", "notepadtextbox"):
                _user32().SendMessageW(target, WM_PASTE, 0, 0)
            else:
                with self._forward(state):
                    _send_key(0x11, False)
                    _send_key(ord("V"), False)
                    _send_key(ord("V"), True)
                    _send_key(0x11, True)
            time.sleep(0.5)
        finally:
            if saved is not None:
                _set_clipboard(saved, None)
        return f"pasted {len(text)} character(s) as {'markdown' if kind == 'md' else kind}"

    def _do_focus(self, app, state, args) -> str:
        index = args.get("element_index")
        el = self._element(state, index)
        node = self._node_at(state, index)
        self._refuse_secure(el)
        if not self._safe(lambda: el.CurrentIsKeyboardFocusable, False):
            raise ComputerUseError(f"element {index} cannot take keyboard focus; click it instead")
        front = _user32().GetForegroundWindow()
        self._safe(lambda: el.SetFocus())
        if front and _user32().GetForegroundWindow() != front:
            _Forward.restore(int(front))
        return f"focused {node.body()}"

    def _do_copy(self, app, state, args) -> str:
        el = self._element(state, args["element_index"]) if args.get("element_index") is not None else self._focused(state)
        self._refuse_secure(el)
        pattern = self._pattern(el, TEXT) if el is not None else None
        if pattern is None:
            raise ComputerUseError("no text element has focus; pass element_index of the text to copy from")
        ranges = self._safe(lambda: pattern.GetSelection())
        parts = []
        for i in range(self._safe(lambda: ranges.Length, 0) or 0):
            piece = self._safe(lambda i=i: str(ranges.GetElement(i).GetText(-1) or ""), "")
            if piece:
                parts.append(piece)
        text = "\n".join(parts)
        if not text:
            raise ComputerUseError("nothing is selected; select text first (select_text, or press_key ctrl+a)")
        clipped = text if len(text) <= MAX_COPY_CHARS else text[:MAX_COPY_CHARS] + "\n…(truncated)"
        return Report(f"copied {len(text)} character(s) (read from the selection; the clipboard was not touched). "
                      f"Copied text:\n<<<\n{clipped}\n>>>")

    # ------------------------------------------------------------------ menus
    def _win32_menu(self, hwnd: int) -> list[tuple[str, int, int, int]]:
        """Items of a classic menu: (title, id, submenu handle, state flags)."""
        user32 = _user32()
        return _menu_items(user32, user32.GetMenu(hwnd))

    def _do_menu(self, app, state, args) -> str:
        path = menu_path(args)
        state = state or self._ensure_window(app)
        user32 = _user32()
        items = self._win32_menu(state.window_id)
        if items:
            if not path:
                return Report("menu bar: " + " | ".join(t for t, *_ in items if t) + ". Pass menu_path to list a menu "
                              "or press an item.")
            walked, item = [], None
            level = items
            for step in path:
                wanted = menu_key(step)
                item = next((i for i in level if menu_key(i[0]) == wanted), None)
                if item is None:
                    raise ComputerUseError(f"{step!r} is not in {' > '.join(walked) or 'the menu bar'}; it has: "
                                           + " | ".join(t for t, *_ in level if t))
                walked.append(item[0])
                level = _menu_items(user32, item[2]) if item[2] else []
            if level:
                lines = []
                for title, _id, sub, flags in level:
                    if not title:
                        continue
                    line = title.replace("\t", " (") + (")" if "\t" in title else "")
                    if flags & 0x8:
                        line += " [checked]"
                    if flags & 0x3:
                        line += " [disabled]"
                    if sub:
                        line += " ▸"
                    lines.append(f"- {line}")
                return Report(" > ".join(walked) + ":\n" + "\n".join(lines))
            if item[3] & 0x3:
                raise ComputerUseError(" > ".join(walked) + " is disabled right now")
            user32.PostMessageW(state.window_id, WM_COMMAND, item[1] & 0xFFFF, 0)
            return "chose " + " > ".join(walked)
        # Ribbon and modern apps expose their menus through UI Automation only.
        automation, _ = _uia()
        root = self._safe(lambda: automation.ElementFromHandle(state.window_id))
        bar = next((el for el in self._walk(root, 4) if self._role(el) == "menu bar"), None)
        if bar is None:
            raise ComputerUseError(f"{app.name} has no menu bar (apps with a ribbon or a hamburger button keep "
                                   "their menu there; click it and read the state)")
        level = [el for el in self._children(bar) if self._role(el) == "menu item"]
        if not path:
            return Report("menu bar: " + " | ".join(self._name(el) for el in level) + ". Pass menu_path to list a "
                          "menu or press an item.")
        walked, item = [], None
        for step in path:
            wanted = menu_key(step)
            item = next((el for el in level if menu_key(self._name(el)) == wanted), None)
            if item is None:
                raise ComputerUseError(f"{step!r} is not in {' > '.join(walked) or 'the menu bar'}; it has: "
                                       + " | ".join(self._name(el) for el in level))
            walked.append(self._name(item))
            expand = self._pattern(item, EXPAND_COLLAPSE)
            if expand is not None and step is not path[-1]:
                expand.Expand()
                time.sleep(0.2)
                level = [el for el in self._walk(item, 3) if self._role(el) == "menu item"]
            else:
                level = []
        if expand := self._pattern(item, EXPAND_COLLAPSE):
            expand.Expand()
            time.sleep(0.2)
            children = [el for el in self._walk(item, 3) if self._role(el) == "menu item"]
            if children:
                return Report(" > ".join(walked) + ":\n" + "\n".join(f"- {self._name(el)}" for el in children))
        invoke = self._pattern(item, INVOKE)
        if invoke is None or not self._safe(lambda: invoke.Invoke() or True, False):
            raise ComputerUseError(f"pressing {' > '.join(walked)} failed")
        return "chose " + " > ".join(walked)

    def _walk(self, root, depth: int) -> list[Any]:
        out, stack = [], [(root, 0)] if root is not None else []
        while stack:
            el, d = stack.pop()
            out.append(el)
            if d < depth:
                stack.extend((c, d + 1) for c in self._children(el))
        return out

    def _do_show_window(self, app, state, args) -> str:
        win = self._pick(app, args.get("window"), args.get("window_id"), state.window_id if state else 0)
        if win is None:
            raise ComputerUseError(f"{app.name} has no open window")
        if not win.minimized:
            return Report("the window is already showing")
        _user32().ShowWindow(win.hwnd, 4)  # SW_SHOWNOACTIVATE
        time.sleep(0.3)
        return "restored the window without activating it"

    # ------------------------------------------------------------------ reading aids
    def zoom(self, session: str, query: str, x, y, width, height) -> Observation:
        with self._lock:
            app, state = self._state_for(session, query)
            if not state.has_screenshot:
                raise ComputerUseError("This window has no screenshot to zoom into; call get_app_state first.")
            try:
                px, py, pw, ph = (float(v) for v in (x, y, width, height))
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("zoom needs x, y, width and height in screenshot pixels") from exc
            full_w, full_h = state.size[0] * state.scale, state.size[1] * state.scale
            px, py = max(0.0, px), max(0.0, py)
            pw, ph = min(pw, full_w - px), min(ph, full_h - py)
            if pw < 4 or ph < 4:
                raise ComputerUseError(f"the region must lie inside the {int(full_w)}x{int(full_h)} screenshot")
            win = next((w for w in self._windows(app.pid) if w.hwnd == state.window_id), None)
            if win is None:
                raise ComputerUseError("The window is gone; call get_app_state again.")
            region = (px / state.scale, py / state.scale, pw / state.scale, ph / state.scale)
            image, _ = self._capture(win, region=region)
            if image is None:
                raise ComputerUseError("The window could not be captured; call get_app_state again.")
            text = (f"Zoomed region ({int(px)}, {int(py)}) {int(pw)}x{int(ph)} of the last screenshot, at full "
                    "pixel density. Coordinates for actions still refer to the last screenshot, not to this image.")
            return Observation(text=text, image=image, app=app.name)

    def find(self, session: str, query: str, text: str = "", role: str = "") -> str:
        with self._lock:
            _, state = self._state_for(session, query)
            needle = " ".join(str(text or "").split()).lower()
            wanted_role = str(role or "").strip().lower()
            if not needle and not wanted_role:
                raise ComputerUseError("find needs text, role, or both")
            hits = []
            for node in state.nodes:
                if wanted_role and wanted_role not in (node.role.lower(), role_name(node.role).lower()):
                    continue
                haystack = " ".join(f"{node.label} {node.value} {node.placeholder}".split()).lower()
                if needle and needle not in haystack:
                    continue
                hits.append(f"{node.index} {node.body()}")
            if not hits and needle and not wanted_role and state.text_boxes:
                seen = ocr.matching(state.text_boxes, needle)
                if seen:
                    return (f"no element matches, but the screenshot shows {len(seen)} line(s) with that text "
                            "(OCR; act with x,y at the centre):\n" + ocr.render(seen[:60]))
            if not hits:
                return "no element of the last tree matches; call get_app_state for a fresh tree, or scroll"
            more = f"\n…and {len(hits) - 60} more; narrow the search." if len(hits) > 60 else ""
            return f"{len(hits)} match(es):\n" + "\n".join(hits[:60]) + more

    # ------------------------------------------------------------------ audio
    def start_audio(self, session: str, query: str, max_duration=None) -> Report:
        try:
            limit = audio.seconds(max_duration)
        except ValueError as exc:
            raise ComputerUseError(str(exc)) from exc
        with self._lock:
            running = self._recordings.get(session)
            if running is not None and not running.stopped:
                raise ComputerUseError(f"Already recording {running.app}; call stop_audio_recording first.")
            app = self.resolve_app(query)
            from . import windows_audio
            try:
                recording = windows_audio.start(app.pid, app.name, limit)
            except (OSError, RuntimeError) as exc:
                raise ComputerUseError(f"Could not record {app.name}: {exc}") from exc
            self._recordings[session] = recording
            return Report(f"Recording the sound {app.name} plays, for up to {limit:g} seconds; the user still hears "
                          "it and nothing else is recorded. Act as usual, then call stop_audio_recording.")

    def stop_audio(self, session: str, query: str = "") -> AudioClip:
        with self._lock:
            recording = self._recordings.get(session)
            if recording is None:
                raise ComputerUseError("Nothing is being recorded; call start_audio_recording first.")
            if query:
                app = self._running_match(query)
                if app is not None and app.pid != recording.pid:
                    raise ComputerUseError(f"The recording is of {recording.app}, not {query}.")
            del self._recordings[session]
        from . import windows_audio
        data, peak = windows_audio.finish(recording)
        return AudioClip(app=recording.app, path=save_recording(recording.app, data), data=data,
                         seconds=round(recording.seconds, 2), peak=peak)

    # ------------------------------------------------------------------ settling
    def settle(self, session: str, query: str) -> float:
        app = self.resolve_app(query, launch=False)
        previous = self._states.get((session, app.pid))
        start = time.monotonic()
        time.sleep(SETTLE_MIN)
        win = self._pick(app, prefer_id=previous.window_id if previous else 0)
        automation, _ = _uia()
        root = self._safe(lambda: automation.ElementFromHandle(win.hwnd)) if win else None
        last, busy = self._quick_signature(root)
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= (BUSY_MAX if busy else SETTLE_MAX):
                break
            check_cancelled()
            time.sleep(0.2)
            current, busy = self._quick_signature(root)
            if current == last and not busy:
                break
            last = current
        return time.monotonic() - start

    def _quick_signature(self, root) -> tuple[tuple, bool]:
        out, busy = [], False
        queue = [root] if root is not None else []
        while queue and len(out) < QUICK_NODES:
            el = queue.pop(0)
            role = self._role(el)
            if role in _BUSY_ROLES:
                ranged = self._pattern(el, RANGE_VALUE)
                current = self._safe(lambda: float(ranged.CurrentValue)) if ranged is not None else None
                maximum = self._safe(lambda: float(ranged.CurrentMaximum)) if ranged is not None else None
                busy = busy or current is None or (maximum is not None and current < maximum)
            out.append((role, self._name(el), self._value_text(el) or "",
                        self._safe(lambda el=el: bool(el.CurrentIsEnabled), True)))
            queue.extend(self._children(el))
        return tuple(out), busy


# ---------------------------------------------------------------------- SendInput and focus
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
        user32 = _user32()
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
        user32 = _user32()
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
        user32, kernel32 = _user32(), _STATE["kernel32"]
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
    _user32().SendInput(len(inputs), array, ctypes.sizeof(_INPUT))


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


# ---------------------------------------------------------------------- clipboard
def _clipboard_text() -> str | None:
    user32, kernel32 = _user32(), _STATE["kernel32"]
    if not user32.OpenClipboard(0):
        return None
    try:
        kernel32.GlobalLock.restype = ctypes.c_void_p
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer) if pointer else None
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _set_clipboard(text: str, html: str | None) -> None:
    user32, kernel32 = _user32(), _STATE["kernel32"]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    def put(fmt: int, data: bytes) -> None:
        handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        pointer = kernel32.GlobalLock(ctypes.c_void_p(handle))
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(ctypes.c_void_p(handle))
        user32.SetClipboardData(fmt, handle)

    for _ in range(10):
        if user32.OpenClipboard(0):
            break
        time.sleep(0.05)
    else:
        raise ComputerUseError("the clipboard is held by another app; try again")
    try:
        user32.EmptyClipboard()
        put(13, text.encode("utf-16-le") + b"\x00\x00")
        if html is not None:
            put(user32.RegisterClipboardFormatW("HTML Format"), cf_html(html) + b"\x00")
    finally:
        user32.CloseClipboard()


def _strip_tags(html: str) -> str:
    import re
    from html import unescape
    return unescape(re.sub(r"<[^>]+>", "", html))


def _menu_items(user32, menu) -> list[tuple[str, int, int, int]]:
    if not menu:
        return []
    out = []
    for position in range(max(0, user32.GetMenuItemCount(menu))):
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetMenuStringW(menu, position, buffer, 256, 0x400)  # MF_BYPOSITION
        sub = user32.GetSubMenu(menu, position)
        out.append((buffer.value.replace("&", ""), int(user32.GetMenuItemID(menu, position)) & 0xFFFFFFFF,
                    int(sub or 0), int(user32.GetMenuState(menu, position, 0x400))))
    return out


def _read_text(image: bytes | None) -> list[ocr.TextBox]:
    if image is None:
        return []
    try:
        return ocr.recognize_tesseract(image)
    except Exception:  # noqa: BLE001 - OCR only adds to a read
        return []


def _text_section(boxes: list[ocr.TextBox]) -> list[str]:
    if not boxes:
        return []
    return ["", "Text on the screenshot (OCR; this app exposes little to UI Automation, so act on it with x,y at a "
                "line's centre):", ocr.render(boxes)]


_BACKEND: WindowsComputer | None = None
_BACKEND_LOCK = threading.Lock()


def backend() -> WindowsComputer:
    global _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is None:
            _BACKEND = WindowsComputer()
        return _BACKEND


__all__ = ["OWN_APPS", "WindowsComputer", "backend", "cf_html", "key_chord", "parse_start_apps", "png_encode",
           "support"]
