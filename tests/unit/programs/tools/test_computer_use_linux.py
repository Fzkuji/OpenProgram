"""The Linux computer_use backend's host-independent parts; the AT-SPI and X11
paths themselves run against real apps in an X11 container (see the design record)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.programs.tools.computer_use.computer_use_agent.desktop import backends, linux, ocr
from openprogram.programs.tools.computer_use.computer_use_agent.desktop.base import ComputerUseError
from openprogram.programs.tools.computer_use.computer_use_agent.targets import app as tool


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


def test_linux_guide_replaces_every_macos_only_sentence():
    for mac, linux_text in tool._LINUX_GUIDE:
        assert mac in tool._GUIDE or linux_text in tool._GUIDE


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
