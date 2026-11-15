"""Pane video recording: the worker names the file, asks the App and words the result."""
from __future__ import annotations

import pytest

from openprogram.host.resources import desktop_link
from openprogram.programs.applications.browser import video


@pytest.fixture
def app(monkeypatch, tmp_path):
    """A desktop link that records what is asked and answers from a script."""
    sent: list[dict] = []
    replies: list[dict] = []

    def request(command, timeout):
        sent.append(command)
        return replies.pop(0) if replies else {"ok": False, "error": "timeout"}

    monkeypatch.setattr(desktop_link, "request", request)
    monkeypatch.setattr(video, "folder", lambda: tmp_path / "video")
    return sent, replies


def test_the_limit_is_sixty_seconds_and_at_most_six_hundred():
    assert video.seconds(None) == 60
    assert video.seconds("90") == 90
    for bad in (0, -1, 601, "soon"):
        with pytest.raises(ValueError):
            video.seconds(bad)


def test_panes_lists_what_the_window_shows(app):
    sent, replies = app
    replies.append({"ok": True, "panes": [{"id": "t1", "kind": "conversation", "title": "Plan",
                                           "width": 800, "height": 600}]})
    result = video.run("panes", {}, "s1")
    assert sent == [{"op": "record_panes", "session_id": "s1"}]
    assert result["ok"] and "- t1: conversation · Plan · 800×600" in result["done"]


def test_start_names_a_file_and_stop_returns_it(app, tmp_path):
    sent, replies = app
    replies.append({"ok": True, "recording": {"pane": "t1", "width": 1600, "height": 1200, "fps": 60}})
    started = video.run("start_video_recording", {"pane": "conversation", "max_duration": 30}, "s1")
    assert started["ok"] and "1600×1200" in started["done"] and "30 seconds" in started["done"]
    command = sent[0]
    assert command["op"] == "record_start" and command["pane"] == "conversation"
    assert command["max_duration"] == 30 and command["session_id"] == "s1"
    path = command["path"]
    assert path.startswith(str(tmp_path / "video")) and path.endswith("-conversation.mp4")

    open(path, "wb").write(b"mp4")
    replies.append({"ok": True, "recording": {"pane": "t1", "path": path, "seconds": 12.4, "width": 1600,
                                              "height": 1200, "fps": 60, "bytes": 3, "ended": "stopped"}})
    stopped = video.run("stop_video_recording", {}, "s1")
    assert stopped["ok"] and stopped["path"] == path and stopped["seconds"] == 12.4
    assert (stopped["width"], stopped["height"]) == (1600, 1200)
    assert f"saved to {path}." in stopped["done"] and "ended before" not in stopped["done"]


def test_a_recording_that_ended_by_itself_says_why(app, tmp_path):
    sent, replies = app
    path = tmp_path / "a.mp4"
    path.write_bytes(b"x")
    replies.append({"ok": True, "recording": {"pane": "t1", "path": str(path), "seconds": 60, "width": 2,
                                              "height": 2, "fps": 60, "bytes": 1, "ended": "limit"}})
    assert "It ended before this call: limit." in video.run("stop_video_recording", {}, "s1")["done"]


def test_failures_are_results_the_agent_can_act_on(app):
    sent, replies = app
    assert video.run("start_video_recording", {}, "s1")["reason_code"] == "invalid_argument"
    assert "between 0 and 600" in video.run("start_video_recording", {"pane": "window", "max_duration": 900}, "s1")["error"]
    assert sent == [], "nothing is asked of the App for a call that is wrong by itself"

    # No window answered.
    assert video.run("panes", {}, "s1")["reason_code"] == "desktop_app_unavailable"

    replies.append({"ok": False, "reason_code": "unknown_pane", "error": 'no pane "x" is on screen',
                    "panes": [{"id": "t1", "kind": "file", "title": "a.md", "width": 10, "height": 20}]})
    unknown = video.run("start_video_recording", {"pane": "x"}, "s1")
    assert unknown["reason_code"] == "unknown_pane" and "Panes: t1 (file, a.md, 10×20)" in unknown["error"]

    replies.append({"ok": False, "reason_code": "not_recording",
                    "error": "Nothing is being recorded; call start_video_recording first."})
    assert video.run("stop_video_recording", {}, "s1")["error"].startswith("Nothing is being recorded")

    replies.append({"ok": True, "recording": {"path": "/nowhere/a.mp4"}})
    assert video.run("stop_video_recording", {}, "s1")["reason_code"] == "recording_missing"

    replies.append({"ok": True, "recording": {"discarded": True}})
    assert video.run("stop_video_recording", {"discard": True}, "s1")["discarded"] is True
    assert sent[-1]["discard"] is True


def test_only_the_newest_recordings_are_kept(app, tmp_path):
    folder = tmp_path / "video"
    folder.mkdir()
    for number in range(25):
        (folder / f"old-{number:02d}.mp4").write_bytes(b"x")
    video.new_path("window")
    assert len(list(folder.glob("*.mp4"))) == video.KEEP_RECORDINGS - 1
