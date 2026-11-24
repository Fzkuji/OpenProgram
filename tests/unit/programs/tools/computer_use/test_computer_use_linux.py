"""The Linux computer_use backend's host-independent parts; the AT-SPI and X11
paths themselves run against real apps in an X11 container (see the design record)."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from openprogram.host import machines

from openprogram.programs.tools.computer_use.computer_use.desktop import backends, linux, ocr
from openprogram.programs.tools.computer_use.computer_use.desktop.base import ComputerUseError
from openprogram.programs.tools.computer_use.computer_use.providers import desktop as provider


def test_tesseract_words_are_joined_into_lines_in_reading_order():
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "5\t1\t1\t1\t2\t1\t10\t60\t50\t20\t91\tSecond",
        "5\t1\t1\t1\t1\t1\t10\t10\t40\t20\t96\tHello",
        "5\t1\t1\t1\t1\t2\t60\t12\t50\t18\t95\tworld",
        "5\t1\t1\t1\t3\t1\t10\t90\t50\t20\t12\tnoise",   # too unsure to keep
        "4\t1\t1\t1\t1\t0\t10\t10\t100\t20\t-1\t",        # a line row, not a word
    ]
    boxes = ocr.boxes_from_tesseract("\n".join([header, *rows]))
    assert [b.text for b in boxes] == ["Hello world", "Second"]
    assert (boxes[0].x, boxes[0].y, boxes[0].width, boxes[0].height) == (10, 10, 100, 20)


def test_key_chords_use_xdotool_names_with_mac_modifiers_mapped():
    chord = linux.LinuxComputer._chord
    assert chord("cmd+c") == ["super+c"]
    assert chord("option+shift+Tab") == ["alt+shift+Tab"]
    assert chord("Enter") == ["Return"]
    assert chord("ctrl++") == ["ctrl+plus"]
    with pytest.raises(ComputerUseError):
        chord("hyper+x")


class _Acc:
    def __init__(self, name, description=""):
        self.name, self.description = name, description

    def get_name(self):
        return self.name

    def get_description(self):
        return self.description


def test_gtk4_class_names_are_not_labels():
    computer = linux.LinuxComputer()
    assert computer._label(_Acc("AdwGizmo"), "filler") == ""
    assert computer._label(_Acc("HistoryView"), "filler") == ""
    assert computer._label(_Acc("GtkMenuButton"), "push button") == ""
    assert computer._label(_Acc("Save"), "push button") == "Save"
    assert computer._label(_Acc("MyBrand"), "push button") == "MyBrand"  # a real name on a control stays
    assert computer._label(_Acc("", "Close the window"), "push button") == "Close the window"


def test_support_needs_linux_with_x11(monkeypatch):
    monkeypatch.setattr(linux.platform, "system", lambda: "Linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert "X11" in linux.support()["reason"]
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("OPENPROGRAM_ALLOW_XWAYLAND", raising=False)
    state = linux.support()
    assert not state["available"] and "Wayland" in state["reason"]


def test_other_hosts_have_no_backend(monkeypatch):
    monkeypatch.setattr(backends.platform, "system", lambda: "FreeBSD")
    assert backends.support() == {"available": False, "reason": "computer_use supports macOS, Linux (X11) and Windows"}


def test_the_linux_note_says_what_the_platform_lacks_and_how_keys_are_named(local_machine):
    app = provider.AppProvider(local_machine, system="Linux")
    note = app.note()
    assert "X11" in note and "Super key" in note and "There is no open_file" in note
    assert "open_file" not in app.extra_actions()
    assert "clipboard" not in app.capabilities


def test_recording_finds_the_streams_an_app_or_its_children_play(monkeypatch):
    listed = """Sink Input #12
\tDriver: protocol-native.c
\tProperties:
\t\tapplication.name = "paplay"
\t\tapplication.process.id = "301"

Sink Input #13
\tProperties:
\t\tapplication.process.id = "999"
"""
    monkeypatch.setattr(linux.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout=listed, stderr=""))
    assert linux.LinuxComputer._streams({300, 301}) == [12]
    assert linux.LinuxComputer._streams({5}) == []


def test_refused_apps_are_the_owners_list_and_openprogram(monkeypatch):
    refused = linux.LinuxComputer._refused
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {})
    assert not refused({"desktop_id": "org.gnome.seahorse.Application", "executable": "seahorse"})
    monkeypatch.setattr("openprogram.state.config.read_config",
                        lambda: {"computer_use": {"refused_apps": ["seahorse"]}})
    assert refused({"desktop_id": "org.gnome.seahorse.Application", "executable": "seahorse"})
    assert refused({"desktop_id": "ai.openprogram.desktop", "executable": "openprogram"})
    assert not refused({"desktop_id": "org.gnome.Calculator", "executable": "gnome-calculator"})


def test_windows_that_stood_over_the_operated_one_are_raised_again_lowest_first(monkeypatch):
    ran: list[tuple] = []
    monkeypatch.setattr(linux, "_x", lambda *args, **kwargs: ran.append(args) or "")
    linux._put_under(30, ["10", "30", "40", "50"])
    assert ran == [("windowraise", "40"), ("windowraise", "50")]
    ran.clear()
    linux._put_under(99, ["10", "30"])
    assert not ran


@pytest.fixture
def local_machine():
    return machines.local_machine({})


def _linux_apps(monkeypatch, running):
    computer = object.__new__(linux.LinuxComputer)
    computer._icons = {}
    installed = [{"name": "GNOME Terminal", "desktop_id": "org.gnome.Terminal", "executable": "gnome-terminal-server",
                  "icon": ""},
                 {"name": "Calculator", "desktop_id": "org.gnome.Calculator", "executable": "gnome-calculator",
                  "icon": ""}]
    monkeypatch.setattr(computer, "_installed", lambda: installed, raising=False)
    monkeypatch.setattr(computer, "_running", lambda: running, raising=False)
    monkeypatch.setattr(linux.LinuxComputer, "_refused", classmethod(lambda cls, app: False))
    return computer


def test_a_running_app_without_a_desktop_entry_is_named_for_itself_not_for_a_match_in_part(monkeypatch):
    computer = _linux_apps(monkeypatch, [linux.App(pid=300, name="xterm")])
    monkeypatch.setattr(linux, "_proc_name", lambda pid: "xterm")
    info = computer.app_info("term")
    assert info["running"] and info["name"] == "xterm" and not info["installed"]
    assert computer.app_info("calc")["name"] == "Calculator"  # not running: the row a launch would start


def test_the_rule_name_matches_desktop_entries_exactly_and_reads_no_bus(monkeypatch):
    computer = _linux_apps(monkeypatch, [])
    monkeypatch.setattr(computer, "_running", lambda: pytest.fail("the accessibility bus is slow"), raising=False)
    monkeypatch.setattr(linux, "_proc_name", lambda pid: {300: "xterm", 301: "gnome-calculator"}.get(pid, ""))
    monkeypatch.setattr(linux, "_proc_exe", lambda pid: {300: "xterm", 301: "gnome-calculator"}.get(pid, ""))
    assert computer.app_name("org.gnome.Calculator") == "Calculator"
    assert computer.app_name("gnome-calculator") == "Calculator"
    assert computer.app_name("301") == "Calculator" and computer.app_name("300") == "xterm"
    assert computer.app_name("term") == "" and computer.app_name("999") == ""



def test_the_desktop_entries_are_read_again_only_when_a_folder_changes(monkeypatch, tmp_path):
    (tmp_path / "home" / "applications").mkdir(parents=True)
    (tmp_path / "shared" / "applications").mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "shared"))
    computer = linux.LinuxComputer()
    scans = []
    monkeypatch.setattr(computer, "_scan_installed", lambda: scans.append(1) or [{"name": "A"}])
    assert computer._installed() == [{"name": "A"}] and computer._installed() == [{"name": "A"}]
    assert len(scans) == 1
    folder = tmp_path / "shared" / "applications"
    stat = os.stat(folder)
    os.utime(folder, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    computer._installed()
    assert len(scans) == 2
