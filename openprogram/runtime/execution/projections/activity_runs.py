"""A branch's runs and what one run did, for Activity's second level.

Pure derivation over data the caller already read: the session's in-memory
index, execution rows, attempts and sub-agent jobs. A run is one model reply
on the branch, or an execution that has no reply on it. Listing costs the
page size on an unchanged conversation; events are read when a row opens,
except for the time chart, which draws every step and takes a short form of them.
"""
from __future__ import annotations

import contextlib

import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from openprogram.state.store.session.nodes import ROLE_LLM, ROLE_USER

ASKED_PREVIEW_CHARS = 200
REPLY_PREVIEW_CHARS = 4000
INPUT_VALUE_CHARS = 2000
EVENT_TEXT_CHARS = 8000
CHAIN_CACHE_ENTRIES = 32
CHART_VALUE_CHARS = 200
CHART_LIST_ITEMS = 8
CHART_ERROR_CHARS = 400
CHART_RESULT_PEEK = 16
CHART_JOB_STEPS = 200
CHART_CACHE_ENTRIES = 2048

_INPUT_DEPTH = 32
_SUB_AGENT_DEPTH = 6
_JOB_ERRORED = "errored"
_TASK_OUTCOMES = frozenset({"quiet", "result", "failed", "skipped", "missed"})


@dataclass(frozen=True)
class RunExecution:
    """One execution of the session and the messages it is anchored to."""

    record: Any
    user_message_id: str | None = None
    assistant_message_id: str | None = None

    @property
    def is_own_run(self) -> bool:
        """Not a sub-agent's. Retry and Create branch start from a saved point of
        their parent, and each is a run of its own."""
        record = self.record
        if record.execution_id.startswith("j_"):
            return False
        return record.parent_execution_id is None or record.source_checkpoint_id is not None


@dataclass(frozen=True)
class ExecutionReads:
    """The execution facts a run needs, read per row and never for the whole list."""

    executions: Sequence[RunExecution]
    attempts_of: Callable[[str], Sequence[Any]]
    event_sequence_of: Callable[[str], int | None]
    checkpoints_of: Callable[[str], Mapping[str, str]] = lambda _execution_id: {}


@dataclass(frozen=True)
class _Entry:
    run_id: str
    when: float
    seq: int
    execution: RunExecution | None = None


@dataclass(frozen=True)
class _Source:
    reply: Any
    user: Any
    link: RunExecution | None


_chains: OrderedDict[tuple[str, str, int], tuple[tuple[_Entry, ...], frozenset[str]]] = OrderedDict()
_chains_lock = threading.Lock()
_chart_steps: OrderedDict[tuple[str, str, str], list[dict[str, Any]]] = OrderedDict()
_chart_steps_lock = threading.Lock()


def list_runs(
    index: Any,
    session_id: str,
    reads: ExecutionReads,
    *,
    head: str | None = None,
    offset: int = 0,
    limit: int = 20,
    chart: "ChartReads | None" = None,
) -> dict[str, Any]:
    """One page of the branch ending at ``head``, newest first."""
    resolved = head or index.head_id
    entries, on_chain = _chain(index, session_id, resolved) if resolved else ((), frozenset())
    extra = _unanchored(index, reads.executions, on_chain, is_current_head=resolved == index.head_id)
    ordered: Sequence[_Entry] = entries
    if extra:
        ordered = sorted((*entries, *extra), key=lambda entry: (entry.when, entry.seq), reverse=True)
    links = _by_assistant(reads.executions)
    rows = []
    for entry in ordered[offset:offset + limit]:
        source = _entry_source(index, entry, links)
        if source is not None:
            row = _row(index, source, reads)
            if chart is not None:
                row["steps"] = _chart_events(index, session_id, source, row, reads, chart)
            rows.append(row)
    page = {"runs": rows, "offset": offset, "total": len(ordered)}
    if chart is not None and offset == 0:
        # The messages this conversation sent elsewhere that are still
        # unanswered ("→ Butler · waiting").
        from openprogram.runtime.execution import hooks
        roster = hooks.registered("agent_roster_facts")
        try:
            page["outgoing"] = list(roster.outgoing(session_id, chart.jobs)) if roster is not None else []
        except Exception:  # noqa: BLE001 - the page shows without them
            page["outgoing"] = []
    return page


@dataclass(frozen=True)
class Ask:
    """A message the user sent, on the branch."""

    message_id: str
    text: str


def asks_on_branch(index: Any, session_id: str, head: str | None) -> list[Any]:
    """What the user themself sent on the branch, oldest first."""
    asks = []
    entries = _chain(index, session_id, head)[0] if head else ()
    for entry in reversed(entries):
        reply = index.get(entry.run_id)
        user = _user_of(index, reply) if reply is not None else None
        if user is not None and _asked_by(_Source(reply, user, None)) == "you":
            asks.append(Ask(user.id, _text(user)))
    return asks


def list_task_runs(index: Any, entries: Iterable[Mapping[str, Any]], reads: ExecutionReads) -> dict[str, Any]:
    """The runs a scheduled task's record kept, in the record's order."""
    links = _by_assistant(reads.executions)
    rows = []
    for entry in entries:
        tip = entry.get("tip")
        node = index.get(tip) if isinstance(tip, str) and tip else None
        if node is not None and node.role == ROLE_LLM:
            source = _Source(node, _user_of(index, node), links.get(node.id))
            rows.append(_row(index, source, reads, task=entry))
        else:
            rows.append(_task_row_without_tip(entry))
    return {"runs": rows, "offset": 0, "total": len(rows)}


def run_events(
    index: Any,
    run_id: str,
    reads: ExecutionReads,
    *,
    jobs: Sequence[Any] = (),
    index_of: Callable[[str], Any] | None = None,
) -> dict[str, Any] | None:
    """Everything one run did, in time order; None when no such run exists."""
    source = _find_source(index, run_id, reads.executions)
    if source is None:
        return None
    return {"run_id": run_id, "revision": _revision(index, source, reads),
            "events": _source_events(index, source, reads, jobs, index_of)}


def _source_events(
    index: Any, source: _Source, reads: ExecutionReads, jobs: Sequence[Any], index_of: Callable[[str], Any] | None,
) -> list[dict[str, Any]]:
    started = _run_start(source) or 0.0
    events: list[dict[str, Any]] = []
    if source.user is not None:
        events.append(_asked_event(source, started))
    if source.reply is not None:
        steps = _steps(index, source.reply)
        paired = _pair_sub_agents(index, source.reply, steps, jobs, index_of, started)
        owners = _task_owners(source.reply)
        for step in steps:
            if step.id not in paired.consumed_steps:
                events.append(_step_event(step, started))
        events.extend(paired.events)
        if owners:
            for event in events:
                task = owners.get(event.get("tool_call_id") or "")
                if task:
                    event["task"] = task
        replied = _replied_event(source.reply, started)
        if replied is not None:
            events.append(replied)
    if source.link is not None:
        events.extend(_attempt_events(source.link, reads))
    events.sort(key=lambda event: event["at"])
    return events


# The time chart's short form of a run's events


@dataclass(frozen=True)
class ChartReads:
    """What the chart needs beyond a row: sub-agent jobs and their conversations."""

    jobs: Sequence[Any] = ()
    index_of: Callable[[str], Any] | None = None


def _chart_value(value: Any, depth: int = 0) -> Any:
    """Enough of a step's input to name the step."""
    if isinstance(value, str):
        return value[:CHART_VALUE_CHARS]
    if depth >= 4:
        return None
    if isinstance(value, Mapping):
        return {key: _chart_value(item, depth + 1) for key, item in list(value.items())[:CHART_LIST_ITEMS]}
    if isinstance(value, list):
        return [_chart_value(item, depth + 1) for item in value[:CHART_LIST_ITEMS]]
    return value


def _owned(event: Mapping[str, Any]) -> dict[str, Any]:
    """The task the call was made for, when its reply stamped one (``state/task_blocks.py``)."""
    task = event.get("task")
    return {"task": task} if task else {}


def _chart_step(event: Mapping[str, Any]) -> dict[str, Any]:
    result = event["result"]
    # A failed step shows why. Any other step is named by how its result opens at most.
    kept = result[:CHART_ERROR_CHARS if event["failed"] else CHART_RESULT_PEEK] if isinstance(result, str) else None
    return {"kind": "step", "at": event["at"], "node_id": event["node_id"], "name": event["name"],
            "input": _chart_value(event["input"]), "failed": event["failed"], "result": kept, **_owned(event)}


def _job_steps(job: Any, index: Any, chart: ChartReads, started: float) -> list[dict[str, Any]]:
    """What a sub-agent did in its own branch, for the chart's last column."""
    if not job.head_id:
        return []
    source = index
    if chart.index_of is not None and job.parent_session_id:
        source = chart.index_of(job.parent_session_id) or index
    steps: list[dict[str, Any]] = []
    replied = None
    seen: set[str] = set()
    cursor: str | None = job.head_id
    while cursor and cursor != job.parent_msg_id and cursor not in seen:
        seen.add(cursor)
        node = source.get(cursor)
        if node is None:
            break
        if _is_run_node(node):
            steps.extend(_chart_step(_step_event(step, started)) for step in _child_steps(source, node))
            if replied is None:
                replied = _replied_event(node, started)
        cursor = node.predecessor
    steps.sort(key=lambda item: item["at"])
    if replied is not None:
        steps.append({"kind": "replied", "at": replied["at"], "text": replied["text"][:CHART_VALUE_CHARS]})
    return steps[-CHART_JOB_STEPS:]


def _chart_event(event: Mapping[str, Any], index: Any, chart: ChartReads, jobs: Mapping[str, Any], started: float) -> dict[str, Any] | None:
    kind = event["kind"]
    if kind == "step":
        return _chart_step(event)
    if kind == "replied":
        return {"kind": kind, "at": event["at"], "text": event["text"][:CHART_VALUE_CHARS]}
    if kind == "attempt":
        failure = event["failure_text"]
        return {**event, "failure_text": failure[:CHART_ERROR_CHARS] if isinstance(failure, str) else None}
    if kind != "subagent":
        return None
    job = jobs.get(event["job_id"])
    error = event["error"]
    return {
        "kind": kind, "at": event["at"], "ended_at": event["ended_at"], "job_id": event["job_id"], "name": event["name"],
        "status": event["status"], "never_started": event["never_started"], "failed": event["failed"],
        "brief": event["brief"][:CHART_VALUE_CHARS], "error": error[:CHART_ERROR_CHARS] if error else None,
        "conversation": event["conversation"],
        "children": [item for child in event["children"] if (item := _chart_event(child, index, chart, jobs, started)) is not None],
        "steps": _job_steps(job, index, chart, started) if job is not None else [],
        **_owned(event),
    }


def _has_unfinished_agent(events: Iterable[Mapping[str, Any]]) -> bool:
    return any(event["kind"] == "subagent" and not event["never_started"]
               and (event["ended_at"] is None or _has_unfinished_agent(event["children"])) for event in events)


def _chart_events(
    index: Any, session_id: str, source: _Source, row: Mapping[str, Any], reads: ExecutionReads, chart: ChartReads,
) -> list[dict[str, Any]]:
    key = (session_id, row["id"], row["revision"])
    with _chart_steps_lock:
        cached = _chart_steps.get(key)
        if cached is not None:
            _chart_steps.move_to_end(key)
            return cached
    started = _run_start(source) or 0.0
    jobs = {job.id: job for job in chart.jobs}
    events = [item for event in _source_events(index, source, reads, chart.jobs, chart.index_of)
              if (item := _chart_event(event, index, chart, jobs, started)) is not None]
    # A run still going, or one whose sub-agent outlives it, is read afresh.
    if row["ended_at"] is not None and not _has_unfinished_agent(events):
        with _chart_steps_lock:
            _chart_steps[key] = events
            while len(_chart_steps) > CHART_CACHE_ENTRIES:
                _chart_steps.popitem(last=False)
    return events


# The branch


def _chain(index: Any, session_id: str, head: str) -> tuple[tuple[_Entry, ...], frozenset[str]]:
    key = (session_id, head, index.next_seq)
    with _chains_lock:
        cached = _chains.get(key)
        if cached is not None:
            _chains.move_to_end(key)
            return cached
    walked = _walk(index, head)
    entries: list[_Entry] = []
    previous = 0.0
    for node in reversed(walked):
        if not _is_run_node(node):
            continue
        user = _user_of(index, node)
        when = (_number(user.created_at) if user is not None else None) or _number(node.created_at) or previous
        previous = when
        entries.append(_Entry(node.id, when, node.seq))
    entries.sort(key=lambda entry: (entry.when, entry.seq), reverse=True)
    value = (tuple(entries), frozenset(node.id for node in walked))
    with _chains_lock:
        _chains[key] = value
        _chains.move_to_end(key)
        while len(_chains) > CHAIN_CACHE_ENTRIES:
            _chains.popitem(last=False)
    return value


def _walk(index: Any, head: str) -> list[Any]:
    """Head to root along the conversation edge, with what summaries covered."""
    walked: list[Any] = []
    seen: set[str] = set()
    cursor: str | None = head
    while cursor and cursor not in seen:
        node = index.get(cursor)
        if node is None:
            break
        seen.add(cursor)
        walked.append(node)
        covered = _metadata(node).get("covers_ids")
        for covered_id in reversed(covered) if isinstance(covered, list) else ():
            covered_node = index.get(covered_id)
            if covered_node is not None and covered_id not in seen:
                seen.add(covered_id)
                walked.append(covered_node)
        cursor = node.predecessor
    return walked


def _is_run_node(node: Any) -> bool:
    if node.role != ROLE_LLM:
        return False
    metadata = _metadata(node)
    return not (metadata.get("covers_ids") or metadata.get("structural") or metadata.get("rewound")
                or metadata.get("function") == "attach")


def _user_of(index: Any, reply: Any) -> Any:
    node = index.get(reply.predecessor) if reply.predecessor else None
    return node if node is not None and node.role == ROLE_USER else None


def _unanchored(
    index: Any, executions: Sequence[RunExecution], on_chain: frozenset[str], *, is_current_head: bool,
) -> list[_Entry]:
    """Executions that are runs of the branch but whose reply is not on it."""
    extra = []
    for link in executions:
        assistant = link.assistant_message_id
        user = link.user_message_id
        if not link.is_own_run or (assistant and assistant in on_chain):
            continue
        reply = index.get(assistant) if assistant else None
        if reply is not None and not _is_run_node(reply):
            continue
        if (user and user in on_chain) or (not assistant and not user and is_current_head):
            extra.append(_Entry(
                reply.id if reply is not None else link.record.execution_id,
                _number(link.record.created_at) or 0.0,
                reply.seq if reply is not None else -1,
                link,
            ))
    return extra


def _by_assistant(executions: Sequence[RunExecution]) -> dict[str, RunExecution]:
    """The newest execution anchored to each reply."""
    links: dict[str, RunExecution] = {}
    for link in sorted(executions, key=lambda item: _number(item.record.created_at) or 0.0):
        if link.assistant_message_id:
            links[link.assistant_message_id] = link
    return links


def _entry_source(index: Any, entry: _Entry, links: Mapping[str, RunExecution]) -> _Source | None:
    if entry.execution is not None:
        return _execution_source(index, entry.execution)
    node = index.get(entry.run_id)
    return None if node is None else _Source(node, _user_of(index, node), links.get(node.id))


def _execution_source(index: Any, link: RunExecution) -> _Source:
    reply = index.get(link.assistant_message_id) if link.assistant_message_id else None
    if reply is not None and reply.role != ROLE_LLM:
        reply = None
    user = index.get(link.user_message_id) if link.user_message_id else None
    if user is not None and user.role != ROLE_USER:
        user = None
    return _Source(reply, user, link)


def _find_source(index: Any, run_id: str, executions: Sequence[RunExecution]) -> _Source | None:
    node = index.get(run_id)
    if node is not None and _is_run_node(node):
        link = _by_assistant(executions).get(node.id)
        return _Source(node, _user_of(index, node) or (_execution_source(index, link).user if link else None), link)
    for link in executions:
        if link.record.execution_id == run_id:
            return _execution_source(index, link)
    return None


# One row


def _row(index: Any, source: _Source, reads: ExecutionReads, *, task: Mapping[str, Any] | None = None) -> dict[str, Any]:
    reply, user, link = source.reply, source.user, source.link
    record = link.record if link is not None else None
    asked = _text(user) if user is not None else ""
    answer = _text(reply) if reply is not None else ""
    reply_text, reply_clipped = _clip(answer, REPLY_PREVIEW_CHARS)
    ended = record.terminal_at if record is not None else _number(_metadata(reply).get("finished_at"))
    row = {
        "id": reply.id if reply is not None else record.execution_id,
        "message_id": reply.id if reply is not None else None,
        "user_message_id": user.id if user is not None else None,
        "execution_id": record.execution_id if record is not None else None,
        "is_report": reply is not None and reply.id.startswith("report_"),
        "asked_by": _asked_by(source),
        "asked_preview": asked[:ASKED_PREVIEW_CHARS],
        "reply": reply_text,
        "reply_truncated": reply_clipped or _spilled(reply),
        "started_at": _run_start(source),
        "ended_at": ended,
        "execution_status": record.status.value if record is not None else None,
        "message_status": _message_status(reply),
        "reason_code": record.reason_code if record is not None else None,
        "task_outcome": None,
        "task_text": None,
        "event_count": _event_count(index, source, reads),
        "revision": _revision(index, source, reads),
    }
    if task is not None:
        row["task_outcome"] = _task_outcome(task)
        row["task_text"] = _task_text(task)
        if row["ended_at"] is None:
            row["ended_at"] = _number(task.get("at"))
    return row


def _task_row_without_tip(entry: Mapping[str, Any]) -> dict[str, Any]:
    at = _number(entry.get("at"))
    return {
        "id": f"task:{entry.get('at')}", "message_id": None, "user_message_id": None, "execution_id": None,
        "is_report": False, "asked_by": "none", "asked_preview": "", "reply": "", "reply_truncated": False,
        "started_at": None, "ended_at": at, "execution_status": None, "message_status": None, "reason_code": None,
        "task_outcome": _task_outcome(entry), "task_text": _task_text(entry), "event_count": 0,
        "revision": "-:-:-:-",
    }


def _task_outcome(entry: Mapping[str, Any]) -> str | None:
    status = entry.get("status")
    return status if status in _TASK_OUTCOMES else None


def _task_text(entry: Mapping[str, Any]) -> str | None:
    text = entry.get("text")
    return text if isinstance(text, str) and text else None


def _asked_by(source: _Source) -> str:
    if source.user is None:
        return "none"
    if "scheduler" in (_metadata(source.user).get("source"), _metadata(source.reply).get("source")):
        return "schedule"
    return "check-in" if _text(source.user).startswith("Always-on") else "you"


def _run_start(source: _Source) -> float | None:
    if source.link is not None and (started := _number(source.link.record.created_at)):
        return started
    for node in (source.user, source.reply):
        if node is not None and (created := _number(node.created_at)) is not None:
            return created
    return None


def _message_status(reply: Any) -> str | None:
    status = _metadata(reply).get("status")
    return status if isinstance(status, str) else None


def _event_count(index: Any, source: _Source, reads: ExecutionReads) -> int:
    steps = _step_ids(index, source.reply) if source.reply is not None else ()
    attempts = reads.attempts_of(source.link.record.execution_id) if source.link is not None else ()
    return len(steps) + len(attempts)


def _revision(index: Any, source: _Source, reads: ExecutionReads) -> str:
    record = source.link.record if source.link is not None else None
    sequence = reads.event_sequence_of(record.execution_id) if record is not None else None
    children = len(index.children_by_caller.get(source.reply.id, ())) if source.reply is not None else None
    parts = (record.status_version if record is not None else None, sequence, children, _message_status(source.reply))
    return ":".join("-" if part is None else str(part) for part in parts)


# Steps


def _task_owners(reply: Any) -> dict[str, str]:
    """Each call of the reply that was made for a task, by its call id.
    The reply's stored blocks carry the stamp (``state/task_blocks.py``)."""
    extra = _metadata(reply).get("extra")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except ValueError:
            return {}
    blocks = extra.get("blocks") if isinstance(extra, Mapping) else None
    if not isinstance(blocks, list):
        return {}
    return {
        block["tool_call_id"]: block["task"] for block in blocks
        if isinstance(block, Mapping) and block.get("type") == "tool"
        and isinstance(block.get("tool_call_id"), str) and isinstance(block.get("task"), str) and block["task"]
    }


def _step_ids(index: Any, reply: Any) -> list[str]:
    return [node.id for node in _child_steps(index, reply)]


def _child_steps(index: Any, reply: Any) -> list[Any]:
    children = (index.get(child_id) for child_id in list(index.children_by_caller.get(reply.id, ())))
    return [node for node in children if node is not None and _is_step(node)]


def _is_step(node: Any) -> bool:
    if node.role in (ROLE_USER, ROLE_LLM):
        return False
    metadata = _metadata(node)
    return not (metadata.get("structural") or metadata.get("function") == "attach" or node.name == "attach")


def _steps(index: Any, reply: Any) -> list[Any]:
    return sorted(_child_steps(index, reply), key=lambda node: (_number(node.created_at) or 0.0, node.seq))


def _step_failed(node: Any) -> bool:
    value = _metadata(node).get("is_error")
    return value.strip().lower() == "true" if isinstance(value, str) else bool(value)


def _step_event(step: Any, started: float) -> dict[str, Any]:
    inputs, inputs_clipped = _bounded(_parsed_input(step))
    result = _output_text(step)
    clipped_result, result_clipped = _clip(result, EVENT_TEXT_CHARS) if result is not None else (None, False)
    return {
        "kind": "step", "at": _number(step.created_at) or started, "node_id": step.id,
        "tool_call_id": _string(_metadata(step).get("tool_call_id")), "name": step.name or "",
        "input": inputs, "input_truncated": inputs_clipped,
        "result": clipped_result, "result_truncated": result_clipped or _spilled(step),
        "failed": _step_failed(step),
    }


# Asked, replied, attempts


def _asked_event(source: _Source, started: float) -> dict[str, Any]:
    text, clipped = _clip(_text(source.user), EVENT_TEXT_CHARS)
    return {"kind": "asked", "at": _number(source.user.created_at) or started, "by": _asked_by(source),
            "text": text, "truncated": clipped or _spilled(source.user)}


def _replied_event(reply: Any, started: float) -> dict[str, Any] | None:
    answer = _text(reply)
    if not answer:
        return None
    text, clipped = _clip(answer, EVENT_TEXT_CHARS)
    return {"kind": "replied", "at": _number(_metadata(reply).get("finished_at")) or _number(reply.created_at) or started,
            "text": text, "truncated": clipped or _spilled(reply)}


def _attempt_events(link: RunExecution, reads: ExecutionReads) -> list[dict[str, Any]]:
    record = link.record
    attempts = sorted(reads.attempts_of(record.execution_id), key=lambda attempt: attempt.generation)
    checkpoints = reads.checkpoints_of(record.execution_id)
    events = []
    for position, attempt in enumerate(attempts):
        last = position == len(attempts) - 1
        events.append({
            "kind": "attempt", "at": attempt.ended_at or attempt.activated_at or attempt.leased_at,
            "number": attempt.generation,
            "ended_at": attempt.ended_at, "status": attempt.status.value, "outcome": attempt.outcome,
            "reason_code": record.reason_code if last else None, "failure_text": attempt.failure_text,
            "checkpoint_id": checkpoints.get(attempt.attempt_id) or (record.checkpoint_head_id if last else None),
        })
    return events


# Sub-agents


@dataclass(frozen=True)
class _PairedSubAgents:
    events: list[dict[str, Any]]
    consumed_steps: set[str]


def _caller_ids(job: Any) -> list[str]:
    ids = []
    for caller in (getattr(job, "caller_msg_id", None), getattr(job, "spawn_caller", None)):
        if isinstance(caller, str) and caller:
            ids.append(caller.rsplit("|node:", 1)[-1])
    return list(dict.fromkeys(ids))


def _started_in_run(index: Any, job: Any, in_run: set[str]) -> bool:
    """Whether the job's caller is one of the run's nodes or was called from one,
    as when a program's helper function calls agent()."""
    for caller in _caller_ids(job):
        seen: set[str] = set()
        cursor: str | None = caller
        while cursor and cursor not in seen:
            if cursor in in_run:
                return True
            seen.add(cursor)
            node = index.get(cursor)
            cursor = node.caller if node is not None else None
    return False


def _calling_step(index: Any, job: Any, steps: Mapping[str, Any]) -> Any:
    """The run's step the job was started from: its own call, or the program
    call whose helper started it."""
    for caller in _caller_ids(job):
        seen: set[str] = set()
        cursor: str | None = caller
        while cursor and cursor not in seen:
            if cursor in steps:
                return steps[cursor]
            seen.add(cursor)
            node = index.get(cursor)
            cursor = node.caller if node is not None else None
    return None


def _is_sub_agent_step(step: Any) -> bool:
    if step.name == "agent":
        return True
    # Conversations recorded while computer_use could hand a task to a new agent keep those calls.
    inputs = _parsed_input(step)
    return step.name == "computer_use" and isinstance(inputs, Mapping) and inputs.get("context") == "fresh"


def _prompt_matches(step: Any, job: Any) -> bool:
    inputs = _parsed_input(step)
    if not isinstance(inputs, Mapping):
        return False
    prompt = getattr(job, "prompt", None) or ""
    if inputs.get("prompt") == prompt and prompt:
        return True
    brief = inputs.get("brief")
    return step.name == "computer_use" and isinstance(brief, str) and bool(brief) and brief in prompt


def _pair_sub_agents(
    index: Any, reply: Any, steps: Sequence[Any], jobs: Sequence[Any],
    index_of: Callable[[str], Any] | None, started: float,
) -> _PairedSubAgents:
    in_run = {reply.id, *(step.id for step in steps)}
    ordered_jobs = sorted(jobs, key=lambda job: _number(job.created_at) or 0.0)
    owned = [job for job in ordered_jobs if _started_in_run(index, job, in_run)]
    free_steps = {step.id: step for step in steps if _is_sub_agent_step(step)}
    pairs: dict[str, Any] = {}

    def pair(job: Any, step: Any) -> None:
        pairs[job.id] = step
        free_steps.pop(step.id, None)

    for job in owned:
        step = next((free_steps[caller] for caller in _caller_ids(job) if caller in free_steps), None)
        if step is not None:
            pair(job, step)
    for job in owned:
        if job.id not in pairs:
            step = next((step for step in free_steps.values() if _prompt_matches(step, job)), None)
            if step is not None:
                pair(job, step)
    left_on_reply = [job for job in owned if job.id not in pairs and reply.id in _caller_ids(job)]
    healthy = [step for step in free_steps.values() if not _step_failed(step)]
    if len(left_on_reply) == 1 and len(healthy) == 1:
        pair(left_on_reply[0], healthy[0])

    seen = {job.id for job in owned}
    by_id = {step.id: step for step in steps}
    events = []
    for job in owned:
        event = _job_event(job, pairs.get(job.id), jobs, index, index_of, seen, 0, started)
        if event["tool_call_id"] is None:
            # Started inside a program's call: the task is that call's.
            calling = _calling_step(index, job, by_id)
            event["tool_call_id"] = _string(_metadata(calling).get("tool_call_id")) if calling is not None else None
        events.append(event)
    consumed = {step.id for step in pairs.values()}
    for step in free_steps.values():
        if _step_failed(step):
            events.append(_never_started_event(step, started))
            consumed.add(step.id)
    return _PairedSubAgents(events, consumed)


def _status_value(job: Any) -> str | None:
    status = getattr(job, "status", None)
    value = getattr(status, "value", status)
    return value if isinstance(value, str) else None


def _job_event(
    job: Any, step: Any, jobs: Sequence[Any], index: Any, index_of: Callable[[str], Any] | None,
    seen: set[str], depth: int, started: float,
) -> dict[str, Any]:
    status = _status_value(job)
    created = _number(job.created_at)
    at = created or (_number(step.created_at) if step is not None else None) or started
    errored = status == _JOB_ERRORED
    never_started = errored and not job.started_at and not job.head_id
    brief, brief_clipped = _clip(job.prompt or "", EVENT_TEXT_CHARS)
    result = job.result_text
    clipped_result, result_clipped = _clip(result, EVENT_TEXT_CHARS) if isinstance(result, str) else (None, False)
    session = job.parent_session_id
    return {
        "kind": "subagent", "at": at, "ended_at": _number(job.completed_at), "job_id": job.id,
        "name": job.label or "Sub-agent", "agent_id": job.agent_id or None, "context": job.context_mode or None,
        "status": status, "never_started": never_started,
        "failed": errored or (step is not None and _step_failed(step)),
        "brief": brief, "brief_truncated": brief_clipped,
        "result": clipped_result, "result_truncated": result_clipped,
        "error": job.error[:EVENT_TEXT_CHARS] if isinstance(job.error, str) and job.error else None,
        "created_at": created, "closed_as": status,
        "conversation": {"session_id": session, "head_id": job.head_id} if session and not never_started else None,
        "tool_call_id": _string(_metadata(step).get("tool_call_id")) if step is not None else None,
        **_agent_facts(job),
        "children": _job_children(job, jobs, index, index_of, seen, depth, started),
    }


def _agent_facts(job: Any) -> dict[str, Any]:
    """The roster facts of a helper: its status in the one status set and the
    tokens and cost of its subtree (``jobs/agent_status.py``, registered as
    the ``agent_roster_facts`` hook)."""
    from openprogram.runtime.execution import hooks
    roster = hooks.registered("agent_roster_facts")
    with contextlib.suppress(Exception):  # noqa: BLE001 - the row still shows; the facts are extra
        if roster is not None:
            return dict(roster.facts(job))
    return {"agent_status": None, "usage": None}


def _job_children(
    job: Any, jobs: Sequence[Any], index: Any, index_of: Callable[[str], Any] | None,
    seen: set[str], depth: int, started: float,
) -> list[dict[str, Any]]:
    if depth + 1 >= _SUB_AGENT_DEPTH or not job.head_id:
        return []
    source = index
    if index_of is not None and job.parent_session_id:
        source = index_of(job.parent_session_id) or index
    own = _own_chain_ids(source, job.head_id, job.parent_msg_id)
    children = [other for other in sorted(jobs, key=lambda item: _number(item.created_at) or 0.0)
                if other.id not in seen and own.intersection(_caller_ids(other))]
    seen.update(other.id for other in children)
    return [_job_event(other, None, jobs, index, index_of, seen, depth + 1, started) for other in children]


def _own_chain_ids(index: Any, head: str, stop_at: str | None) -> set[str]:
    """Nodes a job's own branch produced, excluding the history it forked from."""
    ids: set[str] = set()
    cursor: str | None = head
    while cursor and cursor != stop_at and cursor not in ids:
        node = index.get(cursor)
        if node is None:
            break
        ids.add(cursor)
        ids.update(list(index.children_by_caller.get(cursor, ())))
        cursor = node.predecessor
    return ids


def _never_started_event(step: Any, started: float) -> dict[str, Any]:
    inputs = _parsed_input(step)
    described = (inputs.get("prompt") or inputs.get("brief")) if isinstance(inputs, Mapping) else None
    brief, brief_clipped = _clip(described if isinstance(described, str) else "", EVENT_TEXT_CHARS)
    error = _output_text(step)
    return {
        "kind": "subagent", "at": _number(step.created_at) or started, "ended_at": None, "job_id": None,
        "tool_call_id": _string(_metadata(step).get("tool_call_id")), "name": "Sub-agent", "agent_id": None, "context": None, "status": None, "never_started": True,
        "failed": True, "brief": brief, "brief_truncated": brief_clipped, "result": None, "result_truncated": False,
        "error": error[:EVENT_TEXT_CHARS] if error else None, "created_at": None, "closed_as": None,
        "conversation": None, "children": [],
    }


# Node values


def _metadata(node: Any) -> Mapping[str, Any]:
    value = getattr(node, "metadata", None)
    return value if isinstance(value, Mapping) else {}


def _spilled(node: Any) -> bool:
    return bool(_metadata(node).get("spilled"))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _clip(text: str, limit: int) -> tuple[str, bool]:
    return (text[:limit], True) if len(text) > limit else (text, False)


def _text(node: Any) -> str:
    return _plain_text(node.output)


def _plain_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return _plain_text(value.get("text") or value.get("content"))
    if isinstance(value, list):
        return "\n".join(part for part in map(_plain_text, value) if part)
    return ""


def _output_text(node: Any) -> str | None:
    value = node.output
    if value is None or value == "":
        return None
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def _parsed_input(node: Any) -> Any:
    value = node.input
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            return value
    return value


def _bounded(value: Any, depth: int = 0) -> tuple[Any, bool]:
    """A JSON value with every string at most INPUT_VALUE_CHARS long."""
    if isinstance(value, str):
        return _clip(value, INPUT_VALUE_CHARS)
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    if depth >= _INPUT_DEPTH:
        return None, True
    clipped = False
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            out[str(key)], item_clipped = _bounded(item, depth + 1)
            clipped = clipped or item_clipped
        return out, clipped
    if isinstance(value, (list, tuple)):
        items = []
        for item in value:
            bounded_item, item_clipped = _bounded(item, depth + 1)
            items.append(bounded_item)
            clipped = clipped or item_clipped
        return items, clipped
    return _clip(str(value), INPUT_VALUE_CHARS)
