"""One meeting recording at a time: its files, its transcription queue, its life cycle.

Two tracks arrive in chunks of about 30 seconds, each with its offset
from the meeting start: the microphone (``mic``), which the page records
and posts as self-contained WebM/Opus chunks, and the computer's sound
output (``system``), which the server records itself with ScreenCaptureKit
(``system_audio``) and hands to the same intake as WAV chunks. Every chunk
is stored, then one worker thread per meeting converts it to 16 kHz WAV,
skips it when silent, transcribes it with timestamps and rewrites
``transcript.json``. Stopping first flushes the system track's last
partial chunk, then drains the queue, tells apart the speakers on the
system track (``diarize``; "Others" stays when it cannot run) and starts
the summary turn.

On disk, under ``<state>/meetings/<id>/``::

    meta.json                     title, times, state, counts, conversation,
                                  whether the computer's sound was recorded,
                                  whether its speakers were told apart
    tracks/<track>/<seq>.webm     the audio as recorded (``.wav`` for ``system``)
    tracks/<track>/<seq>.json     that chunk's timed segments (with their
                                  speaker once the system track is diarized)
    transcript.json               the merged, time-ordered transcript
    summary.md                    the summary with the transcript appended

States: ``recording`` → ``transcribing`` → ``summarizing`` → ``done``.
A meeting whose process ended before ``done`` lists as ``interrupted``;
its chunks are kept and ``summarize`` finishes it.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import audio as _audio
from . import diarize as _diarize
from . import summary as _summary
from . import system_audio as _system_audio
from . import transcript as _transcript
from openprogram.host._platform.filesystem import replace_file

TRACKS = ("mic", "system")
MAX_SECONDS = 4 * 3600
MAX_CHUNK_BYTES = 16 * 1024 * 1024
MAX_SEQ = 100_000
LIVE_STATES = ("recording", "transcribing", "summarizing")

#: ``transcriber(wav, duration_ms, language)``; ``language`` ``""`` detects it.
Transcriber = Callable[[bytes, int, str], dict]
#: ``summarizer(title, text, on_conversation(session_id), on_done(answer, error))``.
Summarizer = Callable[[str, str, Callable[[str], None], Callable[[str, str], None]], Any]


class MeetingError(RuntimeError):
    """A meeting cannot start, take audio, or does not exist."""


def meetings_root() -> Path:
    from openprogram.state.paths import get_state_dir
    return Path(get_state_dir()) / "meetings"


def _speech(wav: bytes, duration_ms: int, language: str = "") -> dict:
    from openprogram.runtime import speech_link
    try:
        return speech_link.transcribe_segments(wav, duration_ms, language=language)
    except speech_link.TranscriptionError as exc:
        raise MeetingError(str(exc)) from exc


def language_of(value: Any) -> str:
    """A meeting's spoken language: an ISO 639 code, or ``""`` to detect it per chunk."""
    language = str(value or "").strip().lower()
    if language in ("", "auto"):
        return ""
    if not re.fullmatch(r"[a-z]{2,3}", language):
        raise MeetingError(f"Unknown language: {language[:16]}")
    return language


def _write_json(path: Path, value: Any) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    replace_file(temp, path)


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class Meeting:
    """A meeting being recorded, transcribed or summarized."""

    def __init__(self, directory: Path, title: str, *, transcriber: Transcriber = _speech,
                 converter: Callable[[Path], bytes] = _audio.to_wav,
                 summarizer: Summarizer = _summary.start_conversation,
                 diarize: _diarize.Diarize | None = None,
                 system_source: Callable[[], _system_audio.Source] | None = None,
                 started: float | None = None, language: str = ""):
        self.id = directory.name
        self.directory = directory
        self.title = title
        #: What everyone speaks, when the user said so; ``""`` detects it (never the voice-input setting).
        self.language = language_of(language)
        self.started = started if started is not None else time.time()
        self._mono = time.monotonic()
        self.state = "recording"
        self.end_reason = ""
        self.duration_ms = 0
        self.ended_at: float | None = None
        self.session_id = ""
        self.summary_error = ""
        self.engine = ""
        self.model = ""
        self.errors: list[str] = []
        self.received = {track: 0 for track in TRACKS}
        self.transcribed = 0
        self.skipped = 0
        self.failed = 0
        #: ``recording`` / ``unavailable`` once started; ``""`` before (and in older meetings).
        self.system_audio = ""
        self.system_audio_reason = ""
        self.system_audio_message = ""
        self._system_source = system_source
        self._system: _system_audio.SystemAudio | None = None
        self._finishing = False
        self._chunks: dict[tuple[str, int], dict] = {}
        self._lines: list[dict] = []
        self._seen: set[tuple[str, int]] = set()
        self._transcriber = transcriber
        self._converter = converter
        self._summarizer = summarizer
        self._diarize = diarize
        #: ``""`` until the meeting stops; then ``running`` → ``done`` / ``unavailable`` / ``failed``.
        self.diarization = ""
        self.diarization_reason = ""
        self.diarization_message = ""
        self.speakers = 0
        self._lock = threading.RLock()
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._timer: threading.Timer | None = None
        self.settled = threading.Event()

    # ------------------------------------------------------------------ life cycle
    def begin(self) -> None:
        for track in TRACKS:
            (self.directory / "tracks" / track).mkdir(parents=True, exist_ok=True)
        self._write_meta()
        self._write_transcript()
        self._worker = threading.Thread(target=self._work, name=f"meeting-{self.id}", daemon=True)
        self._worker.start()
        self._start_system_audio()
        self._timer = threading.Timer(MAX_SECONDS, lambda: self.finish("time_limit"))
        self._timer.daemon = True
        self._timer.start()

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._mono) * 1000)

    def _start_system_audio(self) -> None:
        """Record the computer's sound on this machine; without it the meeting is microphone only."""
        try:
            source = (self._system_source or _system_audio.default_source)()
        except Exception as exc:
            source = None
            self._note_system_audio("unavailable", "error", str(exc) or type(exc).__name__)
        if source is not None:
            self._system = _system_audio.SystemAudio(self._take_system_chunk, source, self.elapsed_ms)
            self._system.start()
            status = self._system.status()
            self._note_system_audio(status["system_audio"], status["system_audio_reason"],
                                    status["system_audio_message"])

    def _note_system_audio(self, state: str, reason: str, message: str) -> None:
        with self._lock:
            self.system_audio, self.system_audio_reason, self.system_audio_message = state, reason, message
            self._write_meta()

    def _take_system_chunk(self, seq: int, t_ms: int, data: bytes) -> None:
        try:
            self.append("system", seq, t_ms, data, "wav")
        except MeetingError:
            if self.state == "recording":
                raise
            # Otherwise the meeting ended while the chunk was on its way.

    def finish(self, reason: str = "done") -> None:
        """Stop taking audio; transcription of what arrived and the summary go on."""
        with self._lock:
            if self.state != "recording" or self._finishing:
                return
            self._finishing = True
        # The computer's sound: its last partial chunk goes in while the meeting still records.
        if self._system is not None:
            self._system.stop()
        with self._lock:
            if self.state != "recording":
                return
            self.state = "transcribing"
            self.end_reason = reason
            self.duration_ms = self.elapsed_ms()
            self.ended_at = time.time()
            if self._timer is not None:
                self._timer.cancel()
            self._write_meta()
        self._queue.put(None)

    def discard(self) -> None:
        """Stop and delete everything recorded."""
        with self._lock:
            self.state = "discarded"
            if self._timer is not None:
                self._timer.cancel()
        if self._system is not None:
            self._system.cancel()
        self._queue.put(None)
        shutil.rmtree(self.directory, ignore_errors=True)
        self.settled.set()

    # ------------------------------------------------------------------ audio
    def append(self, track: str, seq: int, t_ms: int, data: bytes, suffix: str = "webm") -> dict:
        """Store one chunk and queue it for transcription. A repeated chunk is ignored."""
        if track not in TRACKS:
            raise MeetingError(f"Unknown track: {track}")
        if not 0 <= int(seq) < MAX_SEQ or int(t_ms) < 0:
            raise MeetingError("Bad chunk number or offset.")
        if not data:
            raise MeetingError("The chunk is empty.")
        if len(data) > MAX_CHUNK_BYTES:
            raise MeetingError("The chunk is too large.")
        suffix = suffix if suffix in {"webm", "ogg", "mp4", "wav"} else "webm"
        with self._lock:
            if self.state != "recording":
                raise MeetingError("The meeting is no longer recording.")
            key = (track, int(seq))
            if key in self._seen:
                return self.status()
            self._seen.add(key)
            path = self.directory / "tracks" / track / f"{int(seq):05d}.{suffix}"
            path.write_bytes(data)
            self.received[track] += 1
            self._write_meta()
        self._queue.put((track, int(seq), int(t_ms), path))
        return self.status()

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                break
            if self.state == "discarded":
                return
            self._transcribe(*job)
        if self.state == "discarded":
            return
        self._separate_speakers()
        if self.state == "discarded":
            return
        self._summarize()

    def _transcribe(self, track: str, seq: int, t_ms: int, path: Path) -> None:
        record = {"track": track, "seq": seq, "t_ms": t_ms, "segments": []}
        try:
            wav = self._converter(path)
            duration, peak = _audio.wav_info(wav)
            record["duration_ms"] = duration
            if peak < _audio.SILENCE_PEAK:
                record["silent"] = True
            else:
                result = self._transcriber(wav, duration, self.language)
                record["segments"] = list(result.get("segments") or [])
                self.engine = str(result.get("engine") or self.engine)
                self.model = str(result.get("model") or self.model)
        except Exception as exc:  # one bad chunk must not stop the meeting
            record["error"] = str(exc) or type(exc).__name__
        if self.state == "discarded":
            return
        _write_json(path.with_suffix(".json"), record)
        with self._lock:
            self._chunks[(track, seq)] = record
            self._lines = _transcript.merge(self._chunks[key] for key in sorted(self._chunks))
            if record.get("error"):
                self.failed += 1
                if record["error"] not in self.errors:
                    self.errors = (self.errors + [record["error"]])[-5:]
            elif record.get("silent"):
                self.skipped += 1
            else:
                self.transcribed += 1
            self._write_transcript()
            self._write_meta()

    # ------------------------------------------------------------------ speakers
    def _note_diarization(self, state: str, reason: str = "", message: str = "") -> None:
        with self._lock:
            self.diarization, self.diarization_reason, self.diarization_message = state, reason, message
            self._write_meta()

    def _system_audio_files(self, keys: list[tuple[str, int]], records: list[dict]):
        """``(t_ms, wav)`` of each system chunk in order, converted one at a time."""
        folder = self.directory / "tracks" / "system"
        for (_track, seq), record in zip(keys, records):
            path = next((p for p in sorted(folder.glob(f"{seq:05d}.*")) if p.suffix != ".json"), None)
            if path is None:
                continue
            try:
                wav = self._converter(path)
            except Exception:  # a chunk that did not convert was not transcribed either
                continue
            yield int(record.get("t_ms") or 0), wav

    def _separate_speakers(self) -> None:
        """Diarize the whole system track once and label its lines "Speaker 1", "Speaker 2"…"""
        with self._lock:
            keys = sorted(key for key in self._chunks if key[0] == "system")
            records = [self._chunks[key] for key in keys]
        if not any(record.get("segments") for record in records):
            return
        self._note_diarization("running")
        try:
            turns = (self._diarize or _diarize.run)(self._system_audio_files(keys, records))
            labelled = _diarize.assign(records, list(turns))
        except _diarize.Unavailable as exc:
            self._note_diarization("unavailable", exc.reason, str(exc))
            return
        except Exception as exc:  # the transcript keeps "Others"
            self._note_diarization("failed", "error", str(exc) or type(exc).__name__)
            return
        with self._lock:
            if self.state == "discarded":
                return
            for key, record in zip(keys, labelled):
                self._chunks[key] = record
                _write_json(self.directory / "tracks" / "system" / f"{key[1]:05d}.json", record)
            self._lines = _transcript.merge(self._chunks[key] for key in sorted(self._chunks))
            self.speakers = len({segment["speaker"] for record in labelled
                                 for segment in record.get("segments") or [] if segment.get("speaker")})
            self._write_transcript()
        self._note_diarization("done")

    # ------------------------------------------------------------------ summary
    def lines(self) -> list[dict]:
        """The merged transcript so far (rebuilt as each chunk is transcribed)."""
        with self._lock:
            return list(self._lines)

    def _summarize(self) -> None:
        lines = self.lines()
        meta = self.meta()
        if not lines:
            self._complete("", "Nothing was transcribed." if not self.errors else self.errors[-1])
            return
        with self._lock:
            self.state = "summarizing"
            self._write_meta()
        try:
            self._summarizer(self.title, _summary.compose(meta, lines), self._conversation, self._complete)
        except Exception as exc:
            self._complete("", f"The summary conversation could not start: {exc}")

    def _conversation(self, session_id: str) -> None:
        """The summary conversation exists (before its turn starts)."""
        with self._lock:
            self.session_id = session_id
            self._write_meta()

    def _complete(self, answer: str, error: str) -> None:
        """The summary turn ended: write ``summary.md``."""
        with self._lock:
            if self.state == "discarded":
                return
            self.summary_error = error if not answer.strip() else ""
            text = _summary.render(self.meta(), answer, self.lines(), self.summary_error)
            (self.directory / "summary.md").write_text(text, encoding="utf-8")
            self.state = "done"
            self._write_meta()
        self.settled.set()

    def resummarize(self) -> None:
        """Run the summary again (after a failure or an interruption)."""
        with self._lock:
            if self.state in ("recording", "transcribing", "summarizing") and not self.settled.is_set():
                raise MeetingError("The meeting is still being processed.")
            self.state = "transcribing"
            self.settled.clear()
        threading.Thread(target=self._summarize, name=f"meeting-summary-{self.id}", daemon=True).start()

    # ------------------------------------------------------------------ files
    def meta(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "title": self.title,
                "language": self.language,
                "started_at": self.started,
                "ended_at": self.ended_at,
                "duration_ms": self.duration_ms if self.state != "recording" else self.elapsed_ms(),
                "state": self.state,
                "end_reason": self.end_reason,
                "chunks": dict(self.received),
                "transcribed": self.transcribed,
                "skipped": self.skipped,
                "failed": self.failed,
                "errors": list(self.errors),
                "engine": self.engine,
                "model": self.model,
                "session_id": self.session_id,
                "summary_error": self.summary_error,
                "has_summary": (self.directory / "summary.md").is_file(),
                "max_seconds": MAX_SECONDS,
                "system_audio": self.system_audio,
                "system_audio_reason": self.system_audio_reason,
                "system_audio_message": self.system_audio_message,
                "diarization": self.diarization,
                "diarization_reason": self.diarization_reason,
                "diarization_message": self.diarization_message,
                "speakers": self.speakers,
            }

    def status(self) -> dict:
        meta = self.meta()
        received = sum(meta["chunks"].values())
        processed = meta["transcribed"] + meta["skipped"] + meta["failed"]
        return {**meta, "elapsed_ms": meta["duration_ms"], "pending": max(0, received - processed),
                "lines": len(self.lines())}

    def _write_meta(self) -> None:
        if self.state == "discarded" or not self.directory.is_dir():
            return
        _write_json(self.directory / "meta.json", self.meta())

    def _write_transcript(self) -> None:
        if self.state == "discarded" or not self.directory.is_dir():
            return
        _write_json(self.directory / "transcript.json", {"id": self.id, "lines": self.lines()})

    @classmethod
    def reopen(cls, directory: Path, **hooks: Any) -> "Meeting":
        """A meeting from its files, to summarize again after an interruption."""
        meta = _read_json(directory / "meta.json", {}) or {}
        try:
            language = language_of(meta.get("language"))
        except MeetingError:
            language = ""
        meeting = cls(directory, str(meta.get("title") or "Meeting"), started=float(meta.get("started_at") or 0),
                      language=language, **hooks)
        meeting.state = "done"
        meeting.duration_ms = int(meta.get("duration_ms") or 0)
        meeting.ended_at = meta.get("ended_at")
        meeting.end_reason = str(meta.get("end_reason") or "")
        meeting.received = {track: int((meta.get("chunks") or {}).get(track) or 0) for track in TRACKS}
        meeting.session_id = str(meta.get("session_id") or "")
        meeting.engine = str(meta.get("engine") or "")
        meeting.model = str(meta.get("model") or "")
        meeting.system_audio = str(meta.get("system_audio") or "")
        meeting.system_audio_reason = str(meta.get("system_audio_reason") or "")
        meeting.system_audio_message = str(meta.get("system_audio_message") or "")
        meeting.diarization = str(meta.get("diarization") or "")
        meeting.diarization_reason = str(meta.get("diarization_reason") or "")
        meeting.diarization_message = str(meta.get("diarization_message") or "")
        meeting.speakers = int(meta.get("speakers") or 0)
        for track in TRACKS:
            for path in sorted((directory / "tracks" / track).glob("*.json")):
                record = _read_json(path)
                if isinstance(record, dict):
                    meeting._chunks[(track, int(record.get("seq") or 0))] = record
        meeting.transcribed = sum(1 for r in meeting._chunks.values() if r.get("segments"))
        meeting._lines = _transcript.merge(meeting._chunks[key] for key in sorted(meeting._chunks))
        meeting.settled.set()
        return meeting


_lock = threading.Lock()
_meetings: dict[str, Meeting] = {}


def _default_title(started: float) -> str:
    return time.strftime("Meeting %Y-%m-%d %H:%M", time.localtime(started))


def start(title: str = "", language: str = "", *, root: Path | None = None, **hooks: Any) -> Meeting:
    """Start recording a meeting; only one records at a time. ``language`` ``""`` detects it."""
    title = " ".join(str(title or "").split())[:160]
    language = language_of(language)
    with _lock:
        if any(m.state == "recording" for m in _meetings.values()):
            raise MeetingError("Another meeting is being recorded.")
        directory = (root or meetings_root()) / uuid.uuid4().hex[:12]
        directory.mkdir(parents=True)
        started = time.time()
        try:
            meeting = Meeting(directory, title or _default_title(started), started=started, language=language,
                              **hooks)
            meeting.begin()
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        _meetings[meeting.id] = meeting
        return meeting


def get(meeting_id: str) -> Meeting | None:
    with _lock:
        return _meetings.get(meeting_id)


def active() -> Meeting | None:
    with _lock:
        return next((m for m in _meetings.values() if m.state == "recording"), None)


def directory_of(meeting_id: str, *, root: Path | None = None) -> Path:
    if not meeting_id or len(meeting_id) > 32 or not all(c in "0123456789abcdef" for c in meeting_id):
        raise MeetingError("Unknown meeting.")
    directory = (root or meetings_root()) / meeting_id
    if not (directory / "meta.json").is_file():
        raise MeetingError("Unknown meeting.")
    return directory


def read_meta(directory: Path) -> dict:
    """A meeting's ``meta.json``; one left mid-way by an ended process reads ``interrupted``."""
    meta = _read_json(directory / "meta.json", {}) or {}
    live = get(directory.name)
    if live is not None and live.state != "discarded":
        return live.status()
    if meta.get("state") in LIVE_STATES:
        meta["state"] = "interrupted"
    meta["has_summary"] = (directory / "summary.md").is_file()
    return meta


def list_meetings(*, root: Path | None = None) -> list[dict]:
    """Every meeting, newest first."""
    base = root or meetings_root()
    if not base.is_dir():
        return []
    rows = [read_meta(child) for child in base.iterdir() if (child / "meta.json").is_file()]
    return sorted(rows, key=lambda row: float(row.get("started_at") or 0), reverse=True)


def transcript_lines(directory: Path) -> list[dict]:
    live = get(directory.name)
    if live is not None and live.state != "discarded":
        return live.lines()
    return list((_read_json(directory / "transcript.json", {}) or {}).get("lines") or [])


def resummarize(meeting_id: str, *, root: Path | None = None, **hooks: Any) -> Meeting:
    directory = directory_of(meeting_id, root=root)
    with _lock:
        meeting = _meetings.get(meeting_id)
        if meeting is None:
            meeting = Meeting.reopen(directory, **hooks)
            _meetings[meeting_id] = meeting
    meeting.resummarize()
    return meeting


def delete(meeting_id: str, *, root: Path | None = None) -> None:
    directory = directory_of(meeting_id, root=root)
    with _lock:
        meeting = _meetings.pop(meeting_id, None)
    if meeting is not None:
        meeting.discard()
    shutil.rmtree(directory, ignore_errors=True)


def stop_all(reason: str = "disabled") -> None:
    with _lock:
        live = [m for m in _meetings.values() if m.state == "recording"]
    for meeting in live:
        meeting.finish(reason)


__all__ = ["MAX_SECONDS", "Meeting", "MeetingError", "TRACKS", "active", "delete", "directory_of", "get",
           "language_of", "list_meetings", "meetings_root", "read_meta", "resummarize", "start", "stop_all",
           "transcript_lines"]
