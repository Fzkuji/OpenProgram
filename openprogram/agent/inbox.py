"""Per-session send_message inbox — queueing for busy targets.

When ``send_message(to="SID:HEAD")`` addresses a branch whose session is
mid-turn (``run_control.is_turn_running``), delivering immediately would
race the running turn for the same head. Instead the message is persisted
here — ``<session-repo>/inbox.json``, same placement pattern as
``jobs.json`` (agent/job/store.py) — and the dispatcher drains the
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

# Inbox depth. 50 is Claude Code's number for the same thing: its
# cross-session mailbox is a 50-entry ring that drops the oldest
# (2.1.226 binary @253677687), and it is the only reference
# implementation with a queue to compare against — openclaw and
# codex-cli deliver into a running thread instead of a mailbox, and the
# other five have no inbox at all. Raise it if a session is legitimately
# addressed by more than 50 senders while one turn runs; the overflow
# notice in the dropped sender's session tells you when that happens.
MAX_PENDING = 50

# Duplicate window. The check only fires against entries that are STILL
# QUEUED, so this bounds one thing: how long a sender has to wait before
# the same text counts as a deliberate resend rather than a retry loop.
# A model that retries because it got no answer retries inside the same
# turn, seconds apart, so 60s covers the loop with room to spare while
# leaving a genuine "I asked five minutes ago, asking again" through.
# No reference implementation has a content-plus-time duplicate check to
# copy: Claude Code dedups by message uuid and weclaw by inbound message
# id for 5 minutes, both of which only catch a byte-identical
# retransmission of the same message object, never a model that composed
# the same text twice. Shorten it if deliberate resends are being
# refused; lengthen it if retry loops get through.
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
    sdir = default_db()._session_dir(session_id)  # noqa: SLF001 — same as job store
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
    chain_messages: int,
    target_head_id: Optional[str],
    chain_generations: int = 0,
    job_id: Optional[str] = None,
    tracked_job: Optional[bool] = None,
    authority: Optional[dict[str, Any]] = None,
) -> str:
    """Queue a message for a busy target session.

    ``message`` is the full delivery body; the sender-receipt header is
    added at drain time. ``chain_messages`` is the SENDER's count at
    send time — drain delivers at count+1, exactly like the direct
    path. ``chain_generations`` is the sender's other count and travels
    through unchanged: a queued delivery creates no agent either.

    ``job_id``: set for tracked-job dispatches (``agent(to=…)``) — the
    pre-created pending Job this entry will run when drained. Drain
    reuses the id (the dispatcher already holds it), delivers with the
    job header, and skips the entry if the job was withdrawn
    (``job_stop``) while queued.

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
        from openprogram.agent.authority import normalize_authority
        entries.append({
            "id": uuid.uuid4().hex[:12],
            "message": message,
            "sender_session_id": sender_session_id,
            "sender_msg_id": sender_msg_id,
            "sender_agent_id": sender_agent_id,
            "agent_id": agent_id,
            "chain_messages": int(chain_messages),
            "chain_generations": int(chain_generations),
            "target_head_id": target_head_id,
            "job_id": job_id,
            "tracked_job": bool(job_id) if tracked_job is None else tracked_job,
            "enqueued_at": now,
            **normalize_authority(authority or {}),
        })
        dropped = None
        if len(entries) > MAX_PENDING:
            dropped = entries.pop(0)  # drop the oldest
        _write(path, entries)
    if dropped is not None:
        preview = str(dropped.get("message") or "").replace("\n", " ")[:200]
        _notify_sender(dropped, (
            f"[send_message] your queued message to session "
            f"{target_session_id} was dropped: the target's inbox is "
            f"full ({MAX_PENDING} pending). Dropped message: {preview}"
        ))
    return "queued"


def _notify_sender(entry: dict[str, Any], content: str) -> None:
    """Leave a system notice in the entry's sender session. Best-effort;
    the notice is a runtime-display node and must not move the sender's
    head."""
    try:
        from openprogram.agent.session_db import default_db
        store = default_db()
        sender = entry.get("sender_session_id")
        if not sender or store.get_session(sender) is None:
            return
        head_before = (store.get_session(sender) or {}).get("head_id")
        store.append_message(sender, {
            "id": uuid.uuid4().hex[:12],
            "role": "assistant",
            "display": "runtime",
            "function": "send_message",
            "content": content,
            "predecessor": head_before,
            "timestamp": time.time(),
        })
        if head_before:
            try:
                store.set_head(sender, head_before)
            except Exception:
                pass
        store.commit_turn(sender, "send_message: inbox notice")
    except Exception:
        pass


def discard_job(session_id: str, job_id: str) -> bool:
    """Withdraw a tracked-job entry (``agent(to=…)``) from the target's
    inbox — job_stop on a still-queued dispatch. Returns True when an
    entry was removed."""
    path = _inbox_path(session_id)
    if path is None or not path.exists():
        return False
    with _session_lock(session_id):
        entries = _load(path)
        remaining = [e for e in entries if e.get("job_id") != job_id]
        if len(remaining) == len(entries):
            return False
        _write(path, remaining)
        return True


def discard_tracked_job(job_id: str) -> bool:
    """Withdraw ``job_id`` from every session inbox that queued it.

    Cancel may resolve a caller-side mirror first; the queued entry
    still lives on the target session.
    """
    from openprogram.store import default_store
    store = default_store()
    removed = False
    if not store.root_path.exists():
        return False
    for sdir in store.root_path.iterdir():
        if not sdir.is_dir():
            continue
        if (sdir / "inbox.json").exists():
            if discard_job(sdir.name, job_id):
                removed = True
    return removed


def clear(session_id: str, *, reason: str = "the target session was stopped") -> int:
    """Session-level cancel: drop every queued entry for ``session_id``
    and leave a system notice in each sender session.

    A user stopping a session wants all of its work to stop — the turn
    in flight AND the queued messages that would each have started a new
    turn at drain time. Returns the number of entries cleared.
    """
    path = _inbox_path(session_id)
    if path is None or not path.exists():
        return 0
    with _session_lock(session_id):
        entries = _load(path)
        if entries:
            _write(path, [])
    for entry in entries:
        preview = str(entry.get("message") or "").replace("\n", " ")[:200]
        _notify_sender(entry, (
            f"[send_message] your queued message to session {session_id} "
            f"was discarded: {reason}. It was not delivered. "
            f"Message: {preview}"
        ))
        # A tracked-job entry has a pending Job entity waiting on the
        # delivery — flip it to cancelled so the dispatcher's
        # job_output doesn't wait forever on work that will never run.
        tid = entry.get("job_id")
        if tid:
            try:
                from openprogram.agent.job import get_runner
                get_runner().cancel_job(str(tid), reason=f"withdrawn: {reason}")
            except Exception:
                pass
    return len(entries)


def drain(session_id: str) -> int:
    """Deliver every queued message for ``session_id``, one turn each,
    through the normal async delivery path (run_agent_turn_async → job
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
        # A tracked-job entry whose job was withdrawn (job_stop while
        # queued) or otherwise finished must not run — drop the entry.
        tid = entry.get("job_id")
        if tid and _job_is_terminal(session_id, str(tid)):
            with _session_lock(session_id):
                remaining = [e for e in _load(path) if e.get("id") != entry.get("id")]
                _write(path, remaining)
            continue
        try:
            _deliver(session_id, entry)
        except Exception:
            continue
        delivered += 1
        with _session_lock(session_id):
            remaining = [e for e in _load(path) if e.get("id") != entry.get("id")]
            _write(path, remaining)
    return delivered


def _job_is_terminal(session_id: str, job_id: str) -> bool:
    try:
        from openprogram.agent.job.store import load_job
        from openprogram.agent.job.types import is_terminal
        t = load_job(session_id, job_id)
        if t is not None and is_terminal(t.status):
            return True
        from openprogram.agent.job import get_runner
        t = get_runner().get_job(job_id)
        return t is not None and is_terminal(t.status)
    except Exception:
        return False


def _deliver(session_id: str, entry: dict[str, Any]) -> None:
    """Trigger one turn on the target for a queued entry — same shape as
    the direct existing-branch delivery in send_message. A tracked-job
    entry (``job_id`` set) delivers with the job header and reuses the
    pre-created job id so the dispatcher's handle stays valid."""
    from openprogram.agent.session_db import default_db
    from openprogram.agent.sub_agent_run import run_agent_turn_async
    from openprogram.programs.tools.agents.send_message.send_message.delivery import (
        sender_header,
        job_header,
    )

    # Continue from the branch's CURRENT head: the turn that made the
    # target busy has advanced it, and the queued message logically
    # follows that turn. The head recorded at enqueue time is the
    # fallback when the session head is unreadable.
    head = (default_db().get_session(session_id) or {}).get("head_id") \
        or entry.get("target_head_id")
    message = str(entry.get("message") or "")
    tid = entry.get("job_id")
    tracked_job = bool(entry.get("tracked_job", bool(tid)))
    header = job_header if tracked_job else sender_header
    prompt = header(
        str(entry.get("sender_session_id") or ""),
        str(entry.get("sender_msg_id") or ""),
    ) + message
    run_agent_turn_async(
        job_id=str(tid) if tid else None,
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
        # Inherit the count recorded at enqueue time (+1 for the child
        # turn) — a queued hop still counts toward the message budget.
        # ``spawn_depth`` is the pre-rename key, read for inboxes
        # written by an older build.
        chain_messages=int(
            entry.get("chain_messages", entry.get("spawn_depth", 0)) or 0
        ) + 1,
        # The queue holds messages and dispatches, never a spawn, so the
        # generation count arrives unchanged and the reply turn back at
        # the sender runs at the same one.
        chain_generations=int(entry.get("chain_generations") or 0),
        caller_chain_generations=int(entry.get("chain_generations") or 0),
        authority=entry,
        creates_agent=False,
        resume_deferred=bool(tid),
    )


__all__ = [
    "MAX_PENDING",
    "DEDUP_WINDOW_SECS",
    "enqueue",
    "drain",
    "discard_job",
    "discard_tracked_job",
    "clear",
    "pending_count",
]
