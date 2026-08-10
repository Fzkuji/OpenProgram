"""Persistent, queryable health of the background memory writer."""
from __future__ import annotations

import json
import logging
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from openprogram import _compat as fcntl
from openprogram.memory.backend import MemoryWriteFailureCode
from openprogram.store.session.git_session import atomic_write_text

from ..workspace_layout import runtime_dir

logger = logging.getLogger(__name__)

STATUS_FILE = "writer-status.json"
# Bumped when the persisted shape changes. A file written by a different
# version is read as "nothing recorded" rather than half-interpreted.
STATUS_SCHEMA_VERSION = 1
_WORKSPACE_ID = re.compile(r"w-[0-9a-f]{8}")
_STATUS_WRITE_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_reason_code(value: object) -> str:
    try:
        return MemoryWriteFailureCode(str(value)).value
    except ValueError:
        return MemoryWriteFailureCode.WRITER_FAILURE_UNKNOWN.value


@contextmanager
def _status_file_lock(path: Path) -> Iterator[None]:
    """Serialize status read-modify-write across OpenProgram processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".json.lock").open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class _WriterStatusStore:
    """The small status file under the workspace's internal runtime dir."""

    def __init__(self, memory_dir: Path):
        self.path = runtime_dir(Path(memory_dir)) / STATUS_FILE

    def load(self) -> dict[str, Any]:
        empty = {
            "last_outcome": None,
            "last_success_at": None,
            "last_failure": None,
        }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return empty
        if (
            not isinstance(payload, dict)
            or payload.get("version") != STATUS_SCHEMA_VERSION
        ):
            return empty
        success = payload.get("last_success_at")
        failure = payload.get("last_failure")
        outcome = payload.get("last_outcome")
        if outcome not in ("success", "failure"):
            outcome = None
        if not isinstance(success, str):
            success = None
        if not (
            isinstance(failure, dict)
            and isinstance(failure.get("at"), str)
            and isinstance(failure.get("retryable"), bool)
        ):
            failure = None
        elif _normalize_reason_code(
            failure.get("reason_code")
        ) != failure.get("reason_code"):
            failure = None
        else:
            failure = {
                "at": failure["at"],
                "reason_code": failure["reason_code"],
                "retryable": failure["retryable"],
            }
        return {
            "last_outcome": outcome,
            "last_success_at": success,
            "last_failure": failure,
        }

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.path,
            json.dumps(
                {"version": STATUS_SCHEMA_VERSION, **payload},
                ensure_ascii=False,
                indent=2,
            ) + "\n",
        )

    def record_success(self) -> None:
        with _STATUS_WRITE_LOCK, _status_file_lock(self.path):
            payload = self.load()
            payload["last_outcome"] = "success"
            payload["last_success_at"] = _now()
            self._save(payload)

    def record_failure(
        self,
        reason_code: MemoryWriteFailureCode | str | None,
        *,
        retryable: bool,
    ) -> None:
        with _STATUS_WRITE_LOCK, _status_file_lock(self.path):
            payload = self.load()
            payload["last_outcome"] = "failure"
            payload["last_failure"] = {
                "at": _now(),
                "reason_code": _normalize_reason_code(reason_code),
                "retryable": bool(retryable),
            }
            self._save(payload)


def record_success(memory_dir: Path) -> None:
    try:
        _WriterStatusStore(memory_dir).record_success()
    except Exception as exc:  # noqa: BLE001 - status cannot fail a write
        logger.debug("memory writer status success was not persisted: %s", exc)


def record_failure(
    memory_dir: Path,
    reason_code: MemoryWriteFailureCode | str | None,
    *,
    retryable: bool,
) -> None:
    try:
        _WriterStatusStore(memory_dir).record_failure(
            reason_code, retryable=retryable,
        )
    except Exception as exc:  # noqa: BLE001 - status cannot fail a write
        logger.debug("memory writer status failure was not persisted: %s", exc)


def record_active_workspace_failure(
    reason_code: MemoryWriteFailureCode | str | None,
    *,
    retryable: bool,
) -> None:
    """Record a failure for the active profile's memory workspace."""
    try:
        from openprogram.memory import store

        record_failure(store.root(), reason_code, retryable=retryable)
    except Exception as exc:  # noqa: BLE001 - observability is non-blocking
        logger.debug("memory writer status root was unavailable: %s", exc)


def _existing_workspace_id(memory_dir: Path) -> str | None:
    path = runtime_dir(memory_dir) / "workspace-id"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if _WORKSPACE_ID.fullmatch(value) else None


def pending_turn_count(memory_dir: Path, session_store: Any | None = None) -> int:
    """Eligible SessionDB SourceRecords without this workspace's marker.

    The current branch and every non-archived branch tip are considered,
    matching forced idle writing. Shared prefixes are counted once. Tool,
    runtime, and authority-denied rows are removed by the writer's own
    ``_records`` boundary. This read-only count does not migrate cursors,
    create an agent, or change node markers.
    """
    if session_store is None:
        from openprogram.agent.session_db import default_db

        session_store = default_db()
    from .. import writing

    workspace_id = _existing_workspace_id(Path(memory_dir))
    total = 0
    for session in session_store.list_sessions(limit=10**9):
        session_id = str(session.get("id") or "").strip()
        if not session_id:
            continue
        messages = session_store.get_messages(session_id) or []
        marked = {
            str(row.get("id"))
            for row in messages
            if workspace_id and row.get(writing.WRITTEN_NODE_MARKER) == workspace_id
        }
        pending_ids = {
            record.message_id
            for _head, branch in writing._eligible_session_branches(
                session_store, session_id, messages,
            )
            for record in writing._records(session_id, branch)
            if record.message_id not in marked
        }
        total += len(pending_ids)
    return total


def status(memory_dir: Path) -> dict[str, Any]:
    result = _WriterStatusStore(memory_dir).load()
    try:
        result["pending_turns"] = pending_turn_count(memory_dir)
    except Exception as exc:  # noqa: BLE001 - status remains queryable
        logger.debug("memory pending count unavailable: %s", exc)
        result["pending_turns"] = None
    return result


__all__ = [
    "STATUS_FILE", "STATUS_SCHEMA_VERSION", "pending_turn_count",
    "record_active_workspace_failure", "record_failure", "record_success",
    "status",
]
