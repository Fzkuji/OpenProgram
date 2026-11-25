"""Win32 and UI Automation through ctypes: constants, the loaded libraries, top-level windows and their processes, and the records the backend keeps."""
from __future__ import annotations

from dataclasses import dataclass, field
import ctypes
from pathlib import PureWindowsPath
import platform
import threading
from typing import Any

from ..common import ocr
from ..common.tree import Node, Snapshot


# A browser window's page: what a browser target claims (see base.claimed).
_CLAIMED_ROLES = frozenset({"document"})

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


_LOCK = threading.Lock()
_STATE: dict[str, Any] = {}


# Win32 signatures, so 64-bit handles pass and return intact:
# (dll, function, restype, argtypes). "H" is any handle, "P" any pointer.
_SIGNATURES = [
    ("user32", "GetWindowTextLengthW", "i", "H"), ("user32", "GetWindowTextW", "i", "HPi"),
    ("user32", "GetClassNameW", "i", "HPi"), ("user32", "PostMessageW", "b", "HUWL"),
    ("user32", "SendMessageW", "L", "HUWL"), ("user32", "GetWindowThreadProcessId", "u", "HP"),
    ("user32", "GetForegroundWindow", "H", ""), ("user32", "SetForegroundWindow", "b", "H"),
    ("user32", "BringWindowToTop", "b", "H"), ("user32", "GetWindow", "H", "Hu"), ("user32", "GetParent", "H", "H"),
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


def _children(hwnd: int) -> list[int]:
    """The direct child windows of ``hwnd`` (EnumChildWindows walks every descendant)."""
    from ctypes import wintypes
    user32 = _user32()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(child, _):
        if int(user32.GetParent(child) or 0) == hwnd and user32.IsWindowVisible(child) and _text(child):
            found.append(int(child))
        return True

    user32.EnumChildWindows(hwnd, visit, 0)
    return found


def _own_process(pid: int, images: dict[int, str]) -> bool:
    if pid not in images:
        images[pid] = PureWindowsPath(_exe(pid)).name.lower()
    return images[pid].startswith("openprogram")


def _embedded_windows(tops: list[int]) -> list[int]:
    """Other applications' windows embedded in an OpenProgram App window.

    An embedded window is a child of the App's window, so the top-level walk
    misses it. Found from the windows themselves, not the worker's records,
    so a backend in an agentic subprocess sees them too.
    """
    from ..embedding import embedded_children
    images: dict[int, str] = {}
    found = embedded_children(tops, is_own=lambda _hwnd, pid: _own_process(pid, images), pid_of=_pid,
                              children_of=_children)
    return [hwnd for hwnd, _ in found]


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
    # Roles whose subtrees were left out because another target operates them (see base.claimed).
    left_out: frozenset = frozenset()
