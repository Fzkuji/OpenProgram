"""macOS Accessibility notifications and process exits, on one run-loop thread.

A worker has no main run loop, so nothing that AppKit delivers there (the
NSWorkspace launch, activate and terminate notifications among them) ever
arrives; measured: an observer on the workspace notification centre in the
worker gets nothing. What does arrive on a thread of its own:

* ``AXObserver`` callbacks, once the observer's run-loop source is added to
  that thread's CFRunLoop;
* a kqueue ``EVFILT_PROC``/``NOTE_EXIT`` per process, read through a
  CFFileDescriptor on the same loop. It also keeps the loop running while
  no observer is added.

:class:`AXEventLoop` runs that thread and keeps one observer per process.
:class:`ScreenEvents` uses it to hear about any window of any application
changing, for the control overlay (``window_watch``).
"""
from __future__ import annotations

import contextlib
import select
import threading
from typing import Any, Callable, Iterable

AX_SUCCESS = 0
AX_ALREADY_REGISTERED = -25209  # kAXErrorNotificationAlreadyRegistered

# What changes where windows are, registered on each application element:
# a notification registered on the application comes for any element in it.
SCREEN_NOTIFICATIONS = ("AXWindowCreated", "AXMoved", "AXResized", "AXUIElementDestroyed",
                        "AXWindowMiniaturized", "AXWindowDeminiaturized", "AXFocusedWindowChanged",
                        "AXApplicationActivated", "AXApplicationDeactivated", "AXApplicationHidden",
                        "AXApplicationShown")


class AXEventLoop:
    """One CFRunLoop thread with an Accessibility observer per process.

    ``on_notification(pid, element, name)`` and ``on_exit(pid)`` run on that
    thread and must be quick. Raises when the loop cannot start or this
    process has no Accessibility access.
    """

    def __init__(self, on_notification: Callable[[int, Any, str], None], on_exit: Callable[[int], None],
                 *, name: str) -> None:
        import ApplicationServices
        import CoreFoundation
        import objc

        if not ApplicationServices.AXIsProcessTrusted():
            raise RuntimeError("No Accessibility access.")
        self._ax = ApplicationServices
        self._cf = CoreFoundation
        self._on_notification = on_notification
        self._on_exit_pid = on_exit
        # PyObjC passes a Python callable as an observer callback only once
        # it is wrapped for that signature.
        self._callback = objc.callbackFor(ApplicationServices.AXObserverCreate)(
            lambda observer, element, notification, refcon: self._on_ax(element, notification))
        self._lock = threading.Lock()
        self._observers: dict[int, tuple[Any, Any]] = {}   # pid -> (observer, run-loop source)
        self._closed = False
        self._loop: Any = None
        self._kqueue = select.kqueue()
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name=name, daemon=True)
        self._thread.start()
        if not ready.wait(5) or self._loop is None:
            self.close()
            raise RuntimeError("The window event loop did not start.")

    def _run(self, ready: threading.Event) -> None:
        cf = self._cf
        try:
            self._loop = cf.CFRunLoopGetCurrent()
            descriptor = cf.CFFileDescriptorCreate(None, self._kqueue.fileno(), False, self._on_exit, None)
            cf.CFFileDescriptorEnableCallBacks(descriptor, cf.kCFFileDescriptorReadCallBack)
            source = cf.CFFileDescriptorCreateRunLoopSource(None, descriptor, 0)
            cf.CFRunLoopAddSource(self._loop, source, cf.kCFRunLoopDefaultMode)
        except Exception:
            self._loop = None
            ready.set()
            return
        ready.set()
        while not self._closed:
            cf.CFRunLoopRun()
        cf.CFFileDescriptorInvalidate(descriptor)

    def _on_ax(self, element, notification) -> None:
        with contextlib.suppress(Exception):
            self._on_notification(self.pid_of(element), element, str(notification))

    def _on_exit(self, descriptor, _flags, _info) -> None:
        try:
            events = self._kqueue.control(None, 16, 0)
        except OSError:
            events = []
        for event in events:
            with contextlib.suppress(Exception):
                self._on_exit_pid(int(event.ident))
        # A CFFileDescriptor's callbacks fire once per enable.
        self._cf.CFFileDescriptorEnableCallBacks(descriptor, self._cf.kCFFileDescriptorReadCallBack)

    def pid_of(self, element) -> int:
        try:
            error, pid = self._ax.AXUIElementGetPid(element, None)
            return int(pid) if error == AX_SUCCESS else 0
        except Exception:
            return 0

    def equal(self, a, b) -> bool:
        return bool(self._cf.CFEqual(a, b))

    # ------------------------------------------------------------------ observers
    def observe(self, pid: int, element: Any, names: Iterable[str]) -> bool:
        """Observe ``names`` on ``element`` of process ``pid``; whether any took."""
        if pid <= 0 or element is None or self._closed:
            return False
        ax, cf = self._ax, self._cf
        with self._lock:
            entry = self._observers.get(pid)
            created = entry is None
            if created:
                error, observer = ax.AXObserverCreate(pid, self._callback, None)
                if error != AX_SUCCESS or observer is None:
                    return False
                entry = (observer, ax.AXObserverGetRunLoopSource(observer))
            codes = [ax.AXObserverAddNotification(entry[0], element, name, None) for name in names]
            if not any(code in (AX_SUCCESS, AX_ALREADY_REGISTERED) for code in codes):
                return False
            if created:
                self._observers[pid] = entry
                cf.CFRunLoopAddSource(self._loop, entry[1], cf.kCFRunLoopDefaultMode)
                with contextlib.suppress(OSError):
                    self._kqueue.control([select.kevent(pid, filter=select.KQ_FILTER_PROC,
                                                        flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                                        fflags=select.KQ_NOTE_EXIT)], 0)
        cf.CFRunLoopWakeUp(self._loop)
        return True

    def unobserve(self, pid: int, element: Any, names: Iterable[str]) -> None:
        with self._lock:
            entry = self._observers.get(pid)
        if entry is None:
            return
        for name in names:
            with contextlib.suppress(Exception):
                self._ax.AXObserverRemoveNotification(entry[0], element, name)

    def drop(self, pid: int) -> None:
        """Stop observing process ``pid`` at all."""
        with self._lock:
            entry = self._observers.pop(pid, None)
        if entry is None:
            return
        with contextlib.suppress(Exception):
            self._cf.CFRunLoopRemoveSource(self._loop, entry[1], self._cf.kCFRunLoopDefaultMode)
        with contextlib.suppress(OSError):
            # Gone with the process already when it exited.
            self._kqueue.control([select.kevent(pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_DELETE)], 0)

    def observed(self) -> set[int]:
        with self._lock:
            return set(self._observers)

    def close(self) -> None:
        self._closed = True
        for pid in list(self.observed()):
            self.drop(pid)
        if self._loop is not None:
            self._cf.CFRunLoopStop(self._loop)
            self._cf.CFRunLoopWakeUp(self._loop)
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(2)
        with contextlib.suppress(OSError):
            self._kqueue.close()


def window_owners(rows: Iterable[dict], own_pid: int) -> set[int]:
    """The processes with an ordinary window on screen: the ones whose
    windows can cover another. The App's own process is left out."""
    out = set()
    for row in rows:
        pid = int(row.get("kCGWindowOwnerPID", 0) or 0)
        if pid > 0 and pid != own_pid and row.get("kCGWindowLayer", 0) == 0:
            out.add(pid)
    return out


class ScreenEvents:
    """Calls ``changed()`` when any window of an observed application is
    created, moved, resized, minimized, restored or closed, focus moves
    between windows or applications, or an observed process exits.

    The worker cannot hear about launches (see the module note), so the
    applications to observe come from the window list: :meth:`track` with
    the owners of the windows on screen, each time the list is read. A new
    application usually activates, which deactivates an observed one, and
    the next read adds it.
    """

    def __init__(self, changed: Callable[[], None]) -> None:
        import ApplicationServices

        self._ax = ApplicationServices
        self._changed = changed
        self._loop = AXEventLoop(lambda _pid, _element, _name: changed(), self._exited,
                                 name="window-overlay-events")
        self._refused: set[int] = set()

    def _exited(self, pid: int) -> None:
        self._loop.drop(pid)
        self._refused.discard(pid)
        self._changed()

    def track(self, pids: Iterable[int]) -> None:
        """Observe these processes too. One stays observed until it exits:
        its windows may be minimized or hidden now and come back later."""
        for pid in set(pids) - self._loop.observed() - self._refused:
            element = self._ax.AXUIElementCreateApplication(pid)
            if not self._loop.observe(pid, element, SCREEN_NOTIFICATIONS):
                # A process that answers no observer (a system agent) is not asked again.
                self._refused.add(pid)

    def close(self) -> None:
        self._loop.close()


__all__ = ["AXEventLoop", "SCREEN_NOTIFICATIONS", "ScreenEvents", "window_owners"]
