"""Default bounds on delegated agents: spend, stall and runtime.

The spend bound is per delegation tree; the runtime bound is a hard stop.

These are soft bounds kept beside resource governance, not inside it. A
governance limit (``agent.resource_limits.*``) makes a Job "strict": every
synchronous tool is refused because it has no guaranteed stop boundary,
and a priced cost ceiling refuses models whose price is unknown. That is
right for a limit the owner set on purpose and wrong for a default every
helper gets, so the defaults here watch from outside and stop a Job the
cooperative way (cascade cancel), the same way a person's Stop does.

* **Tree spend** (``agent.max_tree_usd``, $2): the helpers started from one
  turn, and everything they started, share one allowance. A spawn past it
  is refused, and the running ones are stopped with
  ``budget.tree_cost_exhausted``; the conversation gets the usual notice.
  Only usage with a known price counts (Pi's per-child cost example and
  the Codex app's usage warning are the references; neither stops on an
  unknown price either).
* **Stall** (``agent.stall_minutes``, 10): no model or tool activity for
  that long marks the helper stalled and tells its caller once; nothing is
  stopped (ZCode's stall detection).
* **Hard stop** (``agent.max_agent_hours``, 2): a helper still running after
  that is stopped with ``budget.runtime_exhausted`` (Kimi Code's two-hour
  item limit).

The tree of a Job is keyed by its root delegate: the session and turn
that started the first Job of the chain.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Callable, Iterable, Optional

from openprogram.runtime.jobs.types import Job, JobStatus

_log = logging.getLogger(__name__)

MAX_TREE_USD = 2.0
STALL_MINUTES = 10
MAX_AGENT_HOURS = 2
TREE_COST_REASON = "budget.tree_cost_exhausted"
RUNTIME_REASON = "budget.runtime_exhausted"
# How often the runner's budget loop runs these checks; they read Job
# files and the ledger, so they do not run on every 0.25 s tick.
CHECK_EVERY_S = 5.0


def _config_number(key: str, default: float) -> float:
    try:
        from openprogram.state import config as user_config
        value = (user_config.read_config().get("agent") or {}).get(key)
        if value in (None, "") or isinstance(value, bool):
            return default
        return max(0.0, float(value))
    except Exception:
        return default


def tree_cap_usd() -> float:
    """``agent.max_tree_usd``; 0 = no cap."""
    return _config_number("max_tree_usd", MAX_TREE_USD)


def stall_seconds() -> float:
    """``agent.stall_minutes`` in seconds; 0 = never stalled."""
    return _config_number("stall_minutes", STALL_MINUTES) * 60.0


def max_run_seconds() -> float:
    """``agent.max_agent_hours`` in seconds; 0 = no hard stop."""
    return _config_number("max_agent_hours", MAX_AGENT_HOURS) * 3600.0


def watched(job: Job) -> bool:
    """Whether these bounds apply: an agent Job (a spawn or a message to an
    existing agent), not a self-update step or a program's own Job."""
    return (job.source or "agent_spawn") == "agent_spawn"


def tree_key(job: Job, load: Callable[[str, str], Optional[Job]]) -> tuple[str, str]:
    """(session, turn) of the turn that started this Job's delegation tree."""
    current, seen = job, {job.id}
    while current.parent_job_id and current.parent_job_id not in seen:
        seen.add(current.parent_job_id)
        parent = load(current.parent_session_id, current.parent_job_id)
        if parent is None:
            break
        current = parent
    session = current.caller_session_id or current.parent_session_id
    turn = current.origin_turn_id or current.caller_msg_id or current.spawn_caller or current.id
    return session, str(turn)


def tree_jobs(session_id: str, turn_id: str) -> list[Job]:
    """Every Job of the tree started from ``session_id``'s turn ``turn_id``."""
    from openprogram.runtime.jobs.store import list_jobs
    roots = [j for j in list_jobs(session_id)
             if not j.parent_job_id and watched(j)
             and (j.caller_session_id or j.parent_session_id) == session_id
             and str(j.origin_turn_id or j.caller_msg_id or j.spawn_caller or j.id) == turn_id]
    found: dict[str, Job] = {j.id: j for j in roots}
    sessions = {session_id} | {j.parent_session_id for j in roots}
    frontier = list(found)
    while frontier:
        children = [j for sid in sessions for j in list_jobs(sid)
                    if j.parent_job_id in frontier and j.id not in found]
        for child in children:
            found[child.id] = child
            sessions.add(child.parent_session_id)
        frontier = [c.id for c in children]
    return list(found.values())


def spend_usd(ledger: Any, job_ids: Iterable[str]) -> Decimal:
    """Known-price spend of these Jobs in the usage ledger."""
    total = Decimal(0)
    for job_id in job_ids:
        try:
            usage = ledger.job_resource_usage(job_id)
        except Exception:
            continue
        for value in usage.get("cost_values") or ():
            try:
                total += Decimal(str(value or 0))
            except Exception:
                continue
    return total


def _ledger():
    """The running Worker's ledger, or None when no job runner is up (then
    nothing has been spent through it either)."""
    from openprogram.runtime.jobs.runner import shared
    runner = shared._runner  # noqa: SLF001
    return None if runner is None else runner._governor.ledger  # noqa: SLF001


def spawn_refusal(session_id: str, turn_id: str, *, ledger: Any = None) -> Optional[tuple[Decimal, float]]:
    """``(spent, cap)`` when the tree started from this turn has used its
    allowance, so no further helper may start from it; None otherwise."""
    cap = tree_cap_usd()
    if not cap:
        return None
    jobs = tree_jobs(session_id, turn_id)
    if not jobs:
        return None
    ledger = ledger if ledger is not None else _ledger()
    if ledger is None:
        return None
    spent = spend_usd(ledger, [j.id for j in jobs])
    return (spent, cap) if spent >= Decimal(str(cap)) else None


def check_running(
    entries: list[tuple[str, str, float, float]],
    *,
    now: float,
    load: Callable[[str, str], Optional[Job]],
    ledger: Any,
    cancel: Callable[[str, str], None],
    stalled: Callable[[Job], None],
    already: dict[str, set[str]],
) -> None:
    """One pass over the runner's running Jobs.

    ``entries`` are ``(job_id, session_id, started, last_activity)`` in the
    runner's monotonic clock and ``now`` is the same clock. ``already``
    remembers which Jobs were stopped or reported stalled, so each is
    acted on once (keys ``"stopped"`` and ``"stalled"``).
    """
    stopped, told = already.setdefault("stopped", set()), already.setdefault("stalled", set())
    run_limit, stall_limit, cap = max_run_seconds(), stall_seconds(), tree_cap_usd()
    trees: dict[tuple[str, str], list[Job]] = {}
    for job_id, session_id, started, last_activity in entries:
        if job_id in stopped:
            continue
        job = load(session_id, job_id)
        if job is None or not watched(job) or job.status != JobStatus.RUNNING:
            continue
        if run_limit and now - started >= run_limit:
            stopped.add(job_id)
            cancel(job_id, RUNTIME_REASON)
            continue
        if stall_limit and now - last_activity >= stall_limit and job_id not in told:
            told.add(job_id)
            stalled(job)
        if cap:
            trees.setdefault(tree_key(job, load), []).append(job)
    for (session_id, turn_id), running in trees.items():
        spent = spend_usd(ledger, [j.id for j in tree_jobs(session_id, turn_id)] or [j.id for j in running])
        if spent < Decimal(str(cap)):
            continue
        # Deepest first is the cascade's job; stopping each running root is enough.
        for job in running:
            if job.id not in stopped:
                stopped.add(job.id)
                cancel(job.id, TREE_COST_REASON)
    if len(told) > 5000:
        told.clear()
    if len(stopped) > 5000:
        stopped.clear()
