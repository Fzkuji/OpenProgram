"""Guide the user through a System Settings permission list, as Codex Computer Use does.

macOS grants Screen Recording, Accessibility and the like by a switch in a
list in System Settings; there is no dialog that grants them. Opening the
pane alone leaves the user hunting for the right application in that list.
OpenAI's Codex Computer Use helper solves this with a small accessory window
(``SystemSettingsAccessoryWindow`` holding a ``DraggableApplicationView`` and a
``DragHintView``) pinned to the bottom edge of the System Settings window: it
shows the application's icon, which the user drags straight into the list,
watches the permission, and closes itself once the switch is on. This module
copies that.

The worker side (:func:`start`, :func:`start_for_wait`) runs the capabilities
in order on one background thread: a capability macOS asks about with its own
dialog gets that dialog (``request_access``); a list capability gets its
pane opened and the accessory window. The window is a short-lived helper
process started with the executable the worker runs under, so the icon it
offers and the identity it checks are the ones macOS grants.
"""
from __future__ import annotations

import logging
import platform
import subprocess
import sys
import threading
import time
from typing import Iterable, Sequence

_log = logging.getLogger(__name__)

# Panes whose grant is a switch in a list of applications. The names are the
# ones System Settings shows; the guide's text names them as macOS does.
LIST_PANES = {
    'Privacy_ScreenCapture': ('Screen Recording', '屏幕录制'),
    'Privacy_Accessibility': ('Accessibility', '辅助功能'),
    'Privacy_ListenEvent': ('Input Monitoring', '输入监控'),
    'Privacy_AllFiles': ('Full Disk Access', '完全磁盘访问权限'),
    'Privacy_DevTools': ('Developer Tools', '开发者工具'),
}

SETTINGS_BUNDLE_IDS = ('com.apple.systempreferences',)
SETTINGS_OWNER_NAMES = ('System Settings', 'System Preferences')

PANEL_SIZE = (440.0, 84.0)
PANEL_GAP = 8.0       # between the Settings window's bottom edge and the panel
PANEL_INSET = 12.0    # inside the Settings window when there is no room below
TICK_SECONDS = 0.2    # how often the panel follows the Settings window
CHECK_SECONDS = 1.5   # how often the helper checks the grant afresh
SETTINGS_WAIT_SECONDS = 15.0   # how long the helper waits for Settings to appear
GUIDE_SECONDS = 600.0          # the whole guide closes after ten minutes

EXIT_GRANTED = 0
EXIT_ERROR = 2
EXIT_DISMISSED = 3
EXIT_SETTINGS_CLOSED = 4
EXIT_TIMEOUT = 5

_HELPER_SCRIPT = 'from openprogram.host.system_access.guide import main; main()'


# -- What gets a guide --------------------------------------------------------

def uses_guide(capability: str) -> bool:
    """True when macOS grants the capability by a switch in a System Settings list."""
    from openprogram.host.system_access import capability_spec
    spec = capability_spec(capability)
    return (spec.request_mode == 'native' and spec.settings_list
            and spec.settings_pane in LIST_PANES)


def guide_text(capability: str, *, application: str = 'OpenProgram',
               chinese: bool = False) -> tuple[str, str]:
    """The accessory window's two lines for one capability."""
    from openprogram.host.system_access import capability_spec
    spec = capability_spec(capability)
    english, zh = LIST_PANES.get(spec.settings_pane or '', (spec.settings_label, spec.label_zh))
    if chinese:
        return (f'把 {application} 拖到上方列表，允许{zh}',
                f'{application} 已在列表里？打开它旁边的开关。')
    return (f'Drag {application} to the list above to allow {english}',
            f'Already in the list? Turn on the switch next to {application}.')


# -- Where the window goes ----------------------------------------------------

def cg_to_cocoa(bounds: Sequence[float], primary_height: float) -> tuple[float, float, float, float]:
    """Convert a Quartz window rectangle (top-left origin) to Cocoa (bottom-left)."""
    x, y, width, height = (float(value) for value in bounds)
    return (x, primary_height - (y + height), width, height)


def panel_width(text_width: float) -> float:
    """Wide enough for the longer line beside the icon and arrow, within bounds."""
    return float(min(max(104.0 + float(text_width) + 40.0, PANEL_SIZE[0]), 640.0))


def panel_frame(settings: Sequence[float], visible: Sequence[float],
                size: Sequence[float] = PANEL_SIZE) -> tuple[float, float, float, float]:
    """Place the panel against the System Settings window, in Cocoa coordinates.

    Centred under the window, just below its bottom edge. When the screen has
    no room below, the panel sits inside the window over its bottom edge, so
    it still points up at the list. It never leaves the screen's visible frame.
    """
    sx, sy, sw, sh = (float(value) for value in settings)
    vx, vy, vw, vh = (float(value) for value in visible)
    width, height = (float(value) for value in size)
    x = sx + (sw - width) / 2.0
    y = sy - PANEL_GAP - height
    if y < vy:
        y = sy + PANEL_INSET
    x = min(max(x, vx), vx + vw - width)
    y = min(max(y, vy), vy + vh - height)
    return (x, y, width, height)


def settings_window_bounds(window_infos: Iterable[dict], pids: Iterable[int]) -> tuple[float, float, float, float] | None:
    """The front System Settings window from ``CGWindowListCopyWindowInfo`` rows.

    The rows come front to back; the first normal-level window of the
    Settings process that is big enough to be its main window wins.
    """
    pids = {int(pid) for pid in pids}
    for info in window_infos:
        try:
            owner_pid = int(info.get('kCGWindowOwnerPID', -1))
            owner_name = str(info.get('kCGWindowOwnerName') or '')
            if owner_pid not in pids and owner_name not in SETTINGS_OWNER_NAMES:
                continue
            if int(info.get('kCGWindowLayer', 0)) != 0:
                continue
            if float(info.get('kCGWindowAlpha', 1.0)) <= 0:
                continue
            bounds = info.get('kCGWindowBounds') or {}
            rect = (float(bounds['X']), float(bounds['Y']),
                    float(bounds['Width']), float(bounds['Height']))
        except (KeyError, TypeError, ValueError):
            continue
        if rect[2] >= 200 and rect[3] >= 200:
            return rect
    return None


# -- Worker side: one guide at a time ----------------------------------------

_STATE_LOCK = threading.Lock()
_current: dict | None = None
_GUIDED_WAITS: set[str] = set()


def _launch(capability: str) -> subprocess.Popen | None:
    """Start the accessory window for one capability as the runtime identity."""
    from openprogram.host.system_access import _native_executor
    command = [_native_executor(), '-I', '-B', '-c', _HELPER_SCRIPT, capability]
    try:
        return subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        _log.warning('system settings guide could not start', exc_info=True)
        return None


def _status(capability: str) -> str:
    from openprogram.host import system_access
    try:
        return str(system_access._mac_status(capability).get('status') or 'unknown')
    except Exception:
        _log.debug('system access status unavailable', exc_info=True)
        return 'unknown'


def _stop_current() -> None:
    """End the guide in progress; a newer request replaces it."""
    global _current
    with _STATE_LOCK:
        current, _current = _current, None
    if current is None:
        return
    current['cancel'].set()
    process = current.get('process')
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass


def _run(capabilities: list[str], cancel: threading.Event, state: dict) -> None:
    """Take each capability in order; stop when the user closes a guide."""
    from openprogram.host import system_access
    for capability in capabilities:
        if cancel.is_set():
            return
        if _status(capability) == 'granted':
            continue
        if not uses_guide(capability):
            try:
                system_access.request_access(capability)
            except (RuntimeError, ValueError):
                _log.debug('native request for %s did not run', capability, exc_info=True)
            continue
        system_access._open_settings(capability)
        process = _launch(capability)
        if process is None:
            return
        with _STATE_LOCK:
            state['process'] = process
        deadline = time.monotonic() + GUIDE_SECONDS + 30
        while process.poll() is None and not cancel.is_set():
            if time.monotonic() > deadline:
                process.terminate()
                return
            cancel.wait(0.5)
        if cancel.is_set():
            # Replaced while this window was starting or open.
            if process.poll() is None:
                process.terminate()
            return
        if process.returncode != EXIT_GRANTED:
            return


def _begin(capabilities: list[str]) -> list[str]:
    global _current
    if not capabilities:
        return []
    _stop_current()
    cancel = threading.Event()
    state: dict = {'cancel': cancel, 'capabilities': list(capabilities)}
    with _STATE_LOCK:
        _current = state

    def body():
        global _current
        try:
            _run(list(capabilities), cancel, state)
        except Exception:
            _log.warning('system settings guide failed', exc_info=True)
        finally:
            with _STATE_LOCK:
                if _current is state:
                    _current = None

    threading.Thread(target=body, name='system-access-guide', daemon=True).start()
    return list(capabilities)


def start(capabilities: Iterable[str]) -> list[str]:
    """Guide the missing capabilities in order; return those it will take.

    Unknown ids raise ``ValueError``. Off macOS nothing starts. Only native
    runtime capabilities are taken: a target application or a selected path
    is granted by the operation itself.
    """
    from openprogram.host import system_access
    requested = []
    for capability in capabilities:
        spec = system_access.capability_spec(capability)
        if spec.id not in requested:
            requested.append(spec.id)
    if platform.system() != 'Darwin':
        return []
    snapshot = system_access.report()
    status = {row.get('id'): row.get('status') for row in snapshot.get('capabilities', ())
              if isinstance(row, dict)}
    return start_after_check([
        capability for capability in requested
        if status.get(capability) in {'not_granted', 'unknown'}
    ])


def start_after_check(capabilities: Iterable[str]) -> list[str]:
    """Guide capabilities the caller has just found missing, in order."""
    from openprogram.host import system_access
    if platform.system() != 'Darwin':
        return []
    taken = []
    for capability in capabilities:
        if (system_access.capability_spec(capability).request_mode == 'native'
                and capability not in taken):
            taken.append(capability)
    return _begin(taken)


def start_for_wait(wait_id: str, required_capabilities: Iterable[str]) -> list[str]:
    """Start the guide once for a tool's system-access wait.

    The wait lists the capabilities found missing when it opened. The guide
    starts at the first list capability among them and goes on to the rest;
    dialog capabilities are left to the explicit setup the user chooses.
    """
    if platform.system() != 'Darwin' or not wait_id:
        return []
    with _STATE_LOCK:
        if wait_id in _GUIDED_WAITS:
            return []
        _GUIDED_WAITS.add(wait_id)
        if len(_GUIDED_WAITS) > 512:
            _GUIDED_WAITS.clear()
            _GUIDED_WAITS.add(wait_id)
    guided = []
    for capability in required_capabilities:
        try:
            if uses_guide(str(capability)) and capability not in guided:
                guided.append(str(capability))
        except ValueError:
            continue
    return _begin(guided)


# -- Helper side: the accessory window ----------------------------------------

def _chinese() -> bool:
    try:
        from Foundation import NSLocale
        languages = NSLocale.preferredLanguages() or []
        return bool(languages) and str(languages[0]).startswith('zh')
    except Exception:
        return False


def _build(capability: str):  # pragma: no cover - drawn by AppKit on a real desktop
    """Create the AppKit classes and the window; return (app, controller)."""
    import objc
    import AppKit
    import Quartz
    from Foundation import NSObject, NSTimer, NSMakeRect, NSURL

    from openprogram.host import system_access

    identity = system_access._native_identity()
    bundle_path = identity.get('bundle_path') or ''
    application = identity.get('application') or 'OpenProgram'
    title, subtitle = guide_text(capability, application=application, chinese=_chinese())
    icon = AppKit.NSWorkspace.sharedWorkspace().iconForFile_(bundle_path)

    source_protocol = objc.protocolNamed('NSDraggingSource')

    class DraggableApplicationView(AppKit.NSView, protocols=[source_protocol]):
        def isFlipped(self):
            return False

        def acceptsFirstMouse_(self, event):
            return True

        def mouseDownCanMoveWindow(self):
            return False

        def resetCursorRects(self):
            self.addCursorRect_cursor_(self.bounds(), AppKit.NSCursor.openHandCursor())

        def drawRect_(self, rect):
            if icon is not None:
                icon.drawInRect_(self.bounds())

        def mouseDown_(self, event):
            url = NSURL.fileURLWithPath_(bundle_path)
            item = AppKit.NSDraggingItem.alloc().initWithPasteboardWriter_(url)
            item.setDraggingFrame_contents_(self.bounds(), icon)
            self.beginDraggingSessionWithItems_event_source_([item], event, self)

        def draggingSession_sourceOperationMaskForDraggingContext_(self, session, context):
            return (AppKit.NSDragOperationCopy | AppKit.NSDragOperationLink
                    | AppKit.NSDragOperationGeneric)

    class DragHintView(AppKit.NSView):
        """An arrow pointing up from the icon to the list, gently pulsing."""

        def initWithFrame_(self, frame):
            self = objc.super(DragHintView, self).initWithFrame_(frame)
            if self is not None:
                self.phase = 0.0
            return self

        def drawRect_(self, rect):
            bounds = self.bounds()
            width, height = bounds.size.width, bounds.size.height
            alpha = 0.45 + 0.4 * abs(((self.phase % 2.0) - 1.0))
            AppKit.NSColor.secondaryLabelColor().colorWithAlphaComponent_(alpha).setStroke()
            path = AppKit.NSBezierPath.bezierPath()
            path.setLineWidth_(2.0)
            path.setLineCapStyle_(AppKit.NSLineCapStyleRound)
            path.setLineJoinStyle_(AppKit.NSLineJoinStyleRound)
            path.moveToPoint_((width / 2, 6))
            path.lineToPoint_((width / 2, height - 6))
            path.moveToPoint_((width / 2 - 6, height - 12))
            path.lineToPoint_((width / 2, height - 6))
            path.lineToPoint_((width / 2 + 6, height - 12))
            path.stroke()

        def advance(self):
            self.phase += TICK_SECONDS * 1.5
            self.setNeedsDisplay_(True)

    class SystemSettingsAccessoryWindow(AppKit.NSPanel):
        def canBecomeKeyWindow(self):
            return False

        def canBecomeMainWindow(self):
            return False

    class Controller(NSObject):
        def setup(self):
            self.code = None
            self.started = time.monotonic()
            self.seen_settings = False
            self.granted = threading.Event()
            self.stopped = threading.Event()
            heading = AppKit.NSTextField.labelWithString_(title)
            heading.setFont_(AppKit.NSFont.boldSystemFontOfSize_(13))
            heading.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            detail = AppKit.NSTextField.labelWithString_(subtitle)
            detail.setFont_(AppKit.NSFont.systemFontOfSize_(11))
            detail.setTextColor_(AppKit.NSColor.secondaryLabelColor())
            detail.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            text_width = max(heading.fittingSize().width, detail.fittingSize().width)
            width = panel_width(text_width)
            height = PANEL_SIZE[1]
            self.size = (width, height)
            heading.setFrame_(NSMakeRect(104, 42, width - 140, 20))
            detail.setFrame_(NSMakeRect(104, 22, width - 140, 18))
            panel = SystemSettingsAccessoryWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, width, height),
                AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
                AppKit.NSBackingStoreBuffered, False,
            )
            panel.setLevel_(AppKit.NSFloatingWindowLevel)
            panel.setHidesOnDeactivate_(False)
            panel.setFloatingPanel_(True)
            panel.setOpaque_(False)
            panel.setBackgroundColor_(AppKit.NSColor.clearColor())
            panel.setHasShadow_(True)
            panel.setCollectionBehavior_(
                AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            root = AppKit.NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
            root.setMaterial_(AppKit.NSVisualEffectMaterialPopover)
            root.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
            root.setState_(AppKit.NSVisualEffectStateActive)
            root.setWantsLayer_(True)
            root.layer().setCornerRadius_(12.0)
            root.layer().setMasksToBounds_(True)

            draggable = DraggableApplicationView.alloc().initWithFrame_(NSMakeRect(16, 14, 56, 56))
            draggable.setToolTip_(title)
            self.hint = DragHintView.alloc().initWithFrame_(NSMakeRect(78, 18, 18, 48))
            close = AppKit.NSButton.alloc().initWithFrame_(NSMakeRect(width - 28, height - 28, 18, 18))
            close.setBordered_(False)
            close.setImage_(AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                'xmark.circle.fill', 'Close'))
            close.setContentTintColor_(AppKit.NSColor.tertiaryLabelColor())
            close.setTarget_(self)
            close.setAction_('dismiss:')
            for view in (draggable, self.hint, heading, detail, close):
                root.addSubview_(view)
            panel.setContentView_(root)
            self.panel = panel
            threading.Thread(target=self.watch, daemon=True).start()
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                TICK_SECONDS, self, 'tick:', None, True)
            return self

        @objc.python_method
        def watch(self):
            while not self.stopped.is_set():
                probe = system_access._native_probe()
                row = (probe or {}).get('capabilities', {}).get(capability) or {}
                if row.get('status') == 'granted':
                    self.granted.set()
                    return
                self.stopped.wait(CHECK_SECONDS)

        def dismiss_(self, sender):
            self.finish(EXIT_DISMISSED)

        @objc.python_method
        def finish(self, code):
            if self.code is not None:
                return
            self.code = code
            self.stopped.set()
            self.panel.orderOut_(None)
            app = AppKit.NSApplication.sharedApplication()
            app.stop_(None)
            event = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                AppKit.NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0)
            app.postEvent_atStart_(event, True)

        def tick_(self, timer):
            if self.code is not None:
                return
            now = time.monotonic()
            if self.granted.is_set():
                return self.finish(EXIT_GRANTED)
            if now - self.started > GUIDE_SECONDS:
                return self.finish(EXIT_TIMEOUT)
            pids = [int(app.processIdentifier()) for bundle_id in SETTINGS_BUNDLE_IDS
                    for app in AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)]
            if not pids:
                if self.seen_settings or now - self.started > SETTINGS_WAIT_SECONDS:
                    return self.finish(EXIT_SETTINGS_CLOSED)
                return
            infos = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
                Quartz.kCGNullWindowID,
            ) or []
            bounds = settings_window_bounds(infos, pids)
            if bounds is None:
                self.panel.orderOut_(None)
                return
            self.seen_settings = True
            screens = AppKit.NSScreen.screens()
            primary_height = screens[0].frame().size.height
            settings = cg_to_cocoa(bounds, primary_height)
            centre = (settings[0] + settings[2] / 2, settings[1] + settings[3] / 2)
            screen = next((item for item in screens
                           if AppKit.NSPointInRect(centre, item.frame())), screens[0])
            visible = screen.visibleFrame()
            frame = panel_frame(settings, (visible.origin.x, visible.origin.y,
                                           visible.size.width, visible.size.height), self.size)
            self.panel.setFrame_display_(NSMakeRect(*frame), True)
            if not self.panel.isVisible():
                self.panel.orderFrontRegardless()
            self.hint.advance()

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    controller = Controller.alloc().init().setup()
    return app, controller


def main(argv: Sequence[str] | None = None) -> None:  # pragma: no cover - needs a desktop
    """Helper entry: show the accessory window for one capability until it settles."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or platform.system() != 'Darwin':
        raise SystemExit(EXIT_ERROR)
    try:
        if not uses_guide(args[0]):
            raise SystemExit(EXIT_ERROR)
    except ValueError:
        raise SystemExit(EXIT_ERROR)
    app, controller = _build(args[0])
    app.run()
    raise SystemExit(EXIT_ERROR if controller.code is None else controller.code)
