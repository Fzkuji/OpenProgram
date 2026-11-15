"""Keyed records in one append-only file.

For small, bounded stores with simple lookups. The file is the only truth;
every process folds it into memory and catches up under a file lock before
each read or write, so processes sharing a file always see each other's work.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Iterator, Mapping

from openprogram.state.store.session.session_lock import registry_file_lock
from openprogram.host._platform.filesystem import O_BINARY, replace_file

_log = logging.getLogger(__name__)

# Superseded lines tolerated beyond the live records before a rewrite.
_DEAD_LINE_ALLOWANCE = 256


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(descriptor, view):]
    os.fsync(descriptor)


class RecordLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.records: dict[str, dict[str, Any]] = {}
        self._mutex = threading.Lock()
        self._file_id: int | None = None
        self._offset = 0
        self._lines = 0

    @contextmanager
    def locked(self) -> Iterator["RecordLog"]:
        """Hold the file exclusively with ``records`` up to date. Does not nest."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._mutex, registry_file_lock(self.path.parent, self.path.name):
            self._catch_up()
            yield self

    def put(self, key: str, record: Mapping[str, Any]) -> None:
        record = dict(record)
        self.records[key] = record
        self._append([{"k": key, "r": record}])

    def delete(self, keys: list[str]) -> None:
        present = [key for key in keys if key in self.records]
        if not present:
            return
        for key in present:
            del self.records[key]
        self._append([{"k": key, "gone": True} for key in present])

    def _forget(self) -> None:
        self.records.clear()
        self._file_id = None
        self._offset = 0
        self._lines = 0

    def _apply(self, raw: bytes) -> None:
        try:
            line = json.loads(raw)
            key = line["k"]
            if line.get("gone"):
                self.records.pop(key, None)
            else:
                self.records[key] = line["r"]
        except (ValueError, KeyError, TypeError):
            _log.warning("skipping an unreadable line in %s", self.path)
        self._lines += 1

    def _catch_up(self) -> None:
        try:
            status = self.path.stat()
        except FileNotFoundError:
            self._forget()
            return
        if status.st_ino != self._file_id or status.st_size < self._offset:
            self._forget()
            self._file_id = status.st_ino
        if status.st_size == self._offset:
            return
        with self.path.open("rb") as handle:
            handle.seek(self._offset)
            unread = handle.read()
        complete = unread.rfind(b"\n") + 1
        for raw in unread[:complete].split(b"\n")[:-1]:
            self._apply(raw)
        self._offset += complete
        if complete < len(unread):
            # A write that never finished: this lock means nobody is still writing it.
            os.truncate(self.path, self._offset)

    @staticmethod
    def _encode(lines: list[dict[str, Any]]) -> bytes:
        return "".join(
            json.dumps(line, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
            for line in lines
        ).encode("utf-8")

    def _append(self, lines: list[dict[str, Any]]) -> None:
        data = self._encode(lines)
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | O_BINARY, 0o600)
            try:
                _write_all(descriptor, data)
                self._file_id = os.fstat(descriptor).st_ino
            finally:
                os.close(descriptor)
        except OSError:
            # Memory already moved on; the next catch-up rereads what the file holds.
            self._forget()
            raise
        self._offset += len(data)
        self._lines += len(lines)
        if self._lines > 2 * len(self.records) + _DEAD_LINE_ALLOWANCE:
            self._rewrite()

    def _rewrite(self) -> None:
        data = self._encode([{"k": key, "r": record} for key, record in self.records.items()])
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | O_BINARY, 0o600)
        try:
            _write_all(descriptor, data)
            file_id = os.fstat(descriptor).st_ino
        finally:
            os.close(descriptor)
        replace_file(temporary, self.path)
        self._file_id = file_id
        self._offset = len(data)
        self._lines = len(self.records)


_logs: dict[str, RecordLog] = {}
_logs_guard = threading.Lock()


def record_log(path: str | Path) -> RecordLog:
    """The one log object this process keeps for a file."""
    key = os.path.abspath(path)
    with _logs_guard:
        log = _logs.get(key)
        if log is None:
            log = _logs[key] = RecordLog(key)
        return log
