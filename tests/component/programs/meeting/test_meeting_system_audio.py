"""The computer's sound, recorded on the server: chunking, status and the meeting's use of it.

ScreenCaptureKit cannot run in tests; a fake source pushes float32 sample
buffers the way the stream's output does, and a fake clock says when.
"""
from __future__ import annotations

import io
import json
import threading
import wave
from array import array

import pytest

from openprogram.host._platform import screen_capture_audio as sck
from openprogram.programs.applications.meeting import audio, session, system_audio

RATE = system_audio.RATE


def samples(seconds: float, level: float = 0.5) -> bytes:
    return array("f", [level] * int(seconds * RATE)).tobytes()


class Clock:
    def __init__(self):
        self.ms = 0

    def __call__(self) -> int:
        return self.ms


class FakeSource:
    """Pushes nothing on its own; the test feeds ``sink``."""

    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.sink = None
        self.stopped = False

    def start(self, sink):
        if self.fail is not None:
            raise self.fail
        self.sink = sink

    def stop(self):
        self.stopped = True


def feed(source: FakeSource, clock: Clock, seconds: float, step: float) -> None:
    """``seconds`` of sound in buffers of ``step`` seconds; each arrives as it ends."""
    sent = 0.0
    while sent < seconds - 1e-9:
        size = min(step, seconds - sent)
        sent += size
        clock.ms = round(sent * 1000)
        source.sink(samples(size))


def durations(chunks):
    out = []
    for seq, t_ms, data in chunks:
        with wave.open(io.BytesIO(data)) as reader:
            assert (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) == (RATE, 1, 2)
            out.append((seq, t_ms, round(reader.getnframes() * 1000 / RATE)))
    return out


def capture(source, clock):
    chunks = []
    recorder = system_audio.SystemAudio(lambda seq, t_ms, data: chunks.append((seq, t_ms, data)), source, clock)
    return recorder, chunks


def test_seventy_five_seconds_become_two_full_chunks_and_a_flushed_partial():
    source, clock = FakeSource(), Clock()
    recorder, chunks = capture(source, clock)
    recorder.start()
    assert recorder.status() == {"system_audio": "recording", "system_audio_reason": "", "system_audio_message": ""}
    feed(source, clock, 75, 0.5)
    recorder.stop()
    assert source.stopped
    assert durations(chunks) == [(0, 0, 30_000), (1, 30_000, 30_000), (2, 60_000, 15_000)]


def test_a_buffer_across_a_chunk_boundary_starts_the_next_chunk_at_its_remainder():
    source, clock = FakeSource(), Clock()
    recorder, chunks = capture(source, clock)
    recorder.start()
    clock.ms = 1_000                       # the stream began a second into the meeting
    source.sink(samples(0.0))
    for _ in range(45):                    # 0.7 s buffers: 31.5 s of sound
        clock.ms += 700
        source.sink(samples(0.7))
    recorder.stop()
    assert durations(chunks) == [(0, 1_000, 30_000), (1, 31_000, 1_500)]


def test_samples_pushed_from_the_stream_thread_arrive_in_order():
    source, clock = FakeSource(), Clock()
    recorder, chunks = capture(source, clock)
    recorder.start()
    pusher = threading.Thread(target=feed, args=(source, clock, 61, 0.25))
    pusher.start()
    pusher.join()
    recorder.stop()
    assert [(seq, t_ms) for seq, t_ms, _ in durations(chunks)] == [(0, 0), (1, 30_000), (2, 60_000)]


def test_cancel_drops_the_partial_chunk_and_late_samples():
    source, clock = FakeSource(), Clock()
    recorder, chunks = capture(source, clock)
    recorder.start()
    feed(source, clock, 10, 1)
    recorder.cancel()
    source.sink(samples(40))
    assert chunks == []


@pytest.mark.parametrize(("fail", "reason", "message"), [
    (system_audio.Unavailable("permission", "The user declined TCCs"), "permission", "The user declined TCCs"),
    (system_audio.Unavailable("unsupported", "macOS only"), "unsupported", "macOS only"),
    (RuntimeError("stream broke"), "error", "stream broke"),
])
def test_a_source_that_cannot_start_reports_unavailable_with_its_reason(fail, reason, message):
    source = FakeSource(fail)
    recorder, chunks = capture(source, Clock())
    recorder.start()
    assert recorder.status() == {"system_audio": "unavailable", "system_audio_reason": reason,
                                 "system_audio_message": message}
    recorder.stop()
    assert chunks == [] and not source.stopped


def test_screen_capture_source_is_unsupported_off_macos_and_without_pyobjc(monkeypatch):
    monkeypatch.setattr(system_audio.sys, "platform", "linux")
    with pytest.raises(system_audio.Unavailable) as off_mac:
        system_audio.ScreenCaptureSource().start(lambda data: None)
    assert off_mac.value.reason == "unsupported"

    def no_pyobjc():
        raise ImportError("No module named 'ScreenCaptureKit'")
    monkeypatch.setattr(system_audio.sys, "platform", "darwin")
    monkeypatch.setattr(sck, "frameworks", no_pyobjc)
    with pytest.raises(system_audio.Unavailable) as missing:
        system_audio.ScreenCaptureSource().start(lambda data: None)
    assert missing.value.reason == "unsupported" and "ScreenCaptureKit" in missing.value.message


def test_screen_capture_errors_are_told_apart(monkeypatch):
    declined = RuntimeError("Error Domain=com.apple.ScreenCaptureKit.SCStreamErrorDomain Code=-3801 "
                            "\"The user declined TCCs for application, window, display capture\"")
    assert system_audio.classify(declined, allowed=lambda: None) == "permission"
    assert system_audio.classify(RuntimeError("no answer in time"), allowed=lambda: False) == "permission"
    assert system_audio.classify(RuntimeError("no answer in time"), allowed=lambda: True) == "error"

    monkeypatch.setattr(system_audio.sys, "platform", "darwin")
    monkeypatch.setattr(sck, "frameworks", lambda: (object(), object(), object()))

    def refused():
        raise declined
    monkeypatch.setattr(sck, "shareable_content", refused)
    with pytest.raises(system_audio.Unavailable) as caught:
        system_audio.ScreenCaptureSource().start(lambda data: None)
    assert caught.value.reason == "permission"


# ---------------------------------------------------------------- the meeting

class Speech:
    def __init__(self):
        self.calls = []

    def __call__(self, wav, duration_ms, language=""):
        self.calls.append(duration_ms)
        return {"segments": [{"start_ms": 0, "end_ms": 900, "text": f"line {len(self.calls)}"}],
                "engine": "local", "model": "fake"}


def no_summary(title, text, on_conversation, on_done):
    on_done("## Summary\nDone.", "")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "meetings_root", lambda: tmp_path)
    yield tmp_path
    for live in list(session._meetings.values()):
        live.finish("done")
        live.settled.wait(5)
    session._meetings.clear()


def test_the_meeting_takes_the_computers_sound_as_wav_chunks_and_flushes_on_stop(root, monkeypatch):
    monkeypatch.setattr(audio, "ffmpeg", lambda: None)      # 16 kHz WAV needs no conversion
    source, speech = FakeSource(), Speech()
    meeting = session.start("Sync", root=root, transcriber=speech, summarizer=no_summary,
                            system_source=lambda: source)
    assert meeting.status()["system_audio"] == "recording"
    source.sink(samples(31))                                 # one full chunk and one second more
    meeting.finish()
    assert meeting.settled.wait(5)
    assert source.stopped
    system_dir = meeting.directory / "tracks" / "system"
    assert sorted(path.name for path in system_dir.glob("*.wav")) == ["00000.wav", "00001.wav"]
    records = [json.loads(path.read_text()) for path in sorted(system_dir.glob("*.json"))]
    assert [(r["seq"], r["duration_ms"]) for r in records] == [(0, 30_000), (1, 1_000)]
    assert records[1]["t_ms"] >= records[0]["t_ms"]
    meta = json.loads((meeting.directory / "meta.json").read_text())
    assert meta["chunks"] == {"mic": 0, "system": 2} and meta["transcribed"] == 2
    assert meta["system_audio"] == "recording" and meta["state"] == "done"
    lines = meeting.lines()
    assert {line["speaker"] for line in lines} == {"others"}
    assert "line 1" in " ".join(line["text"] for line in lines) and len(speech.calls) == 2


def test_without_the_computers_sound_the_meeting_records_the_microphone_and_says_why(root):
    def refused():
        return FakeSource(system_audio.Unavailable("permission", "The user declined TCCs"))
    meeting = session.start("Mic only", root=root, transcriber=Speech(), summarizer=no_summary,
                            converter=lambda path: sck.wav(samples(1))[0], system_source=refused)
    status = meeting.status()
    assert (status["system_audio"], status["system_audio_reason"]) == ("unavailable", "permission")
    meeting.append("mic", 0, 0, b"webm bytes")
    meeting.finish()
    assert meeting.settled.wait(5)
    session._meetings.clear()                               # a past meeting, read from disk
    [row] = session.list_meetings(root=root)
    assert (row["system_audio"], row["system_audio_reason"]) == ("unavailable", "permission")
    assert row["chunks"] == {"mic": 1, "system": 0}


def test_a_source_that_cannot_even_be_made_is_an_error(root):
    def broken():
        raise OSError("no audio service")
    meeting = session.start("x", root=root, transcriber=Speech(), summarizer=no_summary, system_source=broken)
    assert (meeting.status()["system_audio_reason"], meeting.status()["system_audio_message"]) == (
        "error", "no audio service")


def test_wav_chunks_in_speech_form_skip_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "ffmpeg", lambda: None)
    data, _ = sck.wav(samples(0.5))
    path = tmp_path / "00000.wav"
    path.write_bytes(data)
    assert audio.to_wav(path) == data
    stereo = tmp_path / "00001.wav"
    with wave.open(str(stereo), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(44100)
        writer.writeframes(b"\0" * 400)
    with pytest.raises(audio.AudioError):                   # anything else still goes through ffmpeg
        audio.to_wav(stereo)
