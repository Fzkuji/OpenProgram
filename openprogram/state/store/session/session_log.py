"""One conversation, one append-only log.

A session is the set of records in its directory:

    <session>/main.jsonl          written by the process holding worker.lock
    <session>/agents/<id>.jsonl   one file per other writing process

Every file has exactly one writer, so no record is ever overwritten and no
file needs a lock. A session's state is the fold of its records in timestamp
order; the same fold rebuilds it after a restart. A line is a JSON object
ending in a CRC of everything before it, so a line cut short by a dying
process is recognised and dropped instead of being half-applied.

Record types:

    node.put     {payload}                a node's full content
    node.patch   {id, set, metadata}      changed fields of a node
    node.delete  {ids}                    nodes removed from the session
    node.stream  {id, from, blocks, text, stream_seq, output, output_tail}
                                          what a streaming reply added since
                                          its last line (``apply_stream_changes``)
    meta.patch   {set, unset}             changed session-level fields
    file.put     {name, content}          a small state file's new content

Unknown types are skipped by the fold, so runtime records can share the file.

A reply's ``metadata.extra`` is folded lazily: a ``node.patch`` that replaced
it (often stored compressed) and the ``node.stream`` lines after it are kept
aside and turned into the one current value when the fold is handed out, so
the versions a later line replaced are never decompressed or parsed. The
text those lines add, to a block and to the reply's ``output``, is kept as
pieces and joined once there, so a reply costs its length to fold however
many lines it streamed in.

A fold that read a lot of log writes a checkpoint (``checkpoint/``): the
folded state with the offset it covers in each file. Opening the session
again reads the checkpoint and only the lines after those offsets, so a
cold open costs what the conversation holds, not how long its log is.
"""
from __future__ import annotations

import base64
import copy
import json
import logging
import os
import threading
import time
import uuid
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from openprogram.host._platform.filesystem import filesystem_path, remove_tree, replace_file

_log = logging.getLogger(__name__)

TURN_RECORD_PREFIX = "turn."
RUN_ROWS_RECORD = "turn.rows"
RUN_ROWS_MARKER = b'"turn.rows"'
STATE_FILE_MARKER = b'"file.put"'
MAIN_LOG = "main.jsonl"
AGENT_LOG_DIRECTORY = "agents"
CHECKPOINT_DIRECTORY = "checkpoint"
FOLD_CHECKPOINT = "fold.json"
RECORDS_CHECKPOINT = "records.json"
#: A fold writes a checkpoint once it read at least this much log past the
#: last one, and at least as much as that checkpoint weighs, so checkpoints
#: never cost more than twice the log they cover.
CHECKPOINT_MIN_BYTES = 4 * 1024 * 1024
_CHECKPOINT_VERSION = 1
_CHECKPOINT_TAIL = 32

_CRC_FIELD = ',"crc":"'
_CRC_SUFFIX_LENGTH = len(_CRC_FIELD) + 8 + len('"}')
_APPEND_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
_META_CACHE_CAPACITY = 1024


# Records


def encode_record(timestamp: int, record_type: str, data: Any) -> bytes:
    """One log line: the record, then a CRC of the bytes before the CRC field."""
    if record_type == "node.patch":
        metadata = data.get("metadata") or {}
        extra = metadata.get("extra")
        if isinstance(extra, str) and len(extra) >= 8192:
            packed = base64.b64encode(zlib.compress(extra.encode("utf-8"), level=1)).decode("ascii")
            if len(packed) < len(extra):
                data = {**data, "metadata": {k: v for k, v in metadata.items() if k != "extra"},
                        "extra_zlib": packed}
    body = json.dumps(
        {"ts": timestamp, "type": record_type, "data": data},
        ensure_ascii=False, default=str, separators=(",", ":"),
    )[:-1].encode("utf-8")
    checksum = zlib.crc32(body) & 0xFFFFFFFF
    return body + f'{_CRC_FIELD}{checksum:08x}"}}\n'.encode("ascii")


def decode_line(line: bytes) -> Optional[dict]:
    """The record on ``line`` (no newline), or None when it fails its CRC."""
    if len(line) <= _CRC_SUFFIX_LENGTH:
        return None
    body, suffix = line[:-_CRC_SUFFIX_LENGTH], line[-_CRC_SUFFIX_LENGTH:]
    if not suffix.startswith(_CRC_FIELD.encode("ascii")):
        return None
    try:
        expected = int(suffix[len(_CRC_FIELD):len(_CRC_FIELD) + 8], 16)
    except ValueError:
        return None
    if zlib.crc32(body) & 0xFFFFFFFF != expected:
        return None
    try:
        return json.loads(body + b"}")
    except (ValueError, TypeError):
        return None


def unpack_extra(packed: str) -> str:
    """The ``metadata.extra`` a ``node.patch`` stored as ``extra_zlib``."""
    return zlib.decompress(base64.b64decode(packed, validate=True)).decode("utf-8")


def read_appended(path: Path, start: int = 0, *, containing: bytes | tuple[bytes, ...] | None = None,
                  ) -> tuple[list[dict], int, bool]:
    """Whole records in ``path`` after byte ``start``.

    Returns the records, the offset to continue from, and whether reading
    stopped at a damaged line. Bytes after the last newline are a write still
    in progress or cut short, and are never a record. ``containing`` skips,
    undecoded, every line without those bytes (any of them, for a tuple).
    A ``node.patch`` keeps a compressed ``extra_zlib`` as it is stored
    (``unpack_extra``).
    """
    try:
        with open(filesystem_path(path), "rb") as handle:
            handle.seek(start)
            content = handle.read()
    except OSError:
        return [], start, False
    end = content.rfind(b"\n")
    if end < 0:
        return [], start, False
    markers = (containing,) if isinstance(containing, bytes) else containing
    records: list[dict] = []
    for line in content[:end].split(b"\n"):
        if markers is not None and not any(marker in line for marker in markers):
            continue
        record = decode_line(line)
        if record is None:
            _log.error("session log %s is damaged after byte %d; reading stops there",
                       path, start)
            return records, start + end + 1, True
        records.append(record)
    return records, start + end + 1, False


def read_log_file(path: Path, *, containing: bytes | None = None) -> list[dict]:
    """Every whole record in ``path``, stopping at the first damaged line."""
    return read_appended(path, containing=containing)[0]


# Fold


@dataclass
class _PendingExtra:
    """A node's ``metadata.extra`` not worked out yet: the compressed value
    a patch stored (None: the node's own), then the stream changes after it."""

    packed: Optional[str] = None
    changes: list = field(default_factory=list)


@dataclass
class FoldedSession:
    """What a session's records add up to."""

    nodes: dict[str, dict] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    #: ``[ts, rows]`` of every ``turn.rows`` line, in the order folded.
    run_rows: list = field(default_factory=list)
    pending: dict[str, _PendingExtra] = field(default_factory=dict)
    #: The ``output_tail`` pieces not yet joined onto each node's ``output``.
    tails: dict[str, list[str]] = field(default_factory=dict)

    def copy(self) -> "FoldedSession":
        return FoldedSession(dict(self.nodes), dict(self.meta), dict(self.files),
                             list(self.run_rows), dict(self.pending),
                             {node_id: list(pieces) for node_id, pieces in self.tails.items()})


def apply_stream_changes(extra: dict, changes: Iterable[dict]) -> None:
    """Advance a reply's parsed ``extra`` by its ``node.stream`` changes, in order.

    ``text: [i, s]`` appends ``s`` to block ``i``'s text; ``from: k`` with
    ``blocks`` replaces every block from ``k`` on; ``stream_seq`` is the
    stream frame the content now reflects. A block's added text is joined
    once, after the last change.
    """
    blocks = extra.get("blocks")
    if not isinstance(blocks, list):
        blocks = extra["blocks"] = []
    added: dict[int, list[str]] = {}
    for change in changes:
        text = change.get("text")
        if isinstance(text, list) and len(text) == 2:
            index, piece = text
            if isinstance(index, int) and 0 <= index < len(blocks) and isinstance(blocks[index], dict):
                added.setdefault(index, []).append(str(piece))
        start = change.get("from")
        if isinstance(start, int) and start >= 0:
            for index in [index for index in added if index >= start]:
                del added[index]
            del blocks[start:]
            blocks.extend(change.get("blocks") or [])
        if "stream_seq" in change:
            extra["stream_seq"] = change["stream_seq"]
    for index, pieces in added.items():
        blocks[index] = {**blocks[index], "text": join_text(blocks[index].get("text"), pieces)}


def join_text(current: Any, pieces: list[str]) -> str:
    """``current`` with ``pieces`` after it: the one place streamed text is put together."""
    return "".join((str(current or ""), *pieces))


def _settled_extra(current: Any, pending: _PendingExtra) -> Any:
    value = unpack_extra(pending.packed) if pending.packed is not None else current
    if not pending.changes:
        return value
    if isinstance(value, dict):
        extra = copy.deepcopy(value)
    else:
        try:
            extra = json.loads(value) if value else {}
        except (TypeError, ValueError):
            extra = {}
        if not isinstance(extra, dict):
            extra = {}
    apply_stream_changes(extra, pending.changes)
    return extra if isinstance(value, dict) else json.dumps(extra, default=str)


def settle(state: FoldedSession) -> FoldedSession:
    """Work out every pending ``extra`` and ``output``: once per node, whatever the log holds."""
    for node_id, pieces in state.tails.items():
        node = state.nodes.get(node_id)
        if node is not None:
            state.nodes[node_id] = {**node, "output": join_text(node.get("output"), pieces)}
    state.tails = {}
    for node_id, pending in state.pending.items():
        node = state.nodes.get(node_id)
        if node is None:
            continue
        current = node.get("metadata")
        current = current if isinstance(current, dict) else {}
        try:
            extra = _settled_extra(current.get("extra"), pending)
        except (ValueError, TypeError, zlib.error):
            _log.error("a stored reply trace of node %s cannot be read; it keeps its earlier one", node_id)
            continue
        state.nodes[node_id] = {**node, "metadata": {**current, "extra": extra}}
    state.pending = {}
    return state


def _patched(state: FoldedSession, node_id: str, fields: dict, metadata: Any) -> Optional[dict]:
    """A new copy of the node with ``fields`` and ``metadata`` merged; earlier
    folds handed the old payload out to readers."""
    node = state.nodes.get(node_id)
    if node is None:
        return None
    node = {**node, **fields}
    if metadata:
        current = node.get("metadata")
        node["metadata"] = {**(current if isinstance(current, dict) else {}), **metadata}
    state.nodes[node_id] = node
    return node


def apply_record(state: FoldedSession, record: dict) -> None:
    """Advance ``state`` by one record; ``settle`` before reading it."""
    record_type = record.get("type")
    data = record.get("data") or {}
    if record_type == "node.put":
        payload = data.get("payload") or {}
        node_id = payload.get("id")
        if node_id:
            state.nodes[node_id] = payload
            state.pending.pop(node_id, None)
            state.tails.pop(node_id, None)
    elif record_type == "node.patch":
        node_id = data.get("id")
        metadata = data.get("metadata")
        fields = data.get("set") or {}
        if _patched(state, node_id, fields, metadata) is None:
            return
        if "output" in fields:
            state.tails.pop(node_id, None)
        if "extra_zlib" in data:
            state.pending[node_id] = _PendingExtra(packed=data["extra_zlib"])
        elif isinstance(metadata, dict) and "extra" in metadata:
            state.pending.pop(node_id, None)
    elif record_type == "node.stream":
        node_id = data.get("id")
        fields = {"output": data["output"]} if "output" in data else {}
        if _patched(state, node_id, fields, data.get("metadata")) is None:
            return
        if "output" in data:
            state.tails.pop(node_id, None)
        elif "output_tail" in data:
            state.tails.setdefault(node_id, []).append(str(data["output_tail"]))
        state.pending.setdefault(node_id, _PendingExtra()).changes.append(data)
    elif record_type == "node.delete":
        for node_id in data.get("ids") or []:
            state.nodes.pop(node_id, None)
            state.pending.pop(node_id, None)
            state.tails.pop(node_id, None)
    elif record_type == "meta.patch":
        state.meta.update(data.get("set") or {})
        for key in data.get("unset") or []:
            state.meta.pop(key, None)
    elif record_type == "file.put":
        name = data.get("name")
        if not name:
            return
        if data.get("content") is None:
            state.files.pop(name, None)
        else:
            state.files[name] = data["content"]
    elif record_type == RUN_ROWS_RECORD:
        state.run_rows.append([record.get("ts", 0), data.get("rows") or []])


@dataclass
class _LogPosition:
    offset: int = 0
    inode: int = 0
    damaged: bool = False


@dataclass
class _FoldProgress:
    """How far a session's files have been folded, and what that added up to."""

    positions: dict[str, _LogPosition] = field(default_factory=dict)
    folded: FoldedSession = field(default_factory=FoldedSession)
    newest: int = 0
    #: Log bytes folded since the last checkpoint, and that checkpoint's size.
    uncovered: int = 0
    checkpoint_size: int = 0


def fold_records(records: Iterable[dict]) -> FoldedSession:
    state = FoldedSession()
    for record in records:
        apply_record(state, record)
    return settle(state)


# Checkpoints


def _log_name(directory: Path, path: Path) -> str:
    return path.relative_to(directory).as_posix()


def _tail_of(path: Path, offset: int) -> Optional[str]:
    """The bytes just before ``offset``: a copy or a rewrite of the file
    under the same inode does not end its covered part the same way."""
    if offset <= 0:
        return ""
    try:
        with open(filesystem_path(path), "rb") as handle:
            handle.seek(max(0, offset - _CHECKPOINT_TAIL))
            return handle.read(min(offset, _CHECKPOINT_TAIL)).hex()
    except OSError:
        return None


def _checkpoint_positions(directory: Path, positions: dict[str, _LogPosition]) -> Optional[dict]:
    stored = {}
    for key, position in positions.items():
        tail = _tail_of(Path(key), position.offset)
        if tail is None:
            return None
        stored[_log_name(directory, Path(key))] = [position.offset, position.inode, position.damaged, tail]
    return stored


def _restored_positions(directory: Path, stored: Any) -> Optional[dict[str, _LogPosition]]:
    """The positions a checkpoint covers, or None when its files changed."""
    if not isinstance(stored, dict):
        return None
    positions: dict[str, _LogPosition] = {}
    for name, entry in stored.items():
        path = directory / name
        try:
            offset, inode, damaged, tail = entry
            info = os.stat(filesystem_path(path))
        except (OSError, TypeError, ValueError):
            return None
        if info.st_ino != inode or info.st_size < offset or _tail_of(path, offset) != tail:
            return None
        positions[str(path)] = _LogPosition(offset=offset, inode=inode, damaged=bool(damaged))
    return positions


def _write_checkpoint(directory: Path, name: str, content: dict) -> int:
    """Replace one checkpoint file whole; its size, or 0 when it was not written."""
    target = directory / CHECKPOINT_DIRECTORY / name
    temporary = target.with_name(f".{name}.{os.getpid()}.{threading.get_ident()}.tmp")
    body = json.dumps(content, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
    try:
        target.parent.mkdir(exist_ok=True)
        with open(filesystem_path(temporary), "wb") as handle:
            handle.write(body)
        replace_file(temporary, target)
    except OSError:
        _log.debug("checkpoint %s not written", target, exc_info=True)
        try:
            os.unlink(filesystem_path(temporary))
        except OSError:
            pass
        return 0
    return len(body)


def _read_checkpoint(directory: Path, name: str) -> Optional[tuple[dict, dict[str, _LogPosition], int]]:
    """A checkpoint whose files still hold what it covers: its content, positions and size."""
    try:
        with open(filesystem_path(directory / CHECKPOINT_DIRECTORY / name), "rb") as handle:
            body = handle.read()
        content = json.loads(body)
    except (OSError, ValueError):
        return None
    if not isinstance(content, dict) or content.get("version") != _CHECKPOINT_VERSION:
        return None
    positions = _restored_positions(directory, content.get("positions"))
    if positions is None:
        return None
    return content, positions, len(body)


def read_records_checkpoint(directory: Path) -> Optional[tuple[dict, dict[str, _LogPosition]]]:
    """The state files and run rows a fold recorded, and the offsets they cover."""
    found = _read_checkpoint(directory, RECORDS_CHECKPOINT)
    return None if found is None else (found[0], found[1])


def node_sort_key(payload: dict) -> tuple:
    return (int(payload.get("seq", -1)), str(payload.get("role") or "x")[:1],
            str(payload.get("id") or ""))


# Process identity and clock


_main_writer_pid: Optional[int] = None
_agent_log_name: tuple[int, str] | None = None
_clock_lock = threading.Lock()
_last_timestamp = 0
# Serialises appends to one file from threads in this process.
_append_locks = tuple(threading.Lock() for _ in range(64))
# Files whose tail this process has already checked.
_prepared_files: set[str] = set()
_meta_cache: "OrderedDict[str, tuple[tuple, dict]]" = OrderedDict()
_meta_cache_lock = threading.Lock()
# Told of each durable conversation write by this process, and nothing else.
_record_observer: Optional[Any] = None
_record_observer_pid: Optional[int] = None
_foreign_change_observer: Optional[Any] = None
_OBSERVED_RECORDS = {"node.put", "node.patch", "node.stream", "node.delete", "meta.patch"}


def set_record_observer(observer: Optional[Any]) -> None:
    """``observer(session_id, record_type, node_ids, changed_keys)`` after each conversation write."""
    global _record_observer, _record_observer_pid
    _record_observer = observer
    _record_observer_pid = os.getpid()


def set_foreign_change_observer(observer: Optional[Any]) -> None:
    """``observer(session_id)`` when this process finds another process's writes."""
    global _foreign_change_observer
    _foreign_change_observer = observer


def report_foreign_change(session_id: str) -> None:
    observer = _foreign_change_observer
    if observer is not None:
        observer(session_id)


def _observed_change(record_type: str, data: dict) -> tuple[tuple[str, ...], frozenset[str]]:
    """The ids a record touches and the keys it changed."""
    if record_type == "node.put":
        payload = data.get("payload") or {}
        return ((payload["id"],) if payload.get("id") else ()), frozenset(payload)
    if record_type == "node.patch":
        return (str(data.get("id") or ""),), frozenset((data.get("set") or {})) | frozenset((data.get("metadata") or {}))
    if record_type == "node.stream":
        changed = {"extra", *(data.get("metadata") or {})}
        if "output" in data or "output_tail" in data:
            changed.add("output")
        return (str(data.get("id") or ""),), frozenset(changed)
    if record_type == "node.delete":
        return tuple(data.get("ids") or ()), frozenset()
    return (), frozenset((data.get("set") or {})) | frozenset(data.get("unset") or ())


def claim_main_writer() -> None:
    """This process now holds worker.lock and writes ``main.jsonl``."""
    global _main_writer_pid
    _main_writer_pid = os.getpid()


def release_main_writer() -> None:
    global _main_writer_pid
    _main_writer_pid = None


def is_main_writer() -> bool:
    return _main_writer_pid == os.getpid()


def _next_timestamp() -> int:
    global _last_timestamp
    with _clock_lock:
        _last_timestamp = max(_last_timestamp + 1, time.time_ns())
        return _last_timestamp


def _this_process_agent_log() -> str:
    global _agent_log_name
    pid = os.getpid()
    if _agent_log_name is None or _agent_log_name[0] != pid:
        _agent_log_name = (pid, f"{pid}-{uuid.uuid4().hex[:8]}.jsonl")
    return _agent_log_name[1]


def _discard_torn_tail(path: Path) -> None:
    """Cut bytes after the last newline left by a writer that died mid-line."""
    native = filesystem_path(path)
    try:
        with open(native, "r+b") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size == 0:
                return
            handle.seek(size - 1)
            if handle.read(1) == b"\n":
                return
            position = size
            keep = 0
            while position > 0 and keep == 0:
                start = max(0, position - 65536)
                handle.seek(start)
                found = handle.read(position - start).rfind(b"\n")
                if found >= 0:
                    keep = start + found + 1
                position = start
            handle.truncate(keep)
            handle.flush()
            os.fsync(handle.fileno())
            _log.warning("dropped an unfinished record at the end of %s", path)
    except FileNotFoundError:
        return


# The log


class SessionLog:
    """Reads and appends one session's records."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self._synced_fingerprint: Optional[tuple] = None
        self._folded: Optional[tuple[tuple, FoldedSession]] = None
        self._fold_progress = _FoldProgress()
        self._fold_lock = threading.Lock()
        self._initialized = False

    # Layout

    @property
    def main_path(self) -> Path:
        return self.path / MAIN_LOG

    @property
    def agent_directory(self) -> Path:
        return self.path / AGENT_LOG_DIRECTORY

    @property
    def workdir_path(self) -> Path:
        """Per-session scratch workspace for agents with no project directory."""
        return self.path / "workdir"

    def _agent_files(self) -> list[Path]:
        try:
            names = os.listdir(filesystem_path(self.agent_directory))
        except OSError:
            return []
        return [self.agent_directory / name for name in sorted(names)
                if name.endswith(".jsonl")]

    def _writer_path(self) -> Path:
        if is_main_writer():
            return self.main_path
        return self.agent_directory / _this_process_agent_log()

    def exists(self) -> bool:
        return self.main_path.exists() or bool(self._agent_files())

    def _ensure_init(self, target: Optional[Path] = None) -> None:
        """Create the session directory and this process's log file."""
        target = self._writer_path() if target is None else target
        target.parent.mkdir(parents=True, exist_ok=True)
        (self.path / "context").mkdir(exist_ok=True)
        self.workdir_path.mkdir(exist_ok=True)
        os.close(os.open(filesystem_path(target), _APPEND_FLAGS, 0o600))

    # Freshness. Files only grow, so total size changes exactly when
    # anything was appended.

    def stat_fingerprint(self) -> tuple:
        try:
            main_size = os.stat(filesystem_path(self.main_path)).st_size
        except OSError:
            main_size = -1
        agent_count = agent_size = 0
        try:
            with os.scandir(filesystem_path(self.agent_directory)) as entries:
                for entry in entries:
                    if entry.name.endswith(".jsonl"):
                        agent_count += 1
                        agent_size += entry.stat().st_size
        except OSError:
            pass
        return (main_size, agent_count, agent_size)

    def mark_synced(self, fingerprint: Optional[tuple] = None) -> None:
        self._synced_fingerprint = (
            self.stat_fingerprint() if fingerprint is None else fingerprint
        )
        self._folded = None

    def stale(self) -> bool:
        """True when records appeared that this process has not loaded."""
        return self._synced_fingerprint != self.stat_fingerprint()

    def _mark_own_write(self, was_synced: bool) -> None:
        """Absorb our own append into the synced stamp, never a foreign one."""
        if was_synced:
            self.mark_synced()

    # Appending

    def _append(self, record_type: str, data: Any, *, durable: bool) -> None:
        # One choice per append: the writer role can change while it runs.
        target = self._writer_path()
        self._ensure_parent(target)
        key = str(target)
        with _append_locks[hash(key) % len(_append_locks)]:
            if key not in _prepared_files:
                _discard_torn_tail(target)
                if len(_prepared_files) > 8192:
                    _prepared_files.clear()
                _prepared_files.add(key)
            line = encode_record(_next_timestamp(), record_type, data)
            descriptor = os.open(filesystem_path(target), _APPEND_FLAGS, 0o600)
            try:
                written = 0
                while written < len(line):
                    written += os.write(descriptor, line[written:])
                if durable:
                    os.fsync(descriptor)
            except BaseException:
                # The next append must look at the tail again.
                _prepared_files.discard(key)
                raise
            finally:
                os.close(descriptor)
        self._notify_observer(record_type, data, durable)

    def _notify_observer(self, record_type: str, data: Any, durable: bool) -> None:
        """Only enqueues, on the writer's thread. A forked child never reaches it."""
        observer = _record_observer
        if observer is None or record_type not in _OBSERVED_RECORDS or os.getpid() != _record_observer_pid:
            return
        if record_type in ("node.patch", "node.stream") and not durable:
            return  # the quiet path: a reply's streaming text
        node_ids, keys = _observed_change(record_type, data)
        # A stream line changes a node's fields as a patch does.
        observer(self.path.name, "node.patch" if record_type == "node.stream" else record_type, node_ids, keys)

    def _ensure_parent(self, target: Path) -> None:
        if not self._initialized:
            self._ensure_init(target)
            self._initialized = True
        else:
            # The directory may have been moved or removed since.
            target.parent.mkdir(parents=True, exist_ok=True)

    def write_history(self, seq: int, role: str, node_id: str, payload: dict) -> None:
        """Record a node's full content, new or replacing an earlier version."""
        was_synced = not self.stale()
        self._append("node.put", {"payload": payload}, durable=True)
        self._mark_own_write(was_synced)

    def patch_node(
        self, node_id: str, fields: dict, metadata: Optional[dict] = None, *, quiet: bool = False,
    ) -> None:
        """Record changed top-level fields and merged metadata of one node.

        ``quiet`` is for a reply's streaming text: no fsync, so a process
        crash loses nothing written and a power loss can lose only the tail.
        """
        data: dict[str, Any] = {"id": node_id, "set": fields}
        if metadata:
            data["metadata"] = metadata
        was_synced = not self.stale()
        self._append("node.patch", data, durable=not quiet)
        self._mark_own_write(was_synced)

    def stream_node(self, node_id: str, change: dict, *, quiet: bool = False) -> None:
        """Record what a streaming reply added since its last line
        (``apply_stream_change``), instead of its whole trace again."""
        was_synced = not self.stale()
        self._append("node.stream", {**change, "id": node_id}, durable=not quiet)
        self._mark_own_write(was_synced)

    def delete_nodes(self, node_ids: list[str]) -> None:
        was_synced = not self.stale()
        self._append("node.delete", {"ids": list(node_ids)}, durable=True)
        self._mark_own_write(was_synced)

    def write_meta(self, meta: dict) -> None:
        """Record what changed between the stored meta and ``meta``."""
        new = json.loads(json.dumps(meta, ensure_ascii=False, default=str))
        old = self.read_meta()
        changed = {key: value for key, value in new.items()
                   if key not in old or old[key] != value}
        removed = [key for key in old if key not in new]
        if not changed and not removed:
            return
        data: dict[str, Any] = {"set": changed}
        if removed:
            data["unset"] = removed
        was_synced = not self.stale()
        self._append("meta.patch", data, durable="head_id" in changed)
        self._mark_own_write(was_synced)
        self._remember_meta(self.stat_fingerprint(), new)

    # Reading

    def append_turn_record(self, record_type: str, data: dict) -> None:
        """Append a turn's progress line; folding sessions skips these."""
        was_synced = not self.stale()
        self._append(record_type, data, durable=False)
        self._mark_own_write(was_synced)

    def append_state_file(self, name: str, content: str | None) -> None:
        """Replace one small state document; ``None`` removes it."""
        was_synced = not self.stale()
        self._append("file.put", {"name": name, "content": content}, durable=False)
        self._mark_own_write(was_synced)

    def read_turn_records(self, execution_id: str | None,
                          record_type: str | None = None) -> list[dict]:
        """The turn progress lines of one execution (``None``: of every
        execution), in written order; ``record_type`` keeps one kind."""
        records: list[dict] = []
        containing = f'"type":"{record_type}"'.encode() if record_type else None
        for path in (self.main_path, *self._agent_files()):
            if not path.exists():
                continue
            for record in read_log_file(path, containing=containing):
                kind = str(record.get("type", ""))
                if (kind.startswith(TURN_RECORD_PREFIX)
                        and (record_type is None or kind == record_type)
                        and (execution_id is None
                             or (record.get("data") or {}).get("execution_id") == execution_id)):
                    records.append(record)
        records.sort(key=lambda record: record["ts"])
        return records

    def read_run_rows(self) -> list[list[dict]]:
        """The run-state rows of every committed change, in written order."""
        from .session_state_file import read_run_rows
        return read_run_rows(self.path)

    def _fold(self) -> tuple[tuple, FoldedSession]:
        with self._fold_lock:
            fingerprint = self.stat_fingerprint()
            if self._folded is None or self._folded[0] != fingerprint:
                self._folded = (fingerprint, self._catch_up())
            return self._folded

    def _catch_up(self) -> FoldedSession:
        """Apply what was appended since the last fold; start over only when
        a file was replaced or a new line sorts before one already applied.
        A first fold starts from the checkpoint when its files still hold
        what it covers."""
        progress = self._fold_progress
        if not progress.positions:
            progress = self._fold_progress = self._from_checkpoint() or progress
        before = sum(position.offset for position in progress.positions.values())
        records = self._read_new(progress)
        if records is None or (records and records[0].get("ts", 0) <= progress.newest):
            progress = self._fold_progress = _FoldProgress()
            before = 0
            records = self._read_new(progress) or []
        if not records:
            return progress.folded
        folded = progress.folded.copy()
        for record in records:
            apply_record(folded, record)
        progress.folded = settle(folded)
        progress.newest = records[-1].get("ts", 0)
        progress.uncovered += sum(position.offset for position in progress.positions.values()) - before
        if progress.uncovered >= max(CHECKPOINT_MIN_BYTES, progress.checkpoint_size):
            self._checkpoint(progress)
        return progress.folded

    def _from_checkpoint(self) -> Optional[_FoldProgress]:
        found = _read_checkpoint(self.path, FOLD_CHECKPOINT)
        if found is None:
            return None
        content, positions, size = found
        folded = FoldedSession(nodes=content.get("nodes") or {}, meta=content.get("meta") or {},
                               files=content.get("files") or {}, run_rows=content.get("run_rows") or [])
        return _FoldProgress(positions=positions, folded=folded, newest=int(content.get("newest") or 0),
                             checkpoint_size=size)

    def _checkpoint(self, progress: _FoldProgress) -> None:
        """Record the fold so the next cold open reads it and the lines after it."""
        positions = _checkpoint_positions(self.path, progress.positions)
        if positions is None:
            return
        folded = progress.folded
        common = {"version": _CHECKPOINT_VERSION, "positions": positions, "newest": progress.newest}
        size = _write_checkpoint(self.path, FOLD_CHECKPOINT, {
            **common, "nodes": folded.nodes, "meta": folded.meta,
            "files": folded.files, "run_rows": folded.run_rows,
        })
        if size:
            _write_checkpoint(self.path, RECORDS_CHECKPOINT, {
                **common, "files": folded.files, "run_rows": folded.run_rows,
            })
            progress.uncovered = 0
            progress.checkpoint_size = size

    def _read_new(self, progress: _FoldProgress) -> Optional[list[dict]]:
        """New records in time order, or None when a known file was replaced."""
        records: list[dict] = []
        seen = 0
        for path in (self.main_path, *self._agent_files()):
            try:
                info = os.stat(filesystem_path(path))
            except OSError:
                continue
            position = progress.positions.get(str(path))
            if position is None:
                position = progress.positions[str(path)] = _LogPosition(inode=info.st_ino)
            elif position.inode != info.st_ino or info.st_size < position.offset:
                return None
            seen += 1
            if position.damaged or info.st_size == position.offset:
                continue
            found, position.offset, position.damaged = read_appended(path, position.offset)
            records.extend(found)
        if seen < len(progress.positions):
            return None
        records.sort(key=lambda record: record.get("ts", 0))
        return records

    def _remember_meta(self, fingerprint: tuple, meta: dict) -> None:
        with _meta_cache_lock:
            _meta_cache[str(self.path)] = (fingerprint, meta)
            _meta_cache.move_to_end(str(self.path))
            while len(_meta_cache) > _META_CACHE_CAPACITY:
                _meta_cache.popitem(last=False)

    def read_meta(self) -> dict:
        fingerprint = self.stat_fingerprint()
        with _meta_cache_lock:
            cached = _meta_cache.get(str(self.path))
        if cached is not None and cached[0] == fingerprint:
            return copy.deepcopy(cached[1])
        fingerprint, folded = self._fold()
        self._remember_meta(fingerprint, copy.deepcopy(folded.meta))
        return copy.deepcopy(folded.meta)

    def list_history(self) -> list[dict]:
        """Current node payloads in seq order."""
        _fingerprint, folded = self._fold()
        return sorted(folded.nodes.values(), key=node_sort_key)

    def read_context_file(self, name: str) -> Optional[Any]:
        target = self.path / "context" / name
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # Cleanup

    def destroy(self) -> None:
        """Delete the session directory."""
        with _meta_cache_lock:
            _meta_cache.pop(str(self.path), None)
        if self.path.exists():
            remove_tree(self.path, ignore_errors=True)
        self._folded = None
        self._fold_progress = _FoldProgress()
        self._initialized = False
        from .session_state_file import forget_directory
        forget_directory(self.path)
