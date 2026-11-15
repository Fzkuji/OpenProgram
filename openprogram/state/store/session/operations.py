"""Durable records of a conversation's foreground operations.

Each branch of a conversation has at most one foreground operation at a
time (a reply, /compact, a retry or edit, a rewind, a branch operation or a
function run). The live slot is kept in memory by the process that runs it;
this file keeps the part another process, or the next start, must see:

* which operations were running, so a restart marks them cancelled rather
  than leaving them to look alive (an operation is never resumed);
* whether a conversation-wide operation (a rewind that changes files on
  disk) holds the conversation's working files. While it does, a new
  execution of that conversation (a scheduled run, a queued Job, a turn
  admitted by another process) waits for it to end before it is admitted.

One JSON file per conversation under ``<state>/projects/.operations``.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from openprogram.state.store.session.atomic_file import atomic_write_text, read_text_with_retry
from openprogram.state.store.session.session_lock import registry_file_lock

#: What a foreground operation is. ``reply`` is a chat turn.
OPERATION_KINDS = ("reply", "compact", "retry", "edit", "rewind", "branch", "function")

#: Kinds that write a reply row of their own.
REPLY_KINDS = frozenset({"reply", "retry", "edit", "function"})

_swept_roots: set[str] = set()


def _root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    from openprogram.state.paths import get_state_dir
    return Path(get_state_dir()) / "projects"


def _safe(session_id: str) -> str:
    return session_id.replace("/", "_").replace("\\", "_").replace("..", "_")


def records_path(session_id: str, root: str | Path | None = None) -> Path:
    return _root(root) / ".operations" / f"{_safe(session_id)}.json"


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(read_text_with_retry(path))
    except (OSError, ValueError):
        return {}
    ops = data.get("operations") if isinstance(data, dict) else None
    return dict(ops) if isinstance(ops, dict) else {}


def _write(path: Path, ops: dict[str, Any]) -> None:
    if not ops:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps({"operations": ops}, ensure_ascii=False, default=str))


def _alive(pid: Any) -> bool:
    if pid == os.getpid():
        return True
    try:
        from openprogram.host._platform.processes import process_alive
        return process_alive(int(pid))
    except (TypeError, ValueError):
        return False


def _lock(session_id: str, root: str | Path | None):
    return registry_file_lock(_root(root), f"operations-{_safe(session_id)}")


def record_operation(session_id: str, op: dict[str, Any], *, root: str | Path | None = None) -> None:
    """Record a running operation. A finished record of the same branch is
    dropped, so the file holds what runs now and the last interrupted one."""
    path = records_path(session_id, root)
    with _lock(session_id, root):
        ops = {key: value for key, value in _read(path).items()
               if value.get("status") == "running"
               or value.get("branch_id") != op.get("branch_id")}
        ops[op["op_id"]] = {
            "op_id": op["op_id"],
            "kind": op.get("kind"),
            "branch_id": op.get("branch_id"),
            "scope": op.get("scope") or "branch",
            "pid": os.getpid(),
            "started_at": op.get("started_at") or time.time(),
            "status": "running",
        }
        _write(path, ops)


def finish_operation(session_id: str, op_id: str, *, root: str | Path | None = None) -> None:
    """The operation ended (finished, failed or stopped): forget it."""
    path = records_path(session_id, root)
    with _lock(session_id, root):
        ops = _read(path)
        if ops.pop(op_id, None) is not None:
            _write(path, ops)


def sweep_interrupted(root: str | Path | None = None, *, force: bool = False) -> int:
    """Mark every running record whose process is gone as cancelled.

    Runs once per process for a state root (the first time anything asks),
    which is how a restart marks the operations it interrupted. Returns how
    many records it marked.
    """
    base = _root(root)
    key = str(base)
    if key in _swept_roots and not force:
        return 0
    _swept_roots.add(key)
    directory = base / ".operations"
    if not directory.is_dir():
        return 0
    marked = 0
    for path in directory.glob("*.json"):
        session_key = path.stem
        try:
            with registry_file_lock(base, f"operations-{session_key}"):
                ops = _read(path)
                changed = False
                for op in ops.values():
                    if op.get("status") == "running" and not _alive(op.get("pid")):
                        op.update(status="cancelled", reason_code="restart", ended_at=time.time())
                        changed = True
                        marked += 1
                if changed:
                    _write(path, ops)
        except (OSError, TimeoutError):
            continue
    return marked


def running_operations(session_id: str, *, root: str | Path | None = None) -> list[dict[str, Any]]:
    """The recorded operations of a conversation still running in a live process."""
    sweep_interrupted(root)
    return [dict(op) for op in _read(records_path(session_id, root)).values()
            if op.get("status") == "running" and _alive(op.get("pid"))]


def interrupted_operations(session_id: str, *, root: str | Path | None = None) -> list[dict[str, Any]]:
    """Operations a restart ended: each is marked cancelled, never resumed."""
    sweep_interrupted(root)
    return [dict(op) for op in _read(records_path(session_id, root)).values()
            if op.get("status") == "cancelled"]


def workspace_hold(session_id: str, *, root: str | Path | None = None) -> dict[str, Any] | None:
    """The conversation-wide operation holding the conversation's files, if any."""
    return next((op for op in running_operations(session_id, root=root)
                 if op.get("scope") == "conversation"), None)


def wait_for_workspace(session_id: str, *, timeout: float = 30.0,
                       root: str | Path | None = None) -> bool:
    """Wait until no conversation-wide operation holds the files. False on timeout."""
    deadline = time.monotonic() + timeout
    while workspace_hold(session_id, root=root) is not None:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def branch_operation_held(session_id: str, branch_id: str | None, *, selected: str | None = None,
                          root: str | Path | None = None) -> dict[str, Any] | None:
    """A running operation that holds ``branch_id`` in any live process:
    one recorded on that branch, one recorded on the selected branch when
    ``branch_id`` is it (``selected``), or one that holds the whole
    conversation (a rewind that changes files). ``None`` when the branch is
    free. A process that admits a turn outside the server (a scheduled
    run) reads this, the same record the server's slots write, so a
    ``/compact`` and a scheduled run never both run on one branch."""
    for op in running_operations(session_id, root=root):
        recorded = op.get("branch_id")
        if (op.get("scope") == "conversation"
                or (branch_id is not None and (recorded == branch_id
                                               or (recorded is None and branch_id == selected)))):
            return op
    return None
