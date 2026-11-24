"""Another application's window in an App tab: the parts that hold on every platform."""
from __future__ import annotations

import asyncio
import json

import pytest

from openprogram.programs.tools.computer_use.computer_use.desktop import embedding
from openprogram.programs.tools.computer_use.computer_use.desktop.embedding import keycodes, linux, windows
from openprogram.programs.tools.computer_use.computer_use.desktop.embedding.macos import candidate_rows
from openprogram.programs.tools.computer_use.computer_use.desktop.keys import COMMAND, OPTION, SHIFT


def test_host_is_validated():
    host = embedding.parse_host({"handle": "4660", "pid": 7, "rect": [1, 2.5, 300, 200], "visible": False})
    assert host == {"handle": 4660, "pid": 7, "rect": [1.0, 2.5, 300.0, 200.0], "visible": False}
    assert embedding.parse_host({"rect": [0, 0, 1, 1]})["visible"] is True
    for bad in (None, {"rect": [1, 2, 3]}, {"rect": [1, 2, 3, "x"]}, {"rect": [0, 0, 1, 1], "handle": "abc"}):
        with pytest.raises(embedding.EmbedError):
            embedding.parse_host(bad)


def test_keys_from_the_page():
    assert keycodes.mac_keycode("KeyA") == 0
    assert keycodes.mac_keycode("Enter") == 36
    assert keycodes.mac_keycode("ArrowUp") == 126
    assert keycodes.mac_keycode("Unidentified") is None
    assert keycodes.modifier_flags(["shift", "alt", "meta", "bogus"]) == SHIFT | OPTION | COMMAND
    # Plain typing carries the character; a shortcut arrives as the bare key.
    assert keycodes.typed_text("a", ["shift"]) == "a"
    assert keycodes.typed_text("c", ["meta"]) == ""
    assert keycodes.typed_text("Enter", []) == ""


def test_only_ordinary_windows_are_offered():
    def row(number, pid, layer=0, width=800, height=600, alpha=1.0):
        return {"kCGWindowNumber": number, "kCGWindowOwnerPID": pid, "kCGWindowLayer": layer, "kCGWindowAlpha": alpha,
                "kCGWindowBounds": {"X": 0, "Y": 0, "Width": width, "Height": height}}
    rows = [row(1, 10), row(2, 10, layer=25), row(3, 11, width=40), row(4, 12, alpha=0), row(5, 99), row(6, 13)]
    assert [r["kCGWindowNumber"] for r in candidate_rows(rows, {99})] == [1, 6]


def test_windows_child_style_drops_the_frame():
    style = windows.WS_POPUP | windows.WS_CAPTION | windows.WS_THICKFRAME | windows.WS_SYSMENU | 0x10000000
    child = windows.child_style(style)
    assert child & windows.WS_CHILD
    assert not child & (windows.WS_POPUP | windows.WS_CAPTION | windows.WS_THICKFRAME | windows.WS_SYSMENU)
    assert child & 0x10000000, "other styles (WS_VISIBLE) stay"
    assert not windows.child_ex_style(windows.WS_EX_APPWINDOW | windows.WS_EX_CLIENTEDGE | 0x8) & (
        windows.WS_EX_APPWINDOW | windows.WS_EX_CLIENTEDGE)


def test_wayland_is_reported_not_attempted():
    assert "Wayland" in linux.unavailable_reason({"WAYLAND_DISPLAY": "wayland-0"})
    assert "X display" in linux.unavailable_reason({})


class _Socket:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


class _Embedder:
    mode = "native"
    place_input = False
    view_input = True

    def __init__(self):
        self.calls = []

    def list_windows(self, excluded):
        self.calls.append(("list", excluded))
        return [{"id": 5, "pid": 2, "app": "Notes", "title": "a", "icon": "", "bounds": [0, 0, 1, 1]}]

    def attach(self, window_id, host):
        self.calls.append(("attach", window_id, host["rect"]))
        if window_id == 9:
            raise embedding.EmbedError("That window is closed.")
        return {"mode": self.mode, "id": window_id, "open": True}

    def input(self, window_id, events):
        self.calls.append(("input", window_id, len(events)))

    def watch(self, window_id):
        self.calls.append(("watch", window_id))
        return {"mode": self.mode, "id": window_id, "open": True, "frame": [10, 20, 300, 200], "input": True}

    def layout(self, window_id, host):
        self.calls.append(("layout", window_id, host["rect"]))
        return {"mode": self.mode, "id": window_id, "open": True}

    def state(self, window_id):
        self.calls.append(("state", window_id))
        return {"mode": self.mode, "id": window_id, "open": True, "frame": [10, 20, 300, 200]}

    def detach(self, window_id):
        self.calls.append(("detach", window_id))


@pytest.fixture
def actions(monkeypatch):
    from openprogram.interfaces.server.ws_actions import embedded_window as module
    monkeypatch.setattr(module, "_holders", {})
    monkeypatch.setattr(module, "_placed", set())
    return module


def test_socket_actions(monkeypatch, actions):
    fake = _Embedder()
    monkeypatch.setattr(embedding, "embedder", lambda: fake)
    socket = _Socket()

    async def run():
        await actions.handle_list(socket, {"action": "embedded_windows", "request_id": "r1", "exclude_pids": [3, "x"]})
        await actions.handle_attach(socket, {"action": "embedded_window_attach", "request_id": "r2", "window_id": 5,
                                             "host": {"rect": [0, 0, 10, 10], "handle": "1"}})
        await actions.handle_attach(socket, {"action": "embedded_window_attach", "request_id": "r3", "window_id": 9,
                                             "host": {"rect": [0, 0, 10, 10]}})
        await actions.handle_attach(socket, {"action": "embedded_window_attach", "request_id": "r4", "window_id": "5",
                                             "host": {"rect": [0, 0, 10, 10]}})
        await actions.handle_input(socket, {"window_id": 5, "events": [{"type": "move"}, "junk"] + [{}] * 300})

    asyncio.run(run())
    replies = [message["data"] for message in socket.sent]
    assert [message["type"] for message in socket.sent] == ["embedded_window"] * 4, "input answers nothing"
    assert replies[0]["status"] == "ok" and replies[0]["windows"][0]["app"] == "Notes" and replies[0]["mode"] == "native"
    assert replies[1] == {"request_id": "r2", "action": "embedded_window_attach", "status": "ok",
                          "window": {"mode": "native", "id": 5, "open": True, "input": False}}
    assert replies[2]["status"] == "error" and replies[2]["error"] == "That window is closed."
    assert replies[3]["status"] == "error" and "window_id" in replies[3]["error"]
    assert ("list", {3}) in fake.calls
    assert ("input", 5, 200) in fake.calls, "events are capped and non-objects dropped"


HOST = {"rect": [0, 0, 10, 10], "handle": "1"}


def _calls(fake, *kinds):
    return [call[:2] for call in fake.calls if call[0] in kinds]


def test_a_view_watches_and_a_tab_places(actions):
    fake = _Embedder()
    view = actions.attach(fake, 5, "view:a", False, None, embedding.parse_host)
    assert view["input"] is True and view["frame"] == [10, 20, 300, 200]
    assert _calls(fake, "attach", "watch") == [("watch", 5)], "a view never moves the window"

    tab = actions.attach(fake, 5, "tab:b", True, HOST, embedding.parse_host)
    assert tab["input"] is False, "a placed child window takes input itself"
    # A second view of a placed window only reads it.
    again = actions.attach(fake, 5, "view:c", False, None, embedding.parse_host)
    assert again["input"] is True and ("state", 5) in fake.calls
    assert _calls(fake, "attach", "watch") == [("watch", 5), ("attach", 5)]

    # Layout comes from the placing holder only.
    actions.layout(fake, 5, "tab:b", HOST, embedding.parse_host)
    actions.layout(fake, 5, "view:a", {"bad": 1}, embedding.parse_host)
    assert [c for c in fake.calls if c[0] == "layout"] == [("layout", 5, [0.0, 0.0, 10.0, 10.0])]

    # The tab closes: the window goes back, and the views keep watching it.
    fake.calls.clear()
    actions.detach(fake, 5, "tab:b")
    assert _calls(fake, "detach", "watch") == [("detach", 5), ("watch", 5)]
    assert actions._holders[5] == {"view:a": False, "view:c": False} and 5 not in actions._placed

    fake.calls.clear()
    actions.detach(fake, 5, "view:a")
    assert fake.calls == []
    actions.detach(fake, 5, "view:c")
    assert fake.calls == [("detach", 5)] and 5 not in actions._holders


def test_the_last_placing_holder_gives_the_window_back(actions):
    fake = _Embedder()
    actions.attach(fake, 5, "tab:a", True, HOST, embedding.parse_host)
    actions.attach(fake, 5, "tab:b", True, HOST, embedding.parse_host)
    fake.calls.clear()
    actions.detach(fake, 5, "tab:a")
    assert fake.calls == [], "another tab still places it"
    actions.detach(fake, 5, "tab:b")
    assert fake.calls == [("detach", 5)] and 5 not in actions._holders


def test_holders_default_to_the_tab_and_a_failed_attach_holds_nothing(monkeypatch, actions):
    fake = _Embedder()
    socket = _Socket()

    async def run():
        await actions.handle_attach(socket, {"action": "embedded_window_attach", "request_id": "1", "window_id": 5,
                                             "host": HOST})
        await actions.handle_attach(socket, {"action": "embedded_window_attach", "request_id": "2", "window_id": 9,
                                             "host": HOST, "holder": "tab:x"})
        await actions.handle_attach(socket, {"action": "embedded_window_attach", "request_id": "3", "window_id": 5,
                                             "holder": "view:y", "place": "no"})
        await actions.handle_state(socket, {"action": "embedded_window_state", "request_id": "4", "window_id": 5,
                                            "holder": "tab"})
        await actions.handle_detach(socket, {"action": "embedded_window_detach", "request_id": "5", "window_id": 5})

    monkeypatch.setattr(embedding, "embedder", lambda: fake)
    asyncio.run(run())
    replies = [m["data"] for m in socket.sent]
    assert replies[0]["status"] == "ok" and actions._holders.get(5) is None, "detached by the default holder"
    assert replies[1]["status"] == "error" and 9 not in actions._holders
    assert replies[2]["status"] == "error" and "place" in replies[2]["error"]
    assert replies[3]["window"]["input"] is False
    assert ("detach", 5) in fake.calls


def test_a_closed_window_drops_its_holders(actions):
    fake = _Embedder()
    actions.attach(fake, 5, "view:a", False, None, embedding.parse_host)
    fake.state = lambda window_id: {"id": window_id, "open": False}
    assert actions.state(fake, 5, "view:a") == {"id": 5, "open": False}
    assert 5 not in actions._holders


def test_embedded_children_are_other_processes_in_app_windows():
    pids = {1: 100, 2: 200, 11: 300, 12: 100, 13: 101, 21: 400, 14: 0}
    own = {100, 101}
    children = {1: [11, 12, 13, 14], 2: [21]}
    found = embedding.embedded_children([1, 2], is_own=lambda _w, pid: pid in own, pid_of=pids.get,
                                        children_of=lambda w: children.get(w, []))
    # 12 is the App's own; 13 is another App process (the GPU process); 21 is in a foreign window.
    assert found == [(11, 300)]


def test_windows_backend_lists_embedded_windows(monkeypatch):
    from openprogram.programs.tools.computer_use.computer_use.desktop import windows as backend
    pids = {1: 100, 2: 200, 11: 300, 12: 100}
    monkeypatch.setattr(backend, "_pid", pids.get)
    monkeypatch.setattr(backend, "_exe", lambda pid: {100: r"C:\Apps\OpenProgram.exe"}.get(pid, r"C:\Windows\notepad.exe"))
    monkeypatch.setattr(backend, "_children", lambda hwnd: {1: [11, 12]}.get(hwnd, []))
    monkeypatch.setattr(backend, "_top_windows", lambda: [1, 2])
    monkeypatch.setattr(backend, "_window_pid", pids.get)
    assert backend._embedded_windows([1, 2]) == [11]
    assert backend.WindowsComputer()._apps_by_pid() == {100: [1], 200: [2], 300: [11]}


def test_linux_backend_lists_embedded_windows(monkeypatch):
    from openprogram.programs.tools.computer_use.computer_use.desktop import linux as backend
    pids = {"1": "100", "2": "200", "11": "300", "12": "100"}
    monkeypatch.setattr(backend, "_clients", lambda: [1, 2])
    monkeypatch.setattr(backend, "_x", lambda command, xid, check=True: pids.get(str(xid), ""))
    monkeypatch.setattr(backend, "_window_classes", lambda xid: ["openprogram", "OpenProgram"] if xid == "1" else ["gedit"])
    monkeypatch.setattr(backend, "_x_children", lambda xid: {1: [11, 12]}.get(xid, []))
    assert backend.LinuxComputer()._x_pids() == {100: [1], 200: [2], 300: [11]}


def test_windows_keys_from_the_page():
    assert keycodes.windows_key("KeyA") == (0x41, False)
    assert keycodes.windows_key("Digit0") == (0x30, False)
    assert keycodes.windows_key("ArrowLeft") == (0x25, True)
    assert keycodes.windows_key("NumpadEnter") == (0x0D, True)
    assert keycodes.windows_key("F12") == (0x7B, False)
    assert keycodes.windows_key("Unidentified") is None


def test_windows_message_parameters():
    assert windows.mouse_lparam(10, 20) == (20 << 16) | 10
    assert windows.mouse_lparam(-1, 5) == (5 << 16) | 0xFFFF, "coordinates are signed words"
    assert windows.key_lparam(0x1E, extended=False, up=False) == 1 | (0x1E << 16)
    assert windows.key_lparam(0x4B, extended=True, up=True) == 1 | (0x4B << 16) | (1 << 24) | (1 << 30) | (1 << 31)
    assert windows.wheel_wparam(-120, windows.MK_SHIFT) == (0xFF88 << 16) | windows.MK_SHIFT
    assert windows.wheel_delta(40) == 120 and windows.wheel_delta(-1e9) == -32768


def _messages(events):
    held: dict = {}
    located = []

    def locate(sx, sy):
        located.append((sx, sy))
        return 77, sx - 100, sy - 200  # the child under the point, its client origin at (100, 200)

    out = []
    for event in events:
        out += windows.event_messages(event, origin=(100, 200), locate=locate,
                                      client_of=lambda hwnd, sx, sy: (sx - 90, sy - 190),
                                      focus=lambda: 55, scan=lambda vk: 0x1E, held=held)
    return out, located, held


def test_windows_pointer_events_become_posted_messages():
    out, located, held = _messages([
        {"type": "move", "x": 5, "y": 6, "buttons": 0},
        {"type": "down", "x": 10, "y": 20, "button": 0, "clicks": 1, "mods": ["shift"]},
        {"type": "move", "x": 12, "y": 22, "buttons": 1},
        {"type": "up", "x": 12, "y": 22, "button": 0},
        {"type": "down", "x": 10, "y": 20, "button": 0, "clicks": 2},
        {"type": "up", "x": 10, "y": 20, "button": 0},
        {"type": "down", "x": 1, "y": 1, "button": 2, "clicks": 1},
        {"type": "wheel", "x": 3, "y": 4, "dx": 0, "dy": 40},
    ])
    w = windows
    assert out[0] == (77, w.WM_MOUSEMOVE, 0, w.mouse_lparam(5, 6))
    assert out[1] == (77, w.WM_MOUSEMOVE, w.MK_SHIFT, w.mouse_lparam(10, 20))
    assert out[2] == (77, w.WM_LBUTTONDOWN, w.MK_SHIFT | w.MK_LBUTTON, w.mouse_lparam(10, 20))
    # While pressed, the window that took the press gets the pointer, in its own client coordinates.
    assert out[3] == (77, w.WM_MOUSEMOVE, w.MK_LBUTTON, w.mouse_lparam(22, 32))
    assert out[4] == (77, w.WM_LBUTTONUP, 0, w.mouse_lparam(22, 32))
    assert out[6][1] == w.WM_LBUTTONDBLCLK
    assert out[9][1] == w.WM_RBUTTONDOWN and out[9][2] == w.MK_RBUTTON
    # The wheel takes screen coordinates; scrolling content down turns the wheel towards the user.
    assert out[10] == (77, w.WM_MOUSEWHEEL, w.wheel_wparam(-120), w.mouse_lparam(103, 204))
    assert located[0] == (105, 206), "x, y are from the window's top-left on screen"


def test_windows_keys_and_text_become_posted_messages():
    w = windows
    out, _, _ = _messages([
        {"type": "key", "code": "KeyA", "key": "a", "down": True, "mods": []},
        {"type": "key", "code": "KeyA", "key": "a", "down": False, "mods": []},
        {"type": "key", "code": "Enter", "key": "Enter", "down": True, "mods": []},
        {"type": "key", "code": "Enter", "key": "Enter", "down": False, "mods": []},
        {"type": "key", "code": "KeyC", "key": "c", "down": True, "mods": ["ctrl"]},
        {"type": "key", "code": "KeyF", "key": "f", "down": True, "mods": ["alt"]},
        {"type": "key", "code": "ArrowLeft", "key": "ArrowLeft", "down": True, "mods": []},
        {"type": "text", "text": "é\n😀"},
        {"type": "focus"},
    ])
    assert out[0] == (55, w.WM_CHAR, ord("a"), 1), "a character goes as the character"
    assert out[1] == (55, w.WM_KEYDOWN, 0x0D, w.key_lparam(0x1E, extended=False, up=False))
    assert out[2] == (55, w.WM_KEYUP, 0x0D, w.key_lparam(0x1E, extended=False, up=True))
    assert out[3] == (55, w.WM_CHAR, 0x03, 1), "Ctrl+C is its control character"
    assert out[4] == (55, w.WM_SYSKEYDOWN, 0x46, w.key_lparam(0x1E, extended=False, up=False, alt=True))
    assert out[5] == (55, w.WM_KEYDOWN, 0x25, w.key_lparam(0x1E, extended=True, up=False))
    assert [m[2] for m in out[6:]] == [0xE9, 0x0D, 0xD83D, 0xDE00], "text goes per UTF-16 unit, newline as CR"
