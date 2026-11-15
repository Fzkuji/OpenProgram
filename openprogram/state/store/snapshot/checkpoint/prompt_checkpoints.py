"""One recovery point per prompt that starts a turn.

Layout under ``.file-recovery/<session_id>/``::

    checkpoints/<user_msg_id>.json   # {path: state} at the moment of the prompt
    blobs/<sha256>                   # immutable content shared by checkpoints

A checkpoint lists every file the session has edited so far, so a change made
by a shell command or by hand between prompts is captured by the next prompt.
A file first appears in the checkpoint of the prompt whose turn first edits it:
a checkpoint names the turns that run for its prompt (``turn_ids``), and an edit
is recorded in the checkpoint of the turn that makes it. Two branches of one
conversation can run turns at once, so that is not always the newest one.
Capturing never fails the turn or the tool: a file that cannot be read is
recorded as ``unavailable`` and shows up as skipped at restore time.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from openprogram.host._platform.filesystem import is_link_metadata

from . import manifest
from .paths import session_backup_root
from openprogram.host._platform.filesystem import replace_file

_log = logging.getLogger(__name__)

CHECKPOINT_DIRECTORY = "checkpoints"
BLOB_DIRECTORY = "blobs"
ORIGIN_FILE = "origin.json"
MAX_WRITERS_PER_PATH = 32


def _unavailable(reason: str) -> dict:
    return {"kind": "unavailable", "reason": reason}


class PromptCheckpoints:
    def __init__(self, session_dir: Path):
        self.root = session_backup_root(Path(session_dir))
        self.directory = self.root / CHECKPOINT_DIRECTORY
        self.blobs = self.root / BLOB_DIRECTORY

    @contextmanager
    def _lock(self):
        from openprogram.host._platform import locking

        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".lock").open("a+") as handle:
            locking.flock(handle.fileno(), locking.LOCK_EX)
            try:
                yield
            finally:
                locking.flock(handle.fileno(), locking.LOCK_UN)

    def _path(self, user_msg_id: str) -> Path:
        return self.directory / f"{user_msg_id}.json"

    def get(self, user_msg_id: str) -> dict | None:
        try:
            value = json.loads(self._path(user_msg_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def all(self) -> list[dict]:
        """Every readable checkpoint, oldest first."""
        rows: list[dict] = []
        try:
            names = [item.name for item in os.scandir(self.directory)]
        except OSError:
            return rows
        for name in names:
            if name.startswith(".") or not name.endswith(".json"):
                continue
            value = self.get(name[:-len(".json")])
            if value is not None:
                rows.append(value)
        rows.sort(key=lambda row: int(row.get("sequence") or 0))
        return rows

    def ids(self) -> set[str]:
        try:
            return {
                item.name[:-len(".json")] for item in os.scandir(self.directory)
                if item.name.endswith(".json") and not item.name.startswith(".")
            }
        except OSError:
            return set()

    def capture(self, user_msg_id: str, turn_id: str | None = None) -> None:
        """Record the tracked files as they are when a prompt starts a turn.

        ``turn_id`` is the turn the prompt starts; its edits belong here.
        """
        with self._lock():
            if self._path(user_msg_id).exists():
                return
            rows = self.all()
            latest = rows[-1] if rows else {}
            # A turn on another branch may have added a path to an older
            # checkpoint after the newest one was taken.
            known: dict[str, dict] = {}
            for row in reversed(rows):
                for path, entry in (row.get("files") or {}).items():
                    known.setdefault(path, entry)
            files = {path: self._refresh(path, entry) for path, entry in known.items()}
            manifest.save(self._path(user_msg_id), {
                "version": 1,
                "user_msg_id": user_msg_id,
                "sequence": int(latest.get("sequence") or 0) + 1,
                "created_at": time.time(),
                "files": files,
                **({"turn_ids": [turn_id]} if turn_id else {}),
            })

    def bind_turn(self, turn_id: str, candidates) -> str | None:
        """Give a turn with no checkpoint of its own the one it belongs to.

        ``candidates`` are turn ids and prompt ids, nearest first: the first
        that owns or is a checkpoint takes the turn. Returns that prompt's id.
        """
        with self._lock():
            rows = self.all()
            for candidate in candidates:
                for row in rows:
                    turns = row.setdefault("turn_ids", [])
                    if candidate != row.get("user_msg_id") and candidate not in turns:
                        continue
                    if turn_id not in turns:
                        turns.append(turn_id)
                        manifest.save(self._path(str(row["user_msg_id"])), row)
                    return str(row["user_msg_id"])
            return None

    def track(
        self,
        abs_path: str,
        content_src: str | Path | None = None,
        *,
        turn_id: str | None = None,
        written_since=None,
    ) -> None:
        """Add a file to its turn's checkpoint before the turn first edits it.

        A turn no checkpoint names records in the newest one. A file the
        checkpoint already lists stays as listed, except when
        ``written_since(checkpoint, others, entry)`` says a turn of a prompt
        this one does not lead to wrote it after the checkpoint read it: the
        state to return to is then the one that turn left.
        """
        with self._lock():
            rows = self.all()
            if not rows:
                return
            owner = next((row for row in rows if turn_id and turn_id in (row.get("turn_ids") or [])), None)
            bound = owner is not None
            if owner is None:
                owner = rows[-1]
            files = owner.setdefault("files", {})
            if abs_path in files:
                others = [row for row in rows if row is not owner]
                if not (bound and written_since and written_since(owner, others, files[abs_path])):
                    return
            files[abs_path] = self._snapshot(abs_path, content_src)
            manifest.save(self._path(str(owner["user_msg_id"])), owner)

    def _refresh(self, path: str, entry: dict) -> dict:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return {"kind": "absent", "captured_sequence": current_mutation_sequence()}
        except OSError:
            return _unavailable("unreadable")
        if (
            entry.get("kind") == "regular"
            and stat.S_ISREG(info.st_mode)
            and entry.get("size") == info.st_size
            and entry.get("mtime_ns") == info.st_mtime_ns
        ):
            return entry
        return self._snapshot(path)

    def _snapshot(self, path: str, content_src: str | Path | None = None) -> dict:
        source = str(content_src) if content_src is not None else path
        # Read before the file, so a write racing this capture counts as later.
        sequence = current_mutation_sequence()
        try:
            info = os.lstat(source)
        except FileNotFoundError:
            return {"kind": "absent", "captured_sequence": sequence}
        except OSError:
            return _unavailable("unreadable")
        if is_link_metadata(info) or not stat.S_ISREG(info.st_mode):
            return _unavailable("unsafe_file_type")
        try:
            digest = self.store_blob(source)
        except OSError:
            _log.warning("checkpoint could not capture %s", path, exc_info=True)
            return _unavailable("unreadable")
        return {
            "kind": "regular",
            "digest": f"sha256:{digest}",
            "mode": f"{stat.S_IMODE(info.st_mode):04o}",
            "size": info.st_size,
            "mtime_ns": info.st_mtime_ns,
            "captured_sequence": sequence,
        }

    def store_blob(self, source: str | Path) -> str:
        """Copy a regular file into the shared store; return its hex digest."""
        self.blobs.mkdir(parents=True, exist_ok=True)
        temporary = self.blobs / f".tmp-{uuid.uuid4().hex}"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            with os.fdopen(os.open(source, flags), "rb") as handle:
                before = os.fstat(handle.fileno())
                digest = hashlib.sha256()
                output_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                with os.fdopen(os.open(temporary, output_flags, 0o600), "wb") as output:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                after = os.fstat(handle.fileno())
            if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                raise OSError(f"{source} changed while reading")
            name = digest.hexdigest()
            destination = self.blobs / name
            if destination.exists():
                return name
            replace_file(temporary, destination)
            return name
        finally:
            temporary.unlink(missing_ok=True)

    def blob_path(self, digest: str) -> Path:
        return self.blobs / str(digest).removeprefix("sha256:")

    def targets(self, user_ids_oldest_first: list[str]) -> dict[str, dict] | None:
        """Each tracked file's state before the listed prompts' turns edited it.

        That is the listing read earliest among the listed checkpoints. A file
        the first checkpoint does not list had not been edited yet, and a turn
        that joined an earlier prompt may first edit a file after a later
        prompt's turn did, so the earliest read is not always the first
        prompt's. ``None`` means the first prompt has no checkpoint.
        """
        if not user_ids_oldest_first:
            return None
        target = self.get(user_ids_oldest_first[0])
        if target is None:
            return None
        states = dict(target.get("files") or {})
        for user_id in user_ids_oldest_first[1:]:
            later = self.get(user_id) or {}
            for path, entry in (later.get("files") or {}).items():
                held = states.get(path)
                if held is None or _read_before(entry, held):
                    states[path] = entry
        return {path: self._with_blob(entry) for path, entry in states.items()}

    def beside_other_prompts(
        self,
        user_ids_oldest_first: list[str],
        first_writes: dict[str, int],
        other_writes: dict[str, int],
    ) -> tuple[dict[str, dict], set[str]]:
        """Targets for paths that turns of other prompts also wrote.

        ``first_writes`` is where the listed prompts' turns first wrote each
        path and ``other_writes`` each path's newest write by a turn outside
        the list, both as workspace sequences. A listing read before the
        other write would take that turn's work back with it, so the target
        is the earliest listing read between the two. A path with no such
        listing, or whose other write comes after the first write and so sits
        on top of the listed turns' own edits, cannot be put back without
        losing the other turn's work: those are the second value.
        """
        rows = [row for row in map(self.get, user_ids_oldest_first) if row]
        targets: dict[str, dict] = {}
        overwritten: set[str] = set()
        for path, newest in other_writes.items():
            first = int(first_writes.get(path) or 0)
            between = [
                entry for row in rows
                if isinstance(entry := (row.get("files") or {}).get(path), dict)
                and newest <= _read_at(entry, -1) < first
            ]
            if newest > first or not between:
                overwritten.add(path)
                continue
            targets[path] = self._with_blob(min(between, key=_read_at))
        return targets, overwritten

    def _with_blob(self, entry: dict) -> dict:
        value = dict(entry)
        if value.get("kind") != "regular":
            return value
        blob = self.blob_path(str(value.get("digest") or ""))
        if not blob.is_file():
            return _unavailable("backup_missing")
        value["blob_path"] = str(blob)
        value.pop("mtime_ns", None)
        return value

    def read_origin(self) -> dict:
        try:
            value = json.loads((self.root / ORIGIN_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}


def _read_at(entry: dict, unknown: int = 0) -> int:
    """The workspace sequence at which a listing was read."""
    value = entry.get("captured_sequence")
    return value if isinstance(value, int) and not isinstance(value, bool) else unknown


def _read_before(entry: dict, other: dict) -> bool:
    """Whether a listing is known to have been read before another."""
    first, second = _read_at(entry, -1), _read_at(other, -1)
    return 0 <= first < second


def carry_checkpoints(
    source_session_dir: Path,
    target_session_dir: Path,
    id_map: dict[str, str],
    *,
    source_session_id: str,
) -> int:
    """Copy a session's checkpoints for the copied messages to a new session.

    A checkpoint is recorded only after every blob it refers to is in place.
    """
    source = PromptCheckpoints(source_session_dir)
    target = PromptCheckpoints(target_session_dir)
    carried = 0
    for row in source.all():
        new_id = id_map.get(str(row.get("user_msg_id")))
        if not new_id:
            continue
        try:
            for entry in (row.get("files") or {}).values():
                if entry.get("kind") == "regular":
                    _copy_blob(source, target, str(entry.get("digest") or ""))
        except OSError:
            _log.warning("checkpoint %s not carried", row.get("user_msg_id"), exc_info=True)
            continue
        copied = {**row, "user_msg_id": new_id}
        if "turn_ids" in row:
            copied["turn_ids"] = [id_map.get(turn, turn) for turn in row["turn_ids"]]
        manifest.save(target._path(new_id), copied)
        carried += 1
    if carried:
        from .store import CheckpointStore
        journal = CheckpointStore(source_session_dir)
        inherited = source.read_origin().get("turn_mutations") or {}
        records = {new: rows for old, new in id_map.items()
                   if (rows := journal.list_file_history(old) or inherited.get(old, []))}
        manifest.save(target.root / ORIGIN_FILE, {
            "turn_mutations": records,
            "source_session_id": source_session_id,
            "through_sequence": current_mutation_sequence(),
        })
    return carried


def _copy_blob(source: PromptCheckpoints, target: PromptCheckpoints, digest: str) -> None:
    from_path = source.blob_path(digest)
    to_path = target.blob_path(digest)
    if to_path.exists():
        return
    target.blobs.mkdir(parents=True, exist_ok=True)
    temporary = target.blobs / f".tmp-{uuid.uuid4().hex}"
    try:
        shutil.copyfile(from_path, temporary)
        os.chmod(temporary, 0o600)
        replace_file(temporary, to_path)
    finally:
        temporary.unlink(missing_ok=True)


def _writers_root() -> Path:
    from openprogram.state.paths import get_state_dir

    return get_state_dir() / "mutation-locks" / "last-writers"


def _writers_file(path: str) -> Path:
    name = hashlib.sha256(path.encode("utf-8")).hexdigest()[:32]
    return _writers_root() / f"{name}.json"


def _read_writers(path: str) -> tuple[dict[str, int], dict[str, int]]:
    """``(finished, started)``: each session's newest write to a path."""
    try:
        value = json.loads(_writers_file(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(value, dict):
        return {}, {}
    if "finished" not in value and value.get("session"):
        return {str(value["session"]): int(value.get("sequence") or 0)}, {}
    return dict(value.get("finished") or {}), dict(value.get("started") or {})


def record_writer(path: str, session: str, sequence: int, stage: str) -> None:
    """Note a session's write to a path through a trusted tool.

    ``stage`` is ``started`` before the file changes, ``finished`` once the
    change is journaled and ``aborted`` when it never happened. A ``started``
    write counts until one of the others arrives, so a rewind planned in the
    middle of another session's edit leaves that file alone. Callers hold the
    workspace sequence lock.
    """
    finished, started = _read_writers(path)
    if stage == "started":
        started[session] = sequence
    else:
        started.pop(session, None)
        if stage == "finished":
            finished[session] = sequence
    newest = dict(sorted(finished.items(), key=lambda item: item[1])[-MAX_WRITERS_PER_PATH:])
    root = _writers_root()
    root.mkdir(parents=True, exist_ok=True)
    manifest.save(_writers_file(path), {"path": path, "finished": newest, "started": started})


def writers_after(path: str, sequence: int) -> list[tuple[str, int]]:
    """Each session's newest write to a path later than ``sequence``."""
    finished, started = _read_writers(path)
    latest: dict[str, int] = {}
    for session, value in (*finished.items(), *started.items()):
        latest[session] = max(latest.get(session, 0), int(value))
    return [(session, value) for session, value in latest.items() if value > sequence]


def current_mutation_sequence() -> int:
    from openprogram.state.paths import get_state_dir

    counter = get_state_dir() / "mutation-locks" / "workspace-sequence"
    try:
        return int(counter.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0
