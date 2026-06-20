"""Assistant-turn lifecycle helpers — placeholder insert + status flip.

Each chat turn writes one ``role="assistant"`` placeholder row first
(empty content, ``status="running"``) and updates it in place at the
end of the turn. The four terminal states:

  * ``completed``   — turn finished cleanly, ``content`` filled.
  * ``error``       — dispatcher caught an exception; ``content`` has
                       the error text, ``metadata.error`` has the
                       exception detail + trace.
  * ``cancelled``   — user clicked stop mid-stream; ``content`` may
                       hold partial output.
  * ``interrupted`` — the worker process died before flipping status.
                       Set by ``reconcile_interrupted_runs()`` on
                       worker startup (see ``webui/_exec_dag.py``).

The placeholder is stamped with the running worker's id so the
startup scan can in principle ignore rows another (still-live) worker
owns — currently single-process so that's moot, but we keep the
field for when we run multiple workers per DB.

Splitting these out of ``dispatcher.py`` keeps that file from
growing past its already-too-large 1500-line mark. The dispatcher
imports ``insert_placeholder`` / ``mark_completed`` / ``mark_error``
and treats them as opaque.
"""
from __future__ import annotations

import json
import os
import time
import traceback
import uuid
from typing import Any, Optional


# Stable per-process identifier stamped on assistant placeholders so
# the startup scan can tell its own (legitimately running) rows from
# stale rows the previous worker left behind. Generated lazily on
# first call so import-time side effects stay nil.
_WORKER_ID_CACHE: Optional[str] = None


def current_worker_id() -> str:
    """Lazy ``<pid>-<rand>`` worker identifier, cached per process."""
    global _WORKER_ID_CACHE
    if _WORKER_ID_CACHE is None:
        _WORKER_ID_CACHE = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"
    return _WORKER_ID_CACHE


def insert_placeholder(
    db: Any,
    session_id: str,
    assistant_msg_id: str,
    user_msg_id: str,
    source: str,
) -> bool:
    """Write the empty assistant placeholder at turn start.

    Returns True if the row landed, False on any failure (caller
    falls back to the legacy append-on-finish path).

    Writes a Call(role=ROLE_LLM) directly — same shape runtime.exec
    uses via _open_model_call_node (session-dag.md step 5).
    """
    try:
        from openprogram.context.nodes import Call, ROLE_LLM
        from openprogram.store import GraphStoreShim

        now = time.time()
        node = Call(
            id=assistant_msg_id,
            created_at=now,
            role=ROLE_LLM,
            output="",
            metadata={
                "parent_id": user_msg_id,
                "source": source,
                "status": "running",
                "worker_id": current_worker_id(),
                "started_at": now,
            },
        )
        shim = GraphStoreShim(db, session_id)
        shim.append(node)
        return True
    except Exception:
        return False


def mark_terminal_status(assistant_msg: dict, *, cancelled: bool) -> None:
    """Stamp the assistant message dict in place for step-5 persist.

    ``cancelled=True`` means the user clicked stop mid-stream (the
    agent loop returned early with partial content). Any other normal
    return is ``completed``.
    """
    assistant_msg["status"] = "cancelled" if cancelled else "completed"
    assistant_msg["completed_at"] = time.time()


def fold_error_into_placeholder(
    db_path: Any,
    session_id: str,
    assistant_msg_id: str,
    exc: BaseException,
) -> Optional[str]:
    """Overwrite the assistant placeholder with error content.

    ``db_path`` is kept as the first arg for caller-compat but it's now
    a SessionStore instance (legacy code passed a Path). We don't need
    a path — we resolve a per-session shim from default_store().
    """
    err_text = f"[error] {type(exc).__name__}: {exc}"
    trace = traceback.format_exc()[:2000]
    try:
        from openprogram.store import GraphStoreShim, default_store
        shim = GraphStoreShim(default_store(), session_id)
        shim.update(
            assistant_msg_id,
            output=err_text,
            metadata={
                "status": "error",
                "error": str(exc),
                "error_type": type(exc).__name__,
                "trace": trace,
            },
        )
        return err_text
    except Exception:
        return None


def write_standalone_error_node(
    db: Any,
    session_id: str,
    user_msg_id: str,
    source: str,
    exc: BaseException,
) -> str:
    """Last-resort error persistence when no placeholder exists.

    Mirrors the pre-lifecycle behaviour: writes a ``role="system"``
    node with the error text. Returns the new node id.
    """
    err_id = uuid.uuid4().hex[:12]
    err_text = f"[error] {type(exc).__name__}: {exc}"
    trace = traceback.format_exc()[:2000]
    db.append_message(session_id, {
        "id": err_id,
        "role": "system",
        "content": err_text,
        "timestamp": time.time(),
        "parent_id": user_msg_id,
        "source": source,
        "extra": json.dumps({"trace": trace}),
    })
    return err_id
