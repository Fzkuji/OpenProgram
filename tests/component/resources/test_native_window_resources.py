"""The chat preview learns which native app window the Agent operates."""
import json

import pytest

import openprogram.host.resources.native_window as nw


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    nw._latest.clear()
    nw.forward_to_parent(None)
    sent = []
    from openprogram.interfaces.server import server as _s
    monkeypatch.setattr(_s, "_broadcast", lambda payload: sent.append(json.loads(payload)), raising=False)
    monkeypatch.setattr(nw, "_owner", lambda: ("conv", "exec-1"))
    yield sent
    nw._latest.clear()
    nw.forward_to_parent(None)


def _step(tmp_path, window_id=7, frame=b"\x89PNG one"):
    shot = tmp_path / f"shot-{window_id}.png"
    shot.write_bytes(frame)
    return {
        "status": "applied",
        "target": {"window_id": window_id, "pid": 42, "app_name": "WeChat", "launch_time": "t"},
        "step": {"observation": {"screenshot_artifact": str(shot)}},
    }


def test_a_step_on_a_window_announces_it_with_its_frame(tmp_path, _clean):
    nw.note_operated_window(_step(tmp_path))

    assert _clean == [{"type": "native.window", "data": {
        "session_id": "conv", "execution_id": "exec-1", "window_id": 7, "pid": 42,
        "app_name": "WeChat", "sequence": 1, "at": _clean[0]["data"]["at"], "has_frame": True,
    }}]
    assert nw.latest_window("conv")["frame"] == b"\x89PNG one"


def test_a_step_without_a_capture_keeps_the_frame_of_the_same_window(tmp_path, _clean):
    nw.note_operated_window(_step(tmp_path))
    nw.note_operated_window({"target": {"window_id": 7, "pid": 42, "app_name": "WeChat"}})
    assert nw.latest_window("conv")["frame"] == b"\x89PNG one"
    assert nw.latest_window("conv")["sequence"] == 2

    nw.note_operated_window({"target": {"window_id": 9, "pid": 42, "app_name": "WeChat"}})
    assert nw.latest_window("conv")["frame"] is None, "another window's frame is never shown"


@pytest.mark.parametrize("output", [
    None, "text", {"target": None}, {"target": {"window_id": "7", "pid": 42, "app_name": "x"}},
    {"target": {"window_id": 7, "pid": 42}},
])
def test_results_that_name_no_window_are_ignored(output, _clean):
    nw.note_operated_window(output)
    assert _clean == []
    assert nw.latest_window("conv") is None


def test_a_subprocess_hands_the_window_to_its_parent(tmp_path, _clean):
    forwarded = []
    nw.forward_to_parent(forwarded.append)

    nw.note_operated_window(_step(tmp_path))

    assert _clean == [] and nw.latest_window("conv") is None
    assert forwarded[0]["frame"] == b"\x89PNG one"
    # The parent names the conversation, whatever the child believed.
    nw.record_operated_window(forwarded[0], session_id="parent-conv", execution_id="exec-p")
    entry = nw.latest_window("parent-conv")
    assert (entry["window_id"], entry["execution_id"], entry["frame"]) == (7, "exec-p", b"\x89PNG one")


def test_the_frame_route_serves_only_the_current_window_of_an_allowed_session(tmp_path, _clean, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.interfaces.server.routes.execution import processes

    monkeypatch.setattr(processes, "_actor_and_session", lambda request: ({}, "conv"))
    monkeypatch.setattr(processes, "_authorize", lambda request, sid, record=None, stop=False: (
        None if sid == "conv" else (_ for _ in ()).throw(KeyError(sid))))
    nw.note_operated_window(_step(tmp_path))
    app = FastAPI()
    processes.register(app)
    with TestClient(app) as client:
        frame = client.get("/api/session/conv/native-window/frame?window_id=7")
        stale = client.get("/api/session/conv/native-window/frame?window_id=9")
        other = client.get("/api/session/other/native-window/frame?window_id=7")
        state = client.get("/api/session/conv/native-window")
    assert frame.status_code == 200 and frame.content == b"\x89PNG one"
    assert frame.headers["content-type"] == "image/png"
    assert stale.status_code == 404
    assert other.status_code == 404
    assert state.json()["window"]["app_name"] == "WeChat"
    assert "frame" not in state.json()["window"]


def test_a_step_point_is_announced_for_the_agent_cursor(_clean):
    nw.note_action_point("Calculator", (412.5, 300.0), "click")
    nw.note_action_point("Calculator", (420.0, 310.0), "click")

    frames = [frame for frame in _clean if frame["type"] == "native.action"]
    assert [(f["data"]["x"], f["data"]["y"], f["data"]["app_name"]) for f in frames] == [
        (412.5, 300.0, "Calculator"), (420.0, 310.0, "Calculator")]
    assert all(f["data"]["session_id"] == "conv" for f in frames)
    assert frames[1]["data"]["sequence"] > frames[0]["data"]["sequence"]


def test_a_subprocess_hands_its_step_point_to_the_worker(_clean):
    forwarded = []
    nw.forward_to_parent(forwarded.append)
    nw.note_action_point("Notes", (1.0, 2.0), "hover")
    assert _clean == []
    assert forwarded[0]["kind"] == "action" and (forwarded[0]["x"], forwarded[0]["y"]) == (1.0, 2.0)

    nw.record_action_point(forwarded[0], session_id="parent-conv")
    assert _clean[0]["type"] == "native.action" and _clean[0]["data"]["session_id"] == "parent-conv"


def test_a_controlled_window_place_is_announced_for_the_control_overlay(_clean):
    place = {"window_id": 7, "app_name": "Calculator", "active": True, "frame": [100, 200, 400, 300],
             "buttons": [8, 6, 52, 14], "occluders": [[1, 2, 3, 4], ["bad"]]}
    nw.note_control(place, owner=("conv", "run"))
    nw.note_control({**place, "active": False, "frame": None}, owner=("conv", "run"))
    frames = [frame["data"] for frame in _clean if frame["type"] == "native.control"]
    assert frames[0]["session_id"] == "conv" and frames[0]["active"] is True
    assert frames[0]["frame"] == [100.0, 200.0, 400.0, 300.0] and frames[0]["occluders"] == [[1.0, 2.0, 3.0, 4.0]]
    assert frames[1]["active"] is False and frames[1]["frame"] is None


def test_a_subprocess_hands_its_controlled_window_to_the_worker(_clean):
    forwarded = []
    nw.forward_to_parent(forwarded.append)
    nw.note_control({"window_id": 7, "active": True, "frame": [0, 0, 10, 10]}, owner=("child", None))
    assert _clean == [] and forwarded[0]["kind"] == "control"
    nw.record_control(forwarded[0], session_id="parent-conv")
    assert _clean[0]["type"] == "native.control" and _clean[0]["data"]["session_id"] == "parent-conv"
