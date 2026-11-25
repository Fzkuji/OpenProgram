"""Read only what an agent appended to a session file since the last scan.

A plain file resumes at the byte after its last complete line when it did
not shrink and the 64 bytes before that offset still hash the same;
otherwise it is read again from the start. A zstd archive is read whole,
and only when its size or modification time changed. A last line without
its newline is left for the next scan.
"""
from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass
from typing import Iterator, Optional

GUARD_BYTES = 64


@dataclass
class FileMark:
    """Where the last scan of one file stopped."""

    size: int
    mtime_ns: int
    offset: int
    guard: str


@dataclass
class Read:
    """One file's new lines. ``restarted`` is true when reading began at the
    start of a file that had been read before, so its parser state resets."""

    lines: Iterator[bytes]
    restarted: bool
    mark: FileMark


def _guard(fh, offset: int) -> str:
    start = max(0, offset - GUARD_BYTES)
    fh.seek(start)
    return hashlib.blake2b(fh.read(offset - start), digest_size=8).hexdigest()


def unchanged(path: str, prev: Optional[FileMark]) -> bool:
    if prev is None:
        return False
    try:
        st = os.stat(path)
    except OSError:
        return True
    return st.st_size == prev.size and st.st_mtime_ns == prev.mtime_ns


def read_new(path: str, prev: Optional[FileMark]) -> Optional[Read]:
    """The lines appended to ``path`` since ``prev``, or None when nothing
    changed or the file cannot be read. ``Read.mark`` is final only once
    ``lines`` has been consumed."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if prev is not None and st.st_size == prev.size and st.st_mtime_ns == prev.mtime_ns:
        return None
    mark = FileMark(size=st.st_size, mtime_ns=st.st_mtime_ns, offset=0, guard="")
    if path.endswith(".zst"):
        return Read(lines=_zst_lines(path, mark), restarted=prev is not None, mark=mark)
    start = 0
    try:
        with open(path, "rb") as fh:
            if prev is not None and prev.offset <= st.st_size and prev.offset > 0:
                if _guard(fh, prev.offset) == prev.guard:
                    start = prev.offset
    except OSError:
        return None
    restarted = prev is not None and start == 0
    return Read(lines=_plain_lines(path, start, mark), restarted=restarted, mark=mark)


def _plain_lines(path: str, start: int, mark: FileMark) -> Iterator[bytes]:
    offset = start
    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            for line in fh:
                if not line.endswith(b"\n"):
                    break
                offset += len(line)
                yield line
            mark.offset = offset
            mark.guard = _guard(fh, offset) if offset else ""
    except OSError:
        mark.size = -1  # read it again next scan
        mark.offset = offset
        mark.guard = ""


def _zst_lines(path: str, mark: FileMark) -> Iterator[bytes]:
    import zstandard

    try:
        with open(path, "rb") as fh:
            reader = io.BufferedReader(zstandard.ZstdDecompressor().stream_reader(fh))
            for line in reader:
                yield line
    except (OSError, zstandard.ZstdError):
        mark.size = -1  # read it again next scan
        return
    mark.offset = mark.size
