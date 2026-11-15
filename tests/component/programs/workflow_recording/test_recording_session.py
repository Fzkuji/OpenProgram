"""A recording's life cycle, typing groups, screenshots and HTTP routes.

The routes and the chat message composition reach the recorder through
the ``workflow_recording`` service; without it they answer as if
recording did not exist.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.programs._host import services as _services
from openprogram.programs.applications.workflow_recording import session
from openprogram.programs.applications.workflow_recording.service import SERVICE, WorkflowRecordingService
from openprogram.interfaces.server.routes import self_updates
from openprogram.interfaces.server.routes.execution import recordings as routes


class FakeCapture:
    def __init__(self, rec):
        self.rec = rec
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "recordings_root", lambda: tmp_path)
    _services.load_programs()
    assert _services.lookup(SERVICE) is not None
    yield tmp_path
    for live in list(session._recordings.values()):
        live.finish("done")
    session._recordings.clear()


def _events(rec):
    return [json.loads(line) for line in (rec.directory / "events.jsonl").read_text().splitlines()]


ELEMENT = {"role": "text field", "title": "Amount", "value": "4"}


def test_typing_is_grouped_per_element_and_backspace_edits_it(root):
    rec = session.start("workflow", capture_factory=FakeCapture, root=root)
    assert rec._capture.started
    for char in "421":
        rec.add({"type": "type", "app": {"name": "Numbers"}, "element": {**ELEMENT, "value": char}, "char": char})
    rec.add({"type": "type", "app": {"name": "Numbers"}, "element": ELEMENT, "backspace": True})
    rec.set_typing_value(ELEMENT, "42.00")
    rec.add({"type": "key", "keys": "return", "app": {"name": "Numbers"}})
    rec.add({"type": "type", "app": {"name": "Numbers"}, "element": {"role": "text field", "secure": True},
             "secure": True})
    rec.finish("done")
    assert rec._capture.stopped
    typed, key, password = _events(rec)
    assert typed["type"] == "type" and typed["text"] == "42" and typed["value"] == "42.00"
    assert key["keys"] == "return"
    assert password == {**password, "type": "type", "secure": True} and "text" not in password
    meta = json.loads((rec.directory / "session.json").read_text())
    assert meta["end_reason"] == "done" and meta["steps"] == 3


def test_screenshots_follow_clicks_and_respect_the_gap(root, monkeypatch):
    rec = session.start("workflow", capture_factory=FakeCapture, root=root)
    shots = []
    rec.add({"type": "click"}, screenshot=lambda: shots.append(1) or b"one")
    rec.add({"type": "click"}, screenshot=lambda: shots.append(2) or b"two")   # inside the gap
    rec.add({"type": "key", "keys": "cmd+c"}, screenshot=lambda: shots.append(3) or b"three")
    monkeypatch.setattr(rec, "_last_shot", rec._last_shot - session.SCREENSHOT_GAP)
    rec.add({"type": "key", "keys": "return"}, screenshot=lambda: shots.append(4) or b"four")
    rec.finish("done")
    assert shots == [1, 4]
    first, second, _, fourth = _events(rec)
    assert first["image"] == "images/0001.jpg" and "image" not in second
    assert fourth["image"] == "images/0002.jpg"
    assert (rec.directory / "images" / "0002.jpg").read_bytes() == b"four"


def test_one_recording_at_a_time_and_discard_deletes_it(root):
    rec = session.start("workflow", capture_factory=FakeCapture, root=root)
    with pytest.raises(session.RecordingError):
        session.start("workflow", capture_factory=FakeCapture, root=root)
    rec.discard()
    assert not rec.directory.exists()
    with pytest.raises(session.RecordingError):
        session.directory_of(rec.id, root=root)
    with pytest.raises(session.RecordingError):
        session.directory_of("../etc", root=root)
    session.start("workflow", capture_factory=FakeCapture, root=root).finish()


def test_narration_after_the_end_is_ignored(root):
    rec = session.start("workflow", capture_factory=FakeCapture, root=root)
    rec.add_narration(1200, "  the  date  changes  ")
    rec.finish()
    rec.add_narration(None, "late")
    assert _events(rec) == [{"t": 1200, "type": "say", "text": "the date changes"}]


def _client(monkeypatch, root):
    monkeypatch.setattr(self_updates, "require_owner", lambda request: None)
    monkeypatch.setattr(WorkflowRecordingService, "start", staticmethod(
        lambda purpose: session.start(purpose, capture_factory=FakeCapture, root=root)))
    app = FastAPI()
    routes.register(app)
    return TestClient(app, base_url="http://127.0.0.1:18100", headers={"origin": "http://127.0.0.1:18100"},
                      client=("127.0.0.1", 4000))


def test_routes_start_narrate_stop_and_serve_steps(root, monkeypatch):
    with _client(monkeypatch, root) as client:
        started = client.post("/api/recordings", json={"purpose": "workflow"}).json()
        rid = started["id"]
        assert started["max_seconds"] == session.MAX_SECONDS
        assert client.post("/api/recordings", json={}).status_code == 409
        live = session.get(rid)
        live.add({"type": "click", "app": {"name": "Finder"}, "element": {"role": "button", "title": "Go"}},
                 screenshot=lambda: b"jpeg")
        assert client.post(f"/api/recordings/{rid}/narration", json={"t": 0, "text": "start here"}).json() == \
            {"ok": True}
        assert client.get(f"/api/recordings/{rid}").json()["state"] == "recording"
        summary = client.post(f"/api/recordings/{rid}/stop").json()
        assert summary["steps"] == 1 and summary["images"] == 1
        steps = client.get(f"/api/recordings/{rid}/steps").json()["rows"]
        assert [row["type"] for row in steps] == ["say", "click"]
        assert steps[1]["image"] == "0001.jpg"
        image = client.get(f"/api/recordings/{rid}/images/0001.jpg")
        assert image.status_code == 200 and image.content == b"jpeg"
        assert client.get(f"/api/recordings/{rid}/images/..%2Fsession.json").status_code == 404
        assert client.get("/api/recordings/ffffff").status_code == 404


def test_events_sharing_a_millisecond_read_back_narration_first_then_in_write_order(tmp_path):
    from openprogram.programs.applications.workflow_recording import describe

    lines = [
        {"t": 5, "type": "click"},
        {"t": 0, "type": "click", "n": 1},
        {"t": 0, "type": "say", "text": "here"},
        {"t": 0, "type": "key", "n": 2},
    ]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(line) for line in lines))
    assert [(e["type"], e.get("n")) for e in describe.read_events(tmp_path)] == [
        ("say", None), ("click", 1), ("key", 2), ("click", None),
    ]


def test_routes_refuse_remote_callers(root, monkeypatch):
    monkeypatch.setattr(self_updates, "require_owner", lambda request: None)
    app = FastAPI()
    routes.register(app)
    with TestClient(app, base_url="https://remote.example", client=("203.0.113.4", 4000)) as client:
        assert client.post("/api/recordings", json={}).status_code == 403


def test_a_missing_grant_names_the_capability(root, monkeypatch):
    monkeypatch.setattr(self_updates, "require_owner", lambda request: None)

    def refuse(purpose):
        raise session.RecordingError("no screen recording", "screen_recording")

    monkeypatch.setattr(WorkflowRecordingService, "start", staticmethod(refuse))
    app = FastAPI()
    routes.register(app)
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 4000)) as client:
        response = client.post("/api/recordings", json={})
    assert response.status_code == 409
    assert response.json() == {"error": "no screen recording", "capability": "screen_recording"}


def test_chat_appends_the_recording_and_its_images(root):
    from openprogram.interfaces.server.ws_actions.chat import _title_from_text, _with_recording
    rec = session.start("workflow", capture_factory=FakeCapture, root=root)
    rec.add({"type": "click", "app": {"name": "Finder"}}, screenshot=lambda: b"jpeg")
    placeholder = f'<recorded-demonstration id="{rec.id}" steps="1"></recorded-demonstration>'
    text, meta, images = _with_recording(f"every Monday\n\n{placeholder}", rec.id)
    assert rec.state == "finished"           # sending finishes a recording still running
    assert text.startswith("every Monday\n\n<recorded-demonstration")
    assert text.count("<recorded-demonstration") == 1
    assert meta == {"id": rec.id, "purpose": "workflow", "steps": 1, "duration_ms": meta["duration_ms"],
                    "images": 1}
    assert len(images) == 1 and images[0]["media_type"] == "image/jpeg"
    assert _title_from_text(text) == "every Monday"
    assert _title_from_text(text.split("\n\n", 1)[1]) == "Recorded demonstration"
    with pytest.raises(ValueError):
        _with_recording("hi", "ffffff")


@pytest.fixture
def disabled(root, monkeypatch):
    """The application disabled: its service withdrawn."""
    monkeypatch.delitem(_services._provided, SERVICE)
    assert _services.lookup(SERVICE) is None


def test_without_the_application_the_routes_are_absent(disabled, monkeypatch):
    monkeypatch.setattr(self_updates, "require_owner", lambda request: None)
    app = FastAPI()
    routes.register(app)
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 4000)) as client:
        for method, path in (("post", "/api/recordings"), ("get", "/api/recordings/abc123"),
                             ("post", "/api/recordings/abc123/stop"),
                             ("get", "/api/recordings/abc123/images/0001.jpg")):
            response = getattr(client, method)(path)
            assert response.status_code == 404
            assert response.json()["reason_code"] == "workflow_recording_not_enabled"


def test_without_the_application_a_message_with_a_recording_is_refused(disabled):
    from openprogram.interfaces.server.ws_actions.chat import _title_from_text, _with_recording
    with pytest.raises(ValueError, match="not enabled"):
        _with_recording("hi", "abc123")
    assert _title_from_text("hello") == "hello"


def test_disabling_the_application_withdraws_the_service_and_stops_a_recording(root, monkeypatch):
    from openprogram.programs._host import registry
    from openprogram.programs._host.ui import catalog

    rec = session.start("workflow", capture_factory=FakeCapture, root=root)
    try:
        with monkeypatch.context() as off:
            off.setattr(catalog, "builtin_enabled", lambda name: name != "workflow_recording")
            assert registry.sync_builtin_application("workflow_recording") is None
            assert _services.lookup(SERVICE) is None
            assert rec.state == "finished" and rec.end_reason == "disabled"
    finally:
        assert registry.sync_builtin_application("workflow_recording") is not None
    assert _services.lookup(SERVICE) is not None
