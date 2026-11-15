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

from openprogram.host._platform import locking
from openprogram.state.memory.backend import MemoryWriteFailureCode
from openprogram.state.store.session.atomic_file import atomic_write_text

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
        locking.flock(handle.fileno(), locking.LOCK_EX)
        try:
            yield
        finally:
            locking.flock(handle.fileno(), locking.LOCK_UN)


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
    ) -> bool:
        """Record one failure; True when nothing will retry it and it starts
        a failure streak, changes its reason, or is the first time this
        reason stops being retried: that is when someone should hear about
        it, since a retryable failure is retried on its own.

        Once a reason has stopped being retried, it reads as stopped until
        a write succeeds or the reason changes: several conversations
        failing for one cause, each a little behind the other, keep saying
        "stopped" rather than flipping back to "retrying" between them, and
        that one cause is announced once.
        """
        code = _normalize_reason_code(reason_code)
        with _STATUS_WRITE_LOCK, _status_file_lock(self.path):
            payload = self.load()
            previous = payload.get("last_failure") or {}
            same_streak = (
                payload.get("last_outcome") == "failure"
                and previous.get("reason_code") == code
            )
            stopped_before = same_streak and previous.get("retryable") is False
            retryable = bool(retryable) and not stopped_before
            news = not retryable and (
                not same_streak or previous.get("retryable") is True
            )
            payload["last_outcome"] = "failure"
            payload["last_failure"] = {
                "at": _now(),
                "reason_code": code,
                "retryable": retryable,
            }
            self._save(payload)
        return news


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
        news = _WriterStatusStore(memory_dir).record_failure(
            reason_code, retryable=retryable,
        )
    except Exception as exc:  # noqa: BLE001 - status cannot fail a write
        logger.debug("memory writer status failure was not persisted: %s", exc)
        return
    if news:
        _announce_failure(reason_code, retryable=False)


def _announce_failure(
    reason_code: MemoryWriteFailureCode | str | None, *, retryable: bool,
) -> None:
    """Tell open clients that memory stopped being written.

    Otherwise the only trace is the overview ``memory_get`` returns, which nobody reads until
    they wonder why memory has not kept up.
    """
    try:
        from openprogram.state.event_sink import emit_ws_frame

        emit_ws_frame({"type": "memory_writer_failed", "data": {
            "reason_code": _normalize_reason_code(reason_code),
            "retryable": bool(retryable),
        }})
    except Exception as exc:  # noqa: BLE001 - observability is non-blocking
        logger.debug("memory writer failure was not announced: %s", exc)


def record_active_workspace_failure(
    reason_code: MemoryWriteFailureCode | str | None,
    *,
    retryable: bool,
) -> None:
    """Record a failure for the active profile's memory workspace."""
    try:
        from openprogram.state.memory import store

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
    """Citable chat turns without this workspace's marker.

    The current branch and every non-archived branch tip are considered,
    matching forced idle writing. Shared prefixes are counted once. Tool,
    runtime, and authority-denied rows are removed by the writer's own
    ``_records`` boundary. This read-only count does not create an agent
    or change node markers.
    """
    if session_store is None:
        from openprogram.state.store.current import current_store

        session_store = current_store()
    from .. import writing

    workspace_id = _existing_workspace_id(Path(memory_dir))
    total = 0
    for session in session_store.list_sessions(limit=10**9, include_archived=True):
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


def retry_summary(memory_dir: Path) -> dict[str, Any]:
    """The idle-session watcher's retries for conversations that write to
    this memory, as the status shows them.

    ``waiting`` sessions are offered again at ``next_attempt_at``;
    ``stopped`` ones failed ``max_identical_failures`` times on the same
    input and wait for their conversation to change.
    """
    from ..session_watcher import SESSION_MAX_ATTEMPTS, retries_for

    entries = retries_for(memory_dir)
    waiting = [e for e in entries.values() if not e.get("stopped")]
    stopped = [e for e in entries.values() if e.get("stopped")]

    def stamp(value: Any) -> str | None:
        if not isinstance(value, (int, float)):
            return None
        return datetime.fromtimestamp(value, timezone.utc).isoformat()

    return {
        "waiting": len(waiting),
        "stopped": len(stopped),
        "next_attempt_at": stamp(min(
            (e["next_at"] for e in waiting), default=None,
        )),
        "max_attempts": max(
            (e["attempts"] for e in entries.values()), default=0,
        ),
        "max_identical_failures": SESSION_MAX_ATTEMPTS,
        "reason_codes": sorted({
            _normalize_reason_code(e.get("reason_code"))
            for e in entries.values()
        }),
    }


def status(memory_dir: Path) -> dict[str, Any]:
    result = _WriterStatusStore(memory_dir).load()
    try:
        result["pending_turns"] = pending_turn_count(memory_dir)
    except Exception as exc:  # noqa: BLE001 - status remains queryable
        logger.debug("memory pending count unavailable: %s", exc)
        result["pending_turns"] = None
    # Both appear only while there is something to say, so a healthy
    # writer reads the same as it always has.
    try:
        retries = retry_summary(memory_dir)
    except Exception as exc:  # noqa: BLE001 - status remains queryable
        logger.debug("memory retry state unavailable: %s", exc)
        retries = None
    if retries and (retries["waiting"] or retries["stopped"]):
        result["retries"] = retries
    # Topic files that are not text: the one thing a file can hold that
    # memory cannot read. Each stays out of memory on its own.
    not_text = unreadable_topics(memory_dir)
    if not_text:
        result["not_text"] = not_text
    paused = writing_paused(memory_dir)
    if paused:
        result["paused"] = paused
    return result


def unreadable_topics(memory_dir: Path) -> list[str]:
    """Topic files (``topics/...``) whose bytes do not decode as text."""
    from ..markdown.parser import NOT_TEXT, read_topic_tree

    try:
        problems = read_topic_tree(Path(memory_dir) / "topics").problems
    except Exception:  # noqa: BLE001 - status remains queryable
        return []
    return sorted(
        f"topics/{relative}" for relative, problem in problems.items()
        if problem == NOT_TEXT
    )


def writing_paused(memory_dir: Path) -> dict[str, Any] | None:
    """Why every conversation's writing into this memory is paused, from
    the watcher's record: this memory's own stop, or the writer's sign-in.
    None while nothing is paused."""
    try:
        from ..session_watcher import load_state, _same_dir

        state = load_state()
    except Exception:  # noqa: BLE001 - status remains queryable
        return None
    for scope, stops in (
        ("credential", state.get("credentials") or {}),
        ("workspace", {
            key: stop for key, stop in (state.get("workspaces") or {}).items()
            if _same_dir(key, memory_dir)
        }),
    ):
        for stop in stops.values():
            return {
                "scope": scope,
                "reason_code": _normalize_reason_code(stop.get("reason_code")),
                "reason": str(stop.get("reason") or ""),
            }
    return None


__all__ = [
    "STATUS_FILE", "STATUS_SCHEMA_VERSION", "pending_turn_count",
    "record_active_workspace_failure", "record_failure", "record_success",
    "status",
]
