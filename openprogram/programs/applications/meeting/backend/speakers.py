"""Which speaker said each line of the computer's sound.

The host diarizes the whole ``system`` track once a meeting stops
(``context.diarize``) and answers speaker turns on the meeting's clock.
:func:`assign` gives each transcribed system segment the speaker whose
turns overlap it most: ``others:1``, ``others:2``… numbered in order of
first appearance in the transcript, shown as "Speaker 1", "Speaker 2"….
"""
from __future__ import annotations

#: A voice heard for less than this over the whole meeting gets no number: on a real
#: four-person meeting the clustering leaves a few one- or two-second strays beside the
#: four real speakers, and a numbered "Speaker 7" for two seconds misleads more than "Others".
MIN_SPEAKER_MS = 3000

#: A speaker turn on the meeting clock: ``(start_ms, end_ms, speaker)``.
Turn = tuple[int, int, int]


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


__all__ = ["MIN_SPEAKER_MS", "Turn", "assign"]
