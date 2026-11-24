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

import logging
import json
import threading
import time
import uuid
from typing import Any, Optional

_log = logging.getLogger(__name__)

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
# Sessions that may hold queued entries, for the worker sweep. Seeded
# from disk on the first sweep, then kept by enqueue()/sweep().
_due: set[str] = set()
_seeded = False


def _session_lock(session_id: str) -> threading.Lock:
    with _locks_master:
        lk = _locks.get(session_id)
        if lk is None:
            lk = threading.Lock()
            _locks[session_id] = lk
        return lk


def _inbox_path(session_id: str):
    """Path to the session's inbox.json, or None when the session repo
    doesn't exist."""
    from openprogram.runtime.context.session_db import default_db
    sessions = default_db()
    if not sessions._session_dir(session_id).exists():  # noqa: SLF001 — same as job store
        return None
    return sessions.state_file(session_id, "inbox.json")


def _load(path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries = blob.get("entries") if isinstance(blob, dict) else None
    return entries if isinstance(entries, list) else []


def _write(path, entries: list[dict[str, Any]]) -> None:
    payload = {"version": 1, "entries": entries}
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")


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
    scope: str = "session",
    sender_name: str = "",
    label: str = "",
    dedup: bool = True,
    chain_ancestors: Optional[list[str]] = None,
    steer: Optional[dict[str, Any]] = None,
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
    (execution cancellation) while queued.

    ``scope`` says what has to be free before the entry runs: the whole
    target session (``"session"``, a cross-session send) or just the
    addressed branch (``"branch"``, a same-session send to a sub-agent
    that is still running) — see ``_ready``.

    ``sender_name`` goes into the receipt header (who sent it);
    ``label`` names the delivery's job after the target branch, so the
    reply notice back at the sender says which branch answered.

    ``chain_ancestors``: who waits above this message (send_message's
    cycle check); the delivery's Job carries them on.

    ``steer``: ``{"command_id", "execution_id"}`` of a steer the sender
    also stored on the running helper it started (send_message/steer.py).
    The entry is the fallback: drain drops it when the helper's turn took
    the steer, and delivers it as a queued message when it did not.

    Returns ``"queued"`` or ``"duplicate"`` (identical message from the
    same sender session already queued within DEDUP_WINDOW_SECS).

    A tracked-job entry is idempotent by ``job_id``: queuing the same Job
    again leaves the one entry and answers ``"queued"``. Job recovery
    relies on that to recreate an entry a crash lost (jobs/handoff.py),
    and passes ``dedup=False`` because the Job was admitted already.
    """
    path = _inbox_path(target_session_id)
    if path is None:
        raise ValueError(f"target session {target_session_id!r} not found")
    with _session_lock(target_session_id):
        entries = _load(path)
        now = time.time()
        if job_id and any(e.get("job_id") == job_id for e in entries):
            return "queued"
        for e in entries if dedup else ():
            if (
                e.get("sender_session_id") == sender_session_id
                and e.get("message") == message
                and now - float(e.get("enqueued_at") or 0) <= DEDUP_WINDOW_SECS
            ):
                return "duplicate"
        from openprogram.state.authority import normalize_authority
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
            "scope": scope,
            "sender_name": sender_name,
            "label": label,
            **({"chain_ancestors": list(chain_ancestors)} if chain_ancestors else {}),
            **({"steer": dict(steer)} if steer else {}),
            **normalize_authority(authority or {}),
        })
        dropped = None
        if len(entries) > MAX_PENDING:
            dropped = entries.pop(0)  # drop the oldest
        _write(path, entries)
        with _locks_master:
            _due.add(target_session_id)
            from openprogram.runtime.jobs.notices import announce_due
            announce_due()
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
        from openprogram.runtime.context.session_db import default_db
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
        }, advance_head=False)
        store.commit_turn(sender, "send_message: inbox notice")
    except Exception:
        # Best-effort notice: the inbox operation itself already succeeded.
        _log.warning("inbox notice not recorded in sender session %s", entry.get("sender_session_id"), exc_info=True)


def discard_job(session_id: str, job_id: str) -> bool:
    """Withdraw a tracked-job entry (``agent(to=…)``) from the target's
    inbox — cancellation of a still-queued dispatch. Returns True when an
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
    from openprogram.state.store import default_store
    store = default_store()
    removed = False
    if not store.root_path.exists():
        return False
    from openprogram.state.store.session.files.placement import iter_session_dirs
    for sid, sdir in iter_session_dirs(store.root_path):
        if store.state_file_at(sdir, "inbox.json").exists():
            if discard_job(sid, job_id):
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
        # The tracked execution does not wait forever on work that will never run.
        tid = entry.get("job_id")
        if tid:
            try:
                from openprogram.runtime.jobs import get_runner
                get_runner().cancel_execution(str(tid), reason=f"withdrawn: {reason}")
            except Exception:
                # The entry is already withdrawn; a stale job is reconciled later.
                _log.warning("tracked job %s not cancelled after inbox discard in session %s", tid, session_id, exc_info=True)
    return len(entries)


def drain(session_id: str) -> int:
    """Deliver every queued message for ``session_id`` whose target is
    free now, one turn each, through the normal async delivery path
    (run_agent_turn_async → job runner → notice back to the sender).
    Called at turn end and by the worker sweep. Returns the number
    delivered.

    Each entry is removed only after its delivery was submitted; a
    failed submission or a still-busy target leaves it queued for the
    next turn end or sweep.
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
        # A tracked-job entry whose execution was withdrawn while
        # queued) or otherwise finished must not run — drop the entry.
        tid = entry.get("job_id")
        if tid and _job_is_terminal(session_id, str(tid)):
            with _session_lock(session_id):
                remaining = [e for e in _load(path) if e.get("id") != entry.get("id")]
                _write(path, remaining)
            continue
        if not _ready(session_id, entry):
            continue
        if _steer_taken(entry):
            # The helper's turn took it at a tool boundary: delivered.
            with _session_lock(session_id):
                remaining = [e for e in _load(path) if e.get("id") != entry.get("id")]
                _write(path, remaining)
            continue
        try:
            _deliver(session_id, entry)
        except Exception:
            # Nothing announces that a failed submission may now work.
            from openprogram.runtime.jobs.notices import retry_soon
            retry_soon()
            continue
        delivered += 1
        with _session_lock(session_id):
            remaining = [e for e in _load(path) if e.get("id") != entry.get("id")]
            _write(path, remaining)
    return delivered


def _steer_taken(entry: dict[str, Any]) -> bool:
    steer = entry.get("steer")
    if not isinstance(steer, dict) or not steer.get("command_id"):
        return False
    from openprogram.programs.tools.agents.send_message.send_message.steer import taken
    return taken(str(steer["command_id"]))


def branch_busy(session_id: str, head_id: Optional[str], *, ignore: Optional[str] = None) -> bool:
    """True while an unfinished job runs on, or is about to extend, the
    branch whose tip is ``head_id``. ``ignore`` is a queued entry's own
    pre-created job, which waits on that branch without running."""
    if not head_id:
        return False
    from openprogram.runtime.jobs.store import list_jobs
    from openprogram.runtime.jobs.types import branch_nodes, is_terminal
    for job in list_jobs(session_id):
        if job.id != ignore and not is_terminal(job.status) and head_id in branch_nodes(job):
            return True
    return False


def target_busy(run_session: str, head_id: Optional[str], sender_session: str) -> bool:
    """Whether a delivery to ``run_session:head_id`` has to wait. A
    cross-session target waits for its session to go quiet; inside the
    sender's own session only the addressed branch matters, because the
    sender's running turn is always there."""
    if run_session != sender_session:
        from openprogram.runtime.conversation.run_control import is_turn_running
        return is_turn_running(run_session)
    return branch_busy(run_session, head_id)


def _branch_tip(session_id: str, head_id: Optional[str]) -> Optional[str]:
    """The current tip of the branch ``head_id`` was the tip of when the
    entry was queued — the sub-agent that kept it busy has extended it."""
    if not head_id:
        return None
    from openprogram.programs.tools.agents.send_message.send_message.addressing import (
        _normalize_existing_target,
    )
    status, tip = _normalize_existing_target(session_id, head_id)
    return tip if status == "ok" and isinstance(tip, str) else head_id


def _ready(session_id: str, entry: dict[str, Any]) -> bool:
    if entry.get("scope") == "branch":
        tip = _branch_tip(session_id, entry.get("target_head_id"))
        return not branch_busy(session_id, tip, ignore=entry.get("job_id"))
    from openprogram.runtime.conversation.run_control import is_turn_running
    return not is_turn_running(session_id)


def has_due() -> bool:
    """Whether a queued message still waits for its agent."""
    with _locks_master:
        return bool(_due)


def sweep() -> None:
    """Deliver whatever has become deliverable, wherever it is queued.
    Turn end drains its own session; this catches the rest: a branch
    that finished inside a busy session, and a turn that ended without
    draining (cancelled, crashed, worker restart)."""
    global _seeded
    if not _seeded:
        from openprogram.state.store import default_store
        from openprogram.state.store.session.files.placement import iter_session_dirs
        sessions = default_store()
        root = sessions.root_path
        found = {sid for sid, sdir in iter_session_dirs(root)
                 if sessions.state_file_at(sdir, "inbox.json").exists()} if root.exists() else set()
        with _locks_master:
            _due.update(found)
        _seeded = True
    with _locks_master:
        due = list(_due)
    for session_id in due:
        try:
            drain(session_id)
        except Exception:
            from openprogram.runtime.jobs.notices import retry_soon
            retry_soon()
            continue
        if pending_count(session_id) == 0:
            with _locks_master:
                _due.discard(session_id)


def _job_is_terminal(session_id: str, job_id: str) -> bool:
    try:
        from openprogram.runtime.jobs.store import load_job
        from openprogram.runtime.jobs.types import is_terminal
        t = load_job(session_id, job_id)
        if t is not None and is_terminal(t.status):
            return True
        from openprogram.runtime.jobs import get_runner
        t = get_runner().get_job(job_id)
        return t is not None and is_terminal(t.status)
    except Exception:
        return False


def _deliver(session_id: str, entry: dict[str, Any]) -> None:
    """Trigger one turn on the target for a queued entry — same shape as
    the direct existing-branch delivery in send_message. A tracked-job
    entry (``job_id`` set) delivers with the job header and reuses the
    pre-created job id so the dispatcher's handle stays valid."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.peer_agents.sub_agent_run import run_agent_turn_async
    from openprogram.programs.tools.agents.send_message.send_message.delivery import (
        sender_header,
        job_header,
    )

    # Continue the branch the message was addressed to, from its CURRENT
    # tip: the turn that made the target busy has advanced it, and the
    # queued message logically follows that turn. Whichever branch is
    # selected in the target session meanwhile does not matter. Only an
    # entry with no recorded branch falls back to the session head.
    if entry.get("target_head_id"):
        head = _branch_tip(session_id, entry.get("target_head_id"))
    else:
        head = (default_db().get_session(session_id) or {}).get("head_id")
    message = str(entry.get("message") or "")
    tid = entry.get("job_id")
    tracked_job = bool(entry.get("tracked_job", bool(tid)))
    sender = (
        str(entry.get("sender_session_id") or ""),
        str(entry.get("sender_msg_id") or ""),
        str(entry.get("sender_name") or ""),
    )
    prompt = (job_header(*sender) if tracked_job
              else sender_header(*sender, other_conversation=str(
                  entry.get("sender_session_id") or "") != session_id)) + message
    run_agent_turn_async(
        job_id=str(tid) if tid else None,
        session_id=session_id,
        prompt=prompt,
        agent_id=str(entry.get("agent_id") or "main"),
        branch_from=head,
        context_mode="inherit" if head else "clean",
        label=str(entry.get("label") or "") or None,
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
        message_origin="agent_delivery",
        chain_ancestors=list(entry.get("chain_ancestors") or []) or None,
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
    "branch_busy",
    "target_busy",
    "sweep",
]
