"""Native reader trust boundaries with controlled accessibility trees."""

from openprogram.programs._authoring.reports import wechat as report_wechat


def node(handle=1, role="AXGroup", title="", subrole="", value="", description=""):
    return dict(
        handle=handle,
        role=role,
        title=title,
        subrole=subrole,
        value=value,
        description=description,
    )


class Bridge:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.actions = []
        self.closed = False

    def nodes(self):
        return next(self.pages)

    def search(self, handle, group):
        self.actions.append(("search", handle, group))

    def select(self, handle, group):
        self.actions.append(("select", handle, group))

    def close(self):
        self.closed = True


def test_login_blocks_before_any_input(monkeypatch):
    bridge = Bridge([[node(value="为了账号安全，请重新登录。")]])
    monkeypatch.setattr(report_wechat, "MacAccessibility", lambda: bridge)
    assert report_wechat.read_group("Group")["status"] == "LOGIN_REQUIRED"
    assert bridge.actions == [] and bridge.closed


def test_message_editor_is_not_search(monkeypatch):
    bridge = Bridge([[node(role="AXTextArea", description="message input")]])
    monkeypatch.setattr(report_wechat, "MacAccessibility", lambda: bridge)
    assert report_wechat.read_group("Group")["status"] == "SEARCH_UNAVAILABLE"
    assert not bridge.actions


def test_wrong_group_header_does_not_release_messages(monkeypatch):
    bridge = Bridge(
        [
            [node(subrole="AXSearchField")],
            [node(role="AXRow", title="Group")],
            [
                node(role="AXHeading", title="Other"),
                node(role="AXRow", description="message", value="private"),
            ],
        ]
    )
    monkeypatch.setattr(report_wechat, "MacAccessibility", lambda: bridge)
    result = report_wechat.read_group("Group")
    assert result["status"] == "GROUP_NOT_VERIFIED" and "private" not in str(result)
    assert bridge.actions == [("search", 1, "Group"), ("select", 1, "Group")]


def test_non_unique_search_result_stops(monkeypatch):
    bridge = Bridge(
        [
            [node(subrole="AXSearchField")],
            [node(role="AXRow", title="Group"), node(2, role="AXRow", title="Group")],
        ]
    )
    monkeypatch.setattr(report_wechat, "MacAccessibility", lambda: bridge)
    assert report_wechat.read_group("Group")["status"] == "GROUP_NOT_UNIQUE"
    assert len(bridge.actions) == 1


def test_message_rows_are_limited_to_verified_group_container(monkeypatch):
    rows = [
        node(),
        node(2),
        node(3, role="AXHeading", title="Group"),
        node(4, role="AXRow", description="message", value="own"),
        node(5, role="AXRow", description="message", value="other"),
    ]
    for i, n in enumerate(rows):
        n.update(index=i, parent=[None, 0, 1, 1, 0][i])
    bridge = Bridge([rows])
    monkeypatch.setattr(report_wechat, "MacAccessibility", lambda: bridge)
    result = report_wechat.read_group("Group")
    assert [b["text"] for b in result["blocks"]] == ["own"]
    assert result["complete"] is False


def test_application_root_is_not_a_verified_conversation(monkeypatch):
    rows = [
        node(),
        node(2, role="AXHeading", title="Group"),
        node(3, role="AXRow", description="message", value="other"),
    ]
    for i, n in enumerate(rows):
        n.update(index=i, parent=None if i == 0 else 0)
    bridge = Bridge([rows])
    monkeypatch.setattr(report_wechat, "MacAccessibility", lambda: bridge)
    result = report_wechat.read_group("Group")
    assert result["status"] == "GROUP_NOT_VERIFIED"


def native_processes(monkeypatch, pids, owners, ax_window="exact-main-window"):
    import sys
    from types import SimpleNamespace as NS

    apps = [
        NS(
            bundleIdentifier=lambda: "com.tencent.xinWeChat",
            processIdentifier=lambda p=p: p,
            launchDate=lambda: "launch",
            isTerminated=lambda: False,
        )
        for p in pids
    ]
    selected = []
    ax = NS(
        AXIsProcessTrusted=lambda: True,
        AXUIElementCreateApplication=lambda pid: selected.append(pid) or pid,
        AXUIElementSetMessagingTimeout=lambda *a: None,
        AXUIElementCopyAttributeValue=lambda *a: (0, None),
        AXUIElementCopyActionNames=lambda *a: (0, []),
        AXUIElementIsAttributeSettable=lambda *a: (0, False),
    )
    windows = [
        dict(
            kCGWindowOwnerPID=pid,
            kCGWindowNumber=pid,
            kCGWindowLayer=0,
            kCGWindowName="微信",
            kCGWindowBounds=dict(Width=924, Height=625),
        )
        for pid in owners
    ]
    monkeypatch.setitem(sys.modules, "ApplicationServices", ax)
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        NS(
            NSApplication=NS(sharedApplication=lambda: None),
            NSWorkspace=NS(sharedWorkspace=lambda: NS(runningApplications=lambda: [])),
            NSRunningApplication=NS(
                runningApplicationWithProcessIdentifier_=lambda pid: next(
                    (a for a in apps if a.processIdentifier() == pid), None
                )
            ),
        ),
    )
    from openprogram.programs.tools.computer_use.computer_use_agent.desktop import processes

    monkeypatch.setattr(processes, "all_pids", lambda: [1, *pids, 999])
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        NS(
            CGPreflightScreenCaptureAccess=lambda: True,
            kCGWindowListOptionAll=0,
            CGWindowListCopyWindowInfo=lambda *a: windows,
        ),
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "ScreenCaptureKit", NS())
    monkeypatch.setitem(
        sys.modules,
        "openprogram.programs._authoring.reports.mac_window",
        NS(WindowUnavailable=RuntimeError),
    )
    from openprogram.programs._authoring.reports.wechat_visual import WeChatWindow

    def native_window(window):
        selected.append(window.pid)
        return NS(
            ax=ax,
            ax_window=ax_window,
            capture_only=ax_window is None,
            validate=lambda: None,
            attr=lambda *a: None,
        )

    monkeypatch.setattr(WeChatWindow, "native_window", native_window)
    return selected


def test_public_reader_selects_only_main_window_owner(monkeypatch):
    selected = native_processes(monkeypatch, [11, 22], [22])
    assert report_wechat.read_group("Group")["status"] == "SEARCH_UNAVAILABLE"
    assert selected == [22]


def test_screenshot_only_window_reads_no_accessibility_and_hands_over_to_visual(monkeypatch):
    import sys

    selected = native_processes(monkeypatch, [22], [22], ax_window=None)
    ax = sys.modules["ApplicationServices"]

    def forbidden(*a):
        raise AssertionError("a screenshot-only window has no Accessibility tree to read")

    ax.AXUIElementCopyActionNames = forbidden
    ax.AXUIElementIsAttributeSettable = forbidden
    # SEARCH_UNAVAILABLE is the status that hands the group to the visual reader.
    assert report_wechat.read_group("Group")["status"] == "SEARCH_UNAVAILABLE"
    assert selected == [22]


def test_refused_background_action_is_not_treated_as_applied(monkeypatch):
    import sys
    import pytest
    from types import SimpleNamespace as NS

    monkeypatch.setitem(
        sys.modules,
        "openprogram.programs._authoring.reports.mac_window",
        NS(WindowUnavailable=RuntimeError),
    )
    bridge = object.__new__(report_wechat.MacAccessibility)
    bridge.check = lambda: None
    bridge.native = NS(
        elements={"0": ("search", [], True)},
        dispatch=lambda plan: {"success": False, "action_status": "refused"},
    )
    with pytest.raises(report_wechat.AccessibilityUnavailable, match="BACKGROUND_ACTION_UNAVAILABLE"):
        bridge._dispatch("window_set_text", "0", text="Group")
    assert bridge.native.elements == {"0": ("search", [], True)}


def test_public_reader_reports_ambiguous_processes_without_reading(monkeypatch):
    selected = native_processes(monkeypatch, [11, 22], [11, 22])
    assert report_wechat.read_group("Group")["status"] == "APP_NOT_UNIQUE"
    assert selected == []


def test_public_reader_reports_absent_process(monkeypatch):
    selected = native_processes(monkeypatch, [], [])
    assert report_wechat.read_group("Group")["status"] == "APP_NOT_RUNNING"
    assert selected == []


def test_screenshot_only_window_refuses_every_action_without_touching_it():
    from openprogram.programs._authoring.reports.mac_window import MacWindow

    window = object.__new__(MacWindow)
    window.capture_only = True
    window.identity = {"pid": 22, "window_id": 7}
    outcome = window.dispatch({"call": "window_press", "args": {"target": "x"}})
    assert outcome["success"] is False and outcome["action_status"] == "refused"
    assert outcome["error"].startswith("window_press refused")
