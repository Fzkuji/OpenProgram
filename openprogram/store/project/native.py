"""Native directory identity and events.

macOS is the local native platform: bookmarks and FSEvents are used
after verification against temporary folders. Linux has live inotify
and inode identity but no cross-restart bookmark. Windows records
volume serial plus file index when available. Event gaps keep Locate.
"""
from __future__ import annotations

import base64
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Callable, Iterable

_log = logging.getLogger(__name__)

_CF = None
_CS = None
_kCFAllocatorDefault = None


def _macos() -> bool:
    return sys.platform == "darwin"


def _load_cf():
    global _CF, _kCFAllocatorDefault
    if _CF is not None:
        return _CF
    import ctypes
    cf = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease.restype = None
    _CF = cf
    _kCFAllocatorDefault = ctypes.c_void_p.in_dll(cf, "kCFAllocatorDefault")
    return cf


def create_bookmark(path: str | Path) -> str:
    if _macos():
        return _macos_create_bookmark(Path(path))
    return ""


def resolve_bookmark(blob: str, *, timeout: float = 2.0) -> str | None:
    if not blob:
        return None
    if _macos():
        return _macos_resolve_bookmark(blob)
    return None


def volume_id(path: str | Path) -> str:
    folder = Path(path)
    if sys.platform == "win32":
        return _windows_volume_id(folder)
    try:
        return str(folder.stat().st_dev)
    except OSError:
        return ""


def capabilities() -> dict[str, bool]:
    return {
        "bookmarks": _macos(),
        "directory_events": _macos() or sys.platform.startswith("linux"),
        "volume_events": _macos(),
        "cross_restart_move": _macos(),
    }


def _macos_create_bookmark(path: Path) -> str:
    import ctypes
    try:
        cf = _load_cf()
    except OSError:
        return ""
    if not path.is_dir():
        return ""
    raw = os.fsencode(str(path.resolve()))
    cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
    url = cf.CFURLCreateFromFileSystemRepresentation(
        None, raw, len(raw), True)
    if not url:
        return ""
    error = ctypes.c_void_p()
    cf.CFURLCreateBookmarkData.restype = ctypes.c_void_p
    cf.CFURLCreateBookmarkData.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    # kCFURLBookmarkCreationSuitableForBookmarkFile = 1 << 10
    data = cf.CFURLCreateBookmarkData(
        None, url, 1 << 10, None, None, ctypes.byref(error))
    cf.CFRelease(url)
    if error.value:
        cf.CFRelease(error)
    if not data:
        return ""
    cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
    cf.CFDataGetLength.restype = ctypes.c_long
    length = int(cf.CFDataGetLength(data))
    cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
    cf.CFDataGetBytePtr.restype = ctypes.POINTER(ctypes.c_ubyte)
    ptr = cf.CFDataGetBytePtr(data)
    blob = bytes(ptr[:length]) if ptr and length > 0 else b""
    cf.CFRelease(data)
    return base64.b64encode(blob).decode("ascii") if blob else ""


def _macos_resolve_bookmark(blob: str) -> str | None:
    import ctypes
    try:
        cf = _load_cf()
        raw = base64.b64decode(blob)
    except (OSError, ValueError):
        return None
    if not raw:
        return None
    cf.CFDataCreate.restype = ctypes.c_void_p
    cf.CFDataCreate.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long]
    data = cf.CFDataCreate(None, raw, len(raw))
    if not data:
        return None
    error = ctypes.c_void_p()
    stale = ctypes.c_bool(False)
    cf.CFURLCreateByResolvingBookmarkData.restype = ctypes.c_void_p
    cf.CFURLCreateByResolvingBookmarkData.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool),
        ctypes.POINTER(ctypes.c_void_p)]
    # kCFURLBookmarkResolutionWithoutUIMask = 1 << 8
    url = cf.CFURLCreateByResolvingBookmarkData(
        None, data, 1 << 8, None, None, ctypes.byref(stale), ctypes.byref(error))
    cf.CFRelease(data)
    if error.value:
        cf.CFRelease(error)
    if not url:
        return None
    buf = ctypes.create_string_buffer(4096)
    cf.CFURLGetFileSystemRepresentation.argtypes = [
        ctypes.c_void_p, ctypes.c_bool, ctypes.c_char_p, ctypes.c_long]
    cf.CFURLGetFileSystemRepresentation.restype = ctypes.c_bool
    ok = cf.CFURLGetFileSystemRepresentation(url, True, buf, 4096)
    cf.CFRelease(url)
    if not ok:
        return None
    text = buf.value.decode("utf-8", "replace")
    return text or None


def _windows_volume_id(path: Path) -> str:
    try:
        import ctypes
        from ctypes import wintypes
        GetFileInformationByHandle = ctypes.windll.kernel32.GetFileInformationByHandle
        CreateFileW = ctypes.windll.kernel32.CreateFileW
        CloseHandle = ctypes.windll.kernel32.CloseHandle

        class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        GENERIC_READ = 0x80000000
        FILE_SHARE = 0x7
        OPEN_EXISTING = 3
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        handle = CreateFileW(
            str(path), GENERIC_READ, FILE_SHARE, None, OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS, None)
        if handle in (0, -1, wintypes.HANDLE(-1).value):
            return ""
        info = BY_HANDLE_FILE_INFORMATION()
        ok = GetFileInformationByHandle(handle, ctypes.byref(info))
        CloseHandle(handle)
        if not ok:
            return ""
        index = (info.nFileIndexHigh << 32) | info.nFileIndexLow
        return f"{info.dwVolumeSerialNumber:08x}:{index:016x}"
    except Exception:
        return ""


class NativePathObserver:
    """Watch registered directories for rename/remove/mount events.

    Not a content-edit watcher. Start/stop release the native stream.
    Unavailable platforms stay idle; callers must not poll as fallback.
    """

    def __init__(self, on_paths: Callable[[list[str]], None]):
        self._on_paths = on_paths
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._paths: list[str] = []
        self._stream = None
        self._runloop = None

    def start(self, paths: Iterable[str | Path]) -> None:
        self._paths = [str(Path(p)) for p in paths if p]
        if not self._paths:
            return
        if _macos():
            self._thread = threading.Thread(
                target=self._macos_run, name="project-fs-events", daemon=True)
            self._thread.start()
            return
        if sys.platform.startswith("linux"):
            self._thread = threading.Thread(
                target=self._linux_run, name="project-inotify", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if _macos() and self._runloop is not None:
            try:
                import ctypes
                cf = _load_cf()
                cf.CFRunLoopStop.argtypes = [ctypes.c_void_p]
                cf.CFRunLoopStop(self._runloop)
            except Exception:
                _log.debug("failed to stop FSEvents run loop", exc_info=True)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None
        self._stream = None
        self._runloop = None

    def _emit(self, changed: list[str]) -> None:
        if self._stop.is_set() or not changed:
            return
        try:
            self._on_paths(changed)
        except Exception:
            _log.exception("project location event handler failed")

    def _macos_run(self) -> None:
        import ctypes
        try:
            cf = _load_cf()
            cs = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/CoreServices.framework/CoreServices")
        except OSError:
            return
        # FSEventStreamCallback = void (*)(stream, info, num, paths, flags, ids)
        callback_t = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint64))

        def _cb(stream, info, count, paths, flags, ids):
            if self._stop.is_set():
                return
            changed = []
            for i in range(count):
                raw = paths[i]
                if not raw:
                    continue
                text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                flag = int(flags[i]) if flags else 0
                # FSEventStreamEventFlag values: RootChanged=0x20,
                # Mount=0x40, Unmount=0x80, Created=0x100, Removed=0x200,
                # Renamed=0x800, ItemIsDir=0x20000. Ignore ordinary file
                # saves: location reconciliation is directory-only.
                structural = 0x40 | 0x80 | 0x100 | 0x200 | 0x800
                if flag & 0x20 or ((flag & structural) and flag & 0x20000):
                    changed.append(text)
            if changed:
                self._emit(changed)

        c_cb = callback_t(_cb)
        self._callback_ref = c_cb  # keep alive
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFArrayCreateMutable.restype = ctypes.c_void_p
        cf.CFArrayAppendValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        array = cf.CFArrayCreateMutable(None, 0, None)
        if not array:
            return
        strings = []
        for path in self._paths:
            item = cf.CFStringCreateWithCString(None, path.encode("utf-8"), 0x08000100)
            if item:
                strings.append(item)
                cf.CFArrayAppendValue(array, item)
        cs.FSEventStreamCreate.restype = ctypes.c_void_p
        # Declare the complete signature.  Without argtypes ctypes narrows
        # pointer arguments to C ints on arm64, which corrupts the stream and
        # can crash the process as soon as the first event is delivered.
        cs.FSEventStreamCreate.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_uint64, ctypes.c_double, ctypes.c_uint32,
        ]
        # IgnoreSelf=8 and FileEvents=0x10.  File-level events are required
        # to observe a directory rename reliably; the callback still filters
        # ordinary content edits below.
        latency = ctypes.c_double(0.25)
        # Keep UseCFTypes disabled: the callback contract above consumes the
        # path array as char **.  Enabling it changes the array to CFStringRef
        # and makes the callback dereference invalid memory.
        # kFSEventStreamEventIdSinceNow prevents replaying an unrelated
        # historical event stream into a temporary test or newly registered
        # project observer.
        stream = cs.FSEventStreamCreate(
            None, c_cb, None, array, ctypes.c_uint64(0xFFFFFFFFFFFFFFFF),
            # WatchRoot (0x4) emits a root-change event when the registered
            # directory itself is renamed or removed.
            latency, ctypes.c_uint32(0x4 | 0x8 | 0x10))
        for item in strings:
            cf.CFRelease(item)
        cf.CFRelease(array)
        if not stream:
            return
        self._stream = stream
        cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        runloop = cf.CFRunLoopGetCurrent()
        self._runloop = runloop
        cs.FSEventStreamScheduleWithRunLoop.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        cf.kCFRunLoopDefaultMode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")
        cs.FSEventStreamScheduleWithRunLoop(stream, runloop, cf.kCFRunLoopDefaultMode)
        cs.FSEventStreamStart.argtypes = [ctypes.c_void_p]
        cs.FSEventStreamStart.restype = ctypes.c_bool
        if not cs.FSEventStreamStart(stream):
            cs.FSEventStreamInvalidate.argtypes = [ctypes.c_void_p]
            cs.FSEventStreamInvalidate(stream)
            cs.FSEventStreamRelease.argtypes = [ctypes.c_void_p]
            cs.FSEventStreamRelease(stream)
            self._stream = None
            return
        cf.CFRunLoopRun.restype = None
        while not self._stop.is_set():
            cf.CFRunLoopRun()
            break
        cs.FSEventStreamStop.argtypes = [ctypes.c_void_p]
        cs.FSEventStreamStop(stream)
        cs.FSEventStreamInvalidate.argtypes = [ctypes.c_void_p]
        cs.FSEventStreamInvalidate(stream)
        cs.FSEventStreamRelease.argtypes = [ctypes.c_void_p]
        cs.FSEventStreamRelease(stream)
        self._stream = None

    def _linux_run(self) -> None:
        import ctypes
        import struct
        try:
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
        except OSError:
            return
        IN_MOVED_FROM = 0x00000040
        IN_MOVED_TO = 0x00000080
        IN_MOVE_SELF = 0x00000800
        IN_DELETE_SELF = 0x00000400
        IN_UNMOUNT = 0x00002000
        mask = IN_MOVED_FROM | IN_MOVED_TO | IN_MOVE_SELF | IN_DELETE_SELF | IN_UNMOUNT
        fd = libc.inotify_init1(0o4000)  # IN_NONBLOCK
        if fd < 0:
            return
        watches = {}
        for path in self._paths:
            wd = libc.inotify_add_watch(fd, path.encode("utf-8"), mask)
            if wd >= 0:
                watches[wd] = path
        try:
            while not self._stop.is_set():
                try:
                    data = os.read(fd, 4096)
                except BlockingIOError:
                    self._stop.wait(0.25)
                    continue
                except OSError:
                    break
                offset = 0
                changed = []
                while offset + 16 <= len(data):
                    wd, _mask, _cookie, name_len = struct.unpack_from("iIII", data, offset)
                    offset += 16 + name_len
                    base = watches.get(wd)
                    if base:
                        changed.append(base)
                self._emit(changed)
        finally:
            os.close(fd)
