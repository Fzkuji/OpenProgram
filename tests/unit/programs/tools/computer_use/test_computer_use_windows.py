"""The Windows computer_use backend's host-independent parts; the UI Automation
and Win32 paths need a Windows host (see the design record)."""
from __future__ import annotations

import ctypes
import json
import struct
import zlib

import pytest

from openprogram.programs.tools.computer_use.computer_use_agent.desktop import backends, windows, windows_audio
from openprogram.programs.tools.computer_use.computer_use_agent.desktop.base import ComputerUseError
from openprogram.programs.tools.computer_use.computer_use_agent.targets import app as tool


def _png_pixels(png: bytes) -> tuple[int, int, bytes]:
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    idat = png.index(b"IDAT")
    size = struct.unpack(">I", png[idat - 4:idat])[0]
    return width, height, zlib.decompress(png[idat + 4:idat + 4 + size])


def test_png_turns_gdi_bgra_rows_into_rgb():
    blue_red = bytes([255, 0, 0, 255, 0, 0, 255, 255])  # BGRA: blue, then red
    width, height, raw = _png_pixels(windows.png_encode(2, 1, blue_red))
    assert (width, height) == (2, 1)
    assert raw == bytes([0, 0, 0, 255, 255, 0, 0])  # filter byte, then RGB blue, RGB red


def test_key_chords_map_to_virtual_keys():
    assert windows.key_chord("Return") == ([], 0x0D, "")
    assert windows.key_chord("ctrl+shift+Tab") == ([0x11, 0x10], 0x09, "")
    assert windows.key_chord("cmd+a") == ([0x5B], ord("A"), "a")
    assert windows.key_chord("F5") == ([], 0x74, "")
    assert windows.key_chord("ctrl++") == ([0x11], 0, "+")
    assert windows.key_chord("plus") == ([], 0, "+")
    assert windows.key_chord("shift") == ([], 0x10, "")
    with pytest.raises(ComputerUseError):
        windows.key_chord("hyper+x")
    with pytest.raises(ComputerUseError):
        windows.key_chord("ctrl+nosuchkey")


def test_html_clipboard_offsets_point_at_the_fragment():
    payload = windows.cf_html("<b>héllo</b>")
    header = dict(line.split(":", 1) for line in payload.split(b"\r\n")[1:5] for line in [line.decode()])
    start, end = int(header["StartFragment"]), int(header["EndFragment"])
    assert payload[start:end].decode() == "<b>héllo</b>"
    assert payload[int(header["StartHTML"]):int(header["EndHTML"])].startswith(b"<html>")
    assert payload[int(header["EndHTML"]):] == b""


def test_start_apps_are_read_from_powershell_json():
    rows = [{"Name": "Notepad", "AppID": "{1AC14E77}\\notepad.exe"},
            {"Name": "Calculator", "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
            {"Name": "", "AppID": "x"}]
    assert windows.parse_start_apps(json.dumps(rows)) == [
        {"name": "Calculator", "app_id": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
        {"name": "Notepad", "app_id": "{1AC14E77}\\notepad.exe"},
    ]
    assert windows.parse_start_apps(json.dumps(rows[0])) == [{"name": "Notepad", "app_id": rows[0]["AppID"]}]
    assert windows.parse_start_apps("not json") == []


def test_control_types_cover_the_uia_range_with_tree_role_names():
    assert set(windows.CONTROL_TYPES) == set(range(50000, 50041))
    assert windows.CONTROL_TYPES[50000] == "button" and windows.CONTROL_TYPES[50004] == "edit"


def test_refused_apps_are_the_owners_list_and_openprogram(monkeypatch):
    refused = windows.WindowsComputer._refused
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {})
    assert not refused(windows.App(pid=2, name="consent", exe=r"C:\Windows\System32\consent.exe"))
    monkeypatch.setattr("openprogram.state.config.read_config",
                        lambda: {"computer_use": {"refused_apps": ["credentialuibroker", "consent"]}})
    assert refused(windows.App(pid=1, name="Windows Security", exe=r"C:\Windows\System32\CredentialUIBroker.exe"))
    assert refused(windows.App(pid=2, name="consent", exe=r"C:\Windows\System32\consent.exe"))
    assert refused({"name": "OpenProgram", "app_id": "ai.openprogram.desktop"})
    assert not refused(windows.App(pid=3, name="Notepad", exe=r"C:\Windows\notepad.exe"))


def test_support_reports_hosts_other_than_windows(monkeypatch):
    monkeypatch.setattr(windows.platform, "system", lambda: "Darwin")
    assert not windows.support()["available"]
    monkeypatch.setattr(backends.platform, "system", lambda: "Windows")
    monkeypatch.setattr(windows, "support", lambda: {"available": False, "reason": "no UIA"})
    assert backends.support() == {"available": False, "reason": "no UIA"}
    with pytest.raises(ComputerUseError, match="no UIA"):
        backends.backend()


def test_windows_guide_replaces_every_macos_only_sentence():
    for mac, windows_text in tool._WINDOWS_GUIDE:
        assert mac in tool._GUIDE or windows_text in tool._GUIDE


def test_recording_format_is_the_shared_16k_mono_float():
    fmt = windows_audio.recorder_format()
    assert ctypes.sizeof(fmt) == 18
    assert (fmt.wFormatTag, fmt.nChannels, fmt.nSamplesPerSec, fmt.wBitsPerSample, fmt.nBlockAlign) == \
        (3, 1, 16_000, 32, 4)
    clip = windows_audio.Recording(app="x", pid=1, limit=0.001)
    clip.take(bytes(4 * 100))
    assert clip.frames == 16 and clip.seconds == pytest.approx(0.001)


def test_a_window_brought_forward_for_input_goes_back_under_the_window_it_stood_under(monkeypatch):
    from openprogram.host._platform import front

    USERS, OPERATED, ABOVE = 100, 200, 150
    calls: list[tuple] = []

    class User32:
        foreground = USERS

        def GetForegroundWindow(self):
            return self.foreground

        def GetWindow(self, hwnd, which):
            return ABOVE if (hwnd, which) == (OPERATED, 3) else 0

        def GetCursorPos(self, point):
            return True

        def SetCursorPos(self, x, y):
            return True

        def IsWindow(self, hwnd):
            return True

        def SetWindowPos(self, hwnd, after, x, y, w, h, flags):
            calls.append(("place", hwnd, after, flags))
            return True

    user32 = User32()
    monkeypatch.setattr(windows, "_user32", lambda: user32)
    monkeypatch.setattr(windows._Forward, "restore", staticmethod(
        lambda hwnd: (calls.append(("front", hwnd)), setattr(user32, "foreground", hwnd))))
    monkeypatch.setattr(windows.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(windows.ctypes, "byref", lambda value: value, raising=False)
    wintypes = type("wintypes", (), {"POINT": lambda: type("P", (), {"x": 5, "y": 6})()})
    monkeypatch.setitem(__import__("sys").modules, "ctypes.wintypes", wintypes)
    monkeypatch.setattr(windows.ctypes, "wintypes", wintypes, raising=False)

    with windows._Forward(OPERATED):
        assert front._standing["forward"] == 1
    assert front._standing["forward"] == 0
    # Forward, the user's window back in front, then the operated one under its old neighbour without activating.
    assert calls == [("front", OPERATED), ("front", USERS), ("place", OPERATED, ABOVE, 0x13)]
