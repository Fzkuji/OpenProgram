"""What the App's screenshot tool asks the Worker: windows to snap to and text on a selection.

Routes in openprogram/interfaces/server/routes/screenshot.py; the window list in
openprogram/host/_platform/screen_windows.py; Windows' own OCR in
openprogram/host/_platform/windows_ocr.py.
"""
from __future__ import annotations

import json
import platform

import pytest

from openprogram.host._platform import screen_windows, windows_ocr
from openprogram.interfaces.server.routes import screenshot


def test_excluded_ids_keep_only_numbers():
    assert screenshot.excluded_ids("12, 7,abc,,-3") == {12, 7}
    assert screenshot.excluded_ids(",".join(str(n) for n in range(100))) == set(range(screenshot.MAX_EXCLUDED))


def test_png_size_reads_the_header():
    header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (640).to_bytes(4, "big") + (480).to_bytes(4, "big")
    assert screenshot.png_size(header + b"rest") == (640, 480)
    with pytest.raises(ValueError):
        screenshot.png_size(b"GIF89a" + b"\x00" * 30)


def test_windows_ocr_lines_join_cjk_and_drop_blanks():
    output = json.dumps([
        {"text": "签 名 校 验 失 败", "box": [10, 20, 100, 14]},
        {"text": "Error code 4012", "box": [10, 40, 90, 12]},
        {"text": "  ", "box": [0, 0, 1, 1]},
        {"text": "no box"},
    ])
    assert windows_ocr.lines_from_json(output) == [
        {"text": "签名校验失败", "box": [10.0, 20.0, 100.0, 14.0]},
        {"text": "Error code 4012", "box": [10.0, 40.0, 90.0, 12.0]},
    ]
    # PowerShell writes a single line as an object, not a list.
    assert windows_ocr.lines_from_json(json.dumps({"text": "A", "box": [1, 2, 3, 4]})) == [
        {"text": "A", "box": [1.0, 2.0, 3.0, 4.0]}]
    assert windows_ocr.lines_from_json("not json") == []
    assert windows_ocr.tidy("Mac 上的 截 图") == "Mac 上的截图"


def test_a_wayland_session_lists_no_windows(monkeypatch):
    monkeypatch.setattr(screen_windows.platform, "system", lambda: "Linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    assert screen_windows.visible_windows() == {"pixels": False, "windows": []}


@pytest.mark.skipif(platform.system() != "Darwin", reason="CoreGraphics")
def test_macos_lists_windows_front_first_in_points():
    listing = screen_windows.visible_windows()
    assert listing["pixels"] is False
    for window in listing["windows"]:
        assert set(window) == {"id", "pid", "app", "title", "frame"}
        assert window["frame"][2] >= screen_windows.MIN_SIZE and window["frame"][3] >= screen_windows.MIN_SIZE
    if listing["windows"]:
        first = listing["windows"][0]["id"]
        assert first not in [w["id"] for w in screen_windows.visible_windows({first})["windows"]]
