"""Native reader trust boundaries with controlled accessibility trees."""

from openprogram.programs.workflow import report_wechat


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


def native_processes(monkeypatch, pids, owners):
    import sys
    from types import SimpleNamespace as NS

    apps = [
        NS(
            bundleIdentifier=lambda: "com.tencent.xinWeChat",
            processIdentifier=lambda p=p: p,
        )
        for p in pids
    ]
    selected = []
    ax = NS(
        AXIsProcessTrusted=lambda: True,
        AXUIElementCreateApplication=lambda pid: selected.append(pid) or pid,
        AXUIElementSetMessagingTimeout=lambda *a: None,
        AXUIElementCopyAttributeValue=lambda *a: (0, None),
    )
    windows = [
        dict(
            kCGWindowOwnerPID=pid,
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
            NSWorkspace=NS(sharedWorkspace=lambda: NS(runningApplications=lambda: apps))
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        NS(kCGWindowListOptionAll=0, CGWindowListCopyWindowInfo=lambda *a: windows),
    )
    monkeypatch.setattr(report_wechat.sys, "platform", "darwin")
    return selected


def test_public_reader_selects_only_main_window_owner(monkeypatch):
    selected = native_processes(monkeypatch, [11, 22], [22])
    assert report_wechat.read_group("Group")["status"] == "SEARCH_UNAVAILABLE"
    assert selected == [22]


def test_public_reader_reports_ambiguous_processes_without_reading(monkeypatch):
    selected = native_processes(monkeypatch, [11, 22], [11, 22])
    assert report_wechat.read_group("Group")["status"] == "APP_NOT_UNIQUE"
    assert selected == []


def test_public_reader_reports_absent_process(monkeypatch):
    selected = native_processes(monkeypatch, [], [])
    assert report_wechat.read_group("Group")["status"] == "APP_NOT_RUNNING"
    assert selected == []
