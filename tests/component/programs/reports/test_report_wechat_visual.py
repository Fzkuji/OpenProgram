"""Visual collection boundaries; these fixtures do not prove live collection."""

import pytest
from openprogram.programs.workflow import report_wechat_visual as visual


@pytest.fixture(autouse=True)
def native_error_boundary(monkeypatch):
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules,
        "gui_harness.adapters.mac_window",
        SimpleNamespace(
            WindowUnavailable=type("WindowUnavailable", (RuntimeError,), {})
        ),
    )


@pytest.fixture
def working_dir(monkeypatch, tmp_path):
    from openprogram.worktree.context import set_worktree, reset_worktree

    monkeypatch.chdir(tmp_path)
    token = set_worktree(None)
    try:
        yield tmp_path
    finally:
        reset_worktree(token)


def test_visual_collection_requires_verified_group_before_releasing_rows():
    frame = {
        "width": 1000,
        "height": 700,
        "lines": [
            {
                "label": "Other group",
                "x": 340,
                "y": 20,
                "w": 180,
                "h": 20,
                "confidence": 1,
            },
            {"label": "A", "x": 340, "y": 100, "w": 20, "h": 20, "confidence": 1},
            {
                "label": "Private content",
                "x": 360,
                "y": 140,
                "w": 180,
                "h": 20,
                "confidence": 1,
            },
        ],
    }
    assert visual.extract_messages(frame, "Target group", ["A"]) == []


def _row(label, x, y):
    return dict(label=label, x=x, y=y, w=100, h=20, confidence=1)


def _frame(rows):
    return dict(
        width=1000,
        height=700,
        captured_at="2026-09-12T22:00:00+08:00",
        evidence="/source/0.json",
        lines=[_row("Target group", 340, 20), *rows],
    )


def test_messages_require_page_local_date_and_complete_author_boundary():
    frame = _frame(
        [
            _row("A", 350, 90),
            _row("unverifiable", 360, 120),
            _row("星期四 15:00", 600, 160),
            _row("A", 350, 200),
            _row("尚未完成。下周验证。", 370, 230),
            _row("B", 350, 280),
            _row("clipped at bottom", 370, 310),
        ]
    )
    blocks = visual.extract_messages(frame, "Target group", ["A", "B"])
    assert len(blocks) == 1
    assert blocks[0]["author"] == "A" and blocks[0]["date"] == "2026-09-10"
    assert blocks[0]["text"] == "尚未完成。下周验证。"
    assert blocks[0]["evidence"]["date_row"]["label"] == "星期四 15:00"


def test_date_quoted_inside_body_cannot_relabel_messages():
    frame = _frame(
        [
            _row("星期四 15:00", 600, 90),
            _row("A", 350, 130),
            _row("2026年9月11日", 370, 160),
            _row("B", 350, 210),
        ]
    )
    (block,) = visual.extract_messages(frame, "Target group", ["A", "B"])
    assert block["date"] == "2026-09-10" and block["text"] == "2026年9月11日"


def test_sidebar_group_match_does_not_verify_header():
    frame = _frame([])
    frame["lines"] = [_row("Target group", 120, 120)]
    assert not visual.group_header(frame, "Target group")


def test_search_group_rejects_controls_without_accessing_native(monkeypatch):
    monkeypatch.setattr(
        visual,
        "WeChatWindow",
        lambda: (_ for _ in ()).throw(AssertionError("must not access GUI")),
    )
    assert (
        visual.read_visual_group("Group\nmessage", ["A"], "/out")["status"]
        == "SCOPE_REQUIRED"
    )


def test_public_visual_reader_only_saves_verified_pages(
    monkeypatch, tmp_path, working_dir
):
    image = tmp_path / "input.png"
    image.write_bytes(b"test fixture")
    frame = _frame(
        [
            _row("星期四 15:00", 600, 90),
            _row("A", 350, 130),
            _row("尚未完成。", 370, 180),
            _row("B", 350, 230),
        ]
    )
    frame["image"] = str(image)

    class Window:
        closed = False

        def observe(self):
            return dict(frame)

        def older(self, *args):
            pass

        def close(self):
            self.closed = True

    window = Window()
    monkeypatch.setattr(visual, "WeChatWindow", lambda: window)
    outcome = visual.read_visual_group("Target group", ["A", "B"], str(tmp_path))
    assert outcome["status"] == "READY" and outcome["complete"] is False
    assert len(outcome["blocks"]) == 1 and window.closed
    import json
    from pathlib import Path

    proof = json.loads(Path(outcome["blocks"][0]["evidence"]["capture"]).read_text())
    assert proof["lines"] == frame["lines"] and proof["png_base64"]


def test_unknown_group_never_persists_private_snapshot(
    monkeypatch, tmp_path, working_dir
):
    class Window:
        closed = False

        def observe(self):
            return _frame([])

        def search(self, *args):
            raise visual.VisualUnavailable("SEARCH_UNAVAILABLE")

        def close(self):
            self.closed = True

    window = Window()
    monkeypatch.setattr(visual, "WeChatWindow", lambda: window)
    assert (
        visual.read_visual_group("Another group", ["A"], str(tmp_path))["status"]
        == "SEARCH_UNAVAILABLE"
    )
    assert list(tmp_path.iterdir()) == [] and window.closed


def test_unlisted_sender_content_does_not_enter_prior_members_report():
    frame = _frame(
        [
            _row("星期四 15:00", 600, 90),
            _row("A", 350, 130),
            _row("A progress", 370, 170),
            _row("Unknown sender", 350, 210),
            _row("Private unrelated message", 370, 250),
            _row("B", 350, 300),
        ]
    )
    (block,) = visual.extract_messages(frame, "Target group", ["A", "B"])
    assert block["text"] == "A progress"


def test_low_confidence_sender_invalidates_attribution():
    unknown = _row("Unknown sender", 350, 210)
    unknown["confidence"] = 0.7
    frame = _frame(
        [
            _row("星期四 15:00", 600, 90),
            _row("A", 350, 130),
            _row("A progress", 370, 170),
            unknown,
            _row("Private unrelated message", 370, 250),
            _row("B", 350, 300),
        ]
    )
    assert visual.extract_messages(frame, "Target group", ["A", "B"]) == []


def test_search_without_native_search_control_never_dispatches():
    from types import SimpleNamespace as NS

    window = object.__new__(visual.WeChatWindow)
    window.check = lambda: None
    window.frame = _frame([_row("搜索", 100, 20)])
    window.native = NS(validate=lambda: None, elements={})
    with pytest.raises(visual.VisualUnavailable, match="BACKGROUND_SEARCH_UNAVAILABLE"):
        window.search(window.frame, "Target group")


def test_public_reader_preserves_roster_names_inside_message(
    monkeypatch, tmp_path, working_dir
):
    image = tmp_path / "source.png"
    image.write_bytes(b"test fixture")
    frame = _frame(
        [
            _row("星期四 15:00", 600, 90),
            _row("A", 350, 130),
            _row("本周协助以下同学：", 370, 170),
            _row("B", 370, 210),
            _row("尚未完成验证。", 390, 250),
            _row("C", 350, 300),
        ]
    )
    frame["image"] = str(image)

    class Window:
        def observe(self):
            return dict(frame)

        def older(self, *args):
            pass

        def close(self):
            pass

    monkeypatch.setattr(visual, "WeChatWindow", Window)
    result = visual.read_visual_group("Target group", ["A", "B", "C"], str(tmp_path))
    assert [(b["author"], b["text"]) for b in result["blocks"]] == [
        ("A", "本周协助以下同学：\nB\n尚未完成验证。")
    ]


@pytest.mark.parametrize("sharing_state", [0, 1])
@pytest.mark.parametrize("pids", [(22,), (11, 22)])
def test_multiple_process_visual_selection_does_not_reopen_bundle(
    monkeypatch, sharing_state, pids
):
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
    window = dict(
        kCGWindowOwnerPID=22,
        kCGWindowLayer=0,
        kCGWindowName="微信",
        kCGWindowNumber=123,
        kCGWindowSharingState=sharing_state,
        kCGWindowBounds=dict(Width=924, Height=625),
    )
    monkeypatch.setattr(visual.sys, "platform", "darwin")
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        NS(
            NSApplication=NS(sharedApplication=lambda: None),
            NSWorkspace=NS(
                sharedWorkspace=lambda: NS(runningApplications=lambda: apps)
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        NS(
            CGPreflightScreenCaptureAccess=lambda: True,
            kCGWindowListOptionAll=0,
            CGWindowListCopyWindowInfo=lambda *a: [window],
        ),
    )
    monkeypatch.setitem(
        sys.modules, "ApplicationServices", NS(AXIsProcessTrusted=lambda: True)
    )
    monkeypatch.setitem(sys.modules, "ScreenCaptureKit", NS())

    def no_open(*a, **kw):
        pytest.fail("multi-instance selection must not reopen a bundle")

    monkeypatch.setattr(visual.subprocess, "run", no_open)
    bridge = visual.WeChatWindow()
    try:
        assert bridge.pid == 22 and bridge.window_id == 123
        bridge.check()
    finally:
        bridge.scratch.cleanup()


def test_actions_use_window_controls_and_invalidate_observation(monkeypatch):
    import sys
    from types import SimpleNamespace as NS

    events = []

    class Unavailable(RuntimeError):
        pass

    monkeypatch.setitem(
        sys.modules,
        "gui_harness.adapters.mac_window",
        NS(WindowUnavailable=Unavailable),
    )
    window = object.__new__(visual.WeChatWindow)
    window.check = lambda: None
    window.bounds = dict(X=0, Y=0, Width=1000, Height=700)
    window.frame = _frame([_row("搜索", 100, 20)])
    window.native = NS(
        validate=lambda: None,
        elements={"search": ("element", [], True)},
        element_bounds={"search": dict(x=20, y=10, width=200, height=40)},
        attr=lambda e, a: "AXSearchField",
        dispatch=events.append,
    )
    frame = window.frame
    window.search(frame, "Target group")
    assert events == [
        {
            "call": "window_set_text",
            "args": {"target": "search", "text": "Target group"},
        }
    ]
    with pytest.raises(visual.VisualUnavailable, match="CONTROL_NOT_VERIFIED"):
        window.search(frame, "Another group")


def test_message_editor_and_other_window_controls_are_not_search_targets():
    from types import SimpleNamespace as NS

    window = object.__new__(visual.WeChatWindow)
    window.check = lambda: None
    window.bounds = dict(X=0, Y=0, Width=1000, Height=700)
    window.frame = _frame([])
    window.native = NS(
        validate=lambda: None,
        elements={"editor": ("editor", [], True)},
        element_bounds={"editor": dict(x=400, y=550, width=500, height=100)},
        attr=lambda *a: "AXSearchField",
    )
    with pytest.raises(visual.VisualUnavailable, match="BACKGROUND_SEARCH_UNAVAILABLE"):
        window.search(window.frame, "Target group")


def test_public_reader_uses_exact_window_session_and_releases_capture(
    monkeypatch, tmp_path, working_dir
):
    import sys
    import time
    import tempfile
    from contextlib import contextmanager
    from types import SimpleNamespace as NS

    calls = []
    capture_dir = tmp_path / "adapter-capture"
    capture_dir.mkdir()
    capture = capture_dir / "observation.png"
    capture.write_bytes(b"fixture")
    native = NS(
        identity={"pid": 22, "launch_time": "launch"},
        observe=lambda: {"img_path": str(capture)},
    )

    @contextmanager
    def session(app, window_id):
        calls.append(("enter", app, window_id))
        try:
            yield native
        finally:
            calls.append(("exit",))

    monkeypatch.setitem(
        sys.modules,
        "gui_harness.adapters.mac_window",
        NS(window_session=session, WindowUnavailable=RuntimeError),
    )
    window = object.__new__(visual.WeChatWindow)
    window.check = lambda: None
    window.pid, window.window_id, window.launch = 22, 123, "launch"
    window.session = window.native = window.frame = None
    window.deadline = time.monotonic() + 10
    window.scratch = tempfile.TemporaryDirectory(dir=tmp_path)
    scratch = window.scratch.name
    window._run = lambda *a: b"[]"
    monkeypatch.setattr(visual, "WeChatWindow", lambda: window)
    result = visual.read_visual_group("Target group", ["A"], str(tmp_path))
    assert result == {"status": "CAPTURE_CONTENT_UNAVAILABLE"}
    assert calls == [("enter", "com.tencent.xinWeChat", 123), ("exit",)]
    from pathlib import Path

    assert not capture_dir.exists() and not Path(scratch).exists()
