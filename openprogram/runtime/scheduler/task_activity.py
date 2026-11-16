"""What a conversation's scheduled tasks did, for its Activity panel.

A run of a task is a top-level execution its schedule started. The run's
stored turn says so (``source: "scheduler"``) and names the task
(``scheduled_task``). A turn saved before turns named their task belongs to
the task of the line it is on: a task's runs follow one another on one line of
messages (``task_branch``), so the line's later runs, its branch record and
its branch entry name the task for the older ones.

Each run comes to one outcome:

``running`` / ``waiting``
    still going, or paused on the reader.
``quiet``
    finished with nothing new; nobody was told.
``notified``
    finished with something for the reader, who got it as a message.
``unable``
    finished, but said it could not do its check, and why.
``failed``
    the execution failed.
``stopped``
    cancelled, by the reader or at the run's time limit.
``interrupted``
    cut off (a restart) and could not go on.

Failed, stopped and interrupted runs did not finish; each has one sentence of
why, in English and Chinese. A run that could not check has its own sentence,
in the task's language. The task's record (``task_runs``) has the last
runs' outcomes as the Worker saw them and is taken first; a run it no longer
keeps is judged from its execution and its reply, as ``run_finish`` judged it.

What a run cost is the provider calls charged to its execution. Calls saved
before calls carried their execution are charged to the run that was going in
the conversation when they were made, and a helper's calls (its Job) to the
run that started it.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

RECENT = 24
TURN_CACHE_ENTRIES = 8192
OUTCOME_CACHE_ENTRIES = 8192
LABEL_CHARS = 160

LIVE = frozenset({"queued", "running", "pausing", "cancelling"})
UNFINISHED = frozenset({"failed", "stopped", "interrupted"})
FILTERS = ("all", "unfinished", "notified")

_SOURCE = "scheduler"
_ROLE_USER = "user"


# What a run's turn says


@dataclass(frozen=True)
class Turn:
    """The parts of an execution's stored turn that Activity reads."""

    source: str | None = None
    task_id: str | None = None
    #: What was asked, on one line, at most ``LABEL_CHARS`` long.
    label: str | None = None


_turns: OrderedDict[tuple[int, str], Turn] = OrderedDict()
_turns_lock = threading.Lock()


def turn_from_payload(payload: Mapping[str, Any] | None) -> Turn:
    payload = payload or {}
    request = payload.get("request", {}) if payload.get("kind") == "chat" else payload
    if not isinstance(request, Mapping):
        return Turn()
    text = request.get("user_text")
    task = request.get("scheduled_task")
    task_id = task.get("id") if isinstance(task, Mapping) else None
    source = request.get("source")
    return Turn(
        source=source if isinstance(source, str) else None,
        task_id=str(task_id) if task_id else None,
        label=" ".join(text.split())[:LABEL_CHARS] if isinstance(text, str) else None,
    )


def turn_of(store: Any, execution_id: str) -> Turn:
    """An execution's turn facts. A stored turn never changes, so each is read once."""
    key = (id(store), execution_id)
    with _turns_lock:
        found = _turns.get(key)
        if found is not None:
            _turns.move_to_end(key)
            return found
    turn = turn_from_payload(store.get_agent_turn_input(execution_id))
    with _turns_lock:
        _turns[key] = turn
        while len(_turns) > TURN_CACHE_ENTRIES:
            _turns.popitem(last=False)
    return turn


# Which task each run belongs to


def _source(node: Any) -> str | None:
    metadata = getattr(node, "metadata", None)
    return metadata.get("source") if isinstance(metadata, Mapping) else None


def _scheduled(node: Any) -> bool:
    return node is not None and _source(node) == _SOURCE


def _mark_line(index: Any, start: str | None, task_id: str, owner: dict[str, str]) -> None:
    """Give ``task_id`` to the scheduled messages from ``start`` back to where the
    line leaves the conversation, or to a message that already has a task."""
    key, seen = start, set()
    while key and key not in seen:
        seen.add(key)
        node = index.get(key)
        if not _scheduled(node) or key in owner:
            return
        owner[key] = task_id
        key = node.predecessor


def _first_owned(index: Any, start: str | None, owner: Mapping[str, str]) -> str | None:
    """The task of the nearest message above ``start`` on its line that has one."""
    key, seen = start, set()
    while key and key not in seen:
        seen.add(key)
        if key in owner:
            return owner[key]
        node = index.get(key)
        if not _scheduled(node):
            return None
        key = node.predecessor
    return None


@dataclass(frozen=True)
class RunAnchor:
    """A top-level execution with the messages it was admitted for and its turn."""

    execution_id: str
    user_message_id: str | None
    turn: Turn


def run_tasks(index: Any, anchors: Sequence[RunAnchor]) -> dict[str, str]:
    """The task of every run the schedule started, by execution id."""
    owner: dict[str, str] = {}
    meta = getattr(index, "meta", None) or {}
    named = [anchor for anchor in anchors if anchor.turn.task_id]
    # Newest first, so each line takes the task its latest run names.
    for anchor in named:
        _mark_line(index, anchor.user_message_id, anchor.turn.task_id, owner)
    for ref in (meta.get("branch_refs") or {}).values():
        if isinstance(ref, Mapping) and ref.get("scheduled_task_id") and ref.get("status") != "aborted":
            _mark_line(index, ref.get("head_id"), str(ref["scheduled_task_id"]), owner)
    for head, entry in (meta.get("branches") or {}).items():
        if isinstance(entry, Mapping) and entry.get("scheduled_task_id"):
            _mark_line(index, head, str(entry["scheduled_task_id"]), owner)
    tasks: dict[str, str] = {}
    for anchor in anchors:
        if anchor.turn.task_id:
            tasks[anchor.execution_id] = anchor.turn.task_id
            continue
        user = index.get(anchor.user_message_id) if anchor.user_message_id else None
        if anchor.turn.source != _SOURCE and not _scheduled(user):
            continue
        found = _first_owned(index, anchor.user_message_id, owner)
        if found is not None:
            tasks[anchor.execution_id] = found
    return tasks


# How a run ended


def _text(node: Any) -> str:
    from openprogram.runtime.execution.projections.activity_runs import _text as text

    return text(node) if node is not None else ""


def _last_round(node: Any) -> str:
    output = getattr(node, "output", None)
    if not isinstance(output, list):
        return ""
    texts = [str(block.get("text")) for block in output
             if isinstance(block, Mapping) and block.get("type") == "text" and block.get("text")]
    return texts[-1] if texts else ""


def judge_reply(reply: str, last_round: str, *, always_on: bool) -> tuple[str, str | None]:
    """A finished run's outcome from its reply, as ``run_finish`` judges it,
    with the reason a run gave for not doing its check."""
    from . import task_branch

    judged = last_round if always_on and last_round and last_round != reply else reply
    reason = task_branch.unable_reason(judged)
    if reason is not None:
        return "unable", reason or None
    return ("quiet" if task_branch.is_quiet(judged) else "notified"), None


_RECORDED = {"quiet": "quiet", "result": "notified", "failed": "unable"}


def outcome_of(status: str, *, recorded: Mapping[str, Any] | None = None, reply: str = "",
               last_round: str = "", always_on: bool = False) -> tuple[str, str | None]:
    """A run's outcome and, for a check it could not do, what it said."""
    if status in LIVE:
        return "running", None
    if status == "paused":
        return "waiting", None
    if status == "failed":
        return "failed", None
    if status == "cancelled":
        return "stopped", None
    if status in ("interrupted", "reconciliation_required"):
        return "interrupted", None
    if recorded is not None and recorded.get("status") in _RECORDED:
        outcome = _RECORDED[recorded["status"]]
        text = recorded.get("text")
        return outcome, (text if outcome == "unable" and isinstance(text, str) and text else None)
    return judge_reply(reply, last_round, always_on=always_on)


def duration_words(seconds: float) -> tuple[str, str]:
    """"3 h 28 min" and "3 小时 28 分", the words the panel's rows use."""
    total = max(0, round(seconds))
    if total < 60:
        return f"{total} s", f"{total} 秒"
    minutes = round(total / 60)
    if minutes < 60:
        return f"{minutes} min", f"{minutes} 分钟"
    hours, rest = divmod(minutes, 60)
    return (f"{hours} h {rest} min", f"{hours} 小时 {rest} 分") if rest else (f"{hours} h", f"{hours} 小时")


def failure_reason(outcome: str, reason_code: str | None, seconds: float | None,
                   note: str | None = None) -> dict[str, str] | None:
    """One sentence of why a run did not finish or could not check, in English
    and Chinese; a run's own words stay in the language it wrote them in."""
    if outcome == "unable":
        text = note or ""
        return {"en": text or "It could not do its check.", "zh": text or "没能完成检查。"}
    if outcome not in UNFINISHED:
        return None
    en_took, zh_took = duration_words(seconds) if seconds is not None else ("", "")
    after_en = f" after {en_took}" if en_took else ""
    after_zh = f"运行 {zh_took} 后" if zh_took else ""
    code = reason_code or ""
    if code == "agent_runner_error":
        return {"en": f"The agent runner stopped with an error{after_en}.",
                "zh": f"智能体运行器出错，{after_zh}停止。" if after_zh else "智能体运行器出错，运行停止。"}
    if code == "time_limit":
        return {"en": f"It reached its time limit and was stopped{after_en}.",
                "zh": f"到了运行时长上限，{after_zh}被停止。" if after_zh else "到了运行时长上限，被停止。"}
    if code == "scheduled_run_start_failed":
        return {"en": "It could not start.", "zh": "没能启动。"}
    if code == "owner_lost_before_checkpoint":
        return {"en": "OpenProgram stopped while it ran, before it saved a point to go on from.",
                "zh": "运行中 OpenProgram 停止了，当时还没保存可以接着运行的位置。"}
    if code == "unfinished_before_session_logs":
        return {"en": "It was still running when OpenProgram changed how it keeps runs, and could not go on.",
                "zh": "OpenProgram 更换运行记录方式时它还在运行，没能接着运行。"}
    if outcome == "stopped":
        return {"en": f"It was stopped{after_en}.", "zh": f"{after_zh}被停止。" if after_zh else "被停止。"}
    if outcome == "interrupted":
        return {"en": f"It was cut off{after_en} and could not go on.",
                "zh": f"{after_zh}被中断，没能接着运行。" if after_zh else "被中断，没能接着运行。"}
    return {"en": f"It failed{after_en}.", "zh": f"{after_zh}失败。" if after_zh else "运行失败。"}


# One run, the list and its summary


@dataclass(frozen=True)
class TaskRun:
    """One run of a scheduled task, as Activity shows it."""

    execution_id: str
    task_id: str
    started_at: float
    ended_at: float | None
    status: str
    reason_code: str | None
    outcome: str
    note: str | None = None

    @property
    def unfinished(self) -> bool:
        return self.outcome in UNFINISHED

    def row(self, cost: float | None = None) -> dict[str, Any]:
        seconds = self.ended_at - self.started_at if self.ended_at is not None else None
        return {
            "execution_id": self.execution_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "reason_code": self.reason_code,
            "outcome": self.outcome,
            "reason": failure_reason(self.outcome, self.reason_code, seconds, self.note),
            "cost": cost,
        }


def summary(runs: Sequence[TaskRun], costs: Mapping[str, float] | None = None) -> dict[str, Any]:
    """Counts, what the runs cost (None when nothing is priced), the last
    run's time and the last ``RECENT`` outcomes, oldest first. ``runs`` is
    newest first."""
    costs = costs or {}
    priced = [costs[run.execution_id] for run in runs if run.execution_id in costs]
    return {
        "runs": len(runs),
        "cost": round(sum(priced), 6) if priced else None,
        "unfinished": sum(run.unfinished for run in runs),
        "notified": sum(run.outcome == "notified" for run in runs),
        "last_at": runs[0].started_at if runs else None,
        "recent": [run.outcome for run in reversed(runs[:RECENT])],
    }


def _matches(run: TaskRun, kind: str) -> bool:
    if kind == "unfinished":
        return run.unfinished
    if kind == "notified":
        return run.outcome == "notified"
    return True


def page(runs: Sequence[TaskRun], *, kind: str = "all", offset: int = 0, limit: int = 20,
         costs: Mapping[str, float] | None = None) -> dict[str, Any]:
    """One page of the runs that ``kind`` keeps, newest first, with the summary."""
    picked = [run for run in runs if _matches(run, kind)]
    costs = costs or {}
    return {
        **summary(runs, costs),
        "filter": kind,
        "offset": offset,
        "total": len(picked),
        "page": [run.row(costs.get(run.execution_id)) for run in picked[offset:offset + limit]],
    }


# Cost


def run_costs(events: Iterable[Mapping[str, Any]], runs: Sequence[Any],
              root_of: Mapping[str, str]) -> dict[str, float]:
    """What each top-level run cost, its helpers included.

    ``events``: the conversation's priced provider calls (``ts``,
    ``execution_id``, ``job_id``, ``cost``). ``runs``: its top-level
    executions (``execution_id``, ``created_at``, ``terminal_at``).
    ``root_of``: the top-level run each helper (Job) hangs under."""
    windows = sorted(((run.created_at or 0.0, run.terminal_at, run.execution_id) for run in runs), reverse=True)
    known = {run.execution_id for run in runs}
    totals: dict[str, float] = {}
    for event in events:
        cost = event.get("cost")
        if cost is None:
            continue
        owner = event.get("execution_id")
        if owner and owner not in known:
            owner = root_of.get(owner)
        if not owner and event.get("job_id"):
            owner = root_of.get(event["job_id"])
        if not owner:
            at = float(event.get("ts") or 0.0)
            owner = next((execution_id for start, end, execution_id in windows
                          if start <= at and (end is None or at <= end)), None)
        if owner in known:
            totals[owner] = totals.get(owner, 0.0) + float(cost)
    return totals


# Reading them


_outcomes: OrderedDict[tuple[int, str, int], tuple[str, str | None]] = OrderedDict()
_outcomes_lock = threading.Lock()


def _ended_outcome(key: tuple[int, str, int], compute) -> tuple[str, str | None]:
    with _outcomes_lock:
        found = _outcomes.get(key)
    if found is not None:
        return found
    value = compute()
    with _outcomes_lock:
        _outcomes[key] = value
        while len(_outcomes) > OUTCOME_CACHE_ENTRIES:
            _outcomes.popitem(last=False)
    return value


def _always_on(session_id: str) -> bool:
    try:
        from openprogram.runtime.conversation.always_on import agent_for_session

        return bool(agent_for_session(session_id))
    except Exception:  # noqa: BLE001 - judged as a plain reply
        return False


def _recorded_runs(task_ids: Iterable[str]) -> dict[str, Mapping[str, Any]]:
    from . import task_runs

    found: dict[str, Mapping[str, Any]] = {}
    for task_id in task_ids:
        for entry in task_runs.recent(task_id):
            if entry.get("execution_id"):
                found[str(entry["execution_id"])] = entry
    return found


def read_runs(session_id: str, store: Any = None, index: Any = None) -> dict[str, list[TaskRun]]:
    """Every scheduled task's runs in the conversation, newest first, by task id."""
    from openprogram.runtime.execution.model import TERMINAL_EXECUTION_STATUSES

    if store is None:
        from openprogram.runtime.execution import default_store
        store = default_store()
    records = [record for record in store.list_for_session(session_id)
               if record.parent_execution_id is None and not record.execution_id.startswith("j_")]
    inputs = {item.execution_id: item for item in store.list_inputs_for_session(session_id)}
    anchors = [RunAnchor(record.execution_id, getattr(inputs.get(record.execution_id), "user_message_id", None),
                         turn_of(store, record.execution_id)) for record in records]
    if not any(anchor.turn.source == _SOURCE or anchor.turn.task_id for anchor in anchors):
        return {}
    if index is None:
        from openprogram.runtime.context.session_db import default_db
        return default_db().read_index(session_id, lambda found: read_runs(session_id, store, found)) or {}
    tasks = run_tasks(index, anchors)
    ended = {status.value for status in TERMINAL_EXECUTION_STATUSES}
    recorded = _recorded_runs(set(tasks.values()))
    always_on: list[bool] = []
    runs: dict[str, list[TaskRun]] = {}
    for record in records:
        task_id = tasks.get(record.execution_id)
        if task_id is None:
            continue
        status = record.status.value

        def compute(record=record, status=status):
            anchor = inputs.get(record.execution_id)
            reply = index.get(anchor.assistant_message_id) if anchor and anchor.assistant_message_id else None
            if not always_on:
                always_on.append(_always_on(session_id))
            return outcome_of(status, recorded=recorded.get(record.execution_id), reply=_text(reply),
                              last_round=_last_round(reply), always_on=always_on[0])

        if status in ended:
            outcome, note = _ended_outcome((id(store), record.execution_id, record.status_version), compute)
        else:
            outcome, note = compute()
        runs.setdefault(task_id, []).append(TaskRun(
            execution_id=record.execution_id, task_id=task_id, started_at=float(record.created_at or 0.0),
            ended_at=float(record.terminal_at) if record.terminal_at is not None else None,
            status=status, reason_code=record.reason_code, outcome=outcome, note=note,
        ))
    for listed in runs.values():
        listed.sort(key=lambda run: run.started_at, reverse=True)
    return runs


def read_costs(session_id: str, store: Any = None) -> dict[str, float]:
    """What each top-level run of the conversation cost, its helpers included."""
    from openprogram.models.usage.ledger import default_ledger
    from openprogram.runtime.execution.conversation_scope import conversation_execution_scope

    if store is None:
        from openprogram.runtime.execution import default_store
        store = default_store()
    executions, parents = conversation_execution_scope(store, session_id)
    top = [item for item in executions if item.session_id == session_id and not parents.get(item.execution_id)
           and not item.execution_id.startswith("j_")]

    def root(execution_id: str) -> str:
        seen = set()
        while parents.get(execution_id) and execution_id not in seen:
            seen.add(execution_id)
            execution_id = parents[execution_id]
        return execution_id

    root_of = {item.execution_id: root(item.execution_id) for item in executions if parents.get(item.execution_id)}
    return run_costs(default_ledger.session_costs(session_id), top, root_of)


__all__ = [
    "FILTERS", "RECENT", "RunAnchor", "TaskRun", "Turn", "duration_words", "failure_reason", "judge_reply",
    "outcome_of", "page", "read_costs", "read_runs", "run_costs", "run_tasks", "summary",
    "turn_from_payload", "turn_of",
]
