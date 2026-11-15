"""Structured write transaction for interactive memory editing.

``MemoryWorkspace.shell()`` runs the same normalize-validate-install
pipeline but reaches it by executing a shell command in the stage. A tool
exposed to a chat or an editor drives the identical pipeline from record
operations and staged hand edits instead.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import threading
from collections.abc import Callable
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..markdown import read_topic_tree
from ..runtime.state import RuntimeStateStore
from ..workspace_layout import (
    ensure_runtime_dir,
    is_internal_path,
    is_state_file,
)

WRITABLE_PREFIX = "topics/"
_WRITE_LOCKS = threading.local()


class TransactionError(Exception):
    """A transaction was rejected. ``code`` is a stable machine-readable tag."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        # Whether the same write may succeed later with nothing changed in
        # it, because what failed is the workspace rather than the write.
        self.retryable = retryable
        self.message = message
        self.path = path
        self.details = details or {}


@dataclass
class TransactionResult:
    revision: str
    block_ids: dict[str, str] = field(default_factory=dict)
    evidence_ids: dict[str, str] = field(default_factory=dict)
    changed_files: list[str] = field(default_factory=list)
    memory_committed: bool = True
    git_committed: bool = False
    git_commit: str | None = None


@dataclass(frozen=True)
class TransactionLimits:
    max_changes: int = 64
    max_bytes: int = 512_000
    max_commit_message_chars: int = 500


def _revision_files(memory_dir: Path):
    root = Path(memory_dir)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        # Retrieval caches are derived and may be rewritten by a read, and the
        # write lock is touched by every transaction. Neither is memory state,
        # so neither may look like a concurrent write.
        if is_internal_path(relative) and not is_state_file(relative):
            continue
        yield relative.as_posix(), path.read_bytes()


def workspace_revision(memory_dir: Path) -> str:
    """Content fingerprint of the committed workspace.

    Derived from bytes rather than mtime so a revision cannot silently repeat
    when two writes land inside one filesystem timestamp granule.
    """
    digest = hashlib.sha256()
    for relative, data in _revision_files(memory_dir):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()[:32]


def workspace_files(memory_dir: Path) -> dict[str, str]:
    """The files ``workspace_revision`` covers, each with its own hash, so
    a write can tell which of them moved."""
    return {
        relative: hashlib.sha256(data).hexdigest()
        for relative, data in _revision_files(memory_dir)
    }


@contextmanager
def workspace_write_lock(memory_dir: Path, *, timeout_s: float = 10.0):
    """Exclusive cross-process lock covering one transaction."""
    lock_path = (ensure_runtime_dir(memory_dir) / "write.lock").resolve()
    held = getattr(_WRITE_LOCKS, "held", None)
    if held is None:
        held = _WRITE_LOCKS.held = {}
    if lock_path in held:
        held[lock_path] += 1
        try:
            yield
        finally:
            held[lock_path] -= 1
        return
    # Owner-only like everything else under the profile. The lock holds no
    # memory, but a file another account can open is one it can hold, and
    # that is enough to stall every write this workspace attempts.
    handle = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _acquire(handle, lock_path, timeout_s)
        held[lock_path] = 1
        try:
            yield
        finally:
            del held[lock_path]
            _release(handle)
    finally:
        os.close(handle)


def _acquire(handle: int, lock_path: Path, timeout_s: float) -> None:
    import time

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            _lock_exclusive(handle)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            if time.monotonic() >= deadline:
                raise TransactionError(
                    "CONCURRENT_UPDATE",
                    "another write transaction holds the workspace lock",
                    details={"lock": lock_path.name},
                ) from exc
            time.sleep(0.05)


if os.name == "nt":  # pragma: no cover - exercised on Windows only
    import msvcrt

    def _lock_exclusive(handle: int) -> None:
        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)

    def _release(handle: int) -> None:
        try:
            os.lseek(handle, 0, os.SEEK_SET)
            msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _lock_exclusive(handle: int) -> None:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(handle: int) -> None:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass


def validate_writable_path(path: str) -> None:
    """Reject a workspace-relative path a hand edit may not write.

    Two rules, and both are about where bytes may land: nothing may climb
    out of the workspace, and inside it only Topic Markdown is authored by
    hand. The core section of ``MEMORY.md`` is rendered from
    ``topics/core.md`` like the rest of the derived views, so an edit there
    is refused rather than overwritten on the next write.
    """
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise TransactionError(
            "PATH_OUTSIDE_WORKSPACE",
            "path escapes the workspace",
            path=path,
        )
    posix = candidate.as_posix()
    if posix.startswith(WRITABLE_PREFIX) and posix.endswith(".md"):
        return
    raise TransactionError(
        "READ_ONLY_PATH",
        "only topics/**/*.md is writable",
        path=path,
    )


def staged_edit(
    root: Path,
    write: Callable[[Path], None],
    *,
    deleting: str = "",
    timeout_s: float = 5.0,
    commit_message: str = "memory: edit topics",
    record_history: bool = True,
    allow_removed: bool = False,
    before_install: Callable[[], None] | None = None,
) -> tuple[bool, str]:
    """Apply a hand edit through the workspace stage, or not at all.

    ``write`` edits the staging copy the way it would edit the real tree.
    Someone editing a topic file by hand can drop a block ID or strand a
    footnote, and nothing else would notice until a later write failed, so
    the check runs here while the person who made the edit is still looking
    at it.

    Two things make that check mean something. The baseline is read from
    the committed workspace *before* anything is staged — read afterwards
    it would measure the edit against itself, and a dropped block ID would
    look like there never was one. And the edit lands only by installing a
    validated stage, so a rejected edit leaves the committed workspace
    byte-for-byte as it was rather than needing to be undone.

    ``deleting`` names a topic whose block IDs go away on purpose. Every
    other committed ID must still be reachable after the edit.
    """
    from ..policy import owner_edit
    from .config import load_memory_config
    from .workspace import MemoryWorkspace

    try:
        # The lock spans staging, validation and install: the background
        # writer stages from this same tree and would otherwise install
        # over the edit, or be installed over by it.
        with owner_edit(), workspace_write_lock(root, timeout_s=timeout_s):
            from ..store import _ensure_git_history

            _ensure_git_history(root)
            with closing(MemoryWorkspace(
                root, config=load_memory_config(),
            )) as space:
                units, block_ids = committed_baseline(space)
                if deleting:
                    block_ids -= {
                        unit.memory_id for unit in units
                        if unit.topic_path == deleting
                    }
                write(space.stage_dir)
                # A trusted surface may checkpoint the pre-edit state, but only
                # after validation succeeds and before committed files move.
                space._before_install = before_install
                install_state(space, units, block_ids, allow_removed=allow_removed)
                try:
                    if record_history:
                        git_commit_state(root, commit_message)
                except Exception as exc:  # noqa: BLE001
                    return True, (
                        "memory changed but Git commit failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
    except TransactionError as exc:
        return False, exc.message
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, ""


def committed_baseline(workspace: Any) -> tuple[list[Any], set[str]]:
    """Snapshot committed topic units and block IDs before staging any edit.

    ``shell()`` can read this from the stage because the stage still matches
    the committed tree at that point. A record edit is applied to the stage
    first, so the baseline must come from ``memory_dir`` instead — reading
    the stage afterwards would treat the edit's own text as pre-existing.

    The tree is read the way memory is read: a file nobody can parse
    strictly is a baseline all the same, and refuses no edit. The IDs an
    edit must keep are those of memory's own files.
    """
    from .workspace import own_block_ids

    units = read_topic_tree(
        workspace.memory_dir / "topics",
        outside=getattr(workspace, "outside", frozenset()),
    ).units
    return units, own_block_ids(units)


def preserve_creation_order(workspace: Any, before_units: list[Any]) -> None:
    """Seed a new Runtime state from the committed Topic order."""
    state_store = RuntimeStateStore(workspace.stage_dir)
    state = state_store.load()
    if state.creation_order:
        return
    state.creation_order = {
        unit.memory_id: unit.created_order for unit in before_units
    }
    state_store.save(state)


def install_state(
    workspace: Any,
    before_units: list[Any],
    before_block_ids: set[str],
    *,
    allow_removed: bool | set[str] = False,
) -> None:
    """Validate staged topics and install them over the committed workspace.

    Mirrors the successful branch of ``MemoryWorkspace.shell()``. Every
    committed block ID must survive the edit except those ``allow_removed``
    names; ``True`` lets the edit remove any, as a writer's may.
    """
    adopt = getattr(workspace, "adopt_person_edits", None)
    if adopt is not None:
        adopt()
    refuse = getattr(workspace, "_refuse_outside_edits", None)
    if refuse is not None:
        refuse(workspace.stage_dir)
    preserve_creation_order(workspace, before_units)
    workspace._normalize_topic_edits(before_block_ids, before_units)
    if allow_removed is True:
        required_ids: set[str] = set()
    else:
        required_ids = before_block_ids - (allow_removed or set())
    workspace._validate_topic_contract(before_units, required_ids)
    workspace._synchronize()


def git_commit_state(
    memory_dir: Path, message: str, *, paths: tuple[str, ...] | None = None,
) -> str | None:
    return RuntimeStateStore(Path(memory_dir)).git_commit(message, paths=paths)


# What people edit by hand. Sources are archived by the write itself, and
# the other tracked files are rebuilt from topics on every install.
_HAND_EDITED = ("topics", "MEMORY.md", "core.md")


def snapshot_outside_edits(memory_dir: Path) -> None:
    """Give uncommitted hand edits their own commit before a write installs.

    Files edited in another editor, and Memory-page edits still waiting for
    their five-minute checkpoint, would otherwise land in the next write's
    commit under its message, and undoing that write would undo them too.
    The install step calls it once the write has passed its checks, so a
    rejected write leaves history exactly as it was.
    """
    from ..checkpoints import checkpoint

    try:
        checkpoint(Path(memory_dir), force=True)
        git_commit_state(
            memory_dir, "memory: edits made outside OpenProgram",
            paths=_HAND_EDITED,
        )
    except Exception as exc:  # noqa: BLE001 - history must not block a write
        logging.getLogger(__name__).debug(
            "memory: outside edits were not committed: %s", exc,
        )
