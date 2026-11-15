"""Telling apart the speakers on the system-audio track.

Two tracks already separate the user ("me", the microphone) from everyone
else ("others", the computer's sound output). When a meeting stops, after
every chunk is transcribed, :func:`run` diarizes the whole system track
once with sherpa-onnx's offline speaker diarization (pyannote
segmentation-3.0 plus a 3D-Speaker ERes2Net embedding model, both ONNX)
and :func:`assign` gives each transcribed system-audio segment the
speaker whose turns overlap it most: ``others:1``, ``others:2``… numbered
in order of first appearance, shown as "Speaker 1", "Speaker 2"….

The number of speakers is not known, so clustering uses a distance
threshold rather than a fixed count. ``sherpa-onnx`` is an optional
dependency (the ``meetings`` extra) and is imported only here, only when a
meeting stops. The two models are downloaded into
``<state>/models/diarization/`` from sherpa-onnx's GitHub releases on
first use. Without the package or the models, :func:`run` raises
:class:`Unavailable` (``not_installed`` / ``no_model``) and the system
track stays "Others".
"""
from __future__ import annotations

import importlib.util
import io
import os
import tarfile
import tempfile
import threading
import wave
from pathlib import Path
from typing import Callable, Iterable
from openprogram.host._platform.filesystem import replace_file

RATE = 16000
#: Clustering distance: smaller finds more speakers, larger fewer. With the CAM++ embeddings
#: 0.6 to 0.75 finds the right speakers on every recording tried: pyannote's AMI meeting
#: excerpt (two people talking over each other, 91% of speech labelled right), sherpa-onnx's
#: four-speaker test recording, a two-voice recording and four synthesized voices; 0.8 merges
#: two of the four Chinese speakers. The top of that range splits one person into two numbers
#: least often on a four-person AMI meeting heard through a conference call (Opus, noise
#: suppression) and through plain playback: seven numbered voices at 0.65, five at 0.75.
#: The model is 3D-Speaker's bilingual (zh/en) CAM++; the ERes2Net zh-cn model sherpa-onnx's
#: example uses merged the AMI pair into one speaker at every threshold above 0.7.
CLUSTER_THRESHOLD = 0.75
#: A voice heard for less than this over the whole meeting gets no number: on a real
#: four-person meeting the clustering leaves a few one- or two-second strays beside the
#: four real speakers, and a numbered "Speaker 7" for two seconds misleads more than "Others".
MIN_SPEAKER_MS = 3000
#: ONNX threads for segmentation and embeddings (four are about three times faster than one).
THREADS = max(1, min(4, os.cpu_count() or 1))

_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION_URL = f"{_RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
SEGMENTATION_FILE = "pyannote-segmentation-3-0.onnx"
EMBEDDING_FILE = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
EMBEDDING_URL = f"{_RELEASES}/speaker-recongition-models/{EMBEDDING_FILE}"

#: A speaker turn on the meeting clock: ``(start_ms, end_ms, speaker)``.
Turn = tuple[int, int, int]
#: ``diarize([(t_ms, wav), …])`` → turns; the meeting's hook (tests pass a fake).
Diarize = Callable[[list[tuple[int, bytes]]], list[Turn]]


class Unavailable(RuntimeError):
    """Diarization cannot run; ``reason`` is ``not_installed`` or ``no_model``."""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


def models_dir() -> Path:
    from openprogram.state.paths import get_state_dir
    return Path(get_state_dir()) / "models" / "diarization"


def installed() -> bool:
    return importlib.util.find_spec("sherpa_onnx") is not None


# ---------------------------------------------------------------------- models
_download_lock = threading.Lock()


def _fetch(url: str, target: Path) -> None:
    """``url`` into ``target`` through a temporary file and an atomic rename."""
    import httpx

    target.parent.mkdir(parents=True, exist_ok=True)
    fh = tempfile.NamedTemporaryFile(dir=target.parent, prefix=f"{target.name}.", suffix=".part", delete=False)
    partial = Path(fh.name)
    try:
        with fh:
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}")
                encoded = response.headers.get("content-encoding", "identity") != "identity"
                expected = None if encoded else response.headers.get("content-length")
                written = 0
                for chunk in response.iter_bytes(1 << 20):
                    fh.write(chunk)
                    written += len(chunk)
        if written == 0 or (expected and expected.isdigit() and written != int(expected)):
            raise RuntimeError(f"incomplete download ({written} of {expected or '?'} bytes)")
        replace_file(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def _fetch_segmentation(target: Path) -> None:
    """The archive's ``model.onnx``, extracted alone into ``target``."""
    archive = target.with_name(target.name + ".tar.bz2")
    _fetch(SEGMENTATION_URL, archive)
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            member = next((m for m in tar.getmembers()
                           if m.isfile() and m.name.endswith("/model.onnx")), None)
            if member is None:
                raise RuntimeError("the segmentation archive has no model.onnx")
            source = tar.extractfile(member)
            assert source is not None
            partial = target.with_name(target.name + ".part")
            with source, open(partial, "wb") as out:
                while block := source.read(1 << 20):
                    out.write(block)
            replace_file(partial, target)
    finally:
        archive.unlink(missing_ok=True)


def download(directory: Path | None = None) -> tuple[Path, Path]:
    """The two model files, downloading whichever is missing (once, under a lock)."""
    directory = directory or models_dir()
    segmentation, embedding = directory / SEGMENTATION_FILE, directory / EMBEDDING_FILE
    ready = lambda path: path.is_file() and path.stat().st_size > 0  # noqa: E731
    if ready(segmentation) and ready(embedding):
        return segmentation, embedding
    with _download_lock:
        try:
            if not ready(segmentation):
                _fetch_segmentation(segmentation)
            if not ready(embedding):
                _fetch(EMBEDDING_URL, embedding)
        except Exception as exc:
            raise Unavailable("no_model", f"The speaker models could not be downloaded: {exc}") from exc
    return segmentation, embedding


# ---------------------------------------------------------------------- audio
def concatenate(chunks: Iterable[tuple[int, bytes]]) -> tuple[bytes, list[tuple[int, int, int]]]:
    """16 kHz mono 16-bit PCM of the chunks in order, and where each one sits.

    Returns the PCM and one ``(concat_ms, t_ms, duration_ms)`` span per
    chunk: where it starts in the concatenation, where on the meeting clock.
    """
    pcm = bytearray()
    spans = []
    for t_ms, wav in chunks:
        try:
            with wave.open(io.BytesIO(wav)) as reader:
                if (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) != (RATE, 1, 2):
                    continue
                frames = reader.readframes(reader.getnframes())
        except (wave.Error, EOFError):
            continue
        if not frames:
            continue
        spans.append((len(pcm) // 2 * 1000 // RATE, int(t_ms), len(frames) // 2 * 1000 // RATE))
        pcm += frames
    return bytes(pcm), spans


def to_meeting_clock(turns: Iterable[tuple[float, float, int]], spans: list[tuple[int, int, int]]) -> list[Turn]:
    """Turns in seconds of the concatenation as turns in ms on the meeting clock.

    A turn that crosses a chunk boundary is split there, so a gap between
    chunks never stretches a turn.
    """
    out = []
    for start_s, end_s, speaker in turns:
        start, end = int(start_s * 1000), int(end_s * 1000)
        for concat_ms, t_ms, duration in spans:
            low, high = max(start, concat_ms), min(end, concat_ms + duration)
            if high > low:
                out.append((t_ms + low - concat_ms, t_ms + high - concat_ms, int(speaker)))
    return sorted(out)


def _sherpa(segmentation: Path, embedding: Path):
    import sherpa_onnx

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation)),
            num_threads=THREADS,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(embedding), num_threads=THREADS),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=CLUSTER_THRESHOLD),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise Unavailable("no_model", "The speaker models are incomplete.")
    return sherpa_onnx.OfflineSpeakerDiarization(config)


def run(chunks: list[tuple[int, bytes]]) -> list[Turn]:
    """Speaker turns of the whole system track: ``chunks`` are its ``(t_ms, wav)`` in order."""
    if not installed():
        raise Unavailable("not_installed", "Speaker separation needs the optional sherpa-onnx package.")
    segmentation, embedding = download()
    import numpy

    pcm, spans = concatenate(chunks)
    if not spans:
        return []
    samples = numpy.frombuffer(pcm, dtype="<i2").astype(numpy.float32) / 32768.0
    diarizer = _sherpa(segmentation, embedding)
    result = diarizer.process(samples).sort_by_start_time()
    return to_meeting_clock(((r.start, r.end, r.speaker) for r in result), spans)


# ---------------------------------------------------------------------- labels
def _speaker_of(start: int, end: int, turns: list[Turn]) -> int | None:
    """The speaker whose turns overlap ``[start, end)`` most; ``None`` when none do."""
    overlap: dict[int, int] = {}
    for low, high, speaker in turns:
        if low >= end:
            break
        shared = min(end, high) - max(start, low)
        if shared > 0:
            overlap[speaker] = overlap.get(speaker, 0) + shared
    if not overlap:
        return None
    return max(overlap, key=lambda speaker: (overlap[speaker], -speaker))


def assign(records: list[dict], turns: list[Turn]) -> list[dict]:
    """The system track's chunk records with ``speaker: "others:<n>"`` on each segment.

    ``n`` counts speakers in order of first appearance in the transcript;
    a segment no turn overlaps keeps no speaker (it reads "Others"), and so
    does one whose only voice was heard for less than ``MIN_SPEAKER_MS`` in all.
    """
    heard: dict[int, int] = {}
    for low, high, speaker in turns:
        heard[speaker] = heard.get(speaker, 0) + (high - low)
    turns = sorted(turn for turn in turns if heard[turn[2]] >= MIN_SPEAKER_MS)
    found = []
    for record in records:
        offset = int(record.get("t_ms") or 0)
        for index, segment in enumerate(record.get("segments") or []):
            start = offset + max(0, int(segment.get("start_ms") or 0))
            end = max(start + 1, offset + int(segment.get("end_ms") or 0))
            found.append((start, index, record, segment, _speaker_of(start, end, turns)))
    numbers: dict[int, int] = {}
    labelled = {id(record): dict(record, segments=[dict(s) for s in record.get("segments") or []])
                for record in records}
    for _start, index, record, segment, speaker in sorted(found, key=lambda row: (row[0], row[1])):
        row = labelled[id(record)]["segments"][index]
        row.pop("speaker", None)
        if speaker is not None:
            number = numbers.setdefault(speaker, len(numbers) + 1)
            row["speaker"] = f"others:{number}"
    return [labelled[id(record)] for record in records]


__all__ = ["CLUSTER_THRESHOLD", "Diarize", "MIN_SPEAKER_MS", "Turn", "Unavailable", "assign", "concatenate", "download",
           "installed", "models_dir", "run", "to_meeting_clock"]
