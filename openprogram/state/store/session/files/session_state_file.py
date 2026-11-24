"""Small named pieces of a session's state, stored as lines of its log.

Jobs, notices, the inbox, todos, open work and always-on state each read and
replace a whole small document. Every replacement is one ``file.put`` line in
the session's log, so their full history stays with the session. Reading folds
those lines, with the run-state rows (``turn.rows``) in the same pass, and only
looks at what was appended since the last read; a first read starts from the
session's checkpoint (``session_log``) when there is one.
"""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from openprogram.host._platform.filesystem import filesystem_path

from .session_log import (
    AGENT_LOG_DIRECTORY,
    MAIN_LOG,
    RUN_ROWS_MARKER,
    RUN_ROWS_RECORD,
    STATE_FILE_MARKER,
    read_appended,
    read_records_checkpoint,
    write_records_checkpoint,
)

if TYPE_CHECKING:
    from ..session_store.service import SessionStore

_MARKERS = (STATE_FILE_MARKER, RUN_ROWS_MARKER)
# The Job runner reads every conversation's jobs every few seconds. Once a
# store has more conversations than this, each of those passes drops the
# oldest entries and reads their logs again from the checkpoint, so the
# limit sits well above a store's size; a conversation's documents are a
# few kilobytes.
_CACHED_DIRECTORIES = 4096
# A first read that had this much to go through leaves a checkpoint behind.
# Without one every process reads the session's logs whole to find a few
# kilobytes of documents: a Worker start read 200 MB over 600 conversations.
CHECKPOINT_AFTER_BYTES = 256 * 1024


class _Folded:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.offsets: dict[str, int] = {}
        self.inodes: dict[str, int] = {}
        self.damaged: set[str] = set()
        self.files: dict[str, str] = {}
        #: ``[ts, rows]`` of every ``turn.rows`` line; None once they were
        #: let go (``release_idle_run_rows``), and a reader of them starts over.
        self.run_rows: list | None = []
        self.rows_used_at = time.monotonic()
        self.started = False
        #: Bytes the checkpoint this began from covers; None once the first read is done.
        self.covered: int | None = None


_folded: "OrderedDict[str, _Folded]" = OrderedDict()
_folded_lock = threading.Lock()


def _folded_for(directory: Path) -> _Folded:
    key = str(directory)
    with _folded_lock:
        state = _folded.get(key)
        if state is None:
            state = _Folded()
            _folded[key] = state
            while len(_folded) > _CACHED_DIRECTORIES:
                _folded.popitem(last=False)
        _folded.move_to_end(key)
        return state


def forget_directory(directory: Path) -> None:
    """A session's directory was deleted or rewritten."""
    with _folded_lock:
        _folded.pop(str(directory), None)


def _log_files(directory: Path) -> list[str]:
    """The session's log files, as the strings its positions are keyed by.

    Every read of a document asks each file whether it grew, so the paths
    are joined as text: building a ``Path`` per file cost more than the
    ``stat`` it was built for.
    """
    base = str(directory)
    agents = os.path.join(base, AGENT_LOG_DIRECTORY)
    try:
        names = sorted(os.listdir(filesystem_path(agents)))
    except OSError:
        names = []
    return [os.path.join(base, MAIN_LOG),
            *(os.path.join(agents, name) for name in names if name.endswith(".jsonl"))]


def _start(directory: Path, state: _Folded) -> None:
    """A first read begins where the session's checkpoint ends."""
    state.started = True
    state.covered = 0
    found = read_records_checkpoint(directory)
    if found is None:
        return
    content, positions = found
    for key, position in positions.items():
        state.offsets[key] = position.offset
        state.inodes[key] = position.inode
        if position.damaged:
            state.damaged.add(key)
    state.files = dict(content.get("files") or {})
    state.run_rows = list(content.get("run_rows") or [])
    state.covered = sum(state.offsets.values())


def _new_records(key: str, state: _Folded) -> list[dict] | None:
    """The new lines of the log at ``key``; None when the file was replaced."""
    start = state.offsets.get(key, 0)
    try:
        info = os.stat(filesystem_path(key))
    except OSError:
        return []
    if key in state.inodes and (state.inodes[key] != info.st_ino or info.st_size < start):
        return None
    state.inodes[key] = info.st_ino
    if key in state.damaged or info.st_size == start:
        return []
    records, state.offsets[key], damaged = read_appended(Path(key), start, containing=_MARKERS)
    if damaged:
        state.damaged.add(key)
    return [record for record in records if record.get("type") in ("file.put", RUN_ROWS_RECORD)]


def _caught_up(directory: Path) -> _Folded:
    """The directory's folded records, with everything appended since read in."""
    state = _folded_for(directory)
    with state.lock:
        if not state.started:
            _start(directory, state)
        records: list[dict] = []
        for path in _log_files(directory):
            found = _new_records(path, state)
            if found is None:
                # A log was rewritten: read every file again from the start.
                state.reset()
                state.started = True
                state.covered = 0
                records = [record for log in _log_files(directory)
                           for record in (_new_records(log, state) or [])]
                break
            records.extend(found)
        _apply(state, records)
        covered, state.covered = state.covered, None
        if covered is not None and sum(state.offsets.values()) - covered >= CHECKPOINT_AFTER_BYTES:
            write_records_checkpoint(directory, state.offsets, state.inodes, state.damaged,
                                     state.files, state.run_rows)
        return state


def _apply(state: _Folded, records: list[dict]) -> None:
    records.sort(key=lambda record: record.get("ts", 0))
    for record in records:
        data = record.get("data") or {}
        if record.get("type") == RUN_ROWS_RECORD:
            if state.run_rows is not None:
                state.run_rows.append([record.get("ts", 0), data.get("rows") or []])
            continue
        name = data.get("name")
        if not name:
            continue
        if data.get("content") is None:
            state.files.pop(name, None)
        else:
            state.files[name] = data["content"]


def read_files(directory: Path) -> dict[str, str]:
    """Every state file of the session in ``directory``, current content."""
    state = _caught_up(directory)
    with state.lock:
        return dict(state.files)


def read_run_rows(directory: Path) -> list[list[dict]]:
    """The run-state rows of every committed change, in written order."""
    while True:
        state = _folded_for(directory)
        with state.lock:
            if state.run_rows is None:
                state.reset()
            state.rows_used_at = time.monotonic()
        state = _caught_up(directory)
        with state.lock:
            if state.run_rows is None:
                continue
            ordered = sorted(state.run_rows, key=lambda entry: entry[0])
        return [rows for _ts, rows in ordered]


def release_idle_run_rows(idle_seconds: float) -> int:
    """Let go of the run rows nobody read for this long; the documents stay.

    The rows are most of what a session's records weigh, and their one
    reader, the run tables, keeps its own copy of the sessions it has in
    memory. A later reader of a session's rows reads its records again.
    """
    cutoff = time.monotonic() - idle_seconds
    with _folded_lock:
        states = list(_folded.values())
    released = 0
    for state in states:
        if state.run_rows is None or state.rows_used_at > cutoff or not state.lock.acquire(blocking=False):
            continue
        try:
            if state.started and state.run_rows is not None and state.rows_used_at <= cutoff:
                state.run_rows = None
                released += 1
        finally:
            state.lock.release()
    return released


class SessionStateFile:
    """One named document; reads and writes like a text file."""

    def __init__(self, store: "SessionStore", directory: Path, name: str):
        self._store = store
        self.directory = directory
        self.name = name

    def exists(self) -> bool:
        return self.name in read_files(self.directory)

    def read_text(self, encoding: str = "utf-8") -> str:
        del encoding
        content = read_files(self.directory).get(self.name)
        if content is None:
            raise FileNotFoundError(f"{self.directory}: {self.name}")
        return content

    def write_text(self, content: str, encoding: str = "utf-8") -> None:
        del encoding
        self._store.write_state_line(self.directory, self.name, content)

    def with_suffix(self, suffix: str) -> Path:
        """A real file beside the log, used as this document's lock."""
        return self.directory / (Path(self.name).stem + suffix)
