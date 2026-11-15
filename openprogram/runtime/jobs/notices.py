"""Tell an agent when work it handed off in the background ends.

As in Claude Code, a background agent (``agent(run_in_background=true)``,
``send_message``) that finishes starts a turn in the conversation that
sent it, saying so. Nothing is said when the sender already read the
result with ``read_conversation``. Each agent's part of the notice is the
result block of ``agent_result.render``: final reply, files changed and
usage, the same shape the ``agent`` tool returns when the caller waits.
A helper the person stopped, one a default limit stopped, and one that has
stalled (``delegation_watch``) are told the same way. A conversation that is
mid-turn gets the notice once its foreground turn ends, and every agent
that finished meanwhile arrives in that one notice. The notice is an
automated event, never a message from the user.

``send_message(to=…, notify_when_idle=true)`` with no message rides the
same queue: a watch entry that turns into one notice, carrying the
target's latest reply, once the target branch or session goes idle.

A message passed on is answered at the end of the chain (``hold``): when
A asks B and B, in that turn, asks C, A's notice waits until C's reply
has reached B and B's turn on it has ended, and then carries B's answer
from that turn, not B's "I asked C" (the always-on hand-off's
``owed_after`` chain). Each conversation's notices file records which
results its notice turns have answered (``answered``).

Pending notices live in ``<caller-session-dir>/notices.json`` so a worker
restart does not lose them; the worker loop (``run_job_notices``) delivers
them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any, Optional

from openprogram.runtime.jobs.types import Job, JobStatus

POLL_SECONDS = 2.0
_REPLY_CHARS = 20_000
# Jobs already told or read, remembered so a second terminal projection
# of the same job cannot notify twice. Only recent ids matter: the
# projections of one job's end happen within seconds of each other.
_DONE_KEPT = 500
_log = logging.getLogger(__name__)

_lock = threading.Lock()
# Sessions that may hold a pending notice. Seeded from disk on the first
# sweep, then kept by queue()/consume().
_due: set[str] = set()
_seeded = False


def deliver_session_of(job: Job) -> str:
    return job.caller_session_id or job.parent_session_id


def _path(session_id: str):
    from openprogram.runtime.context.session_db import default_db
    sessions = default_db()
    if not sessions._session_dir(session_id).exists():  # noqa: SLF001 — same as inbox
        return None
    return sessions.state_file(session_id, "notices.json")


def _load(path) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return [], []
    if not isinstance(blob, dict):
        return [], []
    entries, done = blob.get("entries"), blob.get("done")
    return (entries if isinstance(entries, list) else [],
            done if isinstance(done, list) else [])


def _answered(path) -> list[str]:
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    answered = blob.get("answered") if isinstance(blob, dict) else None
    return answered if isinstance(answered, list) else []


def _write(path, entries: list[dict[str, Any]], done: list[str],
           answered: Optional[list[str]] = None) -> None:
    if answered is None:
        answered = _answered(path) if path.exists() else []
    blob: dict[str, Any] = {"version": 1, "entries": entries, "done": done[-_DONE_KEPT:]}
    if answered:
        blob["answered"] = answered[-_DONE_KEPT:]
    path.write_text(json.dumps(blob), encoding="utf-8")


def answered(session_id: str, job_ids: list[str]) -> None:
    """A notice turn in ``session_id`` about these jobs has ended: a reply
    held for them (``hold``) can go on now."""
    path = _path(session_id)
    if path is None or not job_ids:
        return
    with _lock:
        entries, done = _load(path) if path.exists() else ([], [])
        seen = _answered(path) if path.exists() else []
        _write(path, entries, done, seen + [j for j in job_ids if j not in seen])


def hold(passing_on: Job, *, waits_on: Job) -> bool:
    """Hold the answer ``passing_on``'s sender gets until ``waits_on`` (a
    message ``passing_on``'s turn sent on) has been answered and the
    conversation that sent it has replied in turn. Stands in for the
    ordinary notice of ``passing_on``."""
    session_id = deliver_session_of(passing_on)
    path = _path(session_id) if session_id else None
    if path is None:
        return False
    entry_id = f"relay:{passing_on.id}"
    wait = [deliver_session_of(waits_on), waits_on.id, waits_on.parent_session_id]
    with _lock:
        entries, done = _load(path) if path.exists() else ([], [])
        if passing_on.id in done:
            return False
        for e in entries:
            if e.get("id") == entry_id:
                if wait not in e.setdefault("waits_on", []):
                    e["waits_on"].append(wait)
                break
            if e.get("job_id") == passing_on.id:
                # Queued already (it ended first): wait on this send too.
                e.update(kind="relay", id=entry_id, waits_on=[*e.get("waits_on", []), wait])
                break
        else:
            entries.append({"kind": "relay", "id": entry_id, "job_id": passing_on.id,
                            "job_session_id": passing_on.parent_session_id,
                            "waits_on": [wait], "queued_at": time.time()})
        _write(path, entries, done)
        _due.add(session_id)
    return True


def _settle(path, ids: set[str]) -> None:
    """Drop these entries (job ids or watch ids) and remember them as
    done. Caller holds _lock."""
    entries, done = _load(path)
    _write(path, [e for e in entries if (e.get("job_id") or e.get("id")) not in ids],
           done + sorted(ids - set(done)))


def queue(job: Job) -> bool:
    """Record that ``job``'s sender should hear it ended. Background jobs
    that completed or failed qualify, and one the person stopped on its own
    (``cancel.user``): its sender would otherwise wait for a result that
    never comes (Claude Code tells the model "was stopped by the user"). A
    waited-on job returned its result to the caller, and a job stopped with
    the turn that started it (``cancel.parent``) has nobody left waiting.
    A waited-on job whose waiter died with its process (a Worker crash)
    returned nothing, so it is told like a background one."""
    if not qualifies(job):
        return False
    session_id = deliver_session_of(job)
    path = _path(session_id) if session_id else None
    if path is None:
        return False
    with _lock:
        entries, done = _load(path) if path.exists() else ([], [])
        if job.id in done or any(e.get("job_id") == job.id for e in entries):
            return False
        entries.append({"job_id": job.id, "job_session_id": job.parent_session_id,
                        "queued_at": time.time(),
                        **({"stopped_by_user": True} if stopped_by_user(job) else {})})
        _write(path, entries, done)
        _due.add(session_id)
    return True


def qualifies(job: Job) -> bool:
    """Whether ``job``'s end is told to its sender by a notice (``queue``)."""
    from openprogram.runtime.jobs.handoff import waiter_gone
    if job.wait and not waiter_gone(job):
        return False
    return (job.status in (JobStatus.COMPLETED, JobStatus.ERRORED)
            or stopped_by_user(job) or stopped_by_limit(job))


def stopped_by_user(job: Job) -> bool:
    """Whether the person stopped this job itself, not with its caller."""
    return job.status == JobStatus.CANCELLED and job.reason_code == "cancel.user"


def stopped_by_limit(job: Job) -> bool:
    """Whether a limit stopped this job (spend, runtime): its caller is
    still waiting for a result that is not coming."""
    from openprogram.runtime.jobs.agent_result import LIMIT_STOPS
    return job.status == JobStatus.CANCELLED and job.reason_code in LIMIT_STOPS


def queue_stalled(job: Job, *, minutes: int) -> bool:
    """Tell the sender once that ``job`` has had no activity for ``minutes``.
    The job keeps running; its final notice still comes when it ends."""
    session_id = deliver_session_of(job)
    path = _path(session_id) if session_id else None
    if path is None:
        return False
    entry_id = f"stalled:{job.id}"
    with _lock:
        entries, done = _load(path) if path.exists() else ([], [])
        if entry_id in done or any(e.get("id") == entry_id for e in entries):
            return False
        entries.append({"kind": "stalled", "id": entry_id, "stalled_job_id": job.id,
                        "job_session_id": job.parent_session_id, "minutes": int(minutes),
                        "queued_at": time.time()})
        _write(path, entries, done)
        _due.add(session_id)
    return True


def stalled_told(job: Job) -> bool:
    """Whether ``job``'s sender was told it stalled (``queue_stalled``)."""
    session_id = deliver_session_of(job)
    path = _path(session_id) if session_id else None
    if path is None or not path.exists():
        return False
    entry_id = f"stalled:{job.id}"
    with _lock:
        entries, done = _load(path)
    return entry_id in done or any(e.get("id") == entry_id for e in entries)


def watch(
    *, sender_session_id: str, target_session_id: str, target_head_id: str,
    label: str, agent_id: str, chain_messages: int, chain_generations: int,
    authority: dict[str, Any], watch_id: Optional[str] = None,
) -> bool:
    """Tell the sender once when ``target_session_id:target_head_id`` goes
    idle. False when the sender's session cannot hold the watch.
    ``watch_id``: a fixed id (a migrated hand-off), so a repeat adds none."""
    import uuid
    from openprogram.state.authority import normalize_authority
    path = _path(sender_session_id)
    if path is None:
        return False
    with _lock:
        entries, done = _load(path) if path.exists() else ([], [])
        if any(e.get("kind") == "watch" and e.get("target_session_id") == target_session_id
               and e.get("target_head_id") == target_head_id for e in entries):
            return True
        if watch_id and (watch_id in done or any(e.get("id") == watch_id for e in entries)):
            return True
        entries.append({
            "kind": "watch", "id": watch_id or f"watch_{uuid.uuid4().hex[:12]}",
            "target_session_id": target_session_id, "target_head_id": target_head_id,
            "label": label, "agent_id": agent_id,
            "chain_messages": int(chain_messages), "chain_generations": int(chain_generations),
            "queued_at": time.time(), **normalize_authority(authority),
        })
        _write(path, entries, done)
        _due.add(sender_session_id)
    return True


def tip_reply(session_id: str, tip_id: str) -> str:
    """The text of a branch's tip, for an idle notice."""
    from openprogram.runtime.context.session_db import default_db
    for msg in reversed(default_db().get_messages(session_id)):
        if msg.get("id") == tip_id:
            content = msg.get("content")
            return content.strip() if isinstance(content, str) else ""
    return ""


def _idle_watch_job(session_id: str, entry: dict[str, Any]) -> Optional[Job]:
    """The watch as a finished Job once its target is idle, else None."""
    from openprogram.runtime.conversation import inbox
    from openprogram.state.authority import normalize_authority
    target = str(entry.get("target_session_id") or "")
    tip = inbox._branch_tip(target, entry.get("target_head_id"))  # noqa: SLF001
    if not target or not tip or inbox.target_busy(target, tip, session_id):
        return None
    return Job(
        id=str(entry["id"]), parent_session_id=target,
        caller_session_id=session_id if target != session_id else None,
        prompt="", agent_id=str(entry.get("agent_id") or "main"),
        status=JobStatus.COMPLETED, head_id=tip, result_text=tip_reply(target, tip),
        label=str(entry.get("label") or "") or None, source="idle_watch",
        chain_messages=int(entry.get("chain_messages") or 0),
        caller_chain_generations=int(entry.get("chain_generations") or 0),
        **normalize_authority(entry),
    )


def hold_reply(*, sender_session_id: str, entry_id: str, from_session_id: str,
               waits_on: list[str], label: str, agent_id: str = "main",
               authority: Optional[dict[str, Any]] = None) -> bool:
    """One notice for ``sender_session_id`` carrying ``from_session_id``'s
    latest reply, once ``waits_on`` (``[passer, id, run_session]``, the
    run session empty for a notice of its own) was answered there. A
    stored hand-off that owed earlier conversations an answer becomes this
    (handoff_migration). Idempotent by ``entry_id``."""
    from openprogram.state.authority import normalize_authority
    path = _path(sender_session_id)
    if path is None:
        return False
    with _lock:
        entries, done = _load(path) if path.exists() else ([], [])
        if entry_id in done or any(e.get("id") == entry_id for e in entries):
            return True
        entries.append({"kind": "relay", "id": entry_id, "target_session_id": from_session_id,
                        "label": label, "agent_id": agent_id, "waits_on": [list(waits_on)],
                        "queued_at": time.time(), **normalize_authority(authority or {})})
        _write(path, entries, done)
        _due.add(sender_session_id)
    return True


def _relayed_job(session_id: str, entry: dict[str, Any]) -> tuple[bool, Optional[Job]]:
    """``(ready, job)`` for a held answer: ready once the job and every
    message it passed on ended and each reply was answered by a turn of the
    conversation that passed it on; the job then carries that
    conversation's latest reply. ``job`` is None when nothing is told."""
    from openprogram.runtime.conversation import inbox
    from openprogram.runtime.jobs.store import load_job
    from openprogram.runtime.jobs.types import is_terminal
    from openprogram.state.authority import normalize_authority
    if entry.get("job_id"):
        job = load_job(str(entry.get("job_session_id") or session_id), str(entry["job_id"]))
        if job is None:
            return True, None
        if not is_terminal(job.status):
            return False, None
    else:
        # A held reply with no Job of its own (a migrated hand-off): it
        # reads the conversation's latest reply once the wait is over.
        target = str(entry.get("target_session_id") or "")
        job = Job(id=str(entry["id"]), parent_session_id=target,
                  caller_session_id=session_id if target != session_id else None,
                  prompt="", agent_id=str(entry.get("agent_id") or "main"),
                  status=JobStatus.COMPLETED, wait=False,
                  head_id=_session_head(target), label=str(entry.get("label") or "") or None,
                  source="relay", message_origin="agent_delivery", **normalize_authority(entry))
    relayed = False
    for item in entry.get("waits_on") or []:
        try:
            passer, wait_id, run_session = (str(v or "") for v in item)
        except (TypeError, ValueError):
            continue
        if run_session:
            waited = load_job(run_session, wait_id)
            if waited is None:
                continue
            if not is_terminal(waited.status):
                return False, None
            if not qualifies(waited):
                continue  # no notice reaches the passer; nothing to wait for
        path = _path(passer)
        if path is None or wait_id not in (_answered(path) if path.exists() else []):
            return False, None
        relayed = True
    if not qualifies(job):
        return True, None
    if (relayed or not entry.get("job_id")) and job.status == JobStatus.COMPLETED:
        tip = inbox._branch_tip(job.parent_session_id, job.head_id)  # noqa: SLF001
        if tip and inbox.target_busy(job.parent_session_id, tip, session_id):
            return False, None
        if tip:
            job.head_id = tip
            job.result_text = tip_reply(job.parent_session_id, tip) or job.result_text
    return True, job


def _session_head(session_id: str) -> Optional[str]:
    try:
        from openprogram.runtime.context.session_db import default_db
        return (default_db().get_session(session_id) or {}).get("head_id")
    except Exception:  # noqa: BLE001
        return None


def consume(job: Job) -> None:
    """The sender read ``job``'s result itself; drop its notice."""
    session_id = deliver_session_of(job)
    path = _path(session_id) if session_id else None
    if path is None:
        return
    with _lock:
        _settle(path, {job.id})


def consume_reply(session_id: str, target_session_id: str, head_id: str) -> None:
    """The sender read the reply at ``target_session_id:head_id`` itself
    (an idle watch answers with it); drop notices of finished jobs that
    ended on that node, which would only repeat it."""
    from openprogram.runtime.jobs.store import load_job
    path = _path(session_id)
    if path is None or not path.exists():
        return
    with _lock:
        entries, _done = _load(path)
        ids = set()
        for e in entries:
            if e.get("kind") == "watch" or str(e.get("job_session_id") or session_id) != target_session_id:
                continue
            job = load_job(target_session_id, str(e.get("job_id") or ""))
            if job is not None and job.head_id == head_id:
                ids.add(job.id)
        if ids:
            _settle(path, ids)


def _pending_jobs(session_id: str, entries: list[dict[str, Any]]) -> tuple[list[Job], set[str]]:
    """The jobs these entries stand for, and the entry ids that are
    settled by delivering them. A watch whose target is still busy stays."""
    from openprogram.runtime.jobs.store import load_job
    from openprogram.runtime.jobs.types import is_terminal
    jobs, settled = [], set()
    for e in entries:
        if e.get("kind") == "stalled":
            settled.add(str(e.get("id")))
            job = load_job(str(e.get("job_session_id") or session_id), str(e.get("stalled_job_id") or ""))
            # One that ended meanwhile is told by its own notice.
            if job is not None and not is_terminal(job.status):
                job.notice_stalled_minutes = int(e.get("minutes") or 0)  # type: ignore[attr-defined]
                jobs.append(job)
            continue
        if e.get("kind") == "watch":
            job = _idle_watch_job(session_id, e)
            if job is not None:
                jobs.append(job)
                settled.add(job.id)
            continue
        if e.get("kind") == "relay":
            ready, job = _relayed_job(session_id, e)
            if ready:
                settled.add(str(e.get("id")))
                if job is not None:
                    settled.add(job.id)
                    jobs.append(job)
            continue
        settled.add(str(e.get("job_id")))
        job = load_job(str(e.get("job_session_id") or session_id), str(e.get("job_id") or ""))
        if job is not None:
            jobs.append(job)
    return jobs, settled


def _busy(session_id: str, jobs: list[Job]) -> bool:
    """True while the conversation has a foreground turn, or while an agent
    that dispatched one of these jobs still runs and may read it itself.
    Background agents running in the same session do not count: a
    conversation waiting on three agents hears from each as it ends."""
    from openprogram.runtime.conversation.run_control import is_worker_stopping
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.jobs.store import list_jobs
    from openprogram.runtime.jobs.types import is_terminal

    if is_worker_stopping():
        return True
    background = {j.id for j in list_jobs(session_id) if not j.wait}
    if any(r.execution_id not in background
           for r in default_store().list_nonterminal(session_id=session_id)):
        return True
    from openprogram.runtime.jobs.store import load_job
    for job in jobs:
        if job.parent_job_id:
            parent = load_job(job.parent_session_id, job.parent_job_id)
            if parent is not None and not is_terminal(parent.status):
                return True
    return False


def deliver(session_id: str) -> bool:
    """Start the notice turn for ``session_id``; False while it must wait."""
    path = _path(session_id)
    if path is None or not path.exists():
        return True
    with _lock:
        entries, _ = _load(path)
    if not entries:
        return True
    jobs, ids = _pending_jobs(session_id, entries)
    if jobs and _busy(session_id, jobs):
        return False
    with _lock:
        _settle(path, ids)
    if not jobs:
        return len(ids) == len(entries)
    from openprogram.runtime.jobs import get_runner
    get_runner()._dispatch_followup(*jobs)
    return True


def _clip(text: str) -> str:
    text = text.strip()
    return text[:_REPLY_CHARS] + "…" if len(text) > _REPLY_CHARS else text


def _stalled(job: Job) -> bool:
    return getattr(job, "notice_stalled_minutes", None) is not None


def _kind(job: Job) -> str:
    if job.source == "idle_watch":
        return "idle"
    if _stalled(job):
        return "stalled"
    # A spawn's attach pointer puts its report in context; a delivery to
    # an existing branch has no pointer, so its reply travels inline. So does
    # a waited-on spawn's: its card stays out of context, kept for the tool
    # result its dead waiter never returned.
    return "report" if job.attach_pointer_id and not job.wait else "reply"


def _card_leaves_result_to_notice(job: Job) -> bool:
    """Whether ``job``'s attach card is one whose prompt text never carries
    the result (``attach.result_in == "notice"``, render._attach_report).
    A card that cannot be read counts as one: the result then travels in
    the notice rather than nowhere."""
    try:
        from openprogram.runtime.context.session_db import default_db
        node = default_db().read_index(
            deliver_session_of(job), lambda index: index.nodes_by_id.get(job.attach_pointer_id))
    except Exception:  # noqa: BLE001
        return True
    if node is None:
        return True
    md = node.metadata or {}
    attach = md.get("attach")
    if not isinstance(attach, dict):
        try:
            extra = md.get("extra")
            attach = (json.loads(extra) if isinstance(extra, str) else dict(extra or {})).get("attach")
        except Exception:  # noqa: BLE001
            attach = None
    if not isinstance(attach, dict):
        return True
    return attach.get("result_in") == "notice"


def _address(job: Job) -> str:
    return f"{job.parent_session_id}:{job.head_id or '?'}"


def _first_line(job: Job) -> str:
    label = job.label or job.subject or job.id[:8]
    if _kind(job) == "idle":
        return f"Branch {_address(job)} (\"{label}\") you asked to watch is idle now."
    if _stalled(job):
        return (f"Agent \"{label}\" has had no activity for "
                f"{job.notice_stalled_minutes} minutes and is marked stalled; it is "  # type: ignore[attr-defined]
                f"still running (job {job.id}).")
    if stopped_by_user(job):
        return f"Background agent \"{label}\" was stopped by the user (job {job.id})."
    if stopped_by_limit(job):
        from openprogram.runtime.jobs.agent_result import LIMIT_STOPS
        why = {"timeout": "it ran past the time limit",
               "spend cap": "its delegation reached the spend limit"}.get(
            LIMIT_STOPS[job.reason_code or ""], "it reached a limit")
        return f"Agent \"{label}\" was stopped because {why} (job {job.id})."
    outcome = "failed" if job.status == JobStatus.ERRORED else "finished"
    return f"Agent \"{label}\" {outcome} (job {job.id})."


def notice_items(jobs: list[Job]) -> list[dict[str, Any]]:
    """What the notice says, per job, for the chat to draw one row each:
    ``kind`` (report / reply / idle), the agent or branch ``name`` (empty
    when it has none), whether it ``failed``, its ``address``, and the
    ``line`` that starts its part of the notice text."""
    from openprogram.runtime.jobs.agent_result import status_of
    return [{
        "kind": _kind(job), "name": job.label or "",
        "failed": job.status == JobStatus.ERRORED,
        "status": status_of(job, stalled=_stalled(job))[0],
        "address": _address(job), "line": _first_line(job),
    } for job in jobs]


def notice_text(jobs: list[Job]) -> str:
    """The automated notice for one or more finished jobs."""
    from openprogram.runtime.jobs.agent_result import render
    many = len(jobs) > 1
    if all(j.source == "idle_watch" for j in jobs):
        head = "Agents you are watching are idle." if many else "An agent you are watching is idle."
    elif all(_stalled(j) for j in jobs):
        head = ("Agents you started have stalled." if many
                else "An agent you started has stalled.")
    elif all(stopped_by_user(j) for j in jobs):
        head = ("Background agents you started were stopped." if many
                else "A background agent you started was stopped.")
    else:
        head = ("Background agents you started have finished." if many
                else "A background agent you started has finished.")
    lines = [head + " This is an automated notice, not a message from the user."]
    for job in jobs:
        kind, address = _kind(job), _address(job)
        lines.append("")
        lines.append(_first_line(job))
        if kind == "idle":
            lines.append("Its latest reply:")
            lines.append(_clip(job.result_text or "") or "(no text)")
            lines.append(f"To continue with it, use send_message(to=\"{address}\").")
            continue
        task = (job.prompt or job.description or "").strip()
        if task:
            lines.append(f"Task: {task[:500]}")
        if kind == "stalled":
            lines.append(render(job, stalled=True, body=(
                "(no reply yet) Wait for it, steer it with send_message, or stop it "
                f"with close_agent(\"{job.id}\").")))
            continue
        if stopped_by_user(job) or stopped_by_limit(job):
            # No result is coming; whatever it wrote stays on its branch.
            lines.append(render(job, body=f"(no reply) Its work so far: read_conversation(\"{job.id}\")."))
            continue
        if kind == "report" and not _card_leaves_result_to_notice(job):
            # A card written before results moved into the notice renders
            # the report itself.
            lines.append(render(job, body="(its report is attached above)"))
        elif kind == "report":
            # Its card stays as it was written (the caller's prompt only
            # grows by appending), so the result travels here.
            lines.append(render(job))
            lines.append(f"Its steps: read_conversation(\"{job.id}\").")
        else:
            lines.append(f"Reply from branch {address}:")
            lines.append(render(job))
            lines.append(f"To continue with it, use send_message(to=\"{address}\").")
    lines.append("")
    lines.append(
        "Carry on with the user's request from here: tell the user what "
        + ("they" if many else "it")
        + " found in your own words, or decide the next step. If you have "
        "already used this result, reply briefly. If an agent failed, retry "
        "narrower with send_message to the same agent or tell the user; do "
        "not silently redo its work."
    )
    return "\n".join(lines)


def _seed() -> None:
    global _seeded
    from openprogram.state.store import default_store
    from openprogram.state.store.session.placement import iter_session_dirs
    sessions = default_store()
    root = sessions.root_path
    if root.exists():
        found = set()
        for sid, sdir in iter_session_dirs(root):
            path = sessions.state_file_at(sdir, "notices.json")
            with _lock:
                _answer_interrupted(path)
            if _load(path)[0]:
                found.add(sid)
        with _lock:
            _due.update(found)
    _seeded = True


def _answer_interrupted(path) -> None:
    """At this process's first sweep no notice turn runs yet, so a notice
    taken for delivery (``done``) whose turn never recorded its end
    (``answered``) was cut off by a restart. Its turn is over: count it
    answered, so a reply held on it (``hold``) is not held for ever. The
    held reply then carries the conversation's latest reply. Caller holds
    ``_lock``."""
    if not path.exists():
        return
    entries, done = _load(path)
    seen = _answered(path)
    missing = [i for i in done if i not in seen]
    if missing:
        _write(path, entries, done, seen + missing)


def sweep() -> None:
    if not _seeded:
        _seed()
    with _lock:
        due = list(_due)
    for session_id in due:
        try:
            done = deliver(session_id)
        except Exception:
            _log.warning("agent notice for %s deferred", session_id, exc_info=True)
            continue
        if done:
            with _lock:
                path = _path(session_id)
                if path is None or not path.exists() or not _load(path)[0]:
                    _due.discard(session_id)


def _sweep_all() -> None:
    sweep()
    # Messages queued for busy agents ride the same loop (agent/inbox.py).
    from openprogram.runtime.conversation import inbox
    inbox.sweep()


async def run_job_notices(stop: asyncio.Event) -> None:
    """Worker loop: deliver due notices and queued messages until
    ``stop`` is set."""
    while not stop.is_set():
        await asyncio.to_thread(_sweep_all)
        try:
            await asyncio.wait_for(stop.wait(), POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
