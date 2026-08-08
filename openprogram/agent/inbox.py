"""Per-session send_message inbox — queueing for busy targets.

When ``send_message(to="SID:HEAD")`` addresses a branch whose session is
mid-turn (``run_control.is_turn_running``), delivering immediately would
race the running turn for the same head. Instead the message is persisted
here — ``<session-repo>/inbox.json``, same placement pattern as
``tasks.json`` (agent/task/store.py) — and the dispatcher drains the
inbox at turn end (``_process_turn_once``), re-delivering each entry
through the normal async delivery path so the usual auto-followup returns
the reply to the sender.

Delivery-then-delete: an entry is removed only after its delivery turn
was successfully submitted. A crash between the two may re-deliver a
message (acceptable); the reverse order could silently lose one (not
acceptable).

Limits (mirroring Claude Code cross-session messaging):
  * at most ``MAX_PENDING`` (50) entries per target session — a full
    inbox drops the oldest entry and leaves a system notice in the
    dropped message's sender session;
  * an identical message from the same sender session within
    ``DEDUP_WINDOW_SECS`` (60s) of a still-queued copy is rejected as a
    duplicate.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

MAX_PENDING = 50
DEDUP_WINDOW_SECS = 60.0

_locks: dict[str, threading.Lock] = {}
_locks_master = threading.Lock()


def _session_lock(session_id: str) -> threading.Lock:
    with _locks_master:
        lk = _locks.get(session_id)
        if lk is None:
            lk = threading.Lock()
            _locks[session_id] = lk
        return lk


def _inbox_path(session_id: str) -> Optional[Path]:
    """Path to the session's inbox.json, or None when the session repo
    doesn't exist."""
    from openprogram.agent.session_db import default_db
    sdir = default_db()._session_dir(session_id)  # noqa: SLF001 — same as task store
    if not sdir.exists():
        return None
    return sdir / "inbox.json"


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries = blob.get("entries") if isinstance(blob, dict) else None
    return entries if isinstance(entries, list) else []


def _write(path: Path, entries: list[dict[str, Any]]) -> None:
    payload = {"version": 1, "entries": entries}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, default=str, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def pending_count(session_id: str) -> int:
    path = _inbox_path(session_id)
    if path is None:
        return 0
    with _session_lock(session_id):
        return len(_load(path))


def enqueue(
    target_session_id: str,
    *,
    message: str,
    sender_session_id: str,
    sender_msg_id: str,
    sender_agent_id: Optional[str],
    agent_id: str,
    spawn_depth: int,
    target_head_id: Optional[str],
) -> str:
    """Queue a message for a busy target session.

    ``message`` is the full delivery body (sources already assembled);
    the sender-receipt header is added at drain time. ``spawn_depth`` is
    the SENDER's depth at send time — drain delivers at depth+1, exactly
    like the direct path.

    Returns ``"queued"`` or ``"duplicate"`` (identical message from the
    same sender session already queued within DEDUP_WINDOW_SECS).
    """
    path = _inbox_path(target_session_id)
    if path is None:
        raise ValueError(f"target session {target_session_id!r} not found")
    with _session_lock(target_session_id):
        entries = _load(path)
        now = time.time()
        for e in entries:
            if (
                e.get("sender_session_id") == sender_session_id
                and e.get("message") == message
                and now - float(e.get("enqueued_at") or 0) <= DEDUP_WINDOW_SECS
            ):
                return "duplicate"
        entries.append({
            "id": uuid.uuid4().hex[:12],
            "message": message,
            "sender_session_id": sender_session_id,
            "sender_msg_id": sender_msg_id,
            "sender_agent_id": sender_agent_id,
            "agent_id": agent_id,
            "spawn_depth": int(spawn_depth),
            "target_head_id": target_head_id,
            "enqueued_at": now,
        })
        dropped = None
        if len(entries) > MAX_PENDING:
            dropped = entries.pop(0)  # drop the oldest
        _write(path, entries)
    if dropped is not None:
        _notify_dropped(target_session_id, dropped)
    return "queued"


def _notify_dropped(target_session_id: str, entry: dict[str, Any]) -> None:
    """Leave a system notice in the dropped message's sender session.
    Best-effort; the notice is a runtime-display node and must not move
    the sender's head."""
    try:
        from openprogram.agent.session_db import default_db
        store = default_db()
        sender = entry.get("sender_session_id")
        if not sender or store.get_session(sender) is None:
            return
        head_before = (store.get_session(sender) or {}).get("head_id")
        preview = str(entry.get("message") or "").replace("\n", " ")[:200]
        store.append_message(sender, {
            "id": uuid.uuid4().hex[:12],
            "role": "assistant",
            "display": "runtime",
            "function": "send_message",
            "content": (
                f"[send_message] your queued message to session "
                f"{target_session_id} was dropped: the target's inbox is "
                f"full ({MAX_PENDING} pending). Dropped message: {preview}"
            ),
            "predecessor": head_before,
            "timestamp": time.time(),
        })
        if head_before:
            try:
                store.set_head(sender, head_before)
            except Exception:
                pass
        store.commit_turn(sender, "send_message: inbox-full drop notice")
    except Exception:
        pass


def drain(session_id: str) -> int:
    """Deliver every queued message for ``session_id``, one turn each,
    through the normal async delivery path (run_agent_turn_async → task
    runner → auto-followup back to the sender). Called from the
    dispatcher at turn end. Returns the number delivered.

    Each entry is removed only after its delivery was submitted; a
    failed submission leaves the entry queued for the next turn end.
    """
    path = _inbox_path(session_id)
    if path is None or not path.exists():
        return 0
    with _session_lock(session_id):
        entries = _load(path)
    if not entries:
        return 0
    delivered = 0
    for entry in entries:
        try:
            _deliver(session_id, entry)
        except Exception:
            continue
        delivered += 1
        with _session_lock(session_id):
            remaining = [e for e in _load(path) if e.get("id") != entry.get("id")]
            _write(path, remaining)
    return delivered


def _deliver(session_id: str, entry: dict[str, Any]) -> None:
    """Trigger one turn on the target for a queued entry — same shape as
    the direct existing-branch delivery in send_message."""
    from openprogram.agent.session_db import default_db
    from openprogram.agent.sub_agent_run import run_agent_turn_async
    from openprogram.functions.tools.send_message.send_message import sender_header

    # Continue from the branch's CURRENT head: the turn that made the
    # target busy has advanced it, and the queued message logically
    # follows that turn. The head recorded at enqueue time is the
    # fallback when the session head is unreadable.
    head = (default_db().get_session(session_id) or {}).get("head_id") \
        or entry.get("target_head_id")
    message = str(entry.get("message") or "")
    prompt = sender_header(
        str(entry.get("sender_session_id") or ""),
        str(entry.get("sender_msg_id") or ""),
    ) + message
    run_agent_turn_async(
        session_id=session_id,
        prompt=prompt,
        agent_id=str(entry.get("agent_id") or "main"),
        branch_from=head,
        context_mode="inherit" if head else "clean",
        label=message[:60],
        subject=message[:60],
        description=prompt,
        caller_msg_id=entry.get("sender_msg_id"),
        caller_session_id=entry.get("sender_session_id"),
        # Inherit the depth recorded at enqueue time (+1 for the child
        # turn) — a queued hop still counts toward the spawn-depth guard.
        spawn_depth=int(entry.get("spawn_depth") or 0) + 1,
    )


__all__ = [
    "MAX_PENDING",
    "DEDUP_WINDOW_SECS",
    "enqueue",
    "drain",
    "pending_count",
]
