"""Background input delivery through macOS's private SkyLight framework.

Public CoreGraphics can post an event to one process, but AppKit ignores a
synthetic click that arrives in a window which is not key, and Chromium
drops synthetic input without the window-server stamps a real gesture
carries. WindowServer's own client library, SkyLight, exposes the route
that delivers such events anyway:

* ``SLEventPostToPid`` posts an event to one process the way the HID
  pipeline would, without moving the shared cursor or activating the app.
* ``SLEventSetIntegerValueField`` and ``CGEventSetWindowLocation`` stamp
  the window number, click state and target pid the receiving app checks.
* ``SLPSPostEventRecordTo`` with a focus record makes the target window
  the app's key window for routing without raising it or taking key
  status from the user's window.
* ``SLSEventAuthenticationMessage`` signs a keyboard event on macOS 15+,
  which Chromium-based apps require before they accept it.

The recipe follows the open-source cua-driver (trycua/cua, MIT) and yabai's
focus-without-raise. Every symbol is looked up at first use; when one is
missing the caller falls back to the public API and says so.
"""
from __future__ import annotations

import ctypes
from ctypes import POINTER, Structure, byref, c_char_p, c_double, c_int, c_int64, c_uint32, c_void_p
import platform
import struct
import threading
import time

_SKYLIGHT = "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight"
_COREGRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
_APPSERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
_LIBOBJC = "/usr/lib/libobjc.A.dylib"

# Event fields a real mouse gesture carries; names from the public header
# where one exists, offsets from cua-driver otherwise.
FIELD_CLICK_STATE = 1
FIELD_BUTTON_NUMBER = 3
FIELD_SUBTYPE = 7
FIELD_TARGET_PID = 40
FIELD_WINDOW_NUMBER = 51
FIELD_CLICK_GROUP = 58
FIELD_WINDOW_UNDER_POINTER = 91
FIELD_WINDOW_THAT_HANDLES = 92
SUBTYPE_TOUCH = 3        # what a click from the trackpad carries
SUBTYPE_DEFAULT = 0      # drags and moves

RECORD_SIZE = 0xF8
# Element ids tried when looking for windows on other Spaces; an app's ids
# start at zero and its windows are among the first few hundred.
REMOTE_ELEMENT_LIMIT = 4000


class ProcessSerialNumber(Structure):
    _fields_ = [("high", c_uint32), ("low", c_uint32)]


class SkyLight:
    """Lazily bound private symbols; ``available`` says whether the route exists."""

    def __init__(self) -> None:
        self.available = False
        self.keyboard_auth = False
        self._lock = threading.Lock()
        self._group = 0
        if platform.system() != "Darwin":
            return
        try:
            self.sky = ctypes.CDLL(_SKYLIGHT)
            self.cg = ctypes.CDLL(_COREGRAPHICS)
            self.hiv = ctypes.CDLL(_APPSERVICES)
            self.objc = ctypes.CDLL(_LIBOBJC)
        except OSError:
            return
        try:
            self.sky.SLEventPostToPid.argtypes = [c_int, c_void_p]
            self.sky.SLEventPostToPid.restype = None
            self.sky.SLEventSetIntegerValueField.argtypes = [c_void_p, c_uint32, c_int64]
            self.sky.SLEventSetIntegerValueField.restype = None
            self.cg.CGEventSetWindowLocation.argtypes = [c_void_p, c_double, c_double]
            self.cg.CGEventSetWindowLocation.restype = None
            self.sky.SLPSPostEventRecordTo.argtypes = [POINTER(ProcessSerialNumber), c_char_p]
            self.sky.SLPSPostEventRecordTo.restype = c_int
            self.hiv.GetProcessForPID.argtypes = [c_int, POINTER(ProcessSerialNumber)]
            self.hiv.GetProcessForPID.restype = c_int
        except AttributeError:
            return
        self.available = True
        try:
            self.sky.SLEventSetAuthenticationMessage.argtypes = [c_void_p, c_void_p]
            self.sky.SLEventSetAuthenticationMessage.restype = None
            self.objc.objc_getClass.restype = c_void_p
            self.objc.objc_getClass.argtypes = [c_char_p]
            self.objc.sel_registerName.restype = c_void_p
            self.objc.sel_registerName.argtypes = [c_char_p]
            self.objc.objc_getMetaClass.restype = c_void_p
            self.objc.objc_getMetaClass.argtypes = [c_char_p]
            self.objc.class_respondsToSelector.restype = ctypes.c_bool
            self.objc.class_respondsToSelector.argtypes = [c_void_p, c_void_p]
            self._auth_class = self.objc.objc_getClass(b"SLSEventAuthenticationMessage")
            metaclass = self.objc.objc_getMetaClass(b"SLSEventAuthenticationMessage")
            self._auth_selector = self.objc.sel_registerName(b"messageWithEventRecord:pid:version:")
            self._factory = ctypes.cast(
                self.objc.objc_msgSend,
                ctypes.CFUNCTYPE(c_void_p, c_void_p, c_void_p, c_void_p, c_int, c_uint32))
            # macOS 14 has the class without this class method; the envelope
            # is skipped there. A class method is looked up on the metaclass.
            self.keyboard_auth = bool(self._auth_class) and bool(metaclass) and bool(
                self.objc.class_respondsToSelector(metaclass, self._auth_selector))
        except AttributeError:
            self.keyboard_auth = False
        try:
            self.hiv._AXUIElementGetWindow.argtypes = [c_void_p, POINTER(c_uint32)]
            self.hiv._AXUIElementGetWindow.restype = c_int
            self._ax_get_window = self.hiv._AXUIElementGetWindow
        except AttributeError:
            self._ax_get_window = None
        try:
            self.hiv._AXUIElementCreateWithRemoteToken.argtypes = [c_void_p]
            self.hiv._AXUIElementCreateWithRemoteToken.restype = c_void_p
            self._ax_from_token = self.hiv._AXUIElementCreateWithRemoteToken
        except AttributeError:
            self._ax_from_token = None

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _pointer(event) -> int:
        import objc
        return objc.pyobjc_id(event)

    def next_group(self) -> int:
        """One id per gesture; every event of the gesture carries it."""
        with self._lock:
            self._group = (int(time.time() * 1e9) & 0x7FFFFFFF) or 1
            return self._group

    def window_id(self, ax_element) -> int | None:
        """The CGWindowID of an Accessibility window element."""
        if self._ax_get_window is None:
            return None
        import objc
        out = c_uint32(0)
        if self._ax_get_window(c_void_p(objc.pyobjc_id(ax_element)), byref(out)) != 0:
            return None
        return int(out.value) or None

    def remote_windows(self, pid: int, wanted: set[int], limit: int = REMOTE_ELEMENT_LIMIT) -> dict[int, object]:
        """Accessibility elements of the windows ``wanted`` (window ids) of ``pid``.

        ``AXWindows`` lists only the windows on the current Space. An element can
        also be made from a remote token (pid, 0, ``'coco'``, element id); the
        app's element ids are small integers, so trying them in order finds the
        windows on other Spaces, as AltTab does.
        """
        if self._ax_from_token is None or self._ax_get_window is None or not wanted:
            return {}
        import objc
        from CoreFoundation import CFDataCreate
        from ApplicationServices import AXUIElementCopyAttributeValue
        found: dict[int, object] = {}
        for element_id in range(limit):
            token = struct.pack("<iiIQ", int(pid), 0, 0x636F636F, element_id)
            data = CFDataCreate(None, token, len(token))
            pointer = self._ax_from_token(c_void_p(objc.pyobjc_id(data)))
            if not pointer:
                continue
            element = objc.objc_object(c_void_p=pointer)
            error, role = AXUIElementCopyAttributeValue(element, "AXRole", None)
            if error or role != "AXWindow":
                continue
            wid = self.window_id(element)
            if wid in wanted:
                found[wid] = element
                if len(found) == len(wanted):
                    break
        return found

    def _psn(self, pid: int) -> ProcessSerialNumber | None:
        psn = ProcessSerialNumber()
        if self.hiv.GetProcessForPID(int(pid), byref(psn)) != 0:
            return None
        return psn

    # ------------------------------------------------------------------ routing
    def focus_without_raise(self, pid: int, window_id: int) -> bool:
        """Make ``window_id`` the app's key window for event routing.

        Only the focus record goes out, to the target process. yabai also
        sends a defocus record to the front process; that makes the user's
        key window stop taking their typing, so it is left out.
        """
        psn = self._psn(pid)
        if psn is None:
            return False
        record = bytearray(RECORD_SIZE)
        record[0x04] = RECORD_SIZE
        record[0x08] = 0x0D
        record[0x3C:0x40] = int(window_id).to_bytes(4, "little")
        record[0x8A] = 0x01
        return self.sky.SLPSPostEventRecordTo(byref(psn), bytes(record)) == 0

    def stamp_mouse(self, event, *, pid: int, window_id: int, click_state: int, button: int,
                    subtype: int, group: int, location) -> None:
        """Give a mouse or wheel event the fields of a real gesture in that window.

        ``location`` is the point inside the window, from its top left in
        points; the event's own location stays the screen point.
        """
        p = self._pointer(event)
        self.cg.CGEventSetWindowLocation(p, float(location[0]), float(location[1]))
        for field, value in (
            (FIELD_CLICK_STATE, click_state), (FIELD_BUTTON_NUMBER, button), (FIELD_SUBTYPE, subtype),
            (FIELD_WINDOW_NUMBER, window_id), (FIELD_CLICK_GROUP, group),
            (FIELD_WINDOW_UNDER_POINTER, window_id), (FIELD_WINDOW_THAT_HANDLES, window_id),
            (FIELD_TARGET_PID, pid),
        ):
            self.sky.SLEventSetIntegerValueField(p, field, int(value))

    def post(self, pid: int, event, *, keyboard: bool = False) -> None:
        """Deliver ``event`` to ``pid`` through SkyLight, signed when it is a key event."""
        p = self._pointer(event)
        if keyboard and self.keyboard_auth:
            record = None
            # __CGEvent is {CFRuntimeBase, uint32, SLSEventRecord *}; the record
            # pointer sits at offset 24 on current releases.
            for offset in (24, 32, 16):
                candidate = c_void_p.from_address(p + offset).value
                if candidate:
                    record = candidate
                    break
            if record:
                message = self._factory(self._auth_class, self._auth_selector, record, int(pid), 0)
                if message:
                    self.sky.SLEventSetAuthenticationMessage(p, message)
        self.sky.SLEventPostToPid(int(pid), p)


_INSTANCE: SkyLight | None = None
_INSTANCE_LOCK = threading.Lock()


def skylight() -> SkyLight:
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = SkyLight()
        return _INSTANCE


__all__ = ["SUBTYPE_DEFAULT", "SUBTYPE_TOUCH", "SkyLight", "skylight"]
