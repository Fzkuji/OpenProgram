"""One time-ordered transcript from the two tracks of a meeting.

The microphone track is the user ("me"), the system-audio track everyone
else in the call ("others"; once the system track is diarized,
``others:1``, ``others:2``…, shown as "Speaker 1", "Speaker 2"…). Each chunk's segments carry times from the
chunk's own start; adding the chunk's offset puts them on the meeting's
clock. Without headphones the microphone also hears the other side, so a
microphone line that repeats an overlapping system-audio line is echo and
is dropped. Consecutive lines of one speaker join into one turn.
"""
from __future__ import annotations

import bisect
import difflib
import re
from typing import Iterable

SPEAKERS = {"mic": "me", "system": "others"}
LABELS = {"me": "Me", "others": "Others"}

#: Lines of one speaker this close together (ms) join into one turn.
JOIN_GAP_MS = 1500
JOIN_MAX_CHARS = 600
#: How far apart (ms) an echo may start from the line it repeats.
ECHO_WINDOW_MS = 4000
ECHO_SIMILARITY = 0.75


def clock(ms: int) -> str:
    """``01:02:03`` (or ``02:03`` under an hour) for a time in ms."""
    seconds = max(0, int(ms)) // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.lower()).strip()


def absolute(chunks: Iterable[dict]) -> list[dict]:
    """Every chunk's segments on the meeting clock, with their speaker, in time order.

    A chunk is ``{"track": "mic"|"system", "t_ms", "segments": [{"start_ms",
    "end_ms", "text"}]}``; a segment may carry ``speaker`` already (a
    diarized system track: ``others:1``, ``others:2``…).
    """
    rows = []
    for chunk in chunks:
        offset = int(chunk.get("t_ms") or 0)
        speaker = SPEAKERS.get(str(chunk.get("track")), "others")
        for segment in chunk.get("segments") or []:
            text = " ".join(str(segment.get("text") or "").split())
            if not text:
                continue
            start = offset + max(0, int(segment.get("start_ms") or 0))
            end = max(start, offset + int(segment.get("end_ms") or 0))
            rows.append({"start_ms": start, "end_ms": end,
                         "speaker": str(segment.get("speaker") or speaker), "text": text})
    rows.sort(key=lambda row: (row["start_ms"], row["speaker"] != "me"))
    return rows


def drop_echo(rows: list[dict]) -> list[dict]:
    """Remove microphone lines that repeat a system-audio line said at the same time."""
    others = sorted((row for row in rows if row["speaker"] != "me"), key=lambda row: row["start_ms"])
    starts = [row["start_ms"] for row in others]
    kept = []
    for row in rows:
        if row["speaker"] == "me" and (mine := _norm(row["text"])):
            low = bisect.bisect_left(starts, row["start_ms"] - ECHO_WINDOW_MS)
            high = bisect.bisect_right(starts, row["start_ms"] + ECHO_WINDOW_MS)
            if any(difflib.SequenceMatcher(None, mine, _norm(other["text"])).ratio() >= ECHO_SIMILARITY
                   for other in others[low:high]):
                continue
        kept.append(row)
    return kept


def join_turns(rows: list[dict]) -> list[dict]:
    """Consecutive lines of one speaker, close together, as one turn."""
    turns: list[dict] = []
    for row in rows:
        last = turns[-1] if turns else None
        if (last is not None and last["speaker"] == row["speaker"]
                and row["start_ms"] - last["end_ms"] <= JOIN_GAP_MS
                and len(last["text"]) + len(row["text"]) < JOIN_MAX_CHARS):
            last["text"] = f"{last['text']} {row['text']}"
            last["end_ms"] = max(last["end_ms"], row["end_ms"])
        else:
            turns.append(dict(row))
    return turns


def merge(chunks: Iterable[dict]) -> list[dict]:
    """The meeting's transcript: echo removed, turns joined, in time order, each line labelled."""
    return [{**line, "label": label(line["speaker"])} for line in join_turns(drop_echo(absolute(chunks)))]


def label(speaker: str) -> str:
    """``Me``, ``Others``, or ``Speaker <n>`` for a told-apart speaker on the system track."""
    base, _, number = speaker.partition(":")
    if base == "others" and number:
        return f"Speaker {number}"
    name = LABELS.get(base, base.capitalize() or "Others")
    return f"{name} {number}" if number else name


def as_text(lines: list[dict]) -> str:
    """``[03:12] Me: …`` per line."""
    return "\n".join(f"[{clock(line['start_ms'])}] {label(line['speaker'])}: {line['text']}" for line in lines)


__all__ = ["LABELS", "SPEAKERS", "absolute", "as_text", "clock", "drop_echo", "join_turns", "label", "merge"]
