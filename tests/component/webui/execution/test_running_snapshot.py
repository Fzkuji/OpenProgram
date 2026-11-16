"""GET /api/running snapshot — in-flight tool calls show up as kind=tool."""
import importlib
import time
from types import SimpleNamespace

import pytest

agent_loop = importlib.import_module("openprogram.runtime.agent.loop")
from openprogram.interfaces.server.routes.execution import lifecycle, running


@pytest.fixture(autouse=True)
def _runner_lifecycle():
    """Close the singleton created while collecting the running snapshot."""
    from openprogram.runtime.jobs import runner as runner_mod

    runner_mod.shutdown_runner()
    yield
    runner_mod.shutdown_runner()


def test_collect_includes_running_tool_calls():
    call_id = "tc_test_running_snapshot"
    with agent_loop.RUNNING_TOOL_CALLS_LOCK:
        agent_loop.RUNNING_TOOL_CALLS[call_id] = {
            "tool_name": "bash",
            "label": "sleep 600",
            "started_at": time.time(),
        }
    try:
        items = running._collect()
        tool_items = [i for i in items if i["kind"] == "tool" and i["id"] == call_id]
        assert len(tool_items) == 1
        assert tool_items[0]["label"] == "sleep 600"
        assert tool_items[0]["tool_name"] == "bash"
        assert tool_items[0]["status"] == "running"
    finally:
        with agent_loop.RUNNING_TOOL_CALLS_LOCK:
            agent_loop.RUNNING_TOOL_CALLS.pop(call_id, None)


def test_collect_includes_canonical_ui_projection(monkeypatch):
    from openprogram.runtime.execution.event_effects import RunningExecution

    projection = RunningExecution(
        execution_id="exec-canonical",
        session_id="session-canonical",
        status="running",
        payload={
            "execution": {"created_at": 123.0},
            "ui": {"label": "workflow.run"},
            "event_cursor": {
                "execution_id": "exec-canonical",
                "next_sequence": 8,
                "snapshot_status_version": 7,
            },
        },
        updated_at=123.0,
    )
    monkeypatch.setattr(
        "openprogram.runtime.execution.event_effects.list_running_executions",
        lambda: [projection],
    )
    snapshot = {
        "execution_id": "exec-canonical",
        "status": "running",
        "status_version": 7,
        "created_at": 123.0,
        "capabilities": {"pause": True},
        "resource": None,
        "event_sequence": 7,
    }
    monkeypatch.setattr(
        running, "_canonical_snapshot", lambda _execution_id: snapshot,
    )

    item = next(item for item in running._collect() if item["id"] == "exec-canonical")
    assert item == {
        "kind": "execution",
        "id": "exec-canonical",
        "execution_id": "exec-canonical",
        "session_id": "session-canonical",
        "label": "workflow.run",
        "status": "running",
        "started_at": 123.0,
        "snapshot": snapshot,
        "capabilities": {"pause": True},
        "resource": None,
        "event_cursor": {
            "execution_id": "exec-canonical",
            "next_sequence": 8,
            "snapshot_status_version": 7,
        },
    }


def test_collect_embeds_only_the_nested_job_resource(monkeypatch):
    from openprogram.runtime.jobs.types import JobStatus

    resource = {"resource_state": "active", "usage": {"tokens": {"actual": 1}}}
    job = SimpleNamespace(
        id="job-resource",
        parent_session_id="session-resource",
        status=JobStatus.RUNNING,
        subject="subject",
        prompt="prompt",
        label="label",
        started_at=123.0,
        queued_at=None,
        created_at=123.0,
    )
    view = SimpleNamespace(resource=resource)
    runner = SimpleNamespace(
        list_jobs=lambda *_args, **_kwargs: [job],
        get_job_resource_view=lambda _job_id: view,
    )
    execution = SimpleNamespace(execution_id=job.id)
    monkeypatch.setattr("openprogram.runtime.jobs.runner.get_runner", lambda: runner)
    monkeypatch.setattr(
        "openprogram.runtime.execution.default_store",
        lambda: SimpleNamespace(get_execution=lambda _execution_id: execution),
    )

    def fake_snapshot(_execution, **kwargs):
        assert kwargs["resource"] is resource
        return SimpleNamespace(to_dict=lambda: {
            "execution_id": job.id,
            "status": "running",
            "resource": kwargs["resource"],
            "capabilities": {},
            "event_sequence": 1,
            "status_version": 1,
        })

    monkeypatch.setattr(
        "openprogram.runtime.execution.projections.public.execution_snapshot", fake_snapshot,
    )

    item = next(item for item in running._collect() if item["id"] == job.id)
    assert item["snapshot"]["resource"] == resource
    assert "job_id" not in item["snapshot"]["resource"]


def test_execution_payload_embeds_only_the_nested_job_resource(monkeypatch):
    resource = {"resource_state": "active"}
    execution = SimpleNamespace(execution_id="job-resource", session_id="session")
    job = SimpleNamespace(id="job-resource")
    view = SimpleNamespace(resource=resource)
    reads = []

    def get_job(job_id):
        reads.append(job_id)
        return job

    def get_job_resource_view(_job_id, *, job=None, jobs_in=None):
        # The Job read for the snapshot is the one the view is built from.
        assert job is not None and jobs_in is None
        return view

    monkeypatch.setattr("openprogram.runtime.jobs.get_runner", lambda: SimpleNamespace(
        get_job_resource_view=get_job_resource_view, get_job=get_job,
    ))
    monkeypatch.setattr(
        "openprogram.runtime.execution.default_store", lambda: SimpleNamespace(),
    )

    def fake_snapshot(_execution, **kwargs):
        assert kwargs["resource"] is resource
        return SimpleNamespace(to_dict=lambda: {"resource": kwargs["resource"]})

    monkeypatch.setattr(
        "openprogram.runtime.execution.projections.public.execution_snapshot", fake_snapshot,
    )
    assert lifecycle._execution_payload(execution) == {"resource": resource}
    assert reads == ["job-resource"]


def test_session_executions_read_each_sessions_jobs_once(monkeypatch):
    """Activity's list reads every session's Jobs once, not a Job per row.

    A conversation with many runs (an hourly scheduled task keeps every
    run) made the list look its Job up per row, each lookup taking the
    session's lock and reading its log again: seconds per request.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openprogram.runtime.jobs import store as job_store

    def execution(eid, session_id):
        return SimpleNamespace(execution_id=eid, session_id=session_id, status=SimpleNamespace(value="completed"),
                               created_at=1.0, status_version=1)

    rows = [execution("turn-1", "session"), execution("job-1", "session"),
            execution("turn-2", "session"), execution("job-2", "helper-session")]
    jobs = {"session": [SimpleNamespace(id="job-1")], "helper-session": [SimpleNamespace(id="job-2")]}
    listed = []

    def list_jobs(session_id):
        listed.append(session_id)
        return jobs[session_id]

    views = []

    def get_job_resource_view(job_id, *, job=None, jobs_in=None):
        assert job is not None and job.id == job_id
        # The subtree usage lists sessions through the same reader.
        jobs_in("session")
        views.append(job_id)
        return SimpleNamespace(resource={"for": job_id})

    def get_job(_job_id):
        raise AssertionError("the list looks Jobs up in what it read")

    monkeypatch.setattr(job_store, "list_jobs", list_jobs)
    monkeypatch.setattr("openprogram.runtime.jobs.get_runner", lambda: SimpleNamespace(
        get_job=get_job, get_job_resource_view=get_job_resource_view))
    monkeypatch.setattr("openprogram.runtime.execution.default_store",
                        lambda: SimpleNamespace(get_agent_turn_input=lambda _: {}, list_for_session=lambda _: [],
                                                list_inputs_for_session=lambda _: []))
    monkeypatch.setattr("openprogram.runtime.execution.conversation_scope.conversation_execution_scope",
                        lambda *_: (rows, {}))
    monkeypatch.setattr("openprogram.runtime.execution.projections.activity_branches.conversation_activity_branches",
                        lambda *_, **__: [])

    def fake_snapshot(item, **kwargs):
        return SimpleNamespace(to_dict=lambda: {"execution_id": item.execution_id, "job_id": kwargs["job_id"],
                                                "resource": kwargs["resource"], "event_sequence": 0})

    monkeypatch.setattr("openprogram.runtime.execution.projections.public.execution_snapshot", fake_snapshot)
    monkeypatch.setattr(lifecycle, "_actor_and_session", lambda _: ({}, "session"))
    monkeypatch.setattr("openprogram.runtime.execution.authorization.authorize_session_action", lambda *_: None)
    app = FastAPI()
    lifecycle.register(app)
    with TestClient(app) as client:
        body = client.get("/api/session/session/executions").json()
    assert sorted(listed) == ["helper-session", "session"]
    assert views == ["job-1", "job-2"]
    snapshots = {item["id"]: item["snapshot"] for item in body["items"]}
    assert snapshots["job-1"]["job_id"] == "job-1" and snapshots["job-1"]["resource"] == {"for": "job-1"}
    assert snapshots["turn-1"]["job_id"] is None and snapshots["turn-1"]["resource"] is None


def test_session_jobs_reader_reads_a_session_once(monkeypatch):
    from openprogram.runtime.jobs import store as job_store

    listed = []
    monkeypatch.setattr(job_store, "list_jobs", lambda session_id: listed.append(session_id) or [session_id])
    jobs_in = job_store.session_jobs_reader()
    assert jobs_in("a") == ["a"] and jobs_in("a") == ["a"] and jobs_in("b") == ["b"]
    assert listed == ["a", "b"]
    # A new pass reads again.
    assert job_store.session_jobs_reader()("a") == ["a"]
    assert listed == ["a", "b", "a"]


def test_tool_call_label_prefers_description_then_command():
    assert agent_loop._tool_call_label(
        "bash", {"command": "ls", "description": "List files"}) == "List files"
    assert agent_loop._tool_call_label("bash", {"command": "ls"}) == "ls"
    assert agent_loop._tool_call_label("read", {"path": "/tmp/x"}) == "/tmp/x"
    assert agent_loop._tool_call_label("weird", None) == "weird"
