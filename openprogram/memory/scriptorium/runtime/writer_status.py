"""Persistent, queryable health of the background memory writer."""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openprogram.store.session.git_session import atomic_write_text

from ..workspace_layout import runtime_dir

logger = logging.getLogger(__name__)

STATUS_FILE = "writer-status.json"
UNKNOWN_FAILURE = "UnknownFailure"
_CLASSIFICATION = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}")
_WORKSPACE_ID = re.compile(r"w-[0-9a-f]{8}")
_STATUS_WRITE_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classification(value: object) -> str:
    text = str(value or "").strip()
    return text if _CLASSIFICATION.fullmatch(text) else UNKNOWN_FAILURE


class WriterStatusStore:
    """The small status file under the workspace's internal runtime dir."""

    def __init__(self, memory_dir: Path):
        self.path = runtime_dir(Path(memory_dir)) / STATUS_FILE

    def load(self) -> dict[str, Any]:
        empty = {"last_success_at": None, "last_failure": None}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return empty
        if not isinstance(payload, dict):
            return empty
        success = payload.get("last_success_at")
        failure = payload.get("last_failure")
        if not isinstance(success, str):
            success = None
        if not (
            isinstance(failure, dict)
            and isinstance(failure.get("at"), str)
            and isinstance(failure.get("retryable"), bool)
        ):
            failure = None
        elif _classification(failure.get("reason")) != failure.get("reason"):
            failure = None
        else:
            failure = {
                "at": failure["at"],
                "reason": failure["reason"],
                "retryable": failure["retryable"],
            }
        return {"last_success_at": success, "last_failure": failure}

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    def record_success(self) -> None:
        with _STATUS_WRITE_LOCK:
            payload = self.load()
            payload["last_success_at"] = _now()
            self._save(payload)

    def record_failure(self, reason: object, *, retryable: bool) -> None:
        with _STATUS_WRITE_LOCK:
            payload = self.load()
            payload["last_failure"] = {
                "at": _now(),
                "reason": _classification(reason),
                "retryable": bool(retryable),
            }
            self._save(payload)


def record_success(memory_dir: Path) -> None:
    try:
        WriterStatusStore(memory_dir).record_success()
    except Exception as exc:  # noqa: BLE001 - status cannot fail a write
        logger.debug("memory writer status success was not persisted: %s", exc)


def record_failure(
    memory_dir: Path, reason: object, *, retryable: bool,
) -> None:
    try:
        WriterStatusStore(memory_dir).record_failure(
            reason, retryable=retryable,
        )
    except Exception as exc:  # noqa: BLE001 - status cannot fail a write
        logger.debug("memory writer status failure was not persisted: %s", exc)


def record_current_failure(reason: object, *, retryable: bool) -> None:
    """Best-effort failure record, including resolution of the state root."""
    try:
        from openprogram.memory import store

        record_failure(store.root(), reason, retryable=retryable)
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

    Every session DAG node is considered once through ``get_messages``.
    Tool/runtime turns and authority-denied rows are removed by the same
    ``_records`` boundary the writer uses. This is a read-only count: it does
    not migrate old cursors, create a writer agent, or change node markers.
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
        records = writing._records(session_id, messages)
        marked = {
            str(row.get("id"))
            for row in messages
            if workspace_id and row.get(writing.MARKER) == workspace_id
        }
        total += sum(
            1
            for record in records
            if record.message_id not in marked
        )
    return total


def status(memory_dir: Path) -> dict[str, Any]:
    result = WriterStatusStore(memory_dir).load()
    try:
        result["pending_turns"] = pending_turn_count(memory_dir)
    except Exception as exc:  # noqa: BLE001 - status remains queryable
        logger.debug("memory pending count unavailable: %s", exc)
        result["pending_turns"] = None
    return result


__all__ = [
    "STATUS_FILE", "UNKNOWN_FAILURE", "WriterStatusStore",
    "pending_turn_count", "record_current_failure", "record_failure",
    "record_success", "status",
]
