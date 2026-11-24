"""Drop the reply traces a later line of the same log replaced.

A reply's trace (``metadata.extra``) was once written whole on every change,
so a long turn left hundreds of full copies of one growing value in its log:
a conversation of 1,600 rows weighed 833 MB, 640 MB of it copies only the
last of which is ever read. A ``node.patch`` that sets the trace replaces
whatever came before it, so in each file every such line but a node's last
can lose the trace and nothing a fold hands out changes.

This rewrites files, which the log otherwise never does, so it is for a
session nothing is writing: no Worker, no helper process. The new file is
written beside the old one, the whole session is folded both ways, and the
old file is replaced only when the two folds are the same.
"""
from __future__ import annotations

import heapq
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from openprogram.host._platform.filesystem import filesystem_path, replace_file

from . import session_log as _log

_PATCH = b'"node.patch"'
_TRACE_MARKERS = (b'"extra_zlib"', b'"extra"')
_SUFFIX = ".compact.tmp"


@dataclass(frozen=True)
class Compaction:
    """What compacting one session did: log bytes before and after, traces dropped."""

    before: int
    after: int
    dropped: int


class CompactionMismatch(RuntimeError):
    """The rewritten logs fold to something else; nothing was replaced."""


def _lines(path: Path) -> Iterator[bytes]:
    with open(filesystem_path(path), "rb") as handle:
        yield from handle


def _trace_patch(line: bytes) -> dict | None:
    """The record on ``line`` when it is a patch that sets a reply trace."""
    if _PATCH not in line[:96] or not any(marker in line for marker in _TRACE_MARKERS):
        return None
    record = _log.decode_line(line.rstrip(b"\n"))
    if record is None or record.get("type") != "node.patch":
        return None
    data = record.get("data")
    if not isinstance(data, dict):
        return None
    metadata = data.get("metadata")
    if "extra_zlib" in data or (isinstance(metadata, dict) and "extra" in metadata):
        return record
    return None


def _rewrite(source: Path, target: Path) -> int:
    """Write ``source`` to ``target`` without the replaced traces; how many were dropped."""
    last: dict[str, int] = {}
    for index, line in enumerate(_lines(source)):
        record = _trace_patch(line)
        if record is not None:
            last[str(record["data"].get("id"))] = index
    keep = set(last.values())
    dropped = 0
    with open(filesystem_path(target), "wb") as handle:
        for index, line in enumerate(_lines(source)):
            record = None if index in keep else _trace_patch(line)
            if record is not None:
                data = {key: value for key, value in record["data"].items() if key != "extra_zlib"}
                metadata = data.get("metadata")
                if isinstance(metadata, dict) and "extra" in metadata:
                    data["metadata"] = {key: value for key, value in metadata.items() if key != "extra"}
                line = _log.encode_record(record["ts"], "node.patch", data)
                dropped += 1
            handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    return dropped


def _records(path: Path) -> Iterator[dict]:
    """Whole, undamaged records of one file, as a fold reads them."""
    for line in _lines(path):
        if not line.endswith(b"\n"):
            return  # a write cut short is never a record
        record = _log.decode_line(line[:-1])
        if record is None:
            return  # a fold stops at a damaged line
        yield record


def _folded(paths: list[Path]) -> str:
    """One string for everything the session's records add up to."""
    state = _log.FoldedSession()
    merged = heapq.merge(*(_records(path) for path in paths), key=lambda record: record.get("ts", 0))
    for record in merged:
        _log.apply_record(state, record)
    state = _log.settle(state)
    return json.dumps([state.nodes, state.meta, state.files, state.run_rows], sort_keys=True, default=str)


def session_logs(directory: Path) -> list[Path]:
    """The session's log files that exist, main first."""
    agents = Path(directory) / _log.AGENT_LOG_DIRECTORY
    found = [Path(directory) / _log.MAIN_LOG]
    if agents.is_dir():
        found += sorted(path for path in agents.iterdir() if path.name.endswith(".jsonl"))
    return [path for path in found if path.is_file()]


def compact_session(directory: Path, *, apply: bool = True) -> Compaction:
    """Compact every log of the session in ``directory``.

    With ``apply`` false the files are left as they are and the result says
    what compacting would do. Raises :class:`CompactionMismatch`, with every
    file untouched, when the rewritten logs do not fold to the same session.
    """
    directory = Path(directory)
    logs = session_logs(directory)
    before = sum(path.stat().st_size for path in logs)
    rewritten = {path: path.with_name(path.name + _SUFFIX) for path in logs}
    try:
        dropped = sum(_rewrite(path, target) for path, target in rewritten.items())
        after = sum(target.stat().st_size for target in rewritten.values())
        if not dropped:
            return Compaction(before, before, 0)
        if _folded(logs) != _folded(list(rewritten.values())):
            raise CompactionMismatch(f"{directory}: the compacted logs fold differently")
        if apply:
            for path, target in rewritten.items():
                if target.stat().st_size < path.stat().st_size:
                    replace_file(target, path)
            # Its offsets are of the files just replaced; the next open writes a new one.
            shutil.rmtree(filesystem_path(directory / _log.CHECKPOINT_DIRECTORY), ignore_errors=True)
        return Compaction(before, after, dropped)
    finally:
        for target in rewritten.values():
            try:
                os.unlink(filesystem_path(target))
            except OSError:
                pass


__all__ = ["Compaction", "CompactionMismatch", "compact_session", "session_logs"]
