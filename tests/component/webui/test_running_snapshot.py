"""GET /api/running snapshot — in-flight tool calls show up as kind=tool."""
import importlib
import time
from types import SimpleNamespace

import pytest

agent_loop = importlib.import_module("openprogram.agent.agent_loop")
from openprogram_server._webui.routes import lifecycle, running


@pytest.fixture(autouse=True)
def _runner_lifecycle():
    """Close the singleton created while collecting the running snapshot."""
    from openprogram.agent.job import runner as runner_mod

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
    from openprogram.execution.projections import ExecutionProjectionRecord

    projection = ExecutionProjectionRecord(
        projection_kind="ui",
        event_sequence=7,
        execution_id="exec-canonical",
        session_id="session-canonical",
        status="running",
        payload={
            "execution": {"created_at": 123.0},
            "ui": {"label": "workflow.run"},
        },
        created_at=123.0,
    )
    monkeypatch.setattr(
        "openprogram.execution.projections.list_running_execution_projections",
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
    from openprogram.agent.job.types import JobStatus

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
    monkeypatch.setattr("openprogram.agent.job.runner.get_runner", lambda: runner)
    monkeypatch.setattr(
        "openprogram.execution.default_store",
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
        "openprogram.execution.public.execution_snapshot", fake_snapshot,
    )

    item = next(item for item in running._collect() if item["id"] == job.id)
    assert item["snapshot"]["resource"] == resource
    assert "job_id" not in item["snapshot"]["resource"]


def test_execution_payload_embeds_only_the_nested_job_resource(monkeypatch):
    resource = {"resource_state": "active"}
    execution = SimpleNamespace(execution_id="job-resource")
    view = SimpleNamespace(resource=resource)
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: SimpleNamespace(
        get_job_resource_view=lambda _job_id: view,
    ))
    monkeypatch.setattr(
        "openprogram.execution.default_store", lambda: SimpleNamespace(),
    )

    def fake_snapshot(_execution, **kwargs):
        assert kwargs["resource"] is resource
        return SimpleNamespace(to_dict=lambda: {"resource": kwargs["resource"]})

    monkeypatch.setattr(
        "openprogram.execution.public.execution_snapshot", fake_snapshot,
    )
    assert lifecycle._execution_payload(execution) == {"resource": resource}


def test_tool_call_label_prefers_description_then_command():
    assert agent_loop._tool_call_label(
        "bash", {"command": "ls", "description": "List files"}) == "List files"
    assert agent_loop._tool_call_label("bash", {"command": "ls"}) == "ls"
    assert agent_loop._tool_call_label("read", {"path": "/tmp/x"}) == "/tmp/x"
    assert agent_loop._tool_call_label("weird", None) == "weird"
