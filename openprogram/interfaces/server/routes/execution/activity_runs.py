"""Read-only run listing and per-run events for Activity's second level."""
from __future__ import annotations

import contextlib

from fastapi import Request
from fastapi.responses import JSONResponse

from . import lifecycle

MAX_PAGE = 100
_NOT_FOUND = {"error": "not_found"}
_NO_STORE = {"Cache-Control": "no-store"}


def _may_read(request: Request, session_id: str) -> bool:
    from openprogram.runtime.execution.authorization import ExecutionAuthorizationError, authorize_session_action
    from openprogram.runtime.execution.projections.public import project_id_for_session

    actor, bound_session = lifecycle._actor_and_session(request)
    if bound_session is not None and bound_session != session_id:
        return False
    try:
        authorize_session_action(actor, "execution.snapshot", {
            "project_id": project_id_for_session(session_id), "session_id": session_id,
        })
    except ExecutionAuthorizationError:
        return False
    return True


def _execution_reads(store, session_id: str):
    from openprogram.runtime.execution.projections.activity_runs import ExecutionReads, RunExecution
    from openprogram.runtime.execution.attempts import AttemptStore
    from openprogram.runtime.execution.recovery.checkpoints import ExecutionCheckpointStore

    anchors = {item.execution_id: item for item in store.list_inputs_for_session(session_id)}
    executions = []
    for record in store.list_for_session(session_id):
        anchor = anchors.get(record.execution_id)
        executions.append(RunExecution(
            record,
            anchor.user_message_id if anchor is not None else None,
            anchor.assistant_message_id if anchor is not None else None,
        ))
    attempts = AttemptStore(store)
    checkpoints = ExecutionCheckpointStore(store)

    def checkpoints_of(execution_id: str) -> dict[str, str]:
        return {item.created_by_attempt_id: item.checkpoint_id for item in checkpoints.list_for_execution(execution_id)}

    return ExecutionReads(executions, attempts.list_for_execution, store.latest_event_sequence, checkpoints_of)


def _task_entries(session_id: str, task_id: str) -> list[dict] | None:
    """The task's kept runs, or None when it is not this conversation's task."""
    from openprogram.runtime.scheduler import service, task_runs

    from .conversation_work import task_belongs_to_session

    task = next((item for item in service.list_tasks() if str(item.get("id")) == task_id), None)
    if task is None or not task_belongs_to_session(task, session_id):
        return None
    return task_runs.recent(task_id)


def _conversation_jobs(store, session_id: str) -> list:
    from openprogram.runtime.execution.conversation_scope import conversation_executions
    from openprogram.runtime.jobs.store import list_jobs

    sessions = dict.fromkeys([session_id, *(item.session_id for item in conversation_executions(store, session_id))])
    return [job for scoped_session in sessions for job in list_jobs(scoped_session)]


def _chart_page(session_id: str, reads, head: str | None, offset: int, limit: int) -> dict | None:
    """A page of the branch with every run's steps and its topic."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation import topics
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.projections.activity_runs import ChartReads, asks_on_branch, list_runs

    database = default_db()

    def index_of(other_session: str):
        return database.read_index(other_session, lambda index: index)

    chart = ChartReads(_conversation_jobs(default_store(), session_id), index_of)

    def read(index):
        page = list_runs(index, session_id, reads, head=head, offset=offset, limit=limit, chart=chart)
        return page, asks_on_branch(index, session_id, head or index.head_id)

    found = database.read_index(session_id, read)
    if found is None:
        return None
    page, asks = found
    page["topics_sorting"] = topics.annotate_runs(session_id, database._session_dir(session_id), page["runs"], asks)
    return page


def register(app) -> None:
    @app.get("/api/session/{session_id}/activity/runs")
    def activity_runs(
        session_id: str,
        request: Request,
        head: str | None = None,
        offset: int = 0,
        limit: int = 20,
        task_id: str | None = None,
        chart: bool = False,
    ):
        from openprogram.runtime.context.session_db import default_db
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution.projections.activity_runs import list_runs, list_task_runs

        if not _may_read(request, session_id):
            return JSONResponse(_NOT_FOUND, status_code=404)
        entries = None
        if task_id is not None:
            entries = _task_entries(session_id, task_id)
            if entries is None:
                return JSONResponse(_NOT_FOUND, status_code=404)
        reads = _execution_reads(default_store(), session_id)
        if chart and entries is None:
            page = _chart_page(session_id, reads, head, max(offset, 0), max(1, min(limit, MAX_PAGE)))
            if page is None:
                return JSONResponse(_NOT_FOUND, status_code=404)
            return JSONResponse(page, headers=_NO_STORE)
        if entries is not None:
            page = default_db().read_index(session_id, lambda index: list_task_runs(index, entries, reads))
        else:
            first = max(offset, 0)
            size = max(1, min(limit, MAX_PAGE))
            page = default_db().read_index(
                session_id, lambda index: list_runs(index, session_id, reads, head=head, offset=first, limit=size))
        if page is None:
            return JSONResponse(_NOT_FOUND, status_code=404)
        return JSONResponse(page, headers=_NO_STORE)

    @app.get("/api/session/{session_id}/activity/tasks/{task_id}")
    def activity_task(session_id: str, task_id: str, request: Request, filter: str = "all",
                      offset: int = 0, limit: int = 20):
        """One scheduled task's runs for its page: how many, how many did not
        finish or told the reader, the last 24 outcomes, what they cost, and a
        page of runs newest first (``filter``: all, unfinished, notified)."""
        from openprogram.runtime.scheduler import service, task_activity

        from .conversation_work import task_belongs_to_session

        if not _may_read(request, session_id):
            return JSONResponse(_NOT_FOUND, status_code=404)
        task = next((item for item in service.list_tasks() if str(item.get("id")) == task_id), None)
        if task is None or not task_belongs_to_session(task, session_id) or filter not in task_activity.FILTERS:
            return JSONResponse(_NOT_FOUND, status_code=404)
        runs = task_activity.read_runs(session_id).get(task_id, [])
        body = task_activity.page(runs, kind=filter, offset=max(offset, 0), limit=max(1, min(limit, MAX_PAGE)),
                                  costs=task_activity.read_costs(session_id) if runs else {})
        return JSONResponse({"task_id": task_id, **body}, headers=_NO_STORE)

    @app.get("/api/session/{session_id}/activity/roster")
    def activity_roster(session_id: str, request: Request):
        """What the overview's Agents rows add to the executions: each
        helper's status, subtree usage and tool calls, and the messages
        still unanswered in other conversations."""
        from openprogram.runtime.context.session_db import default_db
        from openprogram.runtime.execution import default_store, hooks

        if not _may_read(request, session_id):
            return JSONResponse(_NOT_FOUND, status_code=404)
        if default_db().read_index(session_id, lambda _index: True) is None:
            return JSONResponse(_NOT_FOUND, status_code=404)
        facts = hooks.registered("agent_roster_facts")
        body = {"agents": {}, "outgoing": []}
        if facts is not None:
            with contextlib.suppress(Exception):  # noqa: BLE001 - the rows still show without their facts
                body = facts.roster(session_id, _conversation_jobs(default_store(), session_id))
        return JSONResponse(body, headers=_NO_STORE)

    @app.get("/api/session/{session_id}/activity/runs/{run_id}/events")
    def activity_run_events(session_id: str, run_id: str, request: Request):
        from openprogram.runtime.context.session_db import default_db
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution.projections.activity_runs import run_events

        if not _may_read(request, session_id):
            return JSONResponse(_NOT_FOUND, status_code=404)
        database = default_db()
        store = default_store()
        reads = _execution_reads(store, session_id)
        jobs = _conversation_jobs(store, session_id)

        def index_of(other_session: str):
            return database.read_index(other_session, lambda index: index)

        body = database.read_index(
            session_id, lambda index: run_events(index, run_id, reads, jobs=jobs, index_of=index_of))
        if body is None:
            return JSONResponse(_NOT_FOUND, status_code=404)
        return JSONResponse(body, headers=_NO_STORE)
