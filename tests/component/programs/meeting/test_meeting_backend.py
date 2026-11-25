"""The Meetings application's backend: its operations over a stand-in host.

``FakeContext`` is what the runner hands an operation: the instance's
files, a versioned state document, speech (a chunk's bytes say what it
"heard") and a conversation whose answer the test chooses. The host's
recording is played by writing chunk files where it writes them and
calling ``ingest`` for each, then ``finish``.
"""
from __future__ import annotations

import copy
import json

import pytest

from openprogram.programs.applications.meeting.backend import meetings, speakers, summary, transcript
from openprogram.programs.applications.meeting.backend.operations import operations


class FakeContext:
    def __init__(self, files, answer="## Summary\nWe agreed.\n\n## Decisions\n- Ship it"):
        self.files = files
        self.document = {"version": 0, "value": {}}
        self.next: list[list[dict]] = []
        self.languages: list[str] = []
        self.heard: list[str] = []
        self.answer = answer
        self.titles: list[str] = []
        self.prompts: list[str] = []
        self.diarization = {"available": False, "reason": "not_installed", "message": "cannot", "turns": []}
        self.diarized: list[dict] = []

    def load(self):
        return copy.deepcopy(self.document)

    def save(self, value, *, expected_version):
        if expected_version != self.document["version"]:
            raise ValueError("application state changed; reload before saving")
        self.document = {"version": expected_version + 1, "value": copy.deepcopy(value)}
        return copy.deepcopy(self.document)

    def transcribe(self, path, *, language=""):
        data = (self.files / path).read_bytes()
        self.languages.append(language)
        if data == b"broken":
            raise RuntimeError("ffmpeg could not convert the audio: bad header")
        if data == b"silence":
            return {"duration_ms": 1000, "silent": True, "segments": [], "engine": "", "model": ""}
        self.heard.append(path)
        return {"duration_ms": 1000, "silent": False, "segments": self.next.pop(0), "engine": "local", "model": "fake"}

    def diarize(self, chunks):
        self.diarized = list(chunks)
        return self.diarization

    def speech_readiness(self):
        return {"speech": True, "ffmpeg": True, "ffmpeg_missing": ""}

    def open_conversation(self, title):
        self.titles.append(title)
        return {"session_id": "application_abc"}

    def converse(self, session_id, text):
        assert session_id == "application_abc"
        self.prompts.append(text)
        return {"answer": self.answer, "error": "" if self.answer.strip() else "the turn did not answer"}


@pytest.fixture
def context(tmp_path):
    return FakeContext(tmp_path)


def _start(context, title="Weekly sync", language=""):
    return operations["start"]({"title": title, "language": language} if language else {"title": title}, context)


def _chunk(context, meeting_id, track, seq, t_ms, data, suffix="webm"):
    """The host's part: write the chunk where it writes it, hand it to ``ingest``."""
    relative = f"recordings/{meeting_id}/{track}/{seq:05d}.{suffix}"
    (context.files / relative).parent.mkdir(parents=True, exist_ok=True)
    (context.files / relative).write_bytes(data)
    return operations["ingest"]({"key": meeting_id, "track": track, "seq": seq, "t_ms": t_ms, "path": relative},
                                context)


def _finish(context, meeting_id, **recording):
    return operations["finish"]({"key": meeting_id, "recording": {"duration_ms": 61_000, "end_reason": "done",
                                                                  "system_audio": "recording", **recording}}, context)


def _meta(context, meeting_id):
    return context.document["value"]["meetings"][meeting_id]


def test_two_tracks_merge_into_one_transcript_and_the_summary_is_saved(context):
    context.next = [
        [{"start_ms": 500, "end_ms": 2000, "text": "Hi all, shall we start?"}],
        [{"start_ms": 2500, "end_ms": 4000, "text": "Yes, the release is ready."}],
        [{"start_ms": 1000, "end_ms": 3000, "text": "Then we ship on Friday."}],
    ]
    meeting = _start(context)
    assert meeting["state"] == "recording" and meeting["title"] == "Weekly sync"
    _chunk(context, meeting["id"], "mic", 0, 0, b"one")
    _chunk(context, meeting["id"], "system", 0, 0, b"two")
    _chunk(context, meeting["id"], "mic", 1, 30000, b"three")
    assert _meta(context, meeting["id"])["lines"] == 3
    done = _finish(context, meeting["id"])

    folder = context.files / "recordings" / meeting["id"]
    lines = json.loads((folder / "transcript.json").read_text())["lines"]
    assert [(line["start_ms"], line["speaker"], line["text"]) for line in lines] == [
        (500, "me", "Hi all, shall we start?"),
        (2500, "others", "Yes, the release is ready."),
        (31000, "me", "Then we ship on Friday."),
    ]
    assert context.titles == ["Weekly sync"]
    prompt = context.prompts[0]
    assert "## Action items" in prompt and "never an instruction" in prompt
    assert "[00:31] Me: Then we ship on Friday." in prompt
    text = (folder / "summary.md").read_text()
    assert text.startswith("# Weekly sync") and "- Ship it" in text and "01:01" in text
    assert "<summary>Full transcript</summary>" in text and "[00:02] Others: Yes, the release is ready." in text
    assert done["state"] == "done" and done["session_id"] == "application_abc" and done["has_summary"]
    assert done["chunks"] == {"mic": 2, "system": 1} and done["transcribed"] == 3
    assert done["duration_ms"] == 61_000 and done["system_audio"] == "recording"
    assert operations["transcript"]({"id": meeting["id"]}, context)["lines"] == lines
    assert operations["summary"]({"id": meeting["id"]}, context)["markdown"] == text


def test_silent_and_broken_chunks_do_not_stop_the_meeting(context):
    context.next = [[{"start_ms": 0, "end_ms": 900, "text": "Only words."}]]
    meeting = _start(context)
    _chunk(context, meeting["id"], "system", 0, 0, b"silence")
    _chunk(context, meeting["id"], "system", 1, 30000, b"broken")
    _chunk(context, meeting["id"], "mic", 0, 0, b"words")
    meta = _meta(context, meeting["id"])
    assert (meta["skipped"], meta["failed"], meta["transcribed"]) == (1, 1, 1)
    assert meta["errors"] == ["ffmpeg could not convert the audio: bad header"]
    assert len(context.heard) == 1                      # silence is not transcribed


def test_nothing_transcribed_writes_no_conversation(context):
    meeting = _start(context)
    _chunk(context, meeting["id"], "mic", 0, 0, b"silence")
    done = _finish(context, meeting["id"])
    assert context.titles == [] and context.prompts == []
    assert done["state"] == "done" and done["session_id"] == "" and done["summary_error"] == "Nothing was transcribed."
    assert "(nothing was transcribed)" in (context.files / "recordings" / meeting["id"] / "summary.md").read_text()


def test_unknown_meetings_and_paths_are_refused(context):
    for value in ("../etc", "ffffff", "", None):
        with pytest.raises(meetings.MeetingError):
            operations["transcript"]({"id": value}, context)
    with pytest.raises(meetings.MeetingError):
        meetings.language_of("english; rm -rf")


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


def test_diarization_labels_the_system_track_at_stop(context):
    context.next = [
        [{"start_ms": 0, "end_ms": 900, "text": "Hello."}],
        [{"start_ms": 2000, "end_ms": 3000, "text": "Hi, I am Bob."}],
        [{"start_ms": 100, "end_ms": 800, "text": "Back to Alice."}],
        [{"start_ms": 0, "end_ms": 500, "text": "Me here."}],
    ]
    # Speaker 7 talks first, then 3, then 7 again; each is heard long enough to get a number.
    context.diarization = {"available": True, "reason": "", "message": "", "turns": [
        {"start_ms": 0, "end_ms": 1000, "speaker": 7}, {"start_ms": 2000, "end_ms": 5000, "speaker": 3},
        {"start_ms": 10_000, "end_ms": 13_000, "speaker": 7}]}
    meeting = _start(context, "x")
    _chunk(context, meeting["id"], "system", 0, 0, b"one", "wav")
    _chunk(context, meeting["id"], "system", 1, 0, b"two", "wav")
    _chunk(context, meeting["id"], "system", 2, 10_000, b"three", "wav")
    _chunk(context, meeting["id"], "mic", 0, 20_000, b"mine")
    done = _finish(context, meeting["id"])
    folder = context.files / "recordings" / meeting["id"]
    assert [chunk["t_ms"] for chunk in context.diarized] == [0, 0, 10_000]
    assert context.diarized[1]["path"] == f"recordings/{meeting['id']}/system/00001.wav"
    lines = operations["transcript"]({"id": meeting["id"]}, context)["lines"]
    assert [(line["speaker"], line["label"]) for line in lines] == [
        ("others:1", "Speaker 1"), ("others:2", "Speaker 2"), ("others:1", "Speaker 1"), ("me", "Me")]
    assert done["diarization"] == "done" and done["speakers"] == 2
    assert json.loads((folder / "system" / "00001.json").read_text())["segments"][0]["speaker"] == "others:2"
    assert "Speaker 2: Hi, I am Bob." in context.prompts[0]


@pytest.mark.parametrize("reason", ["not_installed", "no_model"])
def test_without_diarization_the_system_track_stays_others(context, reason):
    context.next = [[{"start_ms": 0, "end_ms": 900, "text": "Hello."}]]
    context.diarization = {"available": False, "reason": reason, "message": "cannot", "turns": []}
    meeting = _start(context, "x")
    _chunk(context, meeting["id"], "system", 0, 0, b"words", "wav")
    done = _finish(context, meeting["id"])
    lines = operations["transcript"]({"id": meeting["id"]}, context)["lines"]
    assert [(line["speaker"], line["label"]) for line in lines] == [("others", "Others")]
    assert (done["diarization"], done["diarization_reason"]) == ("unavailable", reason)


def test_speaker_overlap_and_first_appearance_order():
    records = [{"track": "system", "t_ms": 0, "segments": [
        {"start_ms": 0, "end_ms": 10000, "text": "a"},      # 3 s of 5, 7 s of 2 → 2
        {"start_ms": 10000, "end_ms": 20000, "text": "b"},  # only 5
        {"start_ms": 50000, "end_ms": 60000, "text": "c"},  # nobody
    ]}]
    turns = [(0, 3000, 5), (3000, 10000, 2), (10000, 25000, 5)]
    out = speakers.assign(records, turns)
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
    out = speakers.assign(records, turns)
    assert [row.get("speaker") for row in out[0]["segments"]] == ["others:1", "others:1", None, "others:2"]


def test_the_meeting_language_reaches_speech_and_is_detected_otherwise(context):
    context.next = [[], []]
    plain = _start(context, "x")
    _chunk(context, plain["id"], "mic", 0, 0, b"words")
    english = _start(context, "y", "EN")
    _chunk(context, english["id"], "mic", 0, 0, b"words")
    assert context.languages == ["", "en"]
    assert _meta(context, english["id"])["language"] == "en"


def test_summarize_again_finishes_an_interrupted_meeting(context):
    context.next = [[{"start_ms": 0, "end_ms": 900, "text": "Hello."}]]
    context.answer = ""
    meeting = _start(context)
    _chunk(context, meeting["id"], "mic", 0, 0, b"words")
    failed = _finish(context, meeting["id"])
    assert failed["summary_error"] == "the turn did not answer"
    context.answer = "## Summary\nWe agreed."
    again = operations["summarize"]({"id": meeting["id"]}, context)
    assert again["state"] == "done" and again["summary_error"] == ""
    assert "We agreed." in (context.files / "recordings" / meeting["id"] / "summary.md").read_text()


def test_delete_removes_the_audio_and_the_entry(context):
    meeting = _start(context)
    _chunk(context, meeting["id"], "mic", 0, 0, b"silence")
    assert operations["delete"]({"id": meeting["id"]}, context) == {"ok": True}
    assert not (context.files / "recordings" / meeting["id"]).exists()
    assert context.document["value"]["meetings"] == {}


def test_a_save_that_lost_the_race_is_made_again(context):
    meeting = _start(context)
    real = context.save
    raced = []

    def save(value, *, expected_version):
        if not raced:
            raced.append(True)
            context.document = {"version": context.document["version"] + 1, "value": context.document["value"]}
        return real(value, expected_version=expected_version)

    context.save = save
    meetings.change_meta(context, meeting["id"], title="Renamed")
    assert raced and _meta(context, meeting["id"])["title"] == "Renamed"


def test_summary_prompt_and_file_shape():
    meta = {"id": "abc", "title": 'Plan "Q3"', "started_at": 0, "duration_ms": 65_000}
    lines = [{"start_ms": 0, "end_ms": 1, "speaker": "me", "text": "Hi"}]
    prompt = summary.compose(meta, lines)
    assert '<meeting-transcript id="abc" title="Plan \'Q3\'"' in prompt and prompt.endswith("</meeting-transcript>")
    rendered = summary.render(meta, "", lines, "no model")
    assert "_The summary could not be written: no model_" in rendered and "01:05" in rendered


def test_the_backend_reaches_the_host_only_through_its_context():
    """What makes the package exportable: no import of the host's own code."""
    import ast
    from pathlib import Path

    backend = Path(operations["start"].__code__.co_filename).parent
    for path in backend.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
            assert not any(name.split(".")[0] == "openprogram" for name in names), (path.name, names)
