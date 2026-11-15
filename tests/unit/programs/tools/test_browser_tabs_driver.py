"""The built-in browser's tab driver: which tab an action goes to, and how a ref is found again."""
from __future__ import annotations

import pytest

from openprogram.programs.applications.browser import tabs


def _surface(tab_id, **extra):
    return {"window_id": "main", "tab_id": tab_id, "target_id": f"T{tab_id}", "binding_id": f"b{tab_id}",
            "title": tab_id.upper(), "origin": "https://x.test", **extra}


@pytest.fixture
def driver(monkeypatch):
    from openprogram.runtime.conversation import surface_context

    value = tabs.Driver()
    listed = {"surfaces": [_surface("a"), _surface("b", visible=True, focused=True)]}
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(surface_context, "capture_pages_for_listing", lambda _context=None, wait=True: listed)
    monkeypatch.setattr(value, "_app_may_come_back", lambda: True)
    value.listed = listed
    yield value
    value._owner.shutdown()


A = tabs._tab_name("main", "a", set())
B = tabs._tab_name("main", "b", set())


def test_an_action_goes_to_the_named_tab_then_the_last_used_then_the_focused(driver):
    assert driver._tab("", "s1").title == "B"
    first = driver._tab(A, "s1")
    assert first.title == "A" and driver._tab(None, "s1") is first
    # Ids stay with their tabs when the App lists them again, in any order.
    driver.listed["surfaces"].reverse()
    driver._refresh()
    assert driver._tab(A, "s2").title == "A"
    with pytest.raises(tabs.BrowserError) as missing:
        driver._tab("t9", "s1")
    assert missing.value.reason_code == "tab_not_found"
    assert [row["tab"] for row in missing.value.extra["tabs"]] == [B, A]


def _turn(**fields):
    from openprogram.runtime.agent.turn_request import TurnRequest, set_turn_request

    return set_turn_request(TurnRequest(session_id="s1", user_text="", agent_id="main", **fields))


@pytest.mark.parametrize("fields", [
    {"source": "scheduler", "interaction": "non-interactive"},
    {"source": "web", "background": True, "interaction": "non-interactive"},
    {"source": "web", "goal_trigger": True, "interaction": "interactive"},
    {"source": "web", "automation": "handoff", "interaction": "interactive"},
    {"source": "telegram", "peer_kind": "direct", "interaction": "interactive"},
    {"source": "agent_spawn", "detached": True, "interaction": "non-interactive"},
])
def test_a_turn_with_no_window_keeps_off_the_tab_in_front_and_keeps_its_own(driver, fields):
    from openprogram.runtime.agent.turn_request import reset_turn_request

    token = _turn(**fields)
    try:
        with pytest.raises(tabs.BrowserError) as missing:
            driver._tab("", "s1")
        assert missing.value.reason_code == "tab_not_found"
        assert "open(url)" in str(missing.value)
        # Naming a tab still works, and that tab is this conversation's from then on.
        named = driver._tab(A, "s1")
        assert named.title == "A" and driver._tab(None, "s1") is named
    finally:
        reset_turn_request(token)


@pytest.mark.parametrize("fields", [
    {"source": "web", "interaction": "interactive"},
    {"source": "agent_spawn", "interaction": "non-interactive"},
    {"source": "hook_continue", "interaction": "non-interactive"},
])
def test_a_person_s_turn_still_starts_on_the_tab_in_front(driver, fields):
    from openprogram.runtime.agent.turn_request import reset_turn_request

    token = _turn(**fields)
    try:
        assert driver._tab("", "s1").title == "B"
    finally:
        reset_turn_request(token)


def test_every_process_names_a_tab_alike_and_a_moved_tab_keeps_its_name(driver):
    assert A == tabs._tab_name("main", "a", set()) != B and A.startswith("t") and len(A) == 5
    # A name another tab already has grows by a letter.
    longer = tabs._tab_name("main", "a", {A})
    assert longer.startswith(A) and len(longer) == 6
    driver._refresh()
    moved = _surface("a", window_id="second", binding_id="b-moved")
    driver.listed["surfaces"] = [moved, _surface("b")]
    driver._refresh()
    assert driver._find(A).window_id == "second"


def test_a_new_listing_releases_the_binding_it_replaces_and_a_new_page_forgets_the_old(driver, monkeypatch):
    from openprogram.interfaces.server.ws_actions import webtab
    released = []
    monkeypatch.setattr(webtab, "release_binding", released.append)
    driver._refresh()
    tab = driver._find(A)
    tab.roles, tab.shots = {"e1": ("button", "Go")}, {"x": (1.0, 0, "", 0, 0)}
    driver.listed["surfaces"][0] = _surface("a", binding_id="b-new", target_id="T-new")
    driver._refresh()
    assert released == ["ba"] and tab.binding_id == "b-new"
    assert tab.roles == {} and tab.shots == {}


def test_snapshot_lines_give_each_ref_its_role_and_name():
    text = '- generic [ref=e1]:\n  - button "Say \\"hi\\"" [ref=e2] [cursor=pointer]\n- <changed> radio "A" [checked] [ref=f1e3]'
    assert tabs._REF_LINE.findall(text) == [("generic", "", "e1"), ("button", 'Say \\"hi\\"', "e2"),
                                            ("radio", "A", "f1e3")]


class _Locator:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class _Page:
    url = "https://x.test/form"

    def __init__(self, refs=(), roles=()):
        self.refs, self.roles = set(refs), dict(roles)

    def locator(self, selector):
        return _Locator(1 if selector.removeprefix("aria-ref=") in self.refs else 0)

    def get_by_role(self, role, name=None, exact=False):
        return _Locator(self.roles.get((role, name), 0))

    def title(self):
        return "Form"


def test_a_ref_whose_element_was_rendered_again_is_found_by_role_and_name(driver, monkeypatch):
    tab = tabs.Tab(id="t1", window_id="main", tab_id="a", roles={"e4": ("button", "Next")})
    _, label = driver._locate(_Page(refs={"e4"}), tab, {"ref": "e4"})
    assert label == "e4"
    _, label = driver._locate(_Page(roles={("button", "Next"): 1}), tab, {"ref": "e4"})
    assert label == 'button "Next"'
    monkeypatch.setattr(driver, "_snapshot", lambda page, tab, full: ('- button "Back" [ref=e9]', ""))
    with pytest.raises(tabs.BrowserError) as gone:
        driver._locate(_Page(), tab, {"ref": "e4"})
    assert gone.value.reason_code == "element_gone" and "Back" in gone.value.extra["page"]


def test_a_result_carries_the_changes_or_the_whole_page_after_moving(driver, monkeypatch):
    tab = tabs.Tab(id="t1", window_id="main", tab_id="a")
    monkeypatch.setattr(driver, "_snapshot", lambda page, tab, full: (None, "- <changed> part") if not full
                        else ("- full", ""))
    assert driver._state(_Page(), tab, full=False, before_url="https://x.test/form")["changes"] == "- <changed> part"
    assert driver._state(_Page(), tab, full=False, before_url="https://x.test/start")["page"] == "- full"
    monkeypatch.setattr(driver, "_snapshot", lambda page, tab, full: ("- new document", None))
    assert driver._state(_Page(), tab, full=False, before_url="https://x.test/form")["page"] == "- new document"


class _Impl:
    """Playwright 1.60's page: the AI snapshot is Frame.ariaSnapshot(mode="ai"), incremental with track."""

    def __init__(self):
        self.calls = []
        self._main_frame = self
        self._timeout = None
        self._channel = self

    def aria_snapshot(self, timeout=None, depth=None, mode=None, boxes=None):
        raise AssertionError("the channel is used")

    def send(self, method, timeout, params):
        self.calls.append(params)
        if "track" in params:
            return '- button "Go" [ref=e1]' if len(self.calls) == 1 else '- <changed> button "Gone" [ref=e2]'
        return '- button "Go" [ref=e1]\n- button "Gone" [ref=e2]'


def test_playwright_160_snapshots_track_changes_and_read_the_whole_page_apart(driver, monkeypatch):
    from openprogram.programs.applications.browser import privacy as _privacy

    monkeypatch.setattr(_privacy, "password_values", lambda page: ())
    impl = _Impl()
    page = type("P", (), {"_impl_obj": impl, "_sync": staticmethod(lambda value: value)})()
    tab = tabs.Tab(id="t1", window_id="main", tab_id="a", target_id="T")
    driver._watches["T"] = tabs._Watch()
    # The first snapshot of a document is the whole page.
    assert driver._snapshot(page, tab, full=False) == ('- button "Go" [ref=e1]', None)
    assert driver._snapshot(page, tab, full=False) == (None, '- <changed> button "Gone" [ref=e2]')
    assert tab.roles == {"e1": ("button", "Go"), "e2": ("button", "Gone")}
    whole, _ = driver._snapshot(page, tab, full=True)
    assert "Gone" in whole and impl.calls[-1] == {"mode": "ai"}
    driver._watches["T"].documents += 1
    assert driver._snapshot(page, tab, full=False)[1] is None


def _as(viewer):
    token = tabs._VIEWER.set(viewer)
    return lambda: tabs._VIEWER.reset(token)


def test_two_agents_of_one_conversation_keep_their_own_current_tab(driver):
    reset = _as("s1/exec-a")
    driver._tab(A, "s1")
    reset()
    reset = _as("s1/exec-b")
    driver._tab(B, "s1")
    reset()
    # A new agent of the conversation starts from the tab the conversation used last.
    reset = _as("s1/exec-c")
    assert driver._tab(None, "s1").id == B
    reset()
    reset = _as("s1/exec-a")
    assert driver._tab(None, "s1").id == A
    reset()


def test_a_result_says_when_another_agent_acted_on_the_tab(driver, monkeypatch):
    tab = tabs.Tab(id="t1", window_id="main", tab_id="a")
    monkeypatch.setattr(driver, "_snapshot", lambda page, tab, full: (None, "- <changed> part"))
    for viewer in ("a", "b"):
        reset = _as(viewer)
        driver._state(_Page(), tab, full=False)
        reset()
    tab.writes.append((next(tabs._writes), "b"))
    reset = _as("b")
    assert "note" not in driver._state(_Page(), tab, full=False)
    reset()
    reset = _as("a")
    assert "another agent acted on this tab 1 time(s)" in driver._state(_Page(), tab, full=False)["note"]
    assert "note" not in driver._state(_Page(), tab, full=False)
    reset()


def test_an_action_already_sent_is_never_sent_again_when_the_tab_goes_away(driver, monkeypatch):
    sent = []

    def operate(tab, action, args):
        sent.append(action)
        return tabs._Acted(done="clicked Next", before_url="", documents=0, pending=set(), created=0)

    def settle(tab, acted):
        raise tabs.BrowserError("tab_gone", "the tab is no longer in the App")
    monkeypatch.setattr(driver, "_operate", operate)
    monkeypatch.setattr(driver, "_settle", settle)
    monkeypatch.setattr(driver, "_catch_choosers", lambda tab: {})
    result = driver.run("click", {"tab": A, "ref": "e1"}, "s1")
    assert sent == ["click"] and result["ok"] and "closed or left the App" in result["note"]


class _Scrolled(_Page):
    def __init__(self, scroll):
        super().__init__()
        self.scroll = scroll

    def evaluate(self, script, *args):
        return [800, 0, self.scroll]


class _Clickable(_Scrolled):
    def __init__(self, scroll=0):
        super().__init__(scroll)
        self.clicks = []
        self.mouse = type("_Mouse", (), {"click": lambda _mouse, x, y, **options: self.clicks.append((x, y))})()
        self.keyboard = type("_Keyboard", (), {"down": lambda _keyboard, key: None, "up": lambda _keyboard, key: None})()


def _png(width, height, colour=(200, 10, 10), spot=None):
    """A PNG of one colour; ``spot`` ((x, y), colour) paints a 20x20 square of another."""
    import struct
    import zlib
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            rgb = spot[1] if spot and abs(x - spot[0][0]) < 10 and abs(y - spot[0][1]) < 10 else colour
            row += bytes(rgb)
        rows.append(b"\x00" + bytes(row))

    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def _filtered_png(width, height, size, pixel, filters):
    """A PNG whose row ``y`` uses PNG filter ``filters[y % len(filters)]``; ``pixel(x, y)`` gives its bytes."""
    import struct
    import zlib

    def paeth(a, b, c):
        estimate = a + b - c
        near = (abs(estimate - a), abs(estimate - b), abs(estimate - c))
        return a if near[0] <= near[1] and near[0] <= near[2] else b if near[1] <= near[2] else c

    above, packed = bytes(width * size), bytearray()
    for y in range(height):
        line = b"".join(bytes(pixel(x, y)) for x in range(width))
        kind = filters[y % len(filters)]
        packed.append(kind)
        for index, value in enumerate(line):
            a = line[index - size] if index >= size else 0
            b, c = above[index], above[index - size] if index >= size else 0
            packed.append((value - (0, a, b, (a + b) >> 1, paeth(a, b, c))[kind]) & 255)
        above = line

    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    body = zlib.compress(bytes(packed))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, {3: 2, 4: 6}[size], 0, 0, 0))
            + chunk(b"IDAT", body[:len(body) // 2]) + chunk(b"IDAT", body[len(body) // 2:]) + chunk(b"IEND", b""))


@pytest.mark.parametrize("size", [3, 4])
@pytest.mark.parametrize("filters", [(0,), (1,), (2,), (3,), (4,), (4, 3, 2, 1, 0)])
def test_a_screenshot_is_read_without_core_graphics(size, filters):
    """Windows and Linux have no Core Graphics; the square is decoded from the PNG itself."""
    def pixel(x, y):
        return ((x * 7 + y * 13) & 255, (x * x + y) & 255, (x ^ y * 5) & 255, 255)[:size]

    image = _filtered_png(40, 30, size, pixel, filters)
    square = tabs._png_pixels_around(image, (20.0, 12.0), 6)
    assert square == b"".join(bytes((*pixel(x, y)[:3], 255)) for y in range(6, 18) for x in range(14, 26))
    # Clamped at the image's edges, and empty when the point is outside it.
    corner = tabs._png_pixels_around(image, (1.0, 29.0), 6)
    assert corner == b"".join(bytes((*pixel(x, y)[:3], 255)) for y in range(23, 30) for x in range(0, 7))
    assert tabs._png_pixels_around(image, (400.0, 12.0), 6) == b""


def _noise_png(width, height, kind):
    """An RGBA PNG whose every row carries filter ``kind`` over fixed noise: the slowest input to undo."""
    import random
    import struct
    import zlib
    noise = random.Random(7)
    rows = b"".join(bytes([kind]) + noise.randbytes(width * 4) for _ in range(height))

    def chunk(name, data):
        return struct.pack("!I", len(data)) + name + data + struct.pack("!I", zlib.crc32(name + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b""))


def test_only_the_bytes_left_of_the_square_are_undone(monkeypatch):
    """A filter reads left and up only, so nothing right of the square is worth the worker's time."""
    image = _filtered_png(200, 60, 4, lambda x, y: ((x * 7 + y * 13) & 255, (x * x + y) & 255, (x ^ y * 5) & 255, 255),
                          (4, 3, 2, 1, 0))
    whole = tabs._png_pixels_around(image, (30.0, 40.0), 8)
    lengths, undo = [], tabs._unfilter
    monkeypatch.setattr(tabs, "_unfilter", lambda kind, line, above, size: (lengths.append(len(line)),
                                                                           undo(kind, line, above, size))[1])
    assert tabs._png_pixels_around(image, (30.0, 40.0), 8) == whole
    assert len(lengths) == 48 and set(lengths) == {38 * 4}


def test_a_row_added_to_the_one_above_wraps_each_byte_on_its_own():
    """Up is one addition over the whole row; a byte that overflows must not carry into its neighbour."""
    line, above = bytes([255, 255, 128, 128, 0, 1, 200, 127]), bytes([255, 1, 128, 127, 0, 255, 100, 1])
    assert tabs._unfilter(2, line, above, 4) == bytes((a + b) & 255 for a, b in zip(line, above))


def test_a_click_near_the_left_edge_of_a_large_screenshot_is_checked_quickly(monkeypatch):
    import time
    monkeypatch.setattr(tabs, "_quartz_pixels_around", lambda png, point, radius: None)
    image = _noise_png(1280, 800, 4)
    began = time.perf_counter()
    assert tabs._same_around(image, image, (100.0, 780.0), 32)
    assert time.perf_counter() - began < 1.5


def test_an_unreadable_screenshot_gives_no_pixels():
    import struct
    assert tabs._png_pixels_around(b"not a png", (1.0, 1.0), 4) == b""
    assert tabs._png_pixels_around(_png(20, 20)[:45], (10.0, 10.0), 4) == b""
    palette = _png(20, 20).replace(struct.pack("!IIBBBBB", 20, 20, 8, 2, 0, 0, 0),
                                   struct.pack("!IIBBBBB", 20, 20, 8, 3, 0, 0, 0))
    assert tabs._png_pixels_around(palette, (10.0, 10.0), 4) == b""


def test_both_readers_see_the_same_square():
    pytest.importorskip("Quartz")
    image = _filtered_png(40, 30, 4, lambda x, y: ((x * 7 + y * 13) & 255, (x * x + y) & 255, (x ^ y * 5) & 255, 255),
                          (4, 3, 2, 1, 0))
    assert tabs._quartz_pixels_around(image, (20.0, 12.0), 6) == tabs._png_pixels_around(image, (20.0, 12.0), 6)


@pytest.mark.parametrize("reader", ["core graphics", "portable"])
def test_coordinates_stop_working_once_the_page_moved_or_changed_at_the_point(driver, monkeypatch, reader):
    if reader == "core graphics":
        pytest.importorskip("Quartz")
    else:
        # What Windows and Linux run: PyObjC is not there.
        monkeypatch.setattr(tabs, "_quartz_pixels_around", lambda png, point, radius: None)
    tab = tabs.Tab(id="t1", window_id="main", tab_id="a")
    seen = _png(200, 120)
    with pytest.raises(tabs.BrowserError) as missing:
        driver._click_point(_Clickable(0), tab, {"x": 1, "y": 1})
    assert missing.value.reason_code == "screenshot_required"
    tab.shots[""] = (1.0, 0, _Page.url, 0, 0, seen)
    fresh = [seen]
    monkeypatch.setattr(driver, "_capture", lambda tab: (fresh[0], _Page.url, [800, 0, 0]))
    # Scrolled since: refused, and the new screenshot comes with the refusal.
    with pytest.raises(tabs.BrowserError) as scrolled:
        driver._click_point(_Clickable(40), tab, {"x": 50, "y": 60})
    assert scrolled.value.reason_code == "screenshot_stale" and scrolled.value.extra["_images"] == [seen]
    assert "A new screenshot is attached" in str(scrolled.value)
    # Acted on since, but the point still looks the same: the click goes ahead.
    tab.shots[""] = (1.0, 0, _Page.url, 0, 0, seen)
    tab.writes.append((next(tabs._writes), "other"))
    fresh[0] = _png(200, 120, spot=((150, 100), (10, 10, 200)))
    page = _Clickable(0)
    assert driver._click_point(page, tab, {"x": 50, "y": 60}) == "clicked (50, 60) of the screenshot"
    assert page.clicks == [(50.0, 60.0)]
    # Acted on since, and the point shows something else: refused, with the new screenshot recorded.
    fresh[0] = _png(200, 120, spot=((50, 60), (10, 10, 200)))
    with pytest.raises(tabs.BrowserError) as changed:
        driver._click_point(_Clickable(0), tab, {"x": 50, "y": 60})
    assert changed.value.reason_code == "screenshot_stale" and changed.value.extra["_images"] == [fresh[0]]
    assert tab.shots[""][5] == fresh[0] and tab.shots[""][1] == tab.last_write()
    # Where the pixels cannot be compared, acted on means stale, as before.
    tab.shots[""] = (1.0, 0, _Page.url, 0, 0, seen)
    monkeypatch.setattr(tabs, "_pixels_around", lambda png, point, radius: b"")
    with pytest.raises(tabs.BrowserError) as unknown:
        driver._click_point(_Clickable(0), tab, {"x": 50, "y": 60})
    assert unknown.value.reason_code == "screenshot_stale"


def test_a_failing_script_says_what_the_page_threw(driver, monkeypatch):
    class _Failing(_Page):
        def evaluate(self, script, *args):
            raise RuntimeError("Page.evaluate: SyntaxError: Unexpected token ';'\n    at eval (eval at <anonymous>)")
    tab = tabs.Tab(id="t1", window_id="main", tab_id="a", target_id="tg")
    monkeypatch.setattr(driver, "_page", lambda tab: _Failing())
    driver._watches["tg"] = tabs._Watch()
    with pytest.raises(tabs.BrowserError) as failed:
        driver._operate(tab, "evaluate", {"script": "a; b"})
    assert failed.value.reason_code == "script_error"
    assert str(failed.value) == "the script failed: SyntaxError: Unexpected token ';'"
    assert tab.last_write(), "the script may have changed the page before it failed"


def test_a_script_on_an_element_runs_on_its_locator(driver, monkeypatch):
    ran = []

    class _Located:
        def count(self):
            return 1

        def evaluate(self, script, *args):
            ran.append(("element", script))
            return "v"

    class _Scripted(_Page):
        def locator(self, selector):
            ran.append(selector)
            return _Located()

        def evaluate(self, script, *args):
            ran.append(("page", script))
            return 2

    tab = tabs.Tab(id="t1", window_id="main", tab_id="a", target_id="tg")
    tab.roles["e3"] = ("textbox", "Name")
    monkeypatch.setattr(driver, "_page", lambda tab: _Scripted())
    driver._watches["tg"] = tabs._Watch()
    monkeypatch.setattr("openprogram.programs.applications.browser.privacy.password_values", lambda page: [])
    assert driver._operate(tab, "evaluate", {"ref": "e3", "script": "(el) => el.value"})["result"] == "v"
    assert driver._operate(tab, "evaluate", {"script": "1 + 1"})["result"] == "2"
    assert [item if isinstance(item, str) else item[0] for item in ran] == ["aria-ref=e3", "element", "page"]


def test_a_list_of_actions_holds_its_tab_until_it_ends(driver, monkeypatch):
    monkeypatch.setattr(tabs, "TAB_WAIT_SECONDS", 0.0)
    tab = driver._tab(A, "s1")
    with tabs.batch():
        assert driver._hold(tab) is False and tab.lock.locked()
        assert driver._hold(tab) is False
    assert not tab.lock.locked()
    tab.lock.acquire()
    try:
        with pytest.raises(tabs.BrowserError) as busy:
            driver._hold(tab)
        assert busy.value.reason_code == "tab_busy"
    finally:
        tab.lock.release()


def test_the_browser_is_refused_while_the_user_turned_off_agent_access(driver, monkeypatch):
    from openprogram.runtime.conversation import surface_context
    monkeypatch.setattr(surface_context, "current", lambda: {"surfaces": [{"preview_status": "disabled"}]})
    result = driver.run("read", {}, "s1")
    assert result["reason_code"] == "page_access_disabled"
    assert result["error"].startswith("[blocked by setting] ") and "To change this: " in result["error"]


def test_a_forked_subprocess_starts_its_own_driver(monkeypatch):
    parent = object()
    monkeypatch.setattr(tabs, "_driver", parent)
    tabs._forget_in_child()
    assert tabs._driver is None and not tabs._driver_lock.locked()


def test_a_tab_the_action_opened_is_reported_and_not_switched_to(driver):
    tab = driver._tab(A, "s1")
    driver.listed["surfaces"].append(_surface("c", opener_tab_id="a", title="Popup"))
    opened = driver._opened(tab)
    assert [row["title"] for row in opened] == ["Popup"]
    assert driver._current("s1") == A


class _Chooser:
    def __init__(self):
        self.files = None

    def set_files(self, payload):
        self.files = payload


def test_upload_without_ref_answers_the_file_chooser_the_last_action_opened(driver, tmp_path, monkeypatch):
    from openprogram.host import sandbox
    monkeypatch.setattr(sandbox, "validate_read_path", lambda path: None)
    tab = tabs.Tab(id="t1", window_id="main", tab_id="a")
    with pytest.raises(tabs.BrowserError) as none:
        driver._answer_chooser(tab, {"path": str(tmp_path / "cv.pdf")})
    assert none.value.reason_code == "no_file_chooser"
    (tmp_path / "cv.pdf").write_bytes(b"%PDF")
    tab.chooser = chooser = _Chooser()
    with pytest.raises(tabs.BrowserError):
        driver._answer_chooser(tab, {"path": str(tmp_path / "missing.pdf")})
    # A bad path leaves the chooser waiting for a good one, which answers it once.
    assert driver._answer_chooser(tab, {"path": str(tmp_path / "cv.pdf")}) == "chose cv.pdf in the file chooser"
    assert chooser.files["name"] == "cv.pdf" and tab.chooser is None


class _Field:
    def __init__(self, index):
        self.index, self.files = index, None

    def set_input_files(self, payload):
        self.files = payload


class _FieldPage:
    def __init__(self):
        self.made = {}

    def locator(self, selector):
        assert selector == 'input[type="file"]'
        page = self

        class All:
            def nth(self, index):
                return page.made.setdefault(index, _Field(index))
        return All()


class _Button:
    def __init__(self, state, fields):
        self.state, self.fields = state, fields

    def evaluate(self, script):
        return self.state if script == tabs.ELEMENT_STATE_SCRIPT else self.fields


def test_a_button_that_opens_no_chooser_lists_the_file_fields_for_upload_by_number(driver, tmp_path, monkeypatch):
    from openprogram.host import sandbox
    monkeypatch.setattr(sandbox, "validate_read_path", lambda path: None)
    (tmp_path / "cv.pdf").write_bytes(b"%PDF")
    tab, page = tabs.Tab(id="t1", window_id="main", tab_id="a"), _FieldPage()
    fields = [{"index": 0, "id": "resumeKey", "name": "", "accept": ".pdf", "multiple": False, "disabled": False,
               "near": "Resume", "beside": True},
              {"index": 1, "id": "", "name": "old", "accept": "", "multiple": False, "disabled": True,
               "near": "", "beside": False}]
    message = driver._no_chooser_message(page, tab, _Button({"text": "Uploading... 100%", "disabled": True}, fields),
                                         "e90")
    assert "it reads 'Uploading... 100%'; it is disabled" in message and "reload it" in message
    # A disabled field is not offered; the one beside the button says so.
    assert "field 1: #resumeKey, accept=.pdf, near 'Resume', the only one in the button's section" in message
    assert "field 2" not in message and list(tab.file_fields) == [1]
    assert driver._upload_to_field(tab, {"field": 1, "path": str(tmp_path / "cv.pdf")}) == "chose cv.pdf in file field 1"
    assert page.made[0].files["name"] == "cv.pdf"
    with pytest.raises(tabs.BrowserError) as unknown:
        driver._upload_to_field(tab, {"field": 2, "path": str(tmp_path / "cv.pdf")})
    assert unknown.value.reason_code == "no_file_field"
    tab.forget_page()
    assert tab.file_fields == {}


def test_a_page_with_no_file_field_says_only_how_to_get_past_a_stuck_upload(driver):
    tab = tabs.Tab(id="t1", window_id="main", tab_id="a")
    message = driver._no_chooser_message(_FieldPage(), tab, _Button({"text": "", "disabled": False}, []), "e5")
    assert message.startswith("clicked e5, but it opened no file chooser and the page has no file field")
    assert tab.file_fields == {}


def test_a_finished_request_logs_its_status_without_asking_the_closed_page(driver):
    class Request:
        method, url, resource_type, frame = "GET", "https://x.test/api", "fetch", None
        existing_response = type("Response", (), {"status": 204})()

        def is_navigation_request(self):
            return False

        def response(self):
            raise AssertionError("a round trip races the page closing")

    class Page:
        main_frame = object()

        def __init__(self):
            self.handlers = {}

        def set_default_timeout(self, _ms):
            pass

        def on(self, event, handler):
            self.handlers[event] = handler

    page, finished, failed = Page(), Request(), Request()
    driver._watch(page, "T")
    for item in (finished, failed):
        page.handlers["request"](item)
    page.handlers["requestfinished"](finished)
    failed.failure = "net::ERR_ABORTED"
    page.handlers["requestfailed"](failed)
    watch = driver._watches["T"]
    assert not watch.pending
    assert [entry["status"] for entry in watch.network] == [204, 0]
    assert watch.network[1]["error"] == "net::ERR_ABORTED"
    assert tabs._response_status(type("Old", (), {"_impl_obj": type("I", (), {"_response": None})()})()) == 0


# -- what the conversation's preview follows --------------------------------


@pytest.fixture
def reports(driver, monkeypatch):
    """Operations the driver reports, as (page_key, operation, follow)."""
    from openprogram.programs.applications.browser import pages
    reported = []
    monkeypatch.setattr(pages, "publish_bound_page", lambda *args, **kwargs: None)
    monkeypatch.setattr(pages, "report_browser_operation",
                        lambda key, operation, *, follow, execution_id=None: reported.append((key, operation, follow)))
    monkeypatch.setattr(driver, "_catch_choosers", lambda tab: {})
    monkeypatch.setattr(driver, "_settle", lambda tab, acted: None)
    monkeypatch.setattr(driver, "_report", lambda tab, before_url: {"ok": True, "tab": tab.id, "url": "https://x.test/"})
    driver.listed["surfaces"][0]["page_key"] = "page:a"
    return reported


def _clicks(tab, action, args):
    tab.aim = {"point": {"x": 120.0, "y": 40.0}, "viewport": {"width": 800, "height": 600}}
    return tabs._Acted(done="clicked e1", before_url="", documents=0, pending=set(), created=0)


def test_a_click_is_followed_from_before_its_input_and_its_outcome_shows_where_it_landed(driver, reports, monkeypatch):
    monkeypatch.setattr(driver, "_operate", _clicks)
    assert driver.run("click", {"tab": A, "ref": "e1"}, "s1")["ok"]
    (key, dispatched, follow), (_, done, done_follow) = reports
    assert key == "page:a" and dispatched["phase"] == "dispatched" and follow is True
    assert "point" not in dispatched
    assert done["phase"] == "acknowledged" and done_follow is True and done["id"] == dispatched["id"]
    assert done["point"] == {"x": 120.0, "y": 40.0, "width": 800.0, "height": 600.0}


def test_each_step_of_a_list_reports_on_its_own(driver, reports, monkeypatch):
    monkeypatch.setattr(driver, "_operate", _clicks)
    with tabs.batch():
        driver.run("click", {"tab": A, "ref": "e1"}, "s1")
        driver.run("click", {"tab": A, "ref": "e2"}, "s1")
    assert [operation["phase"] for _, operation, _ in reports] == ["dispatched", "acknowledged"] * 2
    assert reports[0][1]["id"] != reports[2][1]["id"]


def test_a_read_is_followed_without_marking_the_page_operated(driver, reports, monkeypatch):
    monkeypatch.setattr(driver, "_operate", lambda tab, action, args: {"ok": True, "tab": tab.id, "page": "- x"})
    driver.run("read", {"tab": A}, "s1")
    [(key, operation, follow)] = reports
    assert key == "page:a" and operation["action"] == "read" and operation["phase"] == "acknowledged"
    assert follow is False


def test_a_tab_the_agent_opens_becomes_the_one_followed(driver, reports, monkeypatch):
    from openprogram.runtime.conversation import surface_context
    monkeypatch.setattr(surface_context, "open_page", lambda url, background=True: {
        "ok": True, "surfaces": [_surface("n", page_key="page:n")]})
    monkeypatch.setattr(driver, "_operate", lambda tab, action, args: {"ok": True, "tab": tab.id})
    driver.run("open", {"url": "https://x.test/new"}, "s1")
    [(key, operation, follow)] = reports
    assert key == "page:n" and operation["action"] == "open" and follow is False


def test_a_failed_action_reports_failed_and_does_not_keep_the_page_operated(driver, reports, monkeypatch):
    def fails(tab, action, args):
        raise tabs.BrowserError("not_editable", "e1 does not take text")
    monkeypatch.setattr(driver, "_operate", fails)
    result = driver.run("type", {"tab": A, "ref": "e1", "text": "hi"}, "s1")
    assert result["reason_code"] == "not_editable"
    assert [(operation["phase"], follow) for _, operation, follow in reports] == [("dispatched", True),
                                                                                  ("failed", False)]
    assert reports[1][1]["error"] == "not_editable"


def test_reporting_that_fails_never_fails_the_action(driver, reports, monkeypatch):
    from openprogram.programs.applications.browser import pages

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(pages, "report_browser_operation", broken)
    monkeypatch.setattr(pages, "publish_bound_page", broken)
    monkeypatch.setattr(driver, "_operate", _clicks)
    assert driver.run("click", {"tab": A, "ref": "e1"}, "s1")["ok"]
    monkeypatch.setattr(driver, "_operate", lambda tab, action, args: {"ok": True, "tab": tab.id})
    assert driver.run("read", {"tab": A}, "s1")["ok"]


def test_a_click_aims_at_the_centre_of_its_element_once_in_view():
    class Located:
        scrolled = False

        def scroll_into_view_if_needed(self, timeout=None):
            self.scrolled = True

        def bounding_box(self, timeout=None):
            return {"x": 100, "y": 30, "width": 40, "height": 20}

    class Viewed:
        def evaluate(self, script, *args):
            return {"width": 1280, "height": 720}

    tab = tabs.Tab(id="t1", window_id="main", tab_id="a")
    locator = Located()
    tabs.Driver._aim(None, Viewed(), tab, locator)
    assert locator.scrolled
    assert tab.aim == {"point": {"x": 120.0, "y": 40.0}, "viewport": {"width": 1280, "height": 720}}
    tabs.Driver._aim(None, Viewed(), tab, None, (5.0, 6.0))
    assert tab.aim["point"] == {"x": 5.0, "y": 6.0}


def test_a_subprocess_hands_its_operations_to_the_worker(monkeypatch):
    from openprogram.programs.applications.browser import pages
    handed = []
    pages.forward_operations_to_parent(handed.append)
    try:
        operation = pages.sanitize_operation(action="click", arguments={}, result={"ok": True},
                                             phase="dispatched", operation_id="op_1")
        pages.report_browser_operation("page:a", operation, follow=True, execution_id="exec-child")
    finally:
        pages.forward_operations_to_parent(None)
    assert handed == [{"page_key": "page:a", "operation": operation, "follow": True, "execution_id": "exec-child"}]
    # The worker keeps only the operation's own fields, and names the run it launched when none is given.
    calls = []
    monkeypatch.setattr(pages, "report_browser_operation",
                        lambda key, operation, *, follow, execution_id=None: calls.append(
                            (key, operation, follow, execution_id)))
    pages.report_forwarded_operation({**handed[0], "execution_id": "", "operation": {**operation, "html": "<b>"}},
                                     execution_id="exec-parent")
    assert calls == [("page:a", operation, True, "exec-parent")]
    pages.report_forwarded_operation({"page_key": "page:a", "operation": {"id": "op_2", "phase": "made-up"}})
    assert len(calls) == 1


@pytest.fixture
def opened_store(monkeypatch, tmp_path):
    from openprogram.programs.applications.browser import pages
    monkeypatch.setattr(pages, "resource_db_path", lambda: tmp_path / "resources.db")
    return pages.BrowserResourceStore(tmp_path / "resources.db")


def test_tabs_lists_only_what_this_conversation_opened_unless_all_is_asked(driver, opened_store, monkeypatch):
    monkeypatch.setattr(driver, "_urls", lambda listed: {})
    monkeypatch.setattr(driver, "_on_owner", lambda function, *args: function(*args))
    opened_store.note_tab_opened("main", "a", "s1")
    mine = driver._list("s1")
    assert [row["tab"] for row in mine["tabs"]] == [A] and mine["other_tabs"] == 1
    assert driver._list("s2")["tabs"] == []
    assert sorted(row["tab"] for row in driver._list("s1", True)["tabs"]) == sorted([A, B])


def test_closing_a_conversation_closes_only_the_tabs_it_opened(driver, opened_store, monkeypatch):
    from openprogram.host.resources import desktop_link
    closed = []
    monkeypatch.setattr(tabs, "driver", lambda: driver)
    monkeypatch.setattr(desktop_link, "request_close_tab", lambda binding: closed.append(binding) or {"ok": True})
    opened_store.note_tab_opened("main", "a", "s1")
    opened_store.note_tab_opened("main", "gone", "s1")
    assert tabs.close_conversation_tabs("s1") == 1
    assert closed == ["ba"]
    # The tab the user closed earlier is forgotten with it; the user's own tab b was never touched.
    assert opened_store.tabs_opened_by("s1") == set()
    assert tabs.close_conversation_tabs("s2") == 0


def test_a_tab_dragged_to_another_window_stays_the_conversations(driver, opened_store):
    driver._refresh()
    opened_store.note_tab_opened("main", "a", "s1")
    driver.listed["surfaces"] = [_surface("a", window_id="second", binding_id="b2"), _surface("b")]
    driver._refresh()
    assert opened_store.tabs_opened_by("s1") == {("second", "a")}


def test_a_tab_the_agent_opened_is_recorded_as_its_conversations(driver, opened_store, monkeypatch):
    from openprogram.runtime.conversation import surface_context
    monkeypatch.setattr(driver, "_operate", lambda tab, action, args: {"ok": True, "tab": tab.id})
    monkeypatch.setattr(surface_context, "open_page", lambda url, background=True: {
        "ok": True, "surfaces": [_surface("n", agent_owned=True)]})
    driver.run("open", {"url": "https://x.test/new"}, "s1")
    # A tab the App already had, which the open only borrowed, is not the conversation's to close.
    monkeypatch.setattr(surface_context, "open_page", lambda url, background=True: {
        "ok": True, "surfaces": [_surface("a", agent_owned=False)]})
    driver.run("open", {"url": "https://x.test/"}, "s1")
    assert opened_store.tabs_opened_by("s1") == {("main", "n")}


class _OwnPage:
    url, closed = "https://x.test/own", False

    def is_closed(self):
        return self.closed

    def opener(self):
        return None

    def title(self):
        return "Own"

    def close(self):
        self.closed = True


def test_with_no_app_window_the_tab_opens_in_the_drivers_own_browser(driver, opened_store, monkeypatch):
    from openprogram.runtime.conversation import surface_context
    page = _OwnPage()

    def own_open(url):
        driver._own_pages["o1"] = page
        return driver._own_surface("o1", page)
    monkeypatch.setattr(surface_context, "open_page", lambda url, background=True: {
        "ok": False, "reason_code": "desktop_unavailable", "error": "no App"})
    monkeypatch.setattr(driver, "_own_open", own_open)
    monkeypatch.setattr(driver, "_operate", lambda tab, action, args: {"ok": True, "tab": tab.id})
    opened = driver.run("open", {"url": "https://x.test/own"}, "s1")
    assert opened["ok"] and "background browser" in opened["note"]
    assert opened_store.tabs_opened_by("s1") == {(tabs.OWN_WINDOW, "o1")}
    # It is listed beside the App's tabs, and a later action goes to it without the App.
    assert opened["tab"] in [row["tab"] for row in driver._refresh() for row in [row.row()]]
    assert driver._tab(None, "s1").window_id == tabs.OWN_WINDOW
    # Closed (here by the page itself), it leaves the list.
    page.closed = True
    assert opened["tab"] not in [tab.id for tab in driver._refresh()]


def test_a_listing_with_no_app_is_the_drivers_own_tabs_and_no_failure(driver, monkeypatch):
    from openprogram.host.resources import background_browser
    from openprogram.runtime.conversation import surface_context

    def no_app(_context=None, wait=True):
        raise surface_context.DesktopUnavailableError("no App")
    monkeypatch.setattr(surface_context, "capture_pages_for_listing", no_app)
    monkeypatch.setattr(background_browser, "available", lambda: True)
    monkeypatch.setattr(driver, "_app_may_come_back", lambda: False)
    assert driver._refresh() == []
    # Where no browser can be started either, the listing says the App is what is missing.
    monkeypatch.setattr(background_browser, "available", lambda: False)
    with pytest.raises(tabs.BrowserError) as missing:
        driver._refresh()
    assert missing.value.reason_code == "app_unavailable"
    # So does a listing of an App that is running and does not answer.
    monkeypatch.setattr(background_browser, "available", lambda: True)
    monkeypatch.setattr(driver, "_app_may_come_back", lambda: True)
    with pytest.raises(tabs.BrowserError):
        driver._refresh()


def test_a_listing_does_not_wait_for_an_app_that_is_not_running(driver, monkeypatch):
    from openprogram.runtime.conversation import surface_context
    waits = []
    monkeypatch.setattr(surface_context, "capture_pages_for_listing",
                        lambda _context=None, wait=True: waits.append(wait) or {"surfaces": []})
    monkeypatch.setattr(driver, "_app_may_come_back", lambda: False)
    driver._refresh()
    assert waits == [False]


def test_letting_go_of_the_app_keeps_the_drivers_own_browser_running(driver):
    stopped = []
    driver._playwright = type("P", (), {"stop": lambda self: stopped.append("playwright")})()
    driver._browser = type("B", (), {"close": lambda self: stopped.append("app")})()
    driver._own = object()
    driver._watches.update({"T1": tabs._Watch(), "own:o1": tabs._Watch()})
    driver._disconnect()
    assert stopped == ["app"] and driver._playwright is not None and list(driver._watches) == ["own:o1"]
    driver._own = None
    driver._disconnect()
    assert stopped == ["app", "playwright"] and driver._playwright is None


def _open_one_more(driver, monkeypatch, tab_id="n"):
    from openprogram.runtime.conversation import surface_context
    monkeypatch.setattr(driver, "_operate", lambda tab, action, args: {"ok": True, "tab": tab.id})

    def open_page(url, background=True):
        driver.listed["surfaces"].append(_surface(tab_id, agent_owned=True))
        return {"ok": True, "surfaces": [_surface(tab_id, agent_owned=True)]}
    monkeypatch.setattr(surface_context, "open_page", open_page)


@pytest.fixture
def closing(driver, monkeypatch):
    """Bindings the App was asked to close; a closed tab leaves the listing."""
    from openprogram.host.resources import desktop_link
    closed = []

    def close(binding):
        closed.append(binding)
        driver.listed["surfaces"] = [row for row in driver.listed["surfaces"] if row.get("binding_id") != binding]
        return {"ok": True}
    monkeypatch.setattr(desktop_link, "request_close_tab", close)
    monkeypatch.setattr(tabs, "driver", lambda: driver)
    return closed


def test_one_more_tab_than_a_conversation_keeps_closes_its_least_recently_used(
        driver, opened_store, closing, monkeypatch):
    """A monitor that opens a page on every wake and never closes one holds the limit, not hundreds."""
    monkeypatch.setattr(tabs, "TABS_PER_CONVERSATION", 2)
    driver.listed["surfaces"] = [_surface("a", focused=False, visible=False), _surface("b", focused=False, visible=False)]
    opened_store.note_tab_opened("main", "a", "s1")
    opened_store.note_tab_opened("main", "b", "s1")
    opened_store.note_tab_opened("offline", "x", "s1")   # in a window that is not there now: not counted
    driver._refresh()
    driver._use(driver._find(A), "s1")   # a was used after b was opened, so b goes first
    _open_one_more(driver, monkeypatch)
    result = driver.run("open", {"url": "https://x.test/new"}, "s1")
    assert closing == ["bb"]
    assert "closed 1 tab(s)" in result["note"]
    assert opened_store.tabs_opened_by("s1") == {("main", "a"), ("main", "n"), ("offline", "x")}


def test_the_limit_leaves_the_tab_in_front_of_the_user_and_never_fails_the_open(
        driver, opened_store, closing, monkeypatch):
    monkeypatch.setattr(tabs, "TABS_PER_CONVERSATION", 1)
    driver.listed["surfaces"] = [_surface("a", focused=True, visible=True)]
    opened_store.note_tab_opened("main", "a", "s1")
    _open_one_more(driver, monkeypatch)
    result = driver.run("open", {"url": "https://x.test/new"}, "s1")
    # The only older tab is the one the user is on: nothing closes, and the page still opened.
    assert closing == [] and result["ok"] and "note" not in result
    # A listing that fails leaves the open as it was.
    monkeypatch.setattr(tabs, "TABS_PER_CONVERSATION", 0)
    real = driver._refresh
    monkeypatch.setattr(driver, "_refresh", lambda **kwargs: (_ for _ in ()).throw(tabs.BrowserError("app_unavailable", "x")))
    assert driver._keep_within_limit(driver._find("t1") or tabs.Tab(id="t9", window_id="main", tab_id="n"), "s1") == 0
    monkeypatch.setattr(driver, "_refresh", real)


def test_a_tab_of_the_users_the_driver_had_not_listed_is_not_taken_for_one_the_action_opened(
        driver, opened_store, monkeypatch):
    from openprogram.runtime.execution import run_control
    monkeypatch.setattr(run_control, "get_current_session_id", lambda: "s1")
    tab = driver._tab(A, "s1")
    with driver._lock:
        driver._tabs.pop(("main", "b"), None)   # the user's tab b, not listed by this driver yet
    driver.listed["surfaces"].append(_surface("p", opener_tab_id="a", title="Popup"))
    opened = driver._opened(tab)
    assert [row["title"] for row in opened] == ["Popup"]
    assert opened_store.tabs_opened_by("s1") == {("main", "p")}


def test_a_run_that_ends_closes_the_tabs_it_opened_and_no_others(driver, opened_store, closing):
    opened_store.note_tab_opened("main", "a", "s1", "exec_run")
    opened_store.note_tab_opened("main", "b", "s1")
    opened_store.note_tab_opened("main", "gone", "s1", "exec_run")
    assert tabs.close_run_tabs("exec_run") == 1
    assert closing == ["ba"]
    assert opened_store.tabs_opened_by("s1") == {("main", "b")}
    assert tabs.close_run_tabs("") == 0


@pytest.mark.parametrize("source, speaker, named", [
    ("scheduler", "runtime/scheduler", True), ("web", "runtime/always_on", True), ("web", "user", False)])
def test_only_a_run_that_ends_with_its_answer_is_named_on_the_tab(
        driver, opened_store, monkeypatch, source, speaker, named):
    from openprogram.runtime.agent.turn_request import TurnRequest, reset_turn_request, set_turn_request
    from openprogram.runtime.execution import run_control
    monkeypatch.setattr(run_control, "get_current_execution_id", lambda: "exec_7")
    _open_one_more(driver, monkeypatch)
    token = set_turn_request(TurnRequest("s1", "", "main", source, speaker_id=speaker))
    try:
        driver.run("open", {"url": "https://x.test/new"}, "s1")
    finally:
        reset_turn_request(token)
    assert opened_store.tabs_opened_in("exec_7") == ({("main", "n")} if named else set())
