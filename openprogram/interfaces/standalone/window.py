"""The exported application's own macOS window.

A standalone application on macOS draws its page in a native window
(AppKit and the system's WebKit, through the PyObjC the runtime already
carries), in the launcher's own process: the Dock shows the application's
name and icon, a second double-click brings the open window forward, and
no browser is involved. Closing the window quits the launcher; the server
behind it stops by itself once idle (``server.py``).

``available()`` is false where AppKit or WebKit cannot be loaded (another
system, or an export that names a Python without PyObjC); the launcher
then opens the page in a browser instead.
"""
from __future__ import annotations

import sys

#: WKPermissionDecisionGrant
_GRANT = 1


def available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        _webkit()
    except Exception:  # noqa: BLE001 - any failure to load means no native window
        return False
    return True


def _webkit():
    import objc
    objc.loadBundle("WebKit", {}, bundle_path="/System/Library/Frameworks/WebKit.framework")
    # The WebKit bindings are not installed: describe the two block
    # arguments the delegate receives so PyObjC can call them.
    objc.registerMetaDataForSelector(
        b"NSObject", b"webView:requestMediaCapturePermissionForOrigin:initiatedByFrame:type:decisionHandler:",
        {"arguments": {6: {"callable": {"retval": {"type": b"v"},
                                        "arguments": {0: {"type": b"^v"}, 1: {"type": b"q"}}}}}})
    return objc.lookUpClass("WKWebView"), objc.lookUpClass("WKWebViewConfiguration")


def _menu(title: str):
    """The application and Edit menus: Quit, and the editing keys a text field needs."""
    from AppKit import NSMenu, NSMenuItem
    bar = NSMenu.alloc().init()
    entries = [
        ("", [(f"Hide {title}", "hide:", "h"), (None, None, None), (f"Quit {title}", "terminate:", "q")]),
        ("Edit", [("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), (None, None, None),
                  ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"),
                  ("Select All", "selectAll:", "a")]),
        ("Window", [("Minimize", "performMiniaturize:", "m"), ("Close", "performClose:", "w")]),
    ]
    for name, items in entries:
        holder = NSMenuItem.alloc().init()
        menu = NSMenu.alloc().initWithTitle_(name or title)
        for label, action, key in items:
            if label is None:
                menu.addItem_(NSMenuItem.separatorItem())
            else:
                menu.addItemWithTitle_action_keyEquivalent_(label, action, key)
        holder.setSubmenu_(menu)
        bar.addItem_(holder)
    return bar


def run(url: str, title: str, *, frame: tuple[float, float, float, float] | None = None,
        activate: bool = True, quit_after: float | None = None) -> None:
    """Show ``url`` in a window titled ``title`` until it is closed.

    ``frame`` places the window (x, y, width, height) instead of the saved
    or default place; ``activate`` brings it forward; ``quit_after`` closes
    it after that many seconds. The last three serve checks that must not
    take the screen.
    """
    import objc
    from AppKit import (NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered,
                        NSMakeRect, NSObject, NSWindow, NSWindowStyleMaskClosable,
                        NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
                        NSWindowStyleMaskTitled, NSWorkspace)
    from Foundation import NSURL, NSURLRequest, NSTimer

    WKWebView, WKWebViewConfiguration = _webkit()

    class Delegate(NSObject):
        window = objc.ivar()

        def applicationShouldTerminateAfterLastWindowClosed_(self, app):
            return True

        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
            if not visible and self.window is not None:
                self.window.makeKeyAndOrderFront_(None)
            return True

        # The page asks for the microphone: the application declares it and
        # macOS itself asks the person once (NSMicrophoneUsageDescription).
        def webView_requestMediaCapturePermissionForOrigin_initiatedByFrame_type_decisionHandler_(
                self, view, origin, frame, kind, handler):
            handler(_GRANT)

        # A link that opens a new window goes to the default browser.
        def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
                self, view, configuration, action, features):
            target = action.request().URL()
            if target is not None:
                NSWorkspace.sharedWorkspace().openURL_(target)
            return None

        def closeLater_(self, timer):
            NSApplication.sharedApplication().terminate_(None)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = Delegate.alloc().init()
    app.setDelegate_(delegate)
    app.setMainMenu_(_menu(title))

    style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
             | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
    x, y, width, height = frame or (0, 0, 1100, 760)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(x, y, width, height), style, NSBackingStoreBuffered, False)
    window.setTitle_(title)
    window.setReleasedWhenClosed_(False)
    window.setMinSize_((560, 420))
    if frame is None:
        window.center()
        window.setFrameAutosaveName_("main")

    configuration = WKWebViewConfiguration.alloc().init()
    view = WKWebView.alloc().initWithFrame_configuration_(window.contentView().bounds(), configuration)
    view.setAutoresizingMask_(18)  # width and height follow the window
    view.setUIDelegate_(delegate)
    window.setContentView_(view)
    view.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))

    delegate.window = window
    window.makeKeyAndOrderFront_(None)
    if activate:
        app.activateIgnoringOtherApps_(True)
    if quit_after is not None:
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            quit_after, delegate, "closeLater:", None, False)
    app.run()


__all__ = ["available", "run"]
