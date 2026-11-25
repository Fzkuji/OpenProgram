"""What the host does for an application package: its recordings, host calls and manifest checks.

A recording is played without a microphone or ScreenCaptureKit: chunks are
posted with ``take`` and a fake computer-sound source pushes samples; the
application's operations are a recording ``_runner`` that answers at once.
"""
from __future__ import annotations

import json
from array import array

import pytest

from openprogram.programs._host.ui import audio, catalog, host_calls, state

INSTANCE = "b" * 64
APP = {"id": "test.recorder", "title": "Recorder", "capabilities": ["storage.files", "audio.record", "speech"],
       "recording": {"ingest": "ingest", "finish": "finish"}}


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "home", lambda: tmp_path)
    monkeypatch.setattr(state, "get_instance", lambda key: {"id": key, "app_id": APP["id"], "project_id": ""})
    monkeypatch.setattr(catalog, "get", lambda app_id: APP)
    runs: list[tuple[str, dict, str]] = []

    def runner(instance_id, operation, value, request_key):
        runs.append((operation, value, request_key))
        if value.get("seq") == 1:
            return {"status": "failed", "error": "could not convert"}
        return {"status": "completed"}

    monkeypatch.setattr(audio, "_runner", runner)
    yield runs
    with audio._lock:
        live = list(audio._recordings.values())
        audio._recordings.clear()
    for recording in live:
        recording.cancel()


class FakeSource:
    def __init__(self, fail=None):
        self.fail, self.sink = fail, None

    def start(self, sink):
        if self.fail:
            raise self.fail
        self.sink = sink

    def stop(self):
        pass



def test_chunks_reach_ingest_in_order_then_finish_gets_the_final_status(host, tmp_path):
    started = audio.start(INSTANCE, "m1", tracks=["mic"])
    assert started["state"] == "recording" and started["tracks"] == ["mic"]
    for seq in range(3):
        audio.take(INSTANCE, started["id"], "mic", seq, seq * 30_000, b"chunk %d" % seq, "webm")
    audio.take(INSTANCE, started["id"], "mic", 2, 60_000, b"again", "webm")       # a retried upload
    audio.stop(INSTANCE, started["id"])
    assert audio._recordings[INSTANCE].settled.wait(5)

    ingests = [value for operation, value, _ in host if operation == "ingest"]
    assert [(v["track"], v["seq"], v["t_ms"], v["path"]) for v in ingests] == [
        ("mic", 0, 0, "recordings/m1/mic/00000.webm"),
        ("mic", 1, 30_000, "recordings/m1/mic/00001.webm"),
        ("mic", 2, 60_000, "recordings/m1/mic/00002.webm")]
    assert (tmp_path / "files" / INSTANCE / "recordings/m1/mic/00002.webm").read_bytes() == b"chunk 2"
    [(operation, value, key)] = [run for run in host if run[0] == "finish"]
    assert value["key"] == "m1" and key.endswith(":finish")
    assert value["recording"]["state"] == "processing" and value["recording"]["end_reason"] == "done"
    final = audio.status(INSTANCE)
    assert (final["state"], final["delivered"], final["failed"]) == ("done", 2, 1)
    assert final["errors"] == ["could not convert"] and final["received"] == {"mic": 3}


def test_a_stopped_recording_takes_no_more_chunks_and_one_instance_records_one_thing(host):
    started = audio.start(INSTANCE, "m1", tracks=["mic"])
    with pytest.raises(audio.RecordingError, match="already recording"):
        audio.start(INSTANCE, "m2", tracks=["mic"])
    audio.stop(INSTANCE, started["id"])
    with pytest.raises(audio.RecordingError, match="stopped"):
        audio.take(INSTANCE, started["id"], "mic", 0, 0, b"late", "webm")
    with pytest.raises(FileNotFoundError):
        audio.take(INSTANCE, "not-it", "mic", 0, 0, b"x", "webm")
    for bad in ("../x", "", "a/b"):
        with pytest.raises(audio.RecordingError):
            audio.start(INSTANCE, bad, tracks=["mic"])


def test_the_computers_sound_becomes_wav_chunks_and_its_absence_is_explained(host):
    source = FakeSource()
    started = audio.start(INSTANCE, "m1", tracks=["mic", "system"], system_source=lambda: source)
    assert started["system_audio"] == "recording"
    from openprogram.host._platform import system_sound
    source.sink(array("f", [0.5] * int(31 * system_sound.RATE)).tobytes())
    audio.stop(INSTANCE, started["id"])
    assert audio._recordings[INSTANCE].settled.wait(5)
    paths = [value["path"] for operation, value, _ in host if operation == "ingest"]
    assert paths == ["recordings/m1/system/00000.wav", "recordings/m1/system/00001.wav"]

    refused = FakeSource(system_sound.Unavailable("permission", "The user declined TCCs"))
    again = audio.start(INSTANCE, "m2", tracks=["mic", "system"], system_source=lambda: refused)
    assert (again["system_audio"], again["system_audio_reason"]) == ("unavailable", "permission")


def test_disabling_the_application_cancels_without_finishing(host):
    started = audio.start(INSTANCE, "m1", tracks=["mic"])
    audio.cancel_application(APP["id"])
    assert audio.status(INSTANCE)["state"] == "cancelled"
    with pytest.raises(audio.RecordingError):
        audio.take(INSTANCE, started["id"], "mic", 0, 0, b"x", "webm")
    assert not any(operation == "finish" for operation, _, _ in host)


def test_an_application_without_audio_record_cannot_record(host, monkeypatch):
    monkeypatch.setattr(catalog, "get", lambda app_id: {**APP, "capabilities": ["storage.files"]})
    with pytest.raises(audio.RecordingError, match="audio.record"):
        audio.start(INSTANCE, "m1")


# ---------------------------------------------------------------- host calls

def test_host_calls_need_their_capability_and_stay_inside_the_files(host, monkeypatch):
    from openprogram.runtime import speech_link
    instance = {"id": INSTANCE, "app_id": APP["id"]}
    with pytest.raises(host_calls.HostCallError, match="requires conversation"):
        host_calls.handle(instance, APP, "conversation.open", {"title": "x"})
    with pytest.raises(host_calls.HostCallError, match="unknown host call"):
        host_calls.handle(instance, APP, "shell.run", {})
    with pytest.raises((host_calls.HostCallError, ValueError)):
        host_calls.handle(instance, APP, "speech.transcribe", {"path": "../../state.sqlite"})

    heard = []
    monkeypatch.setattr(speech_link, "transcribe_file",
                        lambda path, language="": heard.append((path, language)) or {"segments": []})
    (state.files_root(INSTANCE) / "a.wav").write_bytes(b"x")
    assert host_calls.handle(instance, APP, "speech.transcribe", {"path": "a.wav", "language": "en"}) == {"segments": []}
    assert heard == [(str(state.files_root(INSTANCE) / "a.wav"), "en")]

    def unavailable(chunks):
        raise speech_link.DiarizationUnavailable("no_model", "the model is not downloaded")
    monkeypatch.setattr(speech_link, "diarize", unavailable)
    answer = host_calls.handle(instance, APP, "speech.diarize", {"chunks": [{"t_ms": 0, "path": "a.wav"}]})
    assert (answer["available"], answer["reason"]) == (False, "no_model")


def test_conversation_ask_only_reaches_conversations_the_application_opened(host):
    instance = {"id": INSTANCE, "app_id": APP["id"]}
    app = {**APP, "capabilities": [*APP["capabilities"], "conversation"]}
    with pytest.raises(host_calls.HostCallError, match="did not open"):
        host_calls.handle(instance, app, "conversation.ask", {"session_id": "someone_elses", "text": "hi"})
    state.remember_conversation(INSTANCE, "application_1")
    assert state.started_conversation(INSTANCE, "application_1")
    assert not state.started_conversation("c" * 64, "application_1")


# ---------------------------------------------------------------- manifest

def _package(tmp_path, **changes):
    raw = {"id": "test.pkg", "title": "Pkg", "version": "1.0.0",
           "backend": {"kind": "python", "entry": "backend:operations"},
           "operations": {"ingest": {"agent": False, "input": {"type": "object"}, "output": {"type": "object"}}},
           "capabilities": ["storage.files", "audio.record"], "recording": {"ingest": "ingest"}}
    raw.update(changes)
    (tmp_path / "application.json").write_text(json.dumps(raw))
    (tmp_path / "backend.py").write_text("operations = {}\n")
    (tmp_path / "index.html").write_text("<!doctype html>")
    return tmp_path


def test_the_manifest_checks_what_recording_and_speech_need(tmp_path):
    assert catalog.manifest(_package(tmp_path))["recording"] == {"ingest": "ingest"}
    with pytest.raises(ValueError, match="storage.files"):
        catalog.manifest(_package(tmp_path, capabilities=["speech"]))
    with pytest.raises(ValueError, match="recording.ingest"):
        catalog.manifest(_package(tmp_path, recording={"ingest": "missing"}))
    with pytest.raises(ValueError, match="recording.ingest"):
        catalog.manifest(_package(tmp_path, recording={"ingest": "ingest", "finish": "missing"}))
