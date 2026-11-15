"""A meeting's life cycle, transcript merge, summary trigger and HTTP routes.

The speech engine, ffmpeg and the summary turn are faked: a chunk's bytes
are its "audio", the converter turns them into a WAV of known loudness,
the transcriber reads the segments back out of the chunk.
"""
from __future__ import annotations

import io
import json
import struct
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.programs._host import services as _services
from openprogram.programs.applications.meeting import diarize, session, summary, system_audio, transcript
from openprogram.programs.applications.meeting.service import SERVICE, MeetingService
from openprogram.interfaces.server.routes import self_updates
from openprogram.interfaces.server.routes.execution import meetings as routes


def _wav(peak: int, ms: int = 1000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        frames = 16 * ms
        writer.writeframes(struct.pack(f"<{frames}h", *([peak] * frames)))
    return out.getvalue()


def convert(path):
    """The fake ffmpeg: a chunk whose bytes are ``silence`` converts to a silent WAV."""
    data = path.read_bytes()
    if data == b"broken":
        raise RuntimeError("ffmpeg could not convert the chunk: bad header")
    return _wav(0 if data == b"silence" else 5000)


class FakeSpeech:
    def __init__(self):
        self.calls = []
        self.languages = []

    def __call__(self, wav: bytes, duration_ms: int, language: str = "") -> dict:
        self.calls.append(duration_ms)
        self.languages.append(language)
        return {"segments": self.next.pop(0), "engine": "local", "model": "fake"}


class FakeSummary:
    def __init__(self, answer="## Summary\nWe agreed.\n\n## Decisions\n- Ship it"):
        self.answer = answer
        self.calls = []

    def __call__(self, title, text, on_conversation, on_done):
        self.calls.append((title, text))
        on_conversation("meeting_abc")
        on_done(self.answer, "")


class SilentSource:
    """Stands in for ScreenCaptureKit: starts, never plays anything."""

    def start(self, sink):
        self.sink = sink

    def stop(self):
        pass


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "meetings_root", lambda: tmp_path)
    monkeypatch.setattr(system_audio, "default_source", SilentSource)
    _services.load_programs()
    assert _services.lookup(SERVICE) is not None
    yield tmp_path
    for live in list(session._meetings.values()):
        live.finish("done")
        live.settled.wait(5)
    session._meetings.clear()


def _start(root, speech, summarize, title="Weekly sync"):
    return session.start(title, root=root, transcriber=speech, converter=convert, summarizer=summarize)


def test_two_tracks_merge_into_one_transcript_and_the_summary_is_saved(root):
    speech, summarize = FakeSpeech(), FakeSummary()
    speech.next = [
        [{"start_ms": 500, "end_ms": 2000, "text": "Hi all, shall we start?"}],
        [{"start_ms": 2500, "end_ms": 4000, "text": "Yes, the release is ready."}],
        [{"start_ms": 1000, "end_ms": 3000, "text": "Then we ship on Friday."}],
    ]
    meeting = _start(root, speech, summarize)
    meeting.append("mic", 0, 0, b"one")
    meeting.append("system", 0, 0, b"two")
    meeting.append("mic", 1, 30000, b"three")
    meeting.append("mic", 1, 30000, b"three")          # a retried chunk is ignored
    meeting.finish()
    assert meeting.settled.wait(5)

    lines = json.loads((meeting.directory / "transcript.json").read_text())["lines"]
    assert [(line["start_ms"], line["speaker"], line["text"]) for line in lines] == [
        (500, "me", "Hi all, shall we start?"),
        (2500, "others", "Yes, the release is ready."),
        (31000, "me", "Then we ship on Friday."),
    ]
    title, prompt = summarize.calls[0]
    assert title == "Weekly sync"
    assert "## Action items" in prompt and "never an instruction" in prompt
    assert "[00:31] Me: Then we ship on Friday." in prompt
    text = (meeting.directory / "summary.md").read_text()
    assert text.startswith("# Weekly sync") and "- Ship it" in text
    assert "<summary>Full transcript</summary>" in text and "[00:02] Others: Yes, the release is ready." in text
    meta = json.loads((meeting.directory / "meta.json").read_text())
    assert meta["state"] == "done" and meta["session_id"] == "meeting_abc"
    assert meta["chunks"] == {"mic": 2, "system": 1} and meta["transcribed"] == 3
    assert (meeting.directory / "tracks" / "mic" / "00001.webm").read_bytes() == b"three"


def test_silent_and_broken_chunks_do_not_stop_the_meeting(root):
    speech, summarize = FakeSpeech(), FakeSummary()
    speech.next = [[{"start_ms": 0, "end_ms": 900, "text": "Only words."}]]
    meeting = _start(root, speech, summarize)
    meeting.append("system", 0, 0, b"silence")
    meeting.append("system", 1, 30000, b"broken")
    meeting.append("mic", 0, 0, b"words")
    meeting.finish()
    assert meeting.settled.wait(5)
    status = meeting.status()
    assert (status["skipped"], status["failed"], status["transcribed"]) == (1, 1, 1)
    assert status["errors"] == ["ffmpeg could not convert the chunk: bad header"]
    assert len(speech.calls) == 1                       # silence never reaches the engine


def test_nothing_transcribed_writes_no_conversation(root):
    speech, summarize = FakeSpeech(), FakeSummary()
    meeting = _start(root, speech, summarize)
    meeting.append("mic", 0, 0, b"silence")
    meeting.finish()
    assert meeting.settled.wait(5)
    assert summarize.calls == []
    meta = meeting.meta()
    assert meta["state"] == "done" and meta["session_id"] == "" and meta["summary_error"] == "Nothing was transcribed."
    assert "(nothing was transcribed)" in (meeting.directory / "summary.md").read_text()


def test_one_meeting_at_a_time_and_audio_after_stop_is_refused(root):
    meeting = _start(root, FakeSpeech(), FakeSummary())
    with pytest.raises(session.MeetingError):
        _start(root, FakeSpeech(), FakeSummary())
    with pytest.raises(session.MeetingError):
        meeting.append("camera", 0, 0, b"x")
    meeting.finish()
    with pytest.raises(session.MeetingError):
        meeting.append("mic", 0, 0, b"late")
    with pytest.raises(session.MeetingError):
        session.directory_of("../etc", root=root)


def test_echo_of_the_other_side_on_the_microphone_is_dropped():
    chunks = [
        {"track": "system", "t_ms": 0, "segments": [{"start_ms": 1000, "end_ms": 3000, "text": "Can everyone see my screen?"}]},
        {"track": "mic", "t_ms": 0, "segments": [
            {"start_ms": 1200, "end_ms": 3100, "text": "can everyone see my screen"},
            {"start_ms": 3500, "end_ms": 4000, "text": "Yes."},
            {"start_ms": 4200, "end_ms": 5000, "text": "Looks good."},
        ]},
    ]
    lines = transcript.merge(chunks)
    assert [(line["speaker"], line["text"]) for line in lines] == [
        ("others", "Can everyone see my screen?"), ("me", "Yes. Looks good.")]
    assert transcript.as_text(lines).splitlines()[1] == "[00:03] Me: Yes. Looks good."
    assert transcript.clock(3_725_000) == "01:02:05"
    assert transcript.label("others:2") == "Speaker 2"
    assert [line["label"] for line in lines] == ["Others", "Me"]


def test_diarization_labels_the_system_track_at_stop(root):
    speech, summarize = FakeSpeech(), FakeSummary()
    speech.next = [
        [{"start_ms": 0, "end_ms": 900, "text": "Hello."}],
        [{"start_ms": 2000, "end_ms": 3000, "text": "Hi, I am Bob."}],
        [{"start_ms": 100, "end_ms": 800, "text": "Back to Alice."}],
        [{"start_ms": 0, "end_ms": 500, "text": "Me here."}],
    ]
    seen = []

    def fake(chunks):
        seen.extend(t_ms for t_ms, _wav in chunks)
        # Speaker 7 talks first, then 3, then 7 again; nobody in the 9.5-9.9 s gap.
        # Each voice is heard for at least MIN_SPEAKER_MS, so both get a number.
        return [(0, 1000, 7), (2000, 5000, 3), (10_000, 13_000, 7)]

    meeting = session.start("x", root=root, transcriber=speech, converter=convert, summarizer=summarize,
                            diarize=fake)
    meeting.append("system", 0, 0, b"one")
    meeting.append("system", 1, 0, b"two")
    meeting.append("system", 2, 10_000, b"three")
    meeting.append("mic", 0, 20_000, b"mine")
    meeting.finish()
    assert meeting.settled.wait(5)
    assert seen == [0, 0, 10_000]
    assert [(line["speaker"], line["label"]) for line in meeting.lines()] == [
        ("others:1", "Speaker 1"), ("others:2", "Speaker 2"), ("others:1", "Speaker 1"), ("me", "Me")]
    status = meeting.status()
    assert status["diarization"] == "done" and status["speakers"] == 2
    saved = json.loads((meeting.directory / "transcript.json").read_text())
    assert saved["lines"][1]["label"] == "Speaker 2"
    assert json.loads((meeting.directory / "tracks" / "system" / "00001.json").read_text())[
        "segments"][0]["speaker"] == "others:2"
    assert "Speaker 2: Hi, I am Bob." in summarize.calls[0][1]


@pytest.mark.parametrize("reason", ["not_installed", "no_model"])
def test_without_diarization_the_system_track_stays_others(root, reason):
    speech = FakeSpeech()
    speech.next = [[{"start_ms": 0, "end_ms": 900, "text": "Hello."}]]

    def unavailable(chunks):
        raise diarize.Unavailable(reason, "cannot")

    meeting = session.start("x", root=root, transcriber=speech, converter=convert, summarizer=FakeSummary(),
                            diarize=unavailable)
    meeting.append("system", 0, 0, b"words")
    meeting.finish()
    assert meeting.settled.wait(5)
    assert [(line["speaker"], line["label"]) for line in meeting.lines()] == [("others", "Others")]
    status = meeting.status()
    assert (status["diarization"], status["diarization_reason"]) == ("unavailable", reason)
    assert json.loads((meeting.directory / "meta.json").read_text())["diarization_reason"] == reason


def test_the_default_diarizer_reports_why_it_cannot_run(monkeypatch, tmp_path):
    monkeypatch.undo()  # the real diarize.run, not the conftest stand-in
    import openprogram.state.paths as paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(diarize, "installed", lambda: False)
    with pytest.raises(diarize.Unavailable) as missing:
        diarize.run([(0, b"")])
    assert missing.value.reason == "not_installed"

    monkeypatch.setattr(diarize, "installed", lambda: True)

    def offline(url, target):
        raise RuntimeError("offline")

    monkeypatch.setattr(diarize, "_fetch", offline)
    with pytest.raises(diarize.Unavailable) as no_model:
        diarize.run([(0, b"")])
    assert no_model.value.reason == "no_model"
    assert diarize.models_dir() == tmp_path / "models" / "diarization"


def test_speaker_overlap_and_first_appearance_order():
    records = [{"track": "system", "t_ms": 0, "segments": [
        {"start_ms": 0, "end_ms": 10000, "text": "a"},      # 3 s of 5, 7 s of 2 → 2
        {"start_ms": 10000, "end_ms": 20000, "text": "b"},  # only 5
        {"start_ms": 50000, "end_ms": 60000, "text": "c"},  # nobody
    ]}]
    turns = [(0, 3000, 5), (3000, 10000, 2), (10000, 25000, 5)]
    out = diarize.assign(records, turns)
    assert [row.get("speaker") for row in out[0]["segments"]] == ["others:1", "others:2", None]
    assert "speaker" not in records[0]["segments"][0]  # the input is not changed


def test_a_voice_heard_for_a_moment_gets_no_number():
    records = [{"track": "system", "t_ms": 0, "segments": [
        {"start_ms": 0, "end_ms": 10000, "text": "a"},      # speaker 1 throughout
        {"start_ms": 10000, "end_ms": 12000, "text": "b"},  # 1.5 s of a stray voice, 0.5 s of 1 → 1
        {"start_ms": 12000, "end_ms": 14000, "text": "c"},  # only the stray voice → Others
        {"start_ms": 14000, "end_ms": 24000, "text": "d"},  # speaker 4, a real one
    ]}]
    turns = [(0, 10000, 1), (10000, 11500, 9), (11500, 12000, 1), (12000, 13000, 9), (14000, 24000, 4)]
    out = diarize.assign(records, turns)
    assert [row.get("speaker") for row in out[0]["segments"]] == ["others:1", "others:1", None, "others:2"]


def test_concatenated_times_map_back_to_the_meeting_clock():
    def wav(ms):
        out = io.BytesIO()
        with wave.open(out, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(b"\0\0" * 16 * ms)
        return out.getvalue()

    pcm, spans = diarize.concatenate([(0, wav(1000)), (5000, wav(2000)), (9000, b"not audio")])
    assert len(pcm) == 2 * 16 * 3000 and spans == [(0, 0, 1000), (1000, 5000, 2000)]
    # One turn from 0.5 s to 1.5 s of the concatenation crosses the chunk boundary.
    assert diarize.to_meeting_clock([(0.5, 1.5, 4)], spans) == [(500, 1000, 4), (5000, 5500, 4)]


def test_meeting_transcription_detects_the_language_unless_one_is_given(root, monkeypatch):
    from openprogram.interfaces.speech import link_registration, stt
    from openprogram.runtime import speech_link

    calls = []
    monkeypatch.setattr(stt, "_engine_for", lambda audio: ("local", "base", stt.VoiceSettings(language="zh")))
    monkeypatch.setitem(stt._SEGMENT_RUNNERS, "local",
                        lambda audio, *, model, language: calls.append(language) or [])
    previous = speech_link.current()
    speech_link.register(link_registration.SpeechToText())
    try:
        session._speech(b"wav", 1000)
        session._speech(b"wav", 1000, "en")
    finally:
        speech_link.unregister()
        if previous is not None:
            speech_link.register(previous)
    assert calls == ["auto", "en"]

    speech = FakeSpeech()
    speech.next = [[], []]
    plain = session.start("x", root=root, transcriber=speech, converter=convert, summarizer=FakeSummary())
    plain.append("mic", 0, 0, b"words")
    plain.finish()
    assert plain.settled.wait(5)
    english = session.start("y", "EN", root=root, transcriber=speech, converter=convert, summarizer=FakeSummary())
    english.append("mic", 0, 0, b"words")
    english.finish()
    assert english.settled.wait(5)
    assert speech.languages == ["", "en"]
    assert json.loads((english.directory / "meta.json").read_text())["language"] == "en"
    with pytest.raises(session.MeetingError):
        session.language_of("english; rm -rf")


def test_an_interrupted_meeting_lists_as_such_and_can_be_summarized_again(root):
    speech, summarize = FakeSpeech(), FakeSummary(answer="")
    speech.next = [[{"start_ms": 0, "end_ms": 900, "text": "Hello."}]]
    meeting = _start(root, speech, summarize)
    meeting.append("mic", 0, 0, b"words")
    meeting.finish()
    assert meeting.settled.wait(5)
    assert "We agreed." not in (meeting.directory / "summary.md").read_text()
    session._meetings.clear()                            # the process ended
    meta = json.loads((meeting.directory / "meta.json").read_text())
    meta["state"] = "summarizing"
    (meeting.directory / "meta.json").write_text(json.dumps(meta))
    assert session.list_meetings(root=root)[0]["state"] == "interrupted"
    again = session.resummarize(meeting.id, root=root, summarizer=FakeSummary(), converter=convert,
                                transcriber=speech)
    assert again.settled.wait(5)
    assert again.meta()["state"] == "done" and "We agreed." in (meeting.directory / "summary.md").read_text()


def test_summary_prompt_and_file_shape():
    meta = {"id": "abc", "title": 'Plan "Q3"', "started_at": 0, "duration_ms": 65_000}
    lines = [{"start_ms": 0, "end_ms": 1, "speaker": "me", "text": "Hi"}]
    prompt = summary.compose(meta, lines)
    assert '<meeting-transcript id="abc" title="Plan \'Q3\'"' in prompt and prompt.endswith("</meeting-transcript>")
    rendered = summary.render(meta, "", lines, "no model")
    assert "_The summary could not be written: no model_" in rendered and "01:05" in rendered


# ---------------------------------------------------------------- routes

def _client(monkeypatch, root, speech, summarize):
    monkeypatch.setattr(self_updates, "require_owner", lambda request: None)
    monkeypatch.setattr(MeetingService, "start", staticmethod(
        lambda title="", language="": session.start(title, language, root=root, transcriber=speech,
                                                    converter=convert, summarizer=summarize)))
    monkeypatch.setattr(MeetingService, "ffmpeg_ready", staticmethod(lambda: True))
    monkeypatch.setattr(session, "meetings_root", lambda: root)
    app = FastAPI()
    routes.register(app)
    return TestClient(app, base_url="http://127.0.0.1:18100")


def test_routes_start_append_stop_and_read(root, monkeypatch):
    speech, summarize = FakeSpeech(), FakeSummary()
    speech.next = [[{"start_ms": 0, "end_ms": 900, "text": "Agenda first."}]]
    with _client(monkeypatch, root, speech, summarize) as client:
        started = client.post("/api/meetings", json={"title": "Standup"}).json()
        mid = started["id"]
        assert started["state"] == "recording" and started["title"] == "Standup"
        assert started["system_audio"] == "recording" and started["system_audio_reason"] == ""
        assert client.post("/api/meetings", json={}).status_code == 409
        assert started["language"] == ""
        assert client.get("/api/meetings").json()["active"] == mid
        posted = client.post(f"/api/meetings/{mid}/audio?track=mic&seq=0&t_ms=0", content=b"words",
                             headers={"content-type": "audio/webm;codecs=opus"})
        assert posted.status_code == 200 and posted.json()["chunks"]["mic"] == 1
        assert client.post(f"/api/meetings/{mid}/audio?track=mic&seq=x&t_ms=0", content=b"w").status_code == 400
        client.post(f"/api/meetings/{mid}/stop")
        assert session.get(mid).settled.wait(5)
        assert client.get(f"/api/meetings/{mid}").json()["state"] == "done"
        assert client.get(f"/api/meetings/{mid}/transcript").json()["lines"][0]["text"] == "Agenda first."
        assert "# Standup" in client.get(f"/api/meetings/{mid}/summary").json()["markdown"]
        listed = client.get("/api/meetings").json()
        assert [row["id"] for row in listed["meetings"]] == [mid] and listed["active"] is None
        assert client.post(f"/api/meetings/{mid}/audio?track=mic&seq=1&t_ms=1", content=b"w").status_code == 409
        assert client.get("/api/meetings/ffffff").status_code == 404
        assert client.delete(f"/api/meetings/{mid}").json() == {"ok": True}
        assert client.get(f"/api/meetings/{mid}").status_code == 404


def test_start_without_ffmpeg_names_the_requirement(root, monkeypatch):
    with _client(monkeypatch, root, FakeSpeech(), FakeSummary()) as client:
        monkeypatch.setattr(MeetingService, "ffmpeg_ready", staticmethod(lambda: False))
        response = client.post("/api/meetings", json={})
    assert response.status_code == 409 and response.json()["reason_code"] == "ffmpeg_missing"
    assert "brew install ffmpeg" in response.json()["error"]


def test_routes_refuse_a_caller_who_is_not_the_owner(root):
    from openprogram.operations.self_update.control.projection import ProjectionAccessError

    def refuse(request):
        raise ProjectionAccessError("owner authentication required")

    app = FastAPI()
    routes.register(app)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(self_updates, "require_owner", refuse)
        with TestClient(app) as client:
            assert client.get("/api/meetings").status_code == 403
            assert client.post("/api/meetings", json={}).status_code == 403


def test_without_the_application_the_routes_are_absent(root, monkeypatch):
    monkeypatch.delitem(_services._provided, SERVICE)
    monkeypatch.setattr(self_updates, "require_owner", lambda request: None)
    app = FastAPI()
    routes.register(app)
    with TestClient(app) as client:
        for method, path in (("get", "/api/meetings"), ("post", "/api/meetings"),
                             ("post", "/api/meetings/abc123/stop")):
            response = getattr(client, method)(path)
            assert response.status_code == 404 and response.json()["reason_code"] == "meeting_not_enabled"


def test_disabling_the_application_stops_a_recording(root, monkeypatch):
    from openprogram.programs._host import registry
    from openprogram.programs._host.ui import catalog

    meeting = _start(root, FakeSpeech(), FakeSummary())
    try:
        with monkeypatch.context() as off:
            off.setattr(catalog, "builtin_enabled", lambda name: name != "meeting")
            assert registry.sync_builtin_application("meeting") is None
            assert _services.lookup(SERVICE) is None
            assert meeting.state != "recording" and meeting.end_reason == "disabled"
    finally:
        assert registry.sync_builtin_application("meeting") is not None
    assert _services.lookup(SERVICE) is not None


def test_speech_link_falls_back_to_plain_text_as_one_segment(monkeypatch):
    from openprogram.runtime import speech_link

    class Plain:
        def transcribe(self, audio):
            return {"text": "hello", "engine": "openai", "model": "m"}

    previous = speech_link.current()
    speech_link.register(Plain())
    try:
        assert speech_link.transcribe_segments(b"x", 4200) == {
            "segments": [{"start_ms": 0, "end_ms": 4200, "text": "hello"}], "engine": "openai", "model": "m"}
    finally:
        speech_link.unregister()
        if previous is not None:
            speech_link.register(previous)


def test_whisper_cpp_timed_lines_parse():
    from openprogram.interfaces.speech import stt
    assert stt.parse_timed_lines("[00:00:01.000 --> 00:00:04.200]   hello there\n"
                                 "whisper_print_timings: x\n[00:01:00,500 --> 00:01:02.000]  bye") == [
        {"start_ms": 1000, "end_ms": 4200, "text": "hello there"},
        {"start_ms": 60500, "end_ms": 62000, "text": "bye"}]
    assert stt.wav_duration_ms(_wav(0, 1500)) == 1500
