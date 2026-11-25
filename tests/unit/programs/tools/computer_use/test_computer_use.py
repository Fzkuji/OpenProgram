"""computer_use's app target: the desktop backends' logic (key chords, tree rendering, diffs) and the
``app`` provider on top of them. Its permission rules are in tests/unit/security/test_computer_use_permissions.py."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from openprogram.programs.tools.computer_use.computer_use.desktop.common import base
from openprogram.programs.tools.computer_use.computer_use.desktop.common import keys
from openprogram.programs.tools.computer_use.computer_use.desktop.macos import computer as macos
from openprogram.programs.tools.computer_use.computer_use.desktop.common import ocr
from openprogram.programs.tools.computer_use.computer_use.desktop.common.tree import (Node, diff, number, render,
    role_name, snapshot)
from openprogram.host import machines
from openprogram.programs.tools.computer_use.computer_use import tool as entry
from openprogram.programs.tools.computer_use.computer_use.contract import Call
from openprogram.programs.tools.computer_use.computer_use.routing.dispatch import run_steps
from openprogram.programs.tools.computer_use.computer_use.providers.window import provider as desktop
from tests.support.screen_targets import environment, install_machines, local
from openprogram.programs.tools.computer_use.computer_use.desktop.macos import (actions as mac_actions, apps as mac_apps, frameworks as mac_frameworks, geometry as mac_geometry, processes as mac_processes, skylight as mac_skylight)
from openprogram.programs.tools.computer_use.computer_use import devices as cu_devices
from openprogram.programs.tools.computer_use.computer_use.providers.window import answers as window_answers


def _decided(decision):
    return decision.outcome, decision.code


def _app(system: str = "Darwin") -> desktop.AppProvider:
    return desktop.AppProvider(machines.local_machine({}), "s", system=system)


def _run(steps, subject):
    """One call of this computer's app target, once the tool has checked it: its locks, then the provider."""
    return run_steps(_app(), subject, steps, Call(conversation="s", agent="s"))


@pytest.fixture
def listed(monkeypatch):
    """This computer's app target is the one target set up, as the tool lists it."""
    install_machines(monkeypatch, [local()], {"local": [_app()]})
    environment(monkeypatch, None)


@pytest.fixture(autouse=True)
def _mac_backend_on_every_host(monkeypatch):
    """The app target runs against the macOS backend these tests fake.

    The host dispatcher would pick the Linux or Windows backend on CI and
    refuse before any faked call ran.
    """
    from openprogram.programs.tools.computer_use.computer_use.desktop import backends
    monkeypatch.setattr(backends, "support", lambda: {"available": True})
    monkeypatch.setattr(backends, "backend", lambda: macos.backend())


# ----------------------------------------------------------------- key chords

def test_plain_named_keys_map_to_macos_keycodes():
    assert keys.parse_chord("Return") == keys.Chord(36, 0)
    assert keys.parse_chord("escape") == keys.Chord(53, 0)
    assert keys.parse_chord("KP_0") == keys.Chord(82, 0)
    assert keys.parse_chord("F5") == keys.Chord(96, 0)


def test_modifiers_combine_into_flags_and_super_is_command():
    chord = keys.parse_chord("super+shift+z")
    assert chord.keycode == 6
    assert chord.flags == keys.COMMAND | keys.SHIFT
    assert keys.parse_chord("ctrl+c").flags == keys.CONTROL
    assert keys.parse_chord("cmd+alt+Tab").flags == keys.COMMAND | keys.OPTION
    assert sorted(keys.parse_chord("ctrl+shift+Tab").modifier_keycodes) == [56, 59]


def test_shifted_characters_add_shift_and_plus_key_is_spelled_out():
    assert keys.parse_chord("A") == keys.Chord(0, keys.SHIFT, "A")
    assert keys.parse_chord("!") == keys.Chord(18, keys.SHIFT, "!")
    assert keys.parse_chord("plus") == keys.Chord(24, keys.SHIFT)
    assert keys.parse_chord("ctrl++") == keys.Chord(24, keys.CONTROL | keys.SHIFT, "+")
    assert keys.parse_chord("a") == keys.Chord(0, 0, "a")


def test_lone_modifier_is_a_key_and_unknown_names_fail():
    assert keys.parse_chord("shift") == keys.Chord(56, keys.SHIFT)
    for bad in ("", "   ", "Hyper+x", "nosuchkey", "ctrl+"):
        with pytest.raises(keys.KeyNameError):
            keys.parse_chord(bad)


def test_click_modifier_lists_accept_plus_or_comma():
    assert keys.modifier_flags("cmd+shift") == keys.COMMAND | keys.SHIFT
    assert keys.modifier_flags("alt, ctrl") == keys.OPTION | keys.CONTROL
    assert keys.modifier_flags(None) == 0
    with pytest.raises(keys.KeyNameError):
        keys.modifier_flags("meta+bogus")


# ----------------------------------------------------------------- tree text

def test_role_names_read_as_words_with_subrole():
    assert role_name("AXTextField") == "text field"
    assert role_name("AXButton", "AXCloseButton") == "button (close button)"
    assert role_name("AXWindow", "AXStandardWindow") == "window (standard window)"
    assert role_name("AXUnknown") == "unknown"


def test_render_shows_index_depth_label_value_flags_and_secondary_actions():
    nodes = [
        Node(role="AXWindow", depth=0, label="Calculator", actions=["AXRaise"], index=0),
        Node(role="AXButton", depth=1, label="7", actions=["AXPress"], index=1),
        Node(role="AXTextField", depth=1, label="Name", value="Ann", flags=["focused", "settable"],
             actions=["AXShowMenu", "AXConfirm"], index=2),
        Node(role="AXTextField", depth=1, placeholder="Search", index=3),
    ]
    assert render(nodes).splitlines() == [
        '0 window "Calculator" actions: AXRaise',
        '  1 button "7"',
        '  2 text field "Name" value="Ann" [focused, settable] actions: AXShowMenu',
        '  3 text field placeholder="Search"',
    ]


def test_bidirectional_marks_are_dropped_from_values():
    assert Node(role="AXStaticText", depth=0, value="\u200e42\u200f", index=0).line() == '0 static text value="42"'


def test_long_values_are_clipped_and_whitespace_collapsed():
    node = Node(role="AXStaticText", depth=0, value="a  b\n" * 100, index=0)
    line = node.line()
    assert "\n" not in line and line.endswith('…"') and len(line) < 200


class _Nodes(list):
    next_index = 0


def _snap(nodes):
    return snapshot(nodes, nodes.next_index)


def _numbered(nodes, previous=None):
    nodes = _Nodes(nodes)
    nodes.next_index = number(nodes, previous)
    return nodes


def test_diff_marks_changed_lines_and_keeps_the_index_of_an_element_changed_in_place():
    before = _numbered([Node(role="AXStaticText", depth=1, value="0"),
                        Node(role="AXButton", depth=1, label="Delete")])
    after = _numbered([Node(role="AXStaticText", depth=1, value="7"),
                       Node(role="AXButton", depth=1, label="All Clear")], _snap(before))
    assert [node.index for node in after] == [0, 1]
    text, is_diff = diff(_snap(before), after)
    assert is_diff
    assert text.splitlines() == [
        '- static text value="0"', '- button "Delete"',
        '+ 0 static text value="7"', '+ 1 button "All Clear"',
    ]


def test_an_insertion_above_does_not_shift_the_indices_the_model_already_has():
    before = _numbered([Node(role="AXButton", depth=0, label=name) for name in ("A", "B", "C")])
    after = _numbered([Node(role="AXStaticText", depth=0, value="banner")]
                      + [Node(role="AXButton", depth=0, label=name) for name in ("A", "B", "C")], _snap(before))
    assert [(node.label or node.value, node.index) for node in after] == [("banner", 3), ("A", 0), ("B", 1), ("C", 2)]
    assert diff(_snap(before), after) == ('+ 3 static text value="banner"', True)


def test_a_line_added_above_elements_that_also_changed_does_not_take_their_indices():
    # A form another agent is filling: a notice appears on top while two fields get values.
    before = _numbered([Node(role="AXTextField", depth=0, label=name) for name in ("Name", "School")]
                       + [Node(role="AXButton", depth=0, label="Save")])
    after = _numbered([Node(role="AXTextField", depth=0, label="Notice"),
                       Node(role="AXTextField", depth=0, label="Name", value="Li"),
                       Node(role="AXTextField", depth=0, label="School", value="THU"),
                       Node(role="AXButton", depth=0, label="Save")], _snap(before))
    assert [(node.label, node.index) for node in after] == [("Notice", 3), ("Name", 0), ("School", 1), ("Save", 2)]
    # A quote inside a label does not end it.
    quoted = _numbered([Node(role="AXButton", depth=0, label='Say "hi"'), Node(role="AXButton", depth=0, label="B")])
    again = _numbered([Node(role="AXButton", depth=0, label="New"),
                       Node(role="AXButton", depth=0, label='Say "hi"', flags=["disabled"]),
                       Node(role="AXButton", depth=0, label="B", flags=["disabled"])], _snap(quoted))
    assert [node.index for node in again] == [2, 0, 1]


def test_a_replaced_element_of_another_role_gets_a_new_index_and_freed_ones_are_not_reused():
    before = _numbered([Node(role="AXButton", depth=0, label="OK"), Node(role="AXButton", depth=0, label="Go")])
    after = _numbered([Node(role="AXTextField", depth=0, value="hi"), Node(role="AXButton", depth=0, label="Go")],
                      _snap(before))
    assert [node.index for node in after] == [2, 1]
    again = _numbered([Node(role="AXButton", depth=0, label="Go")], _snap(after))
    assert [node.index for node in again] == [1] and _snap(again).next_index == 3


def test_diff_without_previous_or_with_no_change():
    nodes = _numbered([Node(role="AXButton", depth=0, label="OK")])
    assert diff(None, nodes) == ('0 button "OK"', False)
    assert diff(_snap(nodes), nodes) == ("(no changes in the Accessibility tree)", True)


def test_diff_falls_back_to_the_full_tree_when_most_lines_changed():
    before = _numbered([Node(role="AXButton", depth=0, label=str(i)) for i in range(30)])
    after = _numbered([Node(role="AXButton", depth=0, label=f"x{i}") for i in range(30)], _snap(before))
    text, is_diff = diff(_snap(before), after)
    assert not is_diff and text == render(after)


# ----------------------------------------------------------------- OCR fallback

def test_vision_boxes_become_screenshot_pixels_in_reading_order():
    found = [("right", (0.5, 0.8, 0.2, 0.1)), ("left", (0.1, 0.81, 0.2, 0.1)), ("below", (0.1, 0.1, 0.3, 0.1)),
             ("  ", (0, 0, 0.1, 0.1))]
    boxes = ocr.boxes_from_normalized(found, 1000, 500)
    assert [b.text for b in boxes] == ["left", "right", "below"]
    assert boxes[1] == ocr.TextBox("right", 500, 50, 200, 50)
    assert boxes[1].line() == '"right" at (600, 75), 200x50'


def test_ocr_lines_are_capped_and_matched_case_insensitively():
    boxes = [ocr.TextBox(f"Item {n}", 0, n * 10, 50, 10) for n in range(ocr.MAX_LINES + 5)]
    rendered = ocr.render(boxes).splitlines()
    assert len(rendered) == ocr.MAX_LINES + 1 and rendered[-1].startswith("…and 5 more")
    assert [b.text for b in ocr.matching(boxes, "item  10")] == ["Item 10"] + [f"Item {n}" for n in range(100, 110)]


def test_recognition_hints_put_preferred_languages_first_then_cjk_and_english():
    supported = ["en-US", "fr-FR", "zh-Hans", "zh-Hant", "ja-JP", "ko-KR", "de-DE"]
    assert ocr.pick_languages(["zh-Hant-TW", "fr-CA"], supported) == [
        "zh-Hant", "fr-FR", "zh-Hans", "ja-JP", "ko-KR", "en-US"]
    assert ocr.pick_languages(["de-AT"], ["en-US", "de-DE"]) == ["de-DE", "en-US"]


def test_touching_outlines_merge_into_one_control_and_text_or_odd_sizes_are_dropped():
    boxes = [(100, 100, 20, 30), (122, 110, 20, 20),   # one icon drawn as two outlines, 2 px apart
             (300, 100, 40, 40),                        # a lone icon
             (500, 104, 30, 30),                        # covers the word below
             (10, 10, 6, 6), (10, 300, 400, 30), (600, 100, 20, 120)]   # a speck, a bar, a sliver
    shapes = ocr.shapes_from_boxes(boxes, [ocr.TextBox("Save", 495, 100, 60, 40),
                                           ocr.TextBox("前", 300, 100, 40, 40)], scale=2.0)  # a misread icon
    assert shapes == [ocr.Shape(100, 100, 42, 30), ocr.Shape(300, 100, 40, 40)]
    assert ocr.render_shapes(shapes).splitlines() == ["(121, 115), 42x30", "(320, 120), 40x40"]
    corners = [(0, 0, 36, 36), (1964, 1064, 36, 36), (900, 500, 40, 40)]   # two window corners and an icon
    assert ocr.shapes_from_boxes(corners, [], scale=2.0, size=(2000, 1100)) == [ocr.Shape(900, 500, 40, 40)]
    many = [ocr.Shape(n * 40, 0, 20, 20) for n in range(ocr.MAX_SHAPES + 2)]
    assert ocr.render_shapes(many).splitlines()[-1].startswith("…and 2 more")


def test_vision_outlines_icons_that_carry_no_text():
    if not ocr.available():
        pytest.skip("Vision is not installed")
    AppKit = pytest.importorskip("AppKit")
    image = AppKit.NSImage.alloc().initWithSize_((600, 160))
    image.lockFocus()
    AppKit.NSColor.whiteColor().set()
    AppKit.NSBezierPath.fillRect_(((0, 0), (600, 160)))
    names = ["gearshape", "trash", "magnifyingglass", "square.and.arrow.up"]
    for i, name in enumerate(names):
        symbol = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        symbol.drawInRect_(((40 + i * 120, 90), (32, 32)))
    font = {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(22)}
    AppKit.NSString.stringWithString_("Save changes").drawAtPoint_withAttributes_((40, 20), font)
    image.unlockFocus()
    bitmap = AppKit.NSBitmapImageRep.alloc().initWithData_(image.TIFFRepresentation())
    png = bytes(bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {}))
    width, height = bitmap.pixelsWide(), bitmap.pixelsHigh()
    scale = width / 600
    shapes = ocr.detect_shapes(png, width, height, ocr.recognize(png, width, height), scale)
    assert len(shapes) == len(names)
    for shape, i in zip(shapes, range(len(names))):
        cx, cy = shape.center
        assert abs(cx / scale - (56 + i * 120)) < 8 and abs(cy / scale - (160 - 106)) < 8


def test_only_the_window_walk_counts_towards_a_sparse_tree():
    nodes = [Node("AXWindow", 0), Node("AXButton", 1), Node("AXButton", 1), Node("AXMenuBar", 0), Node("AXMenuBarItem", 1)]
    assert base.window_node_count(nodes) == 3
    assert base.window_node_count(nodes[:3]) == 3


def test_find_falls_back_to_text_on_the_screenshot(monkeypatch):
    computer = macos.MacComputer()
    state = mac_frameworks.WindowState(pid=1, window_title="Game", nodes=[Node("AXWindow", 0, label="Game", index=0)],
                              has_screenshot=True, scale=2.0, size=(400, 300),
                              text_boxes=[ocr.TextBox("New Game", 100, 200, 120, 30)])
    monkeypatch.setattr(computer, "_state_for", lambda session, query: (None, state))
    assert '"New Game" at (160, 215)' in computer.find("s", "Game", text="new game")
    assert computer.find("s", "Game", text="quit").startswith("no element of the last tree matches")
    assert computer.find("s", "Game", text="Game").startswith("1 match(es)")


def test_vision_reads_mixed_scripts_from_a_rendered_image():
    if not ocr.available():
        pytest.skip("Vision is not installed")
    AppKit = pytest.importorskip("AppKit")
    Foundation = pytest.importorskip("Foundation")
    image = AppKit.NSImage.alloc().initWithSize_((520, 160))
    image.lockFocus()
    AppKit.NSColor.whiteColor().set()
    AppKit.NSBezierPath.fillRect_(((0, 0), (520, 160)))
    font = {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(30)}
    Foundation.NSString.stringWithString_("Start Game").drawAtPoint_withAttributes_((30, 100), font)
    Foundation.NSString.stringWithString_("设置 选项").drawAtPoint_withAttributes_((260, 20), font)
    image.unlockFocus()
    bitmap = AppKit.NSBitmapImageRep.alloc().initWithData_(image.TIFFRepresentation())
    png = bytes(bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {}))
    width, height = bitmap.pixelsWide(), bitmap.pixelsHigh()
    boxes = ocr.recognize(png, width, height)
    assert [b.text.replace(" ", "") for b in boxes] == ["StartGame", "设置选项"]
    x, y = boxes[0].center
    assert x < width / 2 and y < height / 2


def test_running_apps_are_looked_up_per_process_so_a_new_launch_is_seen():
    import os
    import platform
    if platform.system() != "Darwin":
        pytest.skip("libproc is macOS only")
    me = object()

    class FakeAppKit:
        class NSRunningApplication:
            @staticmethod
            def runningApplicationWithProcessIdentifier_(pid):
                return me if pid == os.getpid() else None

    from openprogram.programs.tools.computer_use.computer_use.desktop.macos.processes import running_applications
    assert running_applications(FakeAppKit) == [me]


def test_the_tree_walk_stops_at_a_cycle_back_to_the_app_or_an_element_seen_before(monkeypatch):
    # QuickTime Player with no document lists the application as its window, whose child is itself.
    graph = {"app": ["window", "menubar"], "window": ["button", "app"], "button": ["window"], "menubar": []}
    roles = {"app": "AXApplication", "window": "AXWindow", "button": "AXButton", "menubar": "AXMenuBar"}
    computer = macos.MacComputer()
    monkeypatch.setattr(computer, "_other_space_windows", lambda root, windows: [])
    monkeypatch.setattr(computer, "_service_panel_windows", lambda root, windows: [])
    monkeypatch.setattr(computer, "_node", lambda element, depth: (
        Node(roles[element], depth, label=element), list(graph[element])))
    monkeypatch.setattr(computer, "_attr", lambda element, name: {
        ("app", "AXChildren"): graph["app"], ("app", "AXMenuBar"): "menubar",
        ("app", "AXWindows"): ["app", "window"]}.get((element, name)) or (
        roles.get(element) if name == "AXRole" else None))
    nodes, _elements, complete = computer._read_tree("app", "window")
    assert [n.label for n in nodes] == ["window", "button", "menubar"] and complete
    assert computer._windows("app") == ["window"]
    # A focused "window" that is the application itself is not picked.
    monkeypatch.setattr(computer, "_attr", lambda element, name: {
        ("app", "AXWindows"): ["app"], ("app", "AXFocusedWindow"): "app"}.get((element, name)) or (
        roles.get(element) if name == "AXRole" else None))
    assert computer._pick_window("app", None) is None


def test_menu_titles_match_case_insensitively_without_the_ellipsis():
    assert base.menu_key("Export…") == base.menu_key("export") == base.menu_key(" EXPORT... ")
    assert base.menu_key("Export as PDF…") != base.menu_key("Export")


def test_hold_durations_are_bounded():
    assert base.seconds_arg(None, "duration") == 0 and base.seconds_arg("1.5", "duration") == 1.5
    for bad in (-1, 11, "soon"):
        with pytest.raises(base.ComputerUseError):
            base.seconds_arg(bad, "duration")


# ----------------------------------------------------------------- the tool

def test_tool_is_a_resident_default_offered_to_chat_channels():
    from openprogram.programs import DEFAULT_TOOLS, DEFERRED_DEFAULT_TOOLS, RESIDENT_TOOLS, _unsafe_in_for
    from openprogram.programs._host.runtime import get
    registered = get("computer_use")
    assert registered is not None and not registered._defer
    assert "computer_use" in DEFAULT_TOOLS and "computer_use" not in DEFERRED_DEFAULT_TOOLS
    assert "computer_use" in RESIDENT_TOOLS
    assert set(_unsafe_in_for("computer_use")) == {"plan"}
    # The registration is fixed: the types, never the targets set up now, which <devices> lists.
    assert registered.parameters["properties"]["type"]["enum"] == ["window", "screen", "browser"]
    assert registered.description == entry.DESCRIPTION
    for gone in ("computer_use_agent", "gui_agent", "web_use"):
        assert gone not in DEFAULT_TOOLS and gone not in RESIDENT_TOOLS


def test_the_targets_a_turn_lists_are_the_ones_set_up(monkeypatch):
    from tests.support.screen_targets import PIXELS, FakeProvider, machine
    install_machines(monkeypatch, [local(), machine("lab")],
                     {"local": [_app(), FakeProvider("browser")],
                      "lab": [FakeProvider("screen", "lab", capabilities=PIXELS)]})
    environment(monkeypatch, None)
    assert cu_devices.available_targets() == ["window", "browser", "lab screen"]
    assert list(cu_devices.devices()) == [cu_devices.THIS_COMPUTER, "lab"]


def test_unknown_action_is_an_error_without_touching_the_host(fake, listed):
    computer = fake()
    result = entry.computer_use(type="window", app="Notes", action="fly")
    assert result.is_error and result.text.startswith("UNKNOWN_ACTION:") and computer.calls == []
    result = entry.computer_use(type="printer")
    assert result.is_error and result.text.startswith("UNKNOWN_TARGET:") and 'Targets now: type="window".' in result.text


def test_opening_a_site_in_the_built_in_browser_asks_nothing_and_an_upload_does():
    from openprogram.programs.tools.computer_use.computer_use.providers.browser.built_in import BuiltInBrowser
    browser = BuiltInBrowser(None)
    assert browser.approval({}, [("create_tab", {"url": "https://example.com"})]) is None
    assert browser.approval({}, [("navigate", {"url": "https://example.com"})]) is None
    assert browser.approval({}, [("evaluate", {"script": "1"})]) is None
    assert "Upload" in browser.approval({}, [("upload_file", {"path": "/tmp/a.txt"})]).shown


@pytest.mark.parametrize("launch", [True, False])
def test_refused_apps_are_refused_before_launch_whether_or_not_running(monkeypatch, launch):
    from openprogram.programs.tools.computer_use.computer_use.desktop.macos import computer as macos
    from openprogram.runtime.permissions.refusal import Refused
    monkeypatch.setattr("openprogram.state.config.read_config",
                        lambda: {"computer_use": {"refused_apps": ["com.apple.Passwords"]}})
    computer = macos.MacComputer()
    launched = []
    monkeypatch.setattr(computer, "_running_match", lambda _q: None)
    monkeypatch.setattr(computer, "_installed_match", lambda _q: {
        "bundle_id": "com.apple.Passwords", "display_name": "Passwords", "name": "Passwords", "path": ""})
    monkeypatch.setattr(mac_apps.subprocess, "run", lambda *a, **k: launched.append(a))
    with pytest.raises(Refused, match="computer_use.refused_apps"):
        computer.resolve_app("Passwords", launch=launch)
    assert launched == []


def test_the_rule_name_of_an_app_is_its_display_name_looked_up_once_without_an_icon(monkeypatch):
    from openprogram.programs.tools.computer_use.computer_use.desktop.macos import computer as macos

    class _URL:
        def path(self):
            return "/System/Applications/Utilities/Terminal.app"

    class _Running:
        def bundleURL(self):
            return _URL()

        def localizedName(self):
            return "Terminal"

    class _Manager:
        def displayNameAtPath_(self, path):
            return {"/System/Applications/Utilities/Terminal.app": "Terminal.app",
                    "/Applications/Visual Studio Code.app": "Visual Studio Code"}[path]

    appkit = SimpleNamespace(NSFileManager=SimpleNamespace(defaultManager=lambda: _Manager()))
    monkeypatch.setattr(mac_frameworks, "_appkit", lambda: appkit)
    computer = macos.MacComputer()
    monkeypatch.setattr(computer, "_running_match", lambda q: pytest.fail("the lookup with retries sleeps"))
    monkeypatch.setattr(computer, "_icon", lambda path: pytest.fail("an icon is not part of a rule"))
    monkeypatch.setattr(computer, "_running_match_once", lambda q: _Running() if q == "pid:42" else None)
    monkeypatch.setattr(computer, "_installed_match", lambda q: {
        "path": "/Applications/Visual Studio Code.app", "display_name": "Visual Studio Code",
        "bundle_id": "com.microsoft.VSCode", "name": "Visual Studio Code"} if q == "com.microsoft.VSCode" else None)
    assert computer.app_name("pid:42") == "Terminal"
    assert computer.app_name("com.microsoft.VSCode") == "Visual Studio Code"
    assert computer.app_name("Nowhere") == "" and computer.app_name("  ") == ""


def test_the_installed_apps_are_read_again_only_when_a_folder_changes(monkeypatch, tmp_path):
    from openprogram.programs.tools.computer_use.computer_use.desktop.macos import computer as macos
    monkeypatch.setattr(mac_frameworks, "APP_DIRECTORIES", (str(tmp_path),))
    computer = macos.MacComputer()
    scans = []
    monkeypatch.setattr(computer, "_scan_installed", lambda: scans.append(1) or [{"name": "A"}])
    assert computer._installed() == [{"name": "A"}] and computer._installed() == [{"name": "A"}]
    assert len(scans) == 1
    stat = os.stat(tmp_path)
    os.utime(tmp_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    computer._installed()
    assert len(scans) == 2


def test_a_name_read_off_a_running_app_finds_its_bundle_once_it_has_quit(monkeypatch):
    from openprogram.programs.tools.computer_use.computer_use.desktop.macos import computer as macos
    computer = macos.MacComputer()
    rows = [{"name": "Visual Studio Code", "display_name": "Visual Studio Code", "bundle_id": "com.microsoft.VSCode",
             "path": "/Applications/Visual Studio Code.app", "bundle_name": "Code"},
            {"name": "Code", "display_name": "Code", "bundle_id": "com.example.code", "path": "/Applications/Code.app",
             "bundle_name": "Code"}]
    monkeypatch.setattr(computer, "_installed", lambda: rows)
    assert computer._installed_match("Code")["bundle_id"] == "com.example.code"  # its own file name comes first
    assert computer._installed_match("code ")["path"] == "/Applications/Code.app"
    monkeypatch.setattr(computer, "_installed", lambda: rows[:1])
    assert computer._installed_match("Code")["bundle_id"] == "com.microsoft.VSCode"
    assert computer._installed_match("Cod") is None


def test_no_application_is_refused_by_default_and_own_windows_only_where_cards_show():
    from types import SimpleNamespace
    from openprogram.programs.tools.computer_use.computer_use.desktop.common.base import RefusedApps
    from openprogram.runtime.agent.turn_request import reset_turn_request, set_turn_request
    own = frozenset({"ai.openprogram.desktop"})
    assert not RefusedApps(own).hits("com.apple.Passwords")
    assert RefusedApps(own).hits("ai.openprogram.desktop")  # no turn: nothing says cards are off
    for mode, refused in (("ask", True), ("auto", True), ("bypass", False)):
        token = set_turn_request(SimpleNamespace(permission_mode=mode))
        try:
            assert RefusedApps(own).hits("ai.openprogram.desktop") is refused
        finally:
            reset_turn_request(token)


def test_password_fields_are_refused_only_when_the_owner_turns_that_on(monkeypatch):
    from openprogram.programs.tools.computer_use.computer_use.desktop.common.base import refuse_password_field
    from openprogram.runtime.permissions.refusal import Refused
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {})
    refuse_password_field("typing")
    monkeypatch.setattr("openprogram.state.config.read_config",
                        lambda: {"computer_use": {"refuse_password_fields": True}})
    with pytest.raises(Refused, match="computer_use.refuse_password_fields"):
        refuse_password_field("typing")


# ----------------------------------------------------------------- text helpers

def test_find_text_honours_prefix_and_suffix_and_utf16_offsets():
    from openprogram.programs.tools.computer_use.computer_use.desktop.common.base import find_text
    from openprogram.programs.tools.computer_use.computer_use.desktop.macos.clipboard import utf16_len
    value = "the cat sat on the mat"
    assert find_text(value, "the") == 0
    assert find_text(value, "the", suffix=" mat") == 15
    assert find_text(value, "the", prefix="on ") == 15
    assert find_text(value, "dog") is None
    assert utf16_len("a😀b") == 4


# ----------------------------------------------------------------- tool layer

class _FakeComputer:
    def __init__(self, results):
        self.results, self.calls, self.settled = list(results), [], 0

    def act(self, session, action, step):
        self.calls.append((action, step))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def settle(self, session, app):
        self.settled += 1
        return 0.0

    def get_state(self, session, app, **kwargs):
        # After steps the state comes back as what changed.
        assert kwargs["as_diff"] is True and not kwargs["full"]
        return base.Observation(text="+ 4 button \"=\"", image=None, app=app)


@pytest.fixture
def fake(monkeypatch):
    def install(*results):
        computer = _FakeComputer(results)
        monkeypatch.setattr(macos, "backend", lambda: computer)
        return computer
    return install


def test_an_action_settles_once_and_returns_the_changed_tree(fake):
    computer = fake("pressed button \"7\"")
    result = _run([("click", {"app": "Calculator", "element": 3})], {"app": "Calculator"})
    assert not result.is_error and computer.settled == 1
    assert result.text == 'Done: pressed button "7".\n\n+ 4 button "="'
    assert computer.calls[0][1]["element"] == 3


def test_a_read_only_result_comes_back_without_a_new_state(fake):
    computer = fake(base.Report("menu bar: File | Edit"))
    result = _run([("menu", {})], {"app": "Calculator"})
    assert result.text == "menu bar: File | Edit" and computer.settled == 0


def test_a_batch_runs_in_order_stops_at_the_first_error_and_reports_the_state_once(fake):
    computer = fake("typed 3 character(s)", base.ComputerUseError("element 9 no longer exists"), "unused")
    result = _run([("type_text", {"text": "abc"}), ("click", {"element": 9}), ("press_key", {"key": "Return"})],
                  {"app": "Notes"})
    assert result.is_error and computer.settled == 1 and len(computer.calls) == 2
    assert result.text.startswith("Done:\n1. typed 3 character(s)\nError: step 2 (click): element 9 no longer exists "
                                  "The steps after it did not run.\n1 of 3 steps done.")
    assert all(step["app"] == "Notes" for _, step in computer.calls)


@pytest.mark.parametrize("steps, message", [
    ([{"action": "get_app_state"}, {"action": "click"}], "cannot run among several steps"),
    ([{"action": "click", "args": {"app": "Finder", "element": 1}}, {"action": "wait"}], "names another app"),
    ([{"action": "click"}] * 21, "at most 20"),
    ([{"args": {}}], "needs action"),
])
def test_steps_are_checked_before_any_step_runs(fake, listed, steps, message):
    computer = fake()
    result = entry.computer_use(type="window", app="Notes", actions=steps)
    assert result.is_error and result.text.startswith("BAD_ARGUMENT:") and message in result.text
    assert computer.calls == []


def test_the_first_call_to_a_target_carries_its_guide_once(fake, listed, monkeypatch):
    from openprogram.models.providers.types import AgentToolResult
    from openprogram.programs._authoring import guides
    from openprogram.models.providers.types import TextContent
    monkeypatch.setattr(entry.code_mode, "available", lambda: True)
    computer = fake()
    computer.list_apps = lambda: "Notes (running)"
    args = {"type": "window", "action": "list_apps"}
    # Each turn reads <devices>, which registers the guides of the targets listed now.
    cu_devices.devices()

    def call():
        result = entry.computer_use(**args)
        return guides.attach(entry.NAME, args, AgentToolResult(content=[TextContent(text=result.text)]))

    def text(result):
        from openprogram.models.providers.shared.tool_attachments import record
        entry = record(result.details)
        return (entry["text"] if entry else "") + "".join(part.text for part in result.content)
    token = guides.begin_loop()
    try:
        first = text(call())
        assert first.startswith('<guide name="computer_use.window">\nGuide: type="window".')
        # The target's own note comes with its family's guide.
        assert '<guide name="computer_use.note.window">\nNote: type="window" is this computer, a Mac' in first
        # So does the code guide, so the first code call is already informed.
        assert first.endswith("Notes (running)") and '<guide name="computer_use.code">' in first
        assert text(call()) == "Notes (running)"
        # A request that no longer holds the guide (compacted, cleared) gets it again.
        guides.observe_request([{"content": "earlier turns, summarized"}])
        assert text(call()).startswith('<guide name="computer_use.window">')
        assert entry.computer_use(type="window", action="guide").text.startswith('<guide name="computer_use.window">')
    finally:
        guides.end_loop(token)


def test_no_call_is_turned_back_for_a_guide(listed, monkeypatch):
    """A call that sends code before any guide was seen runs, and its result carries the guides."""
    from openprogram.programs._authoring import guides
    monkeypatch.setattr(entry.code_mode, "available", lambda: True)
    cu_devices.devices()
    call = {"type": "window", "app": "Notes", "code": "pass"}
    token = guides.begin_loop()
    try:
        guides.observe_request([{"content": "nothing about this tool"}])
        assert guides.missing_required(entry.NAME, call) == []
        assert [guide.name for guide in guides.due(entry.NAME, call, failed=False)] \
            == ["computer_use.window", "computer_use.note.window", "computer_use.code"]
    finally:
        guides.end_loop(token)
    # Where code cannot run, its guide is not handed out.
    monkeypatch.setattr(entry.code_mode, "available", lambda: False)
    token = guides.begin_loop()
    try:
        assert [guide.name for guide in guides.due(entry.NAME, {"type": "window"}, failed=False)] \
            == ["computer_use.window", "computer_use.note.window"]
    finally:
        guides.end_loop(token)


def test_every_action_but_list_apps_needs_an_app(fake, listed):
    computer = fake()
    computer.list_apps = lambda: "Notes (running)"
    result = entry.computer_use(type="window", action="list_windows")
    assert result.is_error and result.text.startswith("BAD_ARGUMENT: list_windows needs app")
    assert entry.computer_use(type="window", action="list_apps").text == "Notes (running)"


# ----------------------------------------------------------------- app lookup for the interface

def _ws_reply(cmd):
    import asyncio
    import json as _json
    from openprogram.interfaces.server.ws_actions import session

    sent = []

    class _WS:
        async def send_text(self, text):
            sent.append(_json.loads(text))

    asyncio.run(session.handle_computer_use_apps(_WS(), cmd))
    return sent[0]


def test_app_lookup_dedupes_names_and_launches_nothing(monkeypatch):
    seen = []

    class _Lookup:
        def app_info(self, query):
            seen.append(query)
            return {"query": query, "name": query.title(), "bundle_id": f"com.example.{query}", "icon": ""}

    monkeypatch.setattr(macos, "backend", lambda: _Lookup())
    reply = _ws_reply({"apps": ["notes", " notes ", "mail"], "request_id": "r1"})
    assert reply["type"] == "computer_use_apps" and reply["data"]["request_id"] == "r1"
    assert [app["name"] for app in reply["data"]["apps"]] == ["Notes", "Mail"] and seen == ["notes", "mail"]


def test_the_mention_menu_lists_running_regular_apps_but_not_refused_ones(monkeypatch):
    import os

    class _App:
        def __init__(self, name, bundle, pid, policy=0):
            self.name, self.bundle, self.pid, self.policy = name, bundle, pid, policy
        def bundleIdentifier(self): return self.bundle
        def localizedName(self): return self.name
        def processIdentifier(self): return self.pid
        def activationPolicy(self): return self.policy
        def isTerminated(self): return False
        def bundleURL(self): return None

    class _AppKit:
        NSApplicationActivationPolicyRegular = 0

    apps = [_App("Notes", "com.apple.Notes", 11), _App("Calculator", "com.apple.calculator", 12),
            _App("Passwords", "com.apple.Passwords", 13), _App("Dock helper", "com.apple.dock.extra", 14, policy=1),
            _App("Me", "com.example.me", os.getpid())]
    monkeypatch.setattr("openprogram.state.config.read_config",
                        lambda: {"computer_use": {"refused_apps": ["com.apple.Passwords"]}})
    monkeypatch.setattr(mac_frameworks, "_appkit", lambda: _AppKit)
    monkeypatch.setattr(mac_processes, "running_applications", lambda appkit: apps)
    computer = macos.MacComputer.__new__(macos.MacComputer)
    listed = computer.running_apps()
    assert [(a["name"], a["bundle_id"]) for a in listed] == [("Calculator", "com.apple.calculator"),
                                                             ("Notes", "com.apple.Notes")]

    monkeypatch.setattr(macos, "backend", lambda: computer)
    reply = _ws_reply({"running": True, "request_id": "r2"})["data"]
    assert reply["status"] == "ok" and [a["name"] for a in reply["apps"]] == ["Calculator", "Notes"]


def test_app_lookup_rejects_anything_but_a_short_list_of_names():
    assert _ws_reply({"apps": "notes"})["data"]["status"] == "error"
    assert _ws_reply({"apps": ["x"] * 51})["data"]["status"] == "error"


# ----------------------------------------------------------------- audio

def test_recording_limits_default_to_a_minute_and_stop_at_five():
    from openprogram.programs.tools.computer_use.computer_use.desktop.common import audio
    assert audio.seconds(None) == 60 and audio.seconds("12.5") == 12.5
    for bad in (0, -1, 301, "long"):
        with pytest.raises(ValueError):
            audio.seconds(bad)


def test_float_samples_become_a_16_bit_mono_wav_with_their_peak():
    import io
    import wave
    from array import array
    from openprogram.programs.tools.computer_use.computer_use.desktop.common import audio
    data, peak = audio.wav(array("f", [0.0, 0.5, -1.5, 0.25]).tobytes())
    clip = wave.open(io.BytesIO(data))
    assert (clip.getnchannels(), clip.getsampwidth(), clip.getframerate(), clip.getnframes()) == (1, 2, 16000, 4)
    assert array("h", clip.readframes(4)).tolist() == [0, 16383, -32767, 8191] and peak == 1.0


def test_a_recording_keeps_samples_only_up_to_its_limit():
    from openprogram.programs.tools.computer_use.computer_use.desktop.common import audio
    recording = audio.Recording(app="Music", pid=1, limit=1.0)
    recording.take(b"\0" * 4 * 12_000)
    recording.take(b"\0" * 4 * 12_000)
    assert recording.frames == audio.SAMPLE_RATE and recording.seconds == 1.0


def _clip(peak, tmp_path):
    return base.AudioClip(app="Music", path=tmp_path / "a.wav", data=b"RIFF", seconds=2.0, peak=peak)


def test_a_stopped_recording_reports_its_file_and_transcript(monkeypatch, tmp_path):
    from openprogram.interfaces.speech import stt as voice
    monkeypatch.setattr(voice, "transcribe", lambda data: {"text": " 你好 ", "engine": "local", "model": "m"})
    text = window_answers._clip_text(_clip(0.3, tmp_path), transcribe=True)
    assert text == f"Recorded 2 seconds of Music, saved to {tmp_path / 'a.wav'}.\nTranscript (local, m):\n你好"
    assert "silent" in window_answers._clip_text(_clip(0.0, tmp_path), transcribe=True)
    assert "Transcript" not in window_answers._clip_text(_clip(0.3, tmp_path), transcribe=False)

    def broken(data):
        raise voice.VoiceError("No speech engine is set up.")
    monkeypatch.setattr(voice, "transcribe", broken)
    assert window_answers._clip_text(_clip(0.3, tmp_path), transcribe=True).endswith("Not transcribed: No speech engine is set up.")


def test_audio_actions_reach_the_backend_without_a_state_read(fake):
    computer = fake()
    started = []
    computer.start_audio = lambda session, app, limit: started.append((app, limit)) or base.Report("Recording Music")
    assert _run([("start_audio_recording", {"max_duration": 30})], {"app": "Music"}).text == "Recording Music"
    assert started == [("Music", 30)] and computer.settled == 0
    stopped = _run([("stop_audio_recording", {})], {})  # the approval names the app
    assert stopped.is_error and stopped.text.startswith("BAD_ARGUMENT:")
    assert entry.requires_approval(type="window", app="Music", action="start_audio_recording") == \
        "computer_use controls music"


def test_recordings_are_saved_under_the_state_directory_and_pruned(monkeypatch, tmp_path):
    import openprogram.state.paths as paths
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(base, "KEEP_RECORDINGS", 2)
    saved = [base.save_recording("Quick Time", b"x") for _ in range(3)]
    left = sorted(p.name for p in (tmp_path / "computer-use" / "audio").iterdir())
    assert len(left) == 2 and saved[-1].name in left and saved[0].name.endswith("quick-time.wav")


def test_wait_needs_no_state_and_is_bounded(monkeypatch):
    computer = macos.MacComputer()
    monkeypatch.setattr(computer, "resolve_app", lambda query, launch=True: type(
        "App", (), {"processIdentifier": lambda self: 7})())
    assert computer.act("s", "wait", {"app": "Music", "seconds": 0.05}) == "waited 0.05s"
    with pytest.raises(base.ComputerUseError):
        computer.act("s", "wait", {"app": "Music", "seconds": 11})
    assert {"hover", "wait"} <= desktop.ACTING


def test_a_recording_reaches_models_that_hear_audio_and_only_them():
    import base64
    from openprogram.programs._host.execution import ToolReturn, _normalize_result
    from openprogram.models.providers.shared.google import convert_messages
    from openprogram.models.providers.types import (AssistantMessage, Context, Model, ToolCall, ToolResultMessage,
                                             UserMessage)

    result = _normalize_result(ToolReturn(text="Recorded 1 seconds of Chess.", audio=[b"RIFFwav"]), call_id="c1")
    assert [c.type for c in result.content] == ["text", "audio"]
    assert base64.b64decode(result.content[1].data) == b"RIFFwav" and result.content[1].mime_type == "audio/wav"

    def contents(inputs):
        model = Model(id="gemini-2.5-pro", name="g", api="google-generative-ai", provider="google",
                      base_url="", input=inputs)
        calls = [ToolCall(id=f"c{i}", name="computer_use", arguments={}) for i in (1, 2)]
        messages = [UserMessage(content="listen", timestamp=0),
                    AssistantMessage(content=calls, api=model.api, provider=model.provider, model=model.id, timestamp=0),
                    *[ToolResultMessage(tool_call_id=f"c{i}", tool_name="computer_use", content=result.content, timestamp=0)
                      for i in (1, 2)]]
        return convert_messages(model, Context(messages=messages))

    heard = contents(["text", "audio"])
    # Both function responses stay in one turn; the audio follows it.
    assert sum("functionResponse" in p for p in heard[-2]["parts"]) == 2
    assert [p.get("inlineData", {}).get("mimeType") for p in heard[-1]["parts"]] == [None, "audio/wav", None, "audio/wav"]
    deaf = contents(["text", "image"])
    assert not any("inlineData" in p for turn in deaf for p in turn["parts"])


def test_chat_completions_models_see_tool_result_screenshots_and_hear_recordings():
    import base64
    from openprogram.programs._host.execution import ToolReturn, _normalize_result
    from openprogram.models.providers.adapters.openai_completions.openai_completions import _build_messages
    from openprogram.models.providers.types import (AssistantMessage, Context, ImageContent, Model, TextContent, ToolCall,
                                             ToolResultMessage, UserMessage)

    clip = _normalize_result(ToolReturn(text="Recorded 1 seconds of Chess.", audio=[b"RIFFwav"]), call_id="c1").content
    shot = [TextContent(text="Calculator."), ImageContent(data=base64.b64encode(b"png").decode(), mime_type="image/png")]

    def messages(inputs):
        model = Model(id="m", name="m", api="openai-completions", provider="openrouter", base_url="", input=inputs)
        calls = [ToolCall(id=f"c{i}", name="computer_use", arguments={}) for i in (1, 2)]
        return _build_messages(Context(messages=[
            UserMessage(content="look and listen", timestamp=0),
            AssistantMessage(content=calls, api=model.api, provider=model.provider, model=model.id, timestamp=0),
            ToolResultMessage(tool_call_id="c1", tool_name="computer_use", content=clip, timestamp=0),
            ToolResultMessage(tool_call_id="c2", tool_name="computer_use", content=shot, timestamp=0),
            UserMessage(content="next", timestamp=0)]), model)

    both = messages(["text", "image", "audio"])
    # The tool messages stay together after the calls; the media follow as one user turn.
    assert [m["role"] for m in both] == ["user", "assistant", "tool", "tool", "user", "user"]
    assert [p["type"] for p in both[4]["content"]] == ["text", "input_audio", "image_url"]
    assert both[4]["content"][1]["input_audio"]["format"] == "wav"
    assert [p["type"] for p in messages(["text", "image"])[4]["content"]] == ["text", "image_url"]
    assert [m["role"] for m in messages(["text"])] == ["user", "assistant", "tool", "tool", "user"]


# ----------------------------------------------------------------- other Spaces

def _off_space(monkeypatch, insert_result=True):
    computer = macos.MacComputer()
    inserted = []
    monkeypatch.setattr(mac_frameworks, "_frameworks", lambda: (None, None, None, None))
    monkeypatch.setattr(computer, "_keys_reach", lambda pid, state: False)
    monkeypatch.setattr(computer, "_insert", lambda pid, text: inserted.append(text) or insert_result)
    monkeypatch.setattr(computer, "_focused_element", lambda pid: None)
    monkeypatch.setattr(computer, "_refuse_secure_input", lambda: None)
    monkeypatch.setattr(computer, "_post_chord", lambda *a, **k: pytest.fail("keys posted to another Space"))
    monkeypatch.setattr(computer, "_post_text", lambda *a, **k: pytest.fail("keys posted to another Space"))
    return computer, inserted


def test_keys_for_a_window_on_another_space_become_inserted_text_or_an_error(monkeypatch):
    computer, inserted = _off_space(monkeypatch)
    assert "inserted 5 character(s)" in computer._do_type_text(None, 1, None, {"text": "hello"})
    assert "as plain text" in computer._do_paste(None, 1, None, {"text": "<b>hi</b>", "format": "html"})
    assert inserted == ["hello", "hi"]
    with pytest.raises(base.ComputerUseError, match="another Space"):
        computer._do_press_key(None, 1, None, {"key": "Escape"})
    computer, _ = _off_space(monkeypatch, insert_result=False)
    with pytest.raises(base.ComputerUseError, match="takes no inserted text"):
        computer._do_type_text(None, 1, None, {"text": "x"})


class _Sky:
    available = True

    def __init__(self):
        self.searches = []

    def remote_windows(self, pid, wanted):
        self.searches.append(set(wanted))
        return {wid: f"window {wid}" for wid in wanted if wid != 99}


class _Cg:
    kCGWindowListOptionOnScreenOnly = kCGNullWindowID = 0

    @staticmethod
    def CGWindowListCopyWindowInfo(option, window):  # noqa: N802 - Quartz name
        return [{"kCGWindowNumber": 10}]


class _Ax:
    @staticmethod
    def AXUIElementGetPid(root, _):  # noqa: N802 - HIServices name
        return 0, 7


def test_windows_on_other_spaces_are_found_once_per_set_of_window_ids(monkeypatch):
    computer = macos.MacComputer()
    sky = _Sky()
    rows = [
        {"kCGWindowNumber": 10, "kCGWindowOwnerPID": 7, "kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 500, "Height": 400}},
        {"kCGWindowNumber": 11, "kCGWindowOwnerPID": 7, "kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 500, "Height": 400}},
        {"kCGWindowNumber": 12, "kCGWindowOwnerPID": 7, "kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 1512, "Height": 37}},
        {"kCGWindowNumber": 13, "kCGWindowOwnerPID": 8, "kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 500, "Height": 400}},
    ]
    monkeypatch.setattr(mac_skylight, "skylight", lambda: sky)
    monkeypatch.setattr(mac_frameworks, "_frameworks", lambda: (None, _Ax, _Cg, None))
    monkeypatch.setattr(computer, "_window_rows", lambda: rows)
    monkeypatch.setattr(computer, "_ax_window_id", lambda window: 0)
    # 10 is on screen (listed by AXWindows already), 12 is a menu-bar strip, 13 is another app's.
    assert computer._other_space_windows("root", []) == ["window 11"]
    assert computer._other_space_windows("root", []) == ["window 11"]
    assert sky.searches == [{11}]
    rows.append({"kCGWindowNumber": 99, "kCGWindowOwnerPID": 7, "kCGWindowLayer": 0,
                 "kCGWindowBounds": {"Width": 300, "Height": 200}})
    assert computer._other_space_windows("root", []) == ["window 11"]
    assert sky.searches == [{11}, {11, 99}]


def test_a_step_reports_its_screen_point_for_the_agent_cursor(monkeypatch):
    from types import SimpleNamespace
    import openprogram.host.resources.native_window as nw
    computer = macos.MacComputer()
    state = mac_frameworks.WindowState(pid=1, window_title="Calc", window_id=70, has_screenshot=True, scale=2.0,
                              origin=(800.0, 400.0), size=(500, 300))
    app = SimpleNamespace(processIdentifier=lambda: 1, localizedName=lambda: "Calculator")
    points = []
    monkeypatch.setattr(nw, "note_action_point", lambda name, point, action: points.append((name, point, action)))
    monkeypatch.setattr(mac_actions, "CUE_LEAD_SECONDS", 0)
    monkeypatch.setattr(computer, "_onscreen_ids", lambda pid: {70})

    computer._cue(app, state, "click", {"x": 100, "y": 60})
    assert points == [("Calculator", (850.0, 430.0), "click")]

    computer._cue(app, state, "press_key", {"key": "return"})
    assert len(points) == 1, "a step without a target shows no cursor"

    monkeypatch.setattr(computer, "_onscreen_ids", lambda pid: set())
    computer._cue(app, state, "click", {"x": 100, "y": 60})
    assert len(points) == 1, "a window on another Space shows no cursor here"


def test_a_screenshot_pixel_lands_on_the_same_place_in_the_window_wherever_it_is(monkeypatch):
    """Retina scale, window offset, a window that moved, and the window-relative point stamped on the event."""
    # A 2x screenshot of a window at (800, 400): pixel (100, 60) is 50, 30 points into it.
    assert mac_geometry.screen_point(100, 60, origin=(800.0, 400.0), scale=2.0) == (850.0, 430.0)
    # A large window captured below full density (scale 1.5); a window on a display left of the main one.
    assert mac_geometry.screen_point(300, 150, origin=(-1440.0, 120.0), scale=1.5) == (-1240.0, 220.0)
    assert mac_geometry.window_location((850.0, 430.0), (800.0, 400.0)) == (50.0, 30.0)
    assert mac_geometry.window_location((-1240.0, 220.0), (-1440.0, 120.0)) == (200.0, 100.0)
    assert mac_geometry.live_origin([_row(5, 1, x=10, y=20), _row(70, 1, x=640, y=90, w=500, h=300)], 70) == (640.0, 90.0)
    assert mac_geometry.live_origin([_row(70, 1, w=0, h=0)], 70) is None, "a window with no size has no place"
    assert mac_geometry.live_origin([], 70) is None

    computer = macos.MacComputer.__new__(macos.MacComputer)
    state = mac_frameworks.WindowState(pid=1, window_title="Calc", window_id=70, has_screenshot=True, scale=2.0,
                              origin=(800.0, 400.0), size=(500, 300))
    rows = [_row(70, 1, x=640, y=90, w=500, h=300)]

    class _Cg:
        kCGWindowListOptionIncludingWindow = 8

        @staticmethod
        def CGWindowListCopyWindowInfo(option, window_id):
            assert (option, window_id) == (8, 70)
            return rows

    monkeypatch.setattr(mac_frameworks, "_frameworks", lambda: (None, None, _Cg, None))
    # The user moved the window after it was read: the step follows it.
    computer._follow_window(state)
    assert state.origin == (640.0, 90.0)
    point = computer._point(state, 100, 60)
    assert point == (690.0, 120.0)

    stamped = []

    class _Sky:
        def stamp_mouse(self, event, **fields):
            stamped.append(fields)

        def post(self, pid, event):
            stamped.append(("post", pid))

    computer._post_mouse(_Sky(), None, state, 1, "event", point, click_state=1, button=0, subtype=0, group=9)
    assert stamped[0]["location"] == (50.0, 30.0), "the event carries the point inside the window, not on screen"
    assert stamped[0]["window_id"] == 70 and stamped[0]["pid"] == 1
    assert stamped[1] == ("post", 1), "pointer input goes to the target process only"


def _row(wid, pid, x=0, y=0, w=100, h=100, layer=0):
    return {"kCGWindowNumber": wid, "kCGWindowOwnerPID": pid, "kCGWindowLayer": layer,
            "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}}


def test_the_windows_a_click_would_bring_forward_past_are_listed_deepest_first():
    rows = [_row(1, 10), _row(2, 20, layer=25), _row(3, 30), _row(4, 40, x=500), _row(5, 50), _row(6, 60)]
    # Front to back: 1 is the front app's, 2 a menu-bar layer, 4 lies beside the target, 6 lies below it.
    assert [r["kCGWindowNumber"] for r in mac_geometry.covering_rows(rows, 5, front_pid=10)] == [3]
    assert [r["kCGWindowNumber"] for r in mac_geometry.covering_rows(rows, 5, front_pid=99)] == [3, 1]
    assert mac_geometry.covering_rows(rows, 77, front_pid=10) == []


def test_a_background_click_restores_the_windows_it_brought_forward_past(monkeypatch):
    computer = macos.MacComputer.__new__(macos.MacComputer)
    order = []
    monkeypatch.setattr(computer, "_covering", lambda state: ["deep", "near"])
    monkeypatch.setattr(computer, "_perform", lambda window, action: order.append((window, action)))
    computer._restore_order(computer._covering(None))
    assert order == [("deep", "AXRaise"), ("near", "AXRaise")]


# ----------------------------------------------------------------- new windows and file dialogs

def _windowed(monkeypatch, attrs, ids):
    computer = macos.MacComputer.__new__(macos.MacComputer)
    monkeypatch.setattr(computer, "_attr", lambda element, name: attrs.get((element, name)))
    monkeypatch.setattr(computer, "_windows", lambda root: attrs.get((root, "AXWindows")) or [])
    monkeypatch.setattr(computer, "_ax_window_id", lambda window: ids.get(window, 0))
    return computer


def test_the_next_read_follows_a_window_that_opened_in_front_since_the_last_one(monkeypatch):
    attrs = {("app", "AXWindows"): ["doc", "open"], ("app", "AXFocusedWindow"): "open"}
    computer = _windowed(monkeypatch, attrs, {"doc": 10, "open": 11})
    seen = mac_frameworks.WindowState(pid=1, window_title="doc", window_id=10, window_keys=frozenset({10}))
    assert computer._new_front_window("app", seen) == "open"
    # A window the model already knew of, or a state with no record of windows, keeps the read where it was.
    assert computer._new_front_window("app", mac_frameworks.WindowState(
        pid=1, window_title="doc", window_id=10, window_keys=frozenset({10, 11}))) is None
    assert computer._new_front_window("app", mac_frameworks.WindowState(pid=1, window_title="doc", window_id=10)) is None


def test_file_dialogs_are_told_apart_and_refuse_keys_that_would_be_dropped(monkeypatch):
    attrs = {("panel", "AXIdentifier"): "open-panel", ("doc", "AXChildren"): ["sheet"],
             ("sheet", "AXRole"): "AXSheet", ("sheet", "AXIdentifier"): "save-panel",
             ("plain", "AXChildren"): ["button"], ("button", "AXRole"): "AXButton"}
    computer = _windowed(monkeypatch, attrs, {})
    assert computer._file_panel("panel") and computer._file_panel("doc")
    assert not computer._file_panel("plain") and not computer._file_panel(None)

    from types import SimpleNamespace
    ax = SimpleNamespace(AXUIElementCreateApplication=lambda pid: "app")
    monkeypatch.setattr(mac_frameworks, "_frameworks", lambda: (None, ax, None, None))
    app = SimpleNamespace(localizedName=lambda: "Preview")
    attrs[("app", "AXFocusedWindow")] = "panel"
    with pytest.raises(base.ComputerUseError, match="system file dialog open.*open_file"):
        computer._refuse_file_panel(app, 1)
    attrs[("app", "AXFocusedWindow")] = "plain"
    computer._refuse_file_panel(app, 1)


def _panel_rows():
    # PDF Expert's ⌘O as the window server lists it: the panel service draws the dialog
    # off screen, and the app shows it in a window of its own with the same bounds.
    def row(wid, pid, title, x, y, w, h, onscreen):
        return {"kCGWindowNumber": wid, "kCGWindowOwnerPID": pid, "kCGWindowName": title, "kCGWindowLayer": 0,
                "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}, "kCGWindowIsOnscreen": onscreen}
    return [row(9427, 30701, "", 0, 482, 54, 54, False), row(9426, 30701, "Open", 356, 138, 800, 448, False),
            row(9425, 79143, "Open", 356, 138, 800, 448, True), row(9374, 79143, "Home", 0, 38, 1512, 848, True),
            row(6088, 74864, "", 0, 482, 54, 54, False), row(500, 236, "Other", 356, 138, 800, 448, True)]


def test_an_out_of_process_file_dialog_is_found_from_the_window_list():
    assert mac_geometry.panel_hosts(_panel_rows(), 79143, {30701, 74864}) == [(30701, 9426, 9425, (356.0, 138.0, 800.0, 448.0))]
    # Another app's window with the same bounds is not the dialog's; without a service there is none.
    assert mac_geometry.panel_hosts(_panel_rows(), 236, {30701}) == [(30701, 9426, 500, (356.0, 138.0, 800.0, 448.0))]
    assert mac_geometry.panel_hosts(_panel_rows(), 79143, set()) == []
    assert mac_geometry.panel_hosts(_panel_rows(), 1, {30701}) == []


def test_the_service_side_dialog_reports_the_window_id_the_app_shows_it_in(monkeypatch):
    from types import SimpleNamespace
    computer = macos.MacComputer.__new__(macos.MacComputer)
    monkeypatch.setattr(computer, "_window_rows", _panel_rows)
    monkeypatch.setattr(computer, "_is_panel_service", lambda pid: pid in (30701, 74864))
    assert computer._panels_for(79143) == [(30701, 9426, 9425, (356.0, 138.0, 800.0, 448.0))]
    monkeypatch.setattr(mac_skylight, "skylight", lambda: SimpleNamespace(available=True, window_id=lambda w: {
        "service open": 9426, "home": 9374}[w]))
    assert computer._ax_window_id("service open") == 9425 and computer._ax_window_id("home") == 9374


def test_the_dialog_is_read_from_the_service_when_the_app_no_longer_lists_it(monkeypatch):
    from types import SimpleNamespace
    computer = macos.MacComputer.__new__(macos.MacComputer)
    ax = SimpleNamespace(AXUIElementGetPid=lambda root, _: (0, 79143),
                         AXUIElementCreateApplication=lambda pid: f"app {pid}")
    monkeypatch.setattr(mac_frameworks, "_frameworks", lambda: (None, ax, None, None))
    monkeypatch.setattr(mac_skylight, "skylight", lambda: SimpleNamespace(available=True, window_id=lambda w: {
        "service open": 9426, "service other": 1, "home": 9374}.get(w)))
    monkeypatch.setattr(computer, "_window_rows", _panel_rows)
    monkeypatch.setattr(computer, "_is_panel_service", lambda pid: pid == 30701)
    monkeypatch.setattr(computer, "_frame", lambda w: {"home": (0, 38, 1512, 848)}.get(w))
    monkeypatch.setattr(computer, "_attr", lambda element, name: {
        ("app 30701", "AXWindows"): ["service other", "service open"]}.get((element, name)))
    assert computer._service_panel_windows("app 79143", ["home"]) == ["service open"]
    # While the app lists the dialog itself (by its id or its frame), nothing is added.
    assert computer._service_panel_windows("app 79143", ["home", "service open"]) == []
    monkeypatch.setattr(computer, "_frame", lambda w: {"open": (356, 138, 800, 448)}.get(w))
    assert computer._service_panel_windows("app 79143", ["open"]) == []


def test_the_next_read_follows_a_dialog_that_opened_without_taking_the_focus(monkeypatch):
    # PDF Expert in the background: after ⌘O its document window keeps the focus.
    attrs = {("app", "AXWindows"): ["doc", "open"], ("app", "AXFocusedWindow"): "doc",
             ("open", "AXSubrole"): "AXDialog"}
    computer = _windowed(monkeypatch, attrs, {"doc": 10})
    seen = mac_frameworks.WindowState(pid=1, window_title="doc", window_id=10, window_keys=frozenset({10}))
    assert computer._new_front_window("app", seen) == "open"
    attrs[("open", "AXSubrole")] = "AXStandardWindow"
    assert computer._new_front_window("app", seen) is None


def test_a_file_dialog_without_an_identifier_is_known_by_the_window_the_service_draws(monkeypatch):
    attrs = {("doc", "AXChildren"): ["scroll", "open"], ("open", "AXRole"): "AXWindow",
             ("scroll", "AXRole"): "AXScrollArea"}
    computer = _windowed(monkeypatch, attrs, {"open": 9425})
    monkeypatch.setattr(computer, "_frame", lambda w: None)
    assert not computer._file_panel("doc")
    computer._live_panels = {79143: [(9425, (356.0, 138.0, 800.0, 448.0))]}
    assert computer._file_panel("open") and computer._panel_in("doc") == "open"
    # Without a window id, its frame tells it apart.
    monkeypatch.setattr(computer, "_ax_window_id", lambda w: 0)
    monkeypatch.setattr(computer, "_frame", lambda w: (356.0, 138.0, 800.0, 448.0) if w == "open" else None)
    assert computer._file_panel("open") and not computer._file_panel("scroll")


def test_keys_are_refused_while_a_file_dialog_is_open_anywhere_it_takes_them(monkeypatch):
    from types import SimpleNamespace
    attrs = {("app", "AXWindows"): ["doc", "open"], ("app", "AXFocusedWindow"): "doc",
             ("open", "AXIdentifier"): "open-panel"}
    computer = _windowed(monkeypatch, attrs, {})
    ax = SimpleNamespace(AXUIElementCreateApplication=lambda pid: "app")
    monkeypatch.setattr(mac_frameworks, "_frameworks", lambda: (None, ax, None, None))
    app = SimpleNamespace(localizedName=lambda: "PDF Expert")
    # A dialog window of its own, though the document window has the focus.
    with pytest.raises(base.ComputerUseError, match="file dialog open.*open_file"):
        computer._refuse_file_panel(app, 1)
    # A sheet on a document window that is not focused leaves keys to the focused one.
    attrs.update({("app", "AXWindows"): ["doc", "other"], ("other", "AXChildren"): ["sheet"],
                  ("sheet", "AXRole"): "AXSheet", ("sheet", "AXIdentifier"): "open-panel"})
    del attrs[("open", "AXIdentifier")]
    computer._refuse_file_panel(app, 1)
    # A dialog on screen that Accessibility lost still takes no keys.
    computer._live_panels = {1: [(9425, (0.0, 0.0, 800.0, 448.0))]}
    monkeypatch.setattr(computer, "_frame", lambda w: None)
    with pytest.raises(base.ComputerUseError, match="file dialog open"):
        computer._refuse_file_panel(app, 1)


def test_a_missing_window_title_lists_the_windows_and_points_at_open_file(monkeypatch):
    from types import SimpleNamespace
    attrs = {("app", "AXWindows"): ["doc"], ("doc", "AXTitle"): "0main"}
    computer = _windowed(monkeypatch, attrs, {})
    ax = SimpleNamespace(AXUIElementGetPid=lambda root, _: (0, 7))
    monkeypatch.setattr(mac_frameworks, "_frameworks", lambda: (None, ax, None, None))
    with pytest.raises(base.ComputerUseError, match='its windows are "0main"$'):
        computer._pick_window("app", "Open")
    computer._live_panels = {7: [(9425, (0.0, 0.0, 800.0, 448.0))]}
    with pytest.raises(base.ComputerUseError, match="file dialog is on screen.*open_file"):
        computer._pick_window("app", "Open")


def test_open_file_cancels_an_open_file_dialog_first(monkeypatch):
    computer = macos.MacComputer.__new__(macos.MacComputer)
    pressed, panels = [], ["panel"]
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(computer, "_open_panel", lambda pid: panels[0] if panels else None)
    monkeypatch.setattr(computer, "_attr", lambda element, name: {
        ("panel", "AXChildren"): ["group"], ("group", "AXChildren"): ["ok", "cancel"],
        ("ok", "AXRole"): "AXButton", ("ok", "AXTitle"): "Open",
        ("cancel", "AXRole"): "AXButton", ("cancel", "AXTitle"): "取消"}.get((element, name)))

    def perform(element, action):
        pressed.append((element, action))
        panels.clear()
        return 0
    monkeypatch.setattr(computer, "_perform", perform)
    assert computer._cancel_file_panel(1, "PDF Expert") is True and pressed == [("cancel", "AXPress")]
    assert computer._cancel_file_panel(1, "PDF Expert") is False
    panels.append(True)
    with pytest.raises(base.ComputerUseError, match="does not reach it"):
        computer._cancel_file_panel(1, "PDF Expert")


@pytest.mark.parametrize("name, mode, bundle, message", [
    ("", None, "com.apple.Preview", "path is required"),
    ("relative.pdf", None, "com.apple.Preview", "must be absolute"),
    ("missing.pdf", None, "com.apple.Preview", "does not exist"),
    ("tool", 0o755, "com.apple.Preview", "could run it"),
    ("run.command", 0o644, "com.apple.Preview", "could run it"),
    ("notes.txt", 0o644, "com.apple.Terminal", "could run it"),
])
def test_open_file_opens_existing_documents_only(monkeypatch, tmp_path, name, mode, bundle, message):
    from types import SimpleNamespace
    computer = macos.MacComputer.__new__(macos.MacComputer)
    monkeypatch.setattr(mac_frameworks, "_appkit", lambda: pytest.fail("nothing is opened"))
    path = name
    if mode is not None:
        target = tmp_path / name
        target.write_text("x")
        target.chmod(mode)
        path = str(target)
    elif name == "missing.pdf":
        path = str(tmp_path / name)
    app = SimpleNamespace(localizedName=lambda: "App", bundleIdentifier=lambda: bundle)
    with pytest.raises(base.ComputerUseError, match=message):
        computer._do_open_file(app, 1, None, {"path": path})


def test_open_file_is_an_app_action_that_takes_a_path_and_only_macos_documents_it():
    from openprogram.programs.tools.computer_use.computer_use.actions import vocabulary
    assert "open_file" in desktop.ACTING and "open_file" in macos.MacComputer.STATELESS
    assert vocabulary.FAMILIES["window"].arguments("open_file") == ("path",)
    assert "open_file" in _app("Darwin").extra_actions() and "open_file(path)" in _app("Darwin").note()
    for system in ("Linux", "Windows"):
        assert "open_file" not in _app(system).extra_actions()
        assert "There is no open_file" in _app(system).note()


# The suite's conftest makes the screen read as unlocked for every test; this is the function itself.
_SCREEN_LOCKED = mac_frameworks._screen_locked


@pytest.mark.parametrize("session,locked", [
    ({"CGSSessionScreenIsLocked": True, "kCGSSessionOnConsoleKey": True}, True),
    ({"kCGSSessionOnConsoleKey": True}, False),     # the key is there only while the screen is locked
    (None, False),                                  # no window server session: nothing to tell
])
def test_the_screen_lock_is_read_from_the_login_session(monkeypatch, session, locked):
    from types import SimpleNamespace
    cg = SimpleNamespace(CGSessionCopyCurrentDictionary=lambda: session)
    monkeypatch.setattr(mac_frameworks._Frameworks, "get", classmethod(lambda cls: (None, None, cg, None)))
    assert _SCREEN_LOCKED() is locked

    def broken():
        raise RuntimeError("no window server")
    monkeypatch.setattr(cg, "CGSessionCopyCurrentDictionary", broken)
    assert _SCREEN_LOCKED() is False


def test_a_locked_screen_stops_reads_and_steps_and_says_the_windows_are_still_open(monkeypatch):
    """Locked, macOS answers nothing about windows: every app read as "Window: none open"."""
    monkeypatch.setattr(mac_frameworks, "_screen_locked", lambda: True)
    monkeypatch.setattr(mac_frameworks, "_frameworks", lambda: pytest.fail("nothing is asked of the app"))
    computer = macos.MacComputer.__new__(macos.MacComputer)
    for call in (lambda: computer.get_state("s", "Notes"),
                 lambda: computer.act("s", "click", {"app": "Notes", "element": 1}),
                 lambda: computer.list_windows("Notes"),
                 lambda: computer.zoom("s", "Notes", 0, 0, 10, 10)):
        with pytest.raises(base.NotDispatched, match="screen is locked.*still open"):
            call()


def test_a_locked_screen_is_reported_as_nothing_sent(monkeypatch):
    monkeypatch.setattr(mac_frameworks, "_screen_locked", lambda: True)
    monkeypatch.setattr(mac_frameworks, "_frameworks", lambda: pytest.fail("nothing is asked of the app"))
    computer = macos.MacComputer.__new__(macos.MacComputer)
    computer.app_key = lambda query: "pid:1"
    monkeypatch.setattr(macos, "backend", lambda: computer)
    for steps in ([("get_state", {})], [("click", {"element": 3})]):
        result = _run(steps, {"app": "Notes"})
        assert result.is_error and result.text.startswith("Error: The Mac's screen is locked.")
        assert result.json_data == {"action_dispatched": False}
