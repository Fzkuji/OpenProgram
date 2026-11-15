"""The one shape every agent result comes back in.

A helper's result reaches its caller in two ways: as the ``agent`` tool's
result when the caller waited, and inside the automated notice turn when it
ran in the background or answered a ``send_message``. Both use this block,
so the caller reads one format (Claude Code's ``<task-notification>``
carries status and usage on every result; Grok puts the worktree path in
it)::

    <agent name="Explorer" id="job_…" status="completed">
    final reply (at most 20,000 characters; the rest through read_conversation)
    files: worktree /path, 3 changed (a.py, b.py, c.md)
    usage: 41 s · 12 tool calls · 38k tokens · $0.021
    </agent>

``status`` is one of completed, failed, stopped, stalled. A stop carries a
``reason`` (``user``, ``timeout``, ``spend cap``). ``files`` appears only
when the helper worked in its own worktree. ``usage`` is read from the
usage ledger (tokens and cost of the model calls of the Job and of every
helper it started) and from the helper's branch (tool calls); a price the ledger does not know reads
``cost unknown``.

``usage`` is also stored on the attach card, so the interface can show it
later without changing the prompt the caller already sent.
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any, Optional

from openprogram.runtime.jobs.types import Job, JobStatus

_log = logging.getLogger(__name__)

REPLY_CHARS = 20_000
_FILES_SHOWN = 8

# Stops that came from a limit rather than from a person; each is told to
# the caller like a finished helper, with the reason.
LIMIT_STOPS = {
    "budget.runtime_exhausted": "timeout",
    "budget.tree_cost_exhausted": "spend cap",
    "budget.idle_exhausted": "idle limit",
    "budget.cost_exhausted": "cost limit",
    "budget.token_exhausted": "token limit",
}


def status_of(job: Job, *, stalled: bool = False) -> tuple[str, str]:
    """``(status, reason)`` as the result block names them."""
    if stalled:
        return "stalled", ""
    if job.status == JobStatus.COMPLETED:
        return "completed", ""
    if job.status == JobStatus.CANCELLED:
        if job.reason_code == "cancel.user":
            return "stopped", "user"
        if job.reason_code in LIMIT_STOPS:
            reason = LIMIT_STOPS[job.reason_code]
            return ("failed", reason) if reason == "timeout" else ("stopped", reason)
        return "stopped", ""
    if job.status == JobStatus.ERRORED:
        return "failed", ""
    return job.status.value, ""


def _tool_calls(job: Job) -> Optional[int]:
    """Tool calls the helper made on its branch since it started."""
    if not job.head_id:
        return None
    try:
        from openprogram.runtime.context.session_db import default_db
        db = default_db()
        since = float(job.started_at or job.created_at or 0.0)
        turns = {
            str(m.get("id")) for m in (db.get_branch(job.parent_session_id, job.head_id) or [])
            if float(m.get("timestamp") or m.get("created_at") or since) >= since
        }
        if not turns:
            return 0
        return sum(
            1 for m in db.get_messages(job.parent_session_id)
            if m.get("role") == "tool" and str(m.get("caller") or "") in turns
        )
    except Exception:
        _log.debug("tool calls of job %s not counted", job.id, exc_info=True)
        return None


def subtree_job_ids(job: Job) -> list[str]:
    """``job`` and every helper started under it (``parent_job_id``) in the
    conversations the tree runs in (a helper forked into a conversation none
    of them runs in is not found). Tool calls stay the helper's own; tokens
    and cost are the subtree's, since a helper's helpers spend on its task."""
    found = [job.id]
    try:
        from openprogram.runtime.jobs.store import list_jobs
        # Each helper is stored in the conversation it runs in; its own
        # helpers are looked for there and in the conversation it started in.
        sessions, frontier, seen = {job.parent_session_id}, {job.id}, {job.id}
        while frontier:
            children = [j for sid in sorted(sessions) for j in list_jobs(sid)
                        if j.parent_job_id in frontier and j.id not in seen]
            frontier = {j.id for j in children}
            seen |= frontier
            found += [j.id for j in children]
            sessions |= {j.parent_session_id for j in children}
    except Exception:  # noqa: BLE001 - the helper's own usage still counts
        _log.debug("helpers under job %s not listed", job.id, exc_info=True)
    return found


def _ledger_usage(job_ids: list[str] | str, ledger: Any = None) -> Optional[dict[str, Any]]:
    ids = [job_ids] if isinstance(job_ids, str) else list(job_ids)
    try:
        if ledger is None:
            # Only the running Worker's ledger: rendering a result never
            # starts a job runner of its own.
            from openprogram.runtime.jobs.runner import shared
            runner = shared._runner  # noqa: SLF001
            if runner is None:
                return None
            ledger = runner._governor.ledger  # noqa: SLF001 — the runner's ledger
        rows = [ledger.job_resource_usage(job_id) for job_id in ids]
    except Exception:
        _log.debug("usage of jobs %s not read", ids, exc_info=True)
        return None
    return {
        "total_tokens": sum(int(r.get("total_tokens") or 0) for r in rows),
        "cost_values": [v for r in rows for v in (r.get("cost_values") or ())],
        "unknown_cost_events": sum(int(r.get("unknown_cost_events") or 0) for r in rows),
    }


def usage_of(job: Job, *, ledger: Any = None, now: Optional[float] = None) -> dict[str, Any]:
    """Duration (s), tool calls, tokens and cost (USD text, or None when a
    price is unknown) of one helper Job. Tokens and cost include the helpers
    it started (``subtree_job_ids``). A field that cannot be read is None."""
    end = job.completed_at or (time.time() if now is None else now)
    start = job.started_at or job.queued_at or job.created_at
    duration = max(0.0, float(end) - float(start)) if start else None
    usage = _ledger_usage(subtree_job_ids(job), ledger)
    tokens = cost = None
    if usage is not None:
        tokens = int(usage.get("total_tokens") or 0)
        if not usage.get("unknown_cost_events"):
            total = sum((Decimal(str(v or 0)) for v in usage.get("cost_values") or ()), Decimal(0))
            cost = format(total.quantize(Decimal("0.000001")), "f")
    return {
        "duration_s": round(duration, 1) if duration is not None else None,
        "tool_calls": _tool_calls(job),
        "tokens": tokens,
        "cost_usd": cost,
    }


def _tokens_text(tokens: int) -> str:
    if tokens >= 1000:
        return f"{tokens / 1000:.0f}k tokens" if tokens >= 10_000 else f"{tokens / 1000:.1f}k tokens"
    return f"{tokens} tokens"


def _duration_text(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def usage_line(usage: dict[str, Any]) -> str:
    parts = []
    if usage.get("duration_s") is not None:
        parts.append(_duration_text(float(usage["duration_s"])))
    if usage.get("tool_calls") is not None:
        n = int(usage["tool_calls"])
        parts.append(f"{n} tool call" + ("" if n == 1 else "s"))
    if usage.get("tokens") is not None:
        parts.append(_tokens_text(int(usage["tokens"])))
    if usage.get("tokens") is not None or usage.get("cost_usd") is not None:
        cost = usage.get("cost_usd")
        if cost is None:
            parts.append("cost unknown")
        else:
            value = Decimal(cost)
            parts.append(f"${value:.3f}" if value >= Decimal("0.001") or value == 0 else f"${value:.6f}")
    return "usage: " + " · ".join(parts) if parts else ""


def files_line(job: Job) -> str:
    """Where an isolated helper's changes are, or empty when it shared the folder."""
    if not job.worktree_id:
        return ""
    try:
        from openprogram.state.worktree.manager import get_manager
        manager = get_manager()
        worktree = manager.get_worktree(job.worktree_id)
        if worktree is None:
            return "files: none changed (its worktree was removed)"
        summary = manager.summarize(worktree)
    except Exception:
        _log.debug("worktree of job %s not summarized", job.id, exc_info=True)
        return ""
    names = [str(f.get("path") if isinstance(f, dict) else f) for f in summary.get("files") or ()]
    shown = ", ".join(names[:_FILES_SHOWN]) + (", …" if len(names) > _FILES_SHOWN else "")
    pending = ", uncommitted" if summary.get("dirty") else ""
    return (f"files: worktree {worktree.worktree_path}, {len(names)} changed{pending}"
            + (f" ({shown})" if names else ""))


def files_of(job: Job) -> Optional[dict[str, Any]]:
    """What an isolated helper changed, for its card: ``{"count", "branch",
    "dirty"}``. None when it shared the folder or changed nothing."""
    if not job.worktree_id:
        return None
    try:
        from openprogram.state.worktree.manager import get_manager
        manager = get_manager()
        worktree = manager.get_worktree(job.worktree_id)
        if worktree is None:
            return None
        summary = manager.summarize(worktree)
    except Exception:
        _log.debug("worktree of job %s not summarized", job.id, exc_info=True)
        return None
    count = len(summary.get("files") or ())
    if not count:
        return None
    return {"count": count, "branch": worktree.branch_name, "dirty": bool(summary.get("dirty"))}


def clip_reply(text: str, job_id: str) -> str:
    text = (text or "").strip()
    if len(text) <= REPLY_CHARS:
        return text
    return (text[:REPLY_CHARS] + f"\n… ({len(text) - REPLY_CHARS} more characters: "
            f"read_conversation(\"{job_id}\") reads the whole reply)")


def _attr(value: str) -> str:
    return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def agent_name(job: Job) -> str:
    if job.label:
        return job.label
    try:
        from openprogram.runtime.agent_registry import manager
        spec = manager.get(job.agent_id) if job.agent_id else None
        if spec is not None and spec.name:
            return spec.name
    except Exception:
        _log.debug("agent name of job %s not read", job.id, exc_info=True)
    return job.subject or job.id[:8]


def render(
    job: Job,
    *,
    reply: Optional[str] = None,
    body: Optional[str] = None,
    stalled: bool = False,
    usage: Optional[dict[str, Any]] = None,
    note: str = "",
) -> str:
    """The result block for ``job``. ``reply`` overrides the Job's own
    text (the synchronous path has it before the Job file is final);
    ``body`` replaces the reply line entirely (a report attached elsewhere,
    a stalled helper that has not answered)."""
    status, reason = status_of(job, stalled=stalled)
    attrs = f'name="{_attr(agent_name(job))}" id="{_attr(job.id)}" status="{status}"'
    if reason:
        attrs += f' reason="{reason}"'
    lines = [f"<agent {attrs}>"]
    if body is not None:
        lines.append(body)
    else:
        text = reply if reply is not None else (job.result_text or job.error or "")
        lines.append(clip_reply(text, job.id) or "(no reply)")
    if note:
        lines.append(note)
    files = files_line(job)
    if files:
        lines.append(files)
    line = usage_line(usage if usage is not None else usage_of(job))
    if line:
        lines.append(line)
    lines.append("</agent>")
    return "\n".join(lines)
