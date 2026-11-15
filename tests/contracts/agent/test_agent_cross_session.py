"""Public contract for an async ``agent(start_from="SID:MSG_ID")`` spawn.

The tool writes its live attach card in the caller session while the Job runs
in the target session.  The real JobRunner is intentional here: mocking
``run_agent_turn_async`` would skip the cross-session persistence and terminal
card finalisation that this contract protects.  Only the model-facing turn is
replaced with a deterministic store write.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import threading
import time
from contextvars import copy_context

import pytest


@pytest.fixture
def cross_session_store(tmp_path, monkeypatch):
    from openprogram.runtime.context import session_db as session_db_module
    from openprogram.state.store.session.session_store import SessionStore

    # JobRunner also recovers canonical executions on construction. Isolate
    # that store with the sessions so another test's unfinished execution is
    # not recovered against this fixture's unrelated admission ledger.
    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path",
        lambda: tmp_path / "executions.db",
    )

    store = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(session_db_module, "default_store", lambda: store)
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.default_store",
        lambda: store,
    )
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: store)
    monkeypatch.setattr(
        "openprogram.operations.worker.lock.is_held_by", lambda _pid: True,
    )

    for session_id, user_id, assistant_id in (
        ("source", "source_u", "source_a"),
        ("target", "target_u", "target_a"),
    ):
        store.create_session(session_id, "main", title=session_id)
        store.append_message(session_id, {
            "id": user_id,
            "role": "user",
            "content": f"{session_id} prompt",
            "timestamp": 1,
            "predecessor": None,
        })
        store.append_message(session_id, {
            "id": assistant_id,
            "role": "assistant",
            "content": f"{session_id} reply",
            "timestamp": 2,
            "predecessor": user_id,
        })
        store.commit_turn(session_id, "initial turn")

    import openprogram.runtime.jobs.runner as runner_module

    runner_module.shutdown_runner()
    monkeypatch.setattr(runner_module.shared, "_broadcast", lambda *_a, **_k: None)
    monkeypatch.setattr(runner_module.shared, "emit_safe", lambda *_a, **_k: None)
    monkeypatch.setattr(
        runner_module.shared, "_refresh_context_stats", lambda _session_id: None,
    )
    # Completion finalisation is the subject of this test.  The automatic
    # follow-up is a separate asynchronous turn and would make HEAD assertions
    # depend on a second dispatcher invocation.
    monkeypatch.setattr(
        runner_module.JobRunner, "_dispatch_followup", lambda _self, _job: None,
    )
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", lambda _frame: None)

    try:
        yield store
    finally:
        runner_module.shutdown_runner()
        timer = store._index_timer
        store._flush_index()
        if timer is not None:
            timer.join(timeout=1)
        atexit.unregister(store._flush_index)


def _attach_payload(node) -> dict:
    metadata = dict(node.metadata or {})
    raw = metadata.get("attach") or metadata.get("extra") or {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, dict) and isinstance(raw.get("attach"), dict):
        return raw["attach"]
    return raw if isinstance(raw, dict) else {}


def _row(graph: list[dict], node_id: str) -> dict:
    return next(row for row in graph if row.get("id") == node_id)


def test_cross_session_async_spawn_finalizes_both_session_provenance(
    cross_session_store,
    monkeypatch,
):
    """A real async Job keeps the source card and target branch coherent."""
    store = cross_session_store
    source_head_before = (store.get_session("source") or {})["head_id"]
    target_head_before = (store.get_session("target") or {})["head_id"]
    executed: dict = {}

    def fake_execute(*, request, **_kwargs):
        from openprogram.runtime.conversation.sub_agent_run import AgentTurnResult

        session_id = request.session_id
        prompt = request.user_text
        agent_id = request.agent_id
        branch_from = request.branch_from
        spawn_caller = request.spawn_caller
        advance_head = request.advance_head
        spawned_from_session = request.spawned_from_session

        executed.update({
            "session_id": session_id,
            "branch_from": branch_from,
            "advance_head": advance_head,
            "spawn_caller": spawn_caller,
            "spawned_from_session": spawned_from_session,
        })
        head_before = (store.get_session(session_id) or {})["head_id"]
        from openprogram.state.store.session.nodes import Call, ROLE_USER
        from openprogram.state.store import SessionNodeWriter

        user_metadata = {
            "source": "agent_spawn",
            "agent_id": agent_id,
        }
        if spawned_from_session:
            user_metadata["spawned_from_session"] = spawned_from_session
        SessionNodeWriter(store, session_id, advance_head=False).append(Call(
            id="target_spawn_u",
            role=ROLE_USER,
            output=prompt,
            created_at=time.time(),
            predecessor=branch_from,
            caller=spawn_caller,
            metadata=user_metadata,
        ))
        store.append_message(session_id, {
            "id": "target_spawn_a",
            "role": "assistant",
            "content": "remote result",
            "timestamp": time.time(),
            "predecessor": "target_spawn_u",
            "agent_id": agent_id,
        })
        # Model the dispatcher's ``advance_head=False`` policy while keeping
        # the deterministic fake independent of dispatcher/provider setup.
        store.set_head(session_id, head_before)
        store.commit_turn(session_id, "deterministic remote agent turn")
        from openprogram.state.store.session.context_commit.store import save_commit
        from openprogram.state.store.session.context_commit.types import (
            CURRENT_RULES_VERSION,
            ContextCommit,
            ContextItem,
        )
        save_commit(store, ContextCommit(
            id="target_context_commit",
            session_id=session_id,
            commit_parent=None,
            created_at=time.time(),
            head_node_id="target_spawn_a",
            rules_version=CURRENT_RULES_VERSION,
            total_tokens=17,
            items=[ContextItem(
                source_node_id="target_spawn_a",
                role="assistant",
                rendered="remote context payload",
                tokens=17,
                state="full",
                reason="new",
            )],
        ))
        return AgentTurnResult(
            head_id="target_spawn_a",
            final_text="remote result",
        )

    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    monkeypatch.setattr(
        AgentProductionDriver, "_default_turn_runner",
        staticmethod(fake_execute),
    )

    from openprogram.runtime.execution.run_control.binding import _current_session_id
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
    from openprogram.state.store import _current_turn_id

    def invoke_agent() -> str:
        session_token = _current_session_id.set("source")
        turn_token = _current_turn_id.set("source_a")
        try:
            return _agent_impl(
                prompt="do remote work",
                description="remote-worker",
                start_from="target:target_a",
                run_in_background=True,
            )
        finally:
            _current_turn_id.reset(turn_token)
            _current_session_id.reset(session_token)

    output = copy_context().run(invoke_agent)
    match = re.search(r"execution_id=([^\s]+)", output)
    assert match is not None, output
    job_id = match.group(1)

    from openprogram.runtime.jobs import JobStatus, get_runner

    final = get_runner().await_job(job_id, timeout=5.0)
    assert final is not None
    assert final.status == JobStatus.COMPLETED
    assert final.parent_session_id == "target"
    assert final.caller_session_id == "source"
    assert final.parent_msg_id == "target_a"
    assert final.head_id == "target_spawn_a"
    assert final.attach_pointer_id

    # The card is patched just after the job turns terminal, which await_job
    # can already have seen.
    deadline = time.monotonic() + 30.0
    while True:
        source_pair = store._open("source")
        assert source_pair is not None
        source_attach = source_pair[1].nodes_by_id[final.attach_pointer_id]
        attach = _attach_payload(source_attach)
        if attach["status"] == "completed" or time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    assert attach["status"] == "completed"
    assert attach["job_id"] == job_id
    assert attach["session_id"] == "target"
    assert attach["head_id"] == "target_spawn_a"
    assert attach["source_commit_id"] == "target_context_commit"
    assert source_attach.output == "remote result"

    assert executed == {
        "session_id": "target",
        "branch_from": "target_a",
        "advance_head": False,
        "spawn_caller": "source_a",
        "spawned_from_session": "source",
    }
    assert (store.get_session("source") or {})["head_id"] == source_head_before
    assert (store.get_session("target") or {})["head_id"] == target_head_before

    target_pair = store._open("target")
    assert target_pair is not None
    target_root = target_pair[1].nodes_by_id["target_spawn_u"]
    target_metadata = dict(target_root.metadata or {})
    assert target_root.caller == "source_a"
    assert target_metadata["spawned_from_session"] == "source"

    from openprogram.state.store.session.graph.builder import build_session_graph
    from openprogram.state.store.session.graph import branches as branch_actions

    embed_stat_calls: list[tuple[str | None, str | None]] = []
    real_embed_stats = branch_actions._attach_embed_stats

    def capture_embed_stats(db, session_id, source_commit_id):
        embed_stat_calls.append((session_id, source_commit_id))
        return real_embed_stats(db, session_id, source_commit_id)

    monkeypatch.setattr(
        branch_actions, "_attach_embed_stats", capture_embed_stats,
    )

    source_graph = build_session_graph("source", source_head_before)
    target_graph = build_session_graph("target", target_head_before)
    source_row = _row(source_graph, "source_a")
    target_row = _row(target_graph, "target_spawn_u")
    assert source_row["spawn_out"] is True
    assert ("target", "target_context_commit") in embed_stat_calls
    assert real_embed_stats(
        store, "target", "target_context_commit",
    ) == (1, 17)
    assert target_row["spawn_remote"] is True
    # A remote target is not a node in the source graph, so it must not be
    # emitted as an in-session attach-return edge.
    assert "attach_returns" not in source_row

    monkeypatch.setattr(
        store,
        "list_sessions",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("cross-session attach must not scan all sessions")
        ),
    )
    from openprogram.runtime.context.persistence import rendered_history
    seen = repr(rendered_history(store, "source", head_id=final.attach_pointer_id))
    # A sub-agent's pointer carries its report, not the remote transcript.
    assert "remote result" in seen
    assert "remote context payload" not in seen


def test_cross_session_missing_node_is_rejected_before_admission(
    cross_session_store,
):
    """An exact address must name a real target node before side effects."""
    store = cross_session_store
    source_ids_before = {
        message["id"] for message in store.get_messages("source")
    }

    from openprogram.runtime.execution.run_control.binding import _current_session_id
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
    from openprogram.state.store import _current_turn_id

    session_token = _current_session_id.set("source")
    turn_token = _current_turn_id.set("source_a")
    try:
        output = _agent_impl(
            prompt="must not start",
            start_from="target:not-a-node",
            run_in_background=True,
        )
    finally:
        _current_turn_id.reset(turn_token)
        _current_session_id.reset(session_token)

    assert "[agent error]" in output
    assert "not-a-node" in output
    assert {
        message["id"] for message in store.get_messages("source")
    } == source_ids_before
    from openprogram.runtime.jobs.store import list_jobs
    assert list_jobs("source") == []
    assert list_jobs("target") == []


def test_cross_session_sync_spawn_persists_target_pointer(
    cross_session_store,
    monkeypatch,
):
    """The foreground form writes its attach in source and points to target."""
    store = cross_session_store
    source_head_before = (store.get_session("source") or {})["head_id"]
    target_head_before = (store.get_session("target") or {})["head_id"]
    captured: dict = {}

    def fake_run(**kwargs):
        from openprogram.runtime.conversation.sub_agent_run import AgentTurnResult

        captured.update(kwargs)
        store.append_message("target", {
            "id": "target_sync_a",
            "role": "assistant",
            "content": "sync result",
            "timestamp": time.time(),
            "predecessor": "target_a",
        })
        store.set_head("target", target_head_before)
        store.commit_turn("target", "deterministic sync result")
        return AgentTurnResult(
            head_id="target_sync_a",
            final_text="sync result",
        )

    monkeypatch.setattr(
        "openprogram.runtime.conversation.sub_agent_run.run_agent_turn", fake_run,
    )

    from openprogram.runtime.execution.run_control.binding import _current_session_id
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
    from openprogram.state.store import _current_turn_id

    session_token = _current_session_id.set("source")
    turn_token = _current_turn_id.set("source_a")
    try:
        output = _agent_impl(
            prompt="do synchronous remote work",
            description="sync-remote",
            start_from="target:target_a",
        )
    finally:
        _current_turn_id.reset(turn_token)
        _current_session_id.reset(session_token)

    assert "sync result" in output
    assert captured["session_id"] == "target"
    assert captured["branch_from"] == "target_a"
    assert captured["spawn_caller"] == "source_a"
    assert captured["caller_msg_id"] == "source_a"
    assert captured["caller_session_id"] == "source"
    source_attach = next(
        node for node in store._open("source")[1].all_nodes()
        if (node.metadata or {}).get("function") == "attach"
    )
    attach = _attach_payload(source_attach)
    assert attach["session_id"] == "target"
    assert attach["head_id"] == "target_sync_a"
    assert (store.get_session("source") or {})["head_id"] == source_head_before
    assert (store.get_session("target") or {})["head_id"] == target_head_before


def test_cross_session_sync_failure_persists_errored_pointer(
    cross_session_store,
    monkeypatch,
):
    """The foreground card and tool result agree on a failed child turn."""
    store = cross_session_store
    target_head_before = (store.get_session("target") or {})["head_id"]

    def fake_run(**_kwargs):
        from openprogram.runtime.conversation.sub_agent_run import AgentTurnResult

        store.append_message("target", {
            "id": "target_failed_a",
            "role": "assistant",
            "content": "provider failed",
            "timestamp": time.time(),
            "predecessor": "target_a",
            "is_error": True,
        })
        store.set_head("target", target_head_before)
        store.commit_turn("target", "deterministic failed result")
        return AgentTurnResult(
            head_id="target_failed_a",
            failed=True,
            error="provider failed",
        )

    monkeypatch.setattr(
        "openprogram.runtime.conversation.sub_agent_run.run_agent_turn", fake_run,
    )

    from openprogram.runtime.execution.run_control.binding import _current_session_id
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
    from openprogram.state.store import _current_turn_id

    session_token = _current_session_id.set("source")
    turn_token = _current_turn_id.set("source_a")
    try:
        output = _agent_impl(
            prompt="fail synchronously",
            start_from="target:target_a",
        )
    finally:
        _current_turn_id.reset(turn_token)
        _current_session_id.reset(session_token)

    assert output.startswith("<agent ") and 'status="failed"' in output
    assert "[agent error: head=" in output
    source_pair = store._open("source")
    assert source_pair is not None
    pointer = next(
        node for node in source_pair[1].all_nodes()
        if (node.metadata or {}).get("function") == "attach"
    )
    attach = _attach_payload(pointer)
    assert attach["session_id"] == "target"
    assert attach["head_id"] == "target_failed_a"
    assert attach["status"] == "errored"


def test_same_session_attach_keeps_return_edge_without_remote_markers(
    cross_session_store,
):
    """Remote markers must not alter the existing same-session projection."""
    store = cross_session_store
    source_head = (store.get_session("source") or {})["head_id"]
    store.append_message("source", {
        "id": "local_spawn_a",
        "role": "assistant",
        "content": "local result",
        "timestamp": time.time(),
        "predecessor": "source_a",
    })
    store.set_head("source", source_head)
    store.commit_turn("source", "local result")

    from openprogram.runtime.conversation.sub_agent_run import (
        AgentTurnResult,
        write_attach_pointer_for_spawn,
    )
    pointer_id = write_attach_pointer_for_spawn(
        session_id="source",
        caller_msg_id="source_a",
        result=AgentTurnResult(
            head_id="local_spawn_a",
            final_text="local result",
        ),
        label="local",
        prompt="do local work",
        chosen_agent="main",
    )
    assert pointer_id

    from openprogram.state.store.session.graph.builder import build_session_graph
    source_row = _row(build_session_graph("source", source_head), "source_a")
    assert source_row["attach_returns"] == ["local_spawn_a"]
    assert "spawn_out" not in source_row
    assert "spawn_remote" not in source_row


@pytest.mark.parametrize("terminal_status", ["errored", "cancelled"])
def test_cross_session_non_success_terminal_updates_source_card(
    cross_session_store,
    terminal_status,
):
    """Failure and cancellation close the source placeholder as well."""
    from openprogram.runtime.conversation.sub_agent_run import write_attach_placeholder_for_spawn

    pointer_id = write_attach_placeholder_for_spawn(
        session_id="source",
        target_session_id="target",
        caller_msg_id="source_a",
        label="remote-terminal",
        prompt="finish with a terminal state",
        chosen_agent="main",
        node_id=f"remote_{terminal_status}",
        job_id=f"job_{terminal_status}",
    )
    assert pointer_id

    from openprogram.runtime.jobs import get_runner
    from openprogram.runtime.jobs.types import Job, JobStatus

    status = JobStatus(terminal_status)
    get_runner()._update_attach_card(Job(
        id=f"job_{terminal_status}",
        parent_session_id="target",
        caller_session_id="source",
        caller_msg_id="source_a",
        prompt="finish with a terminal state",
        agent_id="main",
        attach_pointer_id=pointer_id,
        status=status,
        error=terminal_status,
    ))

    source_pair = cross_session_store._open("source")
    assert source_pair is not None
    pointer = source_pair[1].nodes_by_id[pointer_id]
    attach = _attach_payload(pointer)
    assert attach["status"] == terminal_status
    assert attach["session_id"] == "target"
    assert attach["job_id"] == f"job_{terminal_status}"
    assert attach.get("head_id") is None
    assert pointer.output == terminal_status


def test_running_cross_session_placeholder_is_not_expanded_into_context(
    cross_session_store,
):
    """A turn cannot consume a remote agent card before it is terminal."""
    from openprogram.runtime.conversation.sub_agent_run import write_attach_placeholder_for_spawn

    pointer_id = write_attach_placeholder_for_spawn(
        session_id="source",
        target_session_id="target",
        caller_msg_id="source_a",
        label="remote-running",
        prompt="still generating",
        chosen_agent="main",
        node_id="remote_running_pointer",
        job_id="remote_running_job",
    )
    assert pointer_id

    pointer = next(
        message for message in cross_session_store.get_messages("source")
        if message["id"] == pointer_id
    )
    from openprogram.runtime.context.persistence import rendered_history
    seen = rendered_history(cross_session_store, "source", head_id=pointer_id)
    # Until it is terminal the card reads only as running, with nothing of the remote run.
    assert seen[-1]["id"] == pointer_id and seen[-1]["content"] == "(running)"


def test_unpublished_accepted_job_recovery_publishes_cross_session_spawn(
    cross_session_store,
):
    """Recovery finishes a hand-off whose card was persisted before its
    admission was published: the submitter is gone, so the Job is published
    and runs, and the source card stays open for its result."""
    from openprogram.runtime.conversation.sub_agent_run import write_attach_placeholder_for_spawn

    pointer_id = write_attach_placeholder_for_spawn(
        session_id="source",
        target_session_id="target",
        caller_msg_id="source_a",
        label="remote-fenced",
        prompt="crash before publish",
        chosen_agent="main",
        node_id="remote_fenced_pointer",
        job_id="remote_fenced_job",
    )
    assert pointer_id

    from openprogram.runtime.jobs import get_runner
    from openprogram.runtime.jobs.types import Job, JobStatus

    runner = get_runner()
    job = Job(
        id="remote_fenced_job",
        parent_session_id="target",
        caller_session_id="source",
        caller_msg_id="source_a",
        origin_turn_id="source_a",
        relation="linked",
        creates_agent=True,
        prompt="crash before publish",
        agent_id="main",
        attach_pointer_id=pointer_id,
    )
    runner.admit_job_entity(
        job,
        creates_agent=True,
        caller_turn_id="source_a",
        dispatch_ready=False,
    )

    runner._recover_deferred_inboxes()

    recovered = runner.get_job(job.id)
    assert recovered is not None
    assert recovered.status == JobStatus.QUEUED
    assert runner._governor.dispatch_published(job.id)
    source_pair = cross_session_store._open("source")
    assert source_pair is not None
    pointer = source_pair[1].nodes_by_id[pointer_id]
    attach = _attach_payload(pointer)
    assert attach["status"] == "running"
    assert attach["session_id"] == "target"


def test_startup_reconcile_closes_cross_session_legacy_placeholder(
    cross_session_store,
):
    """A pre-governor orphan must not leave its source card running."""
    from openprogram.runtime.conversation.sub_agent_run import write_attach_placeholder_for_spawn

    pointer_id = write_attach_placeholder_for_spawn(
        session_id="source",
        target_session_id="target",
        caller_msg_id="source_a",
        label="legacy-remote",
        prompt="interrupted by restart",
        chosen_agent="main",
        node_id="legacy_remote_pointer",
        job_id="legacy_remote_job",
    )
    assert pointer_id

    from openprogram.runtime.jobs.store import save_job
    from openprogram.runtime.jobs.types import Job, JobStatus

    save_job("target", Job(
        id="legacy_remote_job",
        parent_session_id="target",
        caller_session_id="source",
        caller_msg_id="source_a",
        origin_turn_id="source_a",
        relation="linked",
        creates_agent=True,
        prompt="interrupted by restart",
        agent_id="main",
        attach_pointer_id=pointer_id,
        status=JobStatus.RUNNING,
    ))

    from openprogram.runtime.jobs import get_runner
    runner = get_runner()
    recovered = runner.get_job("legacy_remote_job")
    assert recovered is not None
    assert recovered.status == JobStatus.ERRORED

    source_pair = cross_session_store._open("source")
    assert source_pair is not None
    attach = _attach_payload(source_pair[1].nodes_by_id[pointer_id])
    assert attach["status"] == "errored"
    assert attach["session_id"] == "target"
    assert "worker died" in source_pair[1].nodes_by_id[pointer_id].output


def test_job_execution_waits_for_accepted_side_effect(
    cross_session_store,
    monkeypatch,
):
    """A durable Job is not claimable until its accepted callback returns."""
    from openprogram.runtime.conversation.sub_agent_run import AgentTurnResult

    callback_entered = threading.Event()
    callback_release = threading.Event()
    callback_done = threading.Event()
    execution_started = threading.Event()

    def fake_execute(*, request, **_kwargs):
        execution_started.set()
        return AgentTurnResult(head_id="race_head", final_text="done")

    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    monkeypatch.setattr(
        AgentProductionDriver, "_default_turn_runner",
        staticmethod(fake_execute),
    )

    def accepted(_job):
        callback_entered.set()
        assert callback_release.wait(30)
        callback_done.set()

    from openprogram.runtime.jobs import get_runner
    runner = get_runner()
    result: dict[str, str] = {}

    def submit() -> None:
        result["job_id"] = runner.spawn_job(
            session_id="target",
            prompt="wait for side effect",
            agent_id="main",
            parent_msg_id="target_a",
            on_accepted=accepted,
        )

    submit_thread = threading.Thread(target=submit)
    submit_thread.start()
    assert callback_entered.wait(30)
    started_before_callback = execution_started.wait(0.75)
    callback_release.set()
    submit_thread.join(timeout=30)
    assert not submit_thread.is_alive()
    final = runner.await_job(result["job_id"], timeout=5.0)

    assert callback_done.is_set()
    assert not started_before_callback
    assert final is not None and final.status.value == "completed"


def test_governed_worker_loss_closes_cross_session_source_card(
    cross_session_store,
    monkeypatch,
    tmp_path,
):
    """Lease recovery must update the canonical Job and its source card."""
    from openprogram.runtime.conversation.sub_agent_run import write_attach_placeholder_for_spawn

    pointer_id = write_attach_placeholder_for_spawn(
        session_id="source",
        target_session_id="target",
        caller_msg_id="source_a",
        label="governed-remote",
        prompt="worker will disappear",
        chosen_agent="main",
        node_id="governed_remote_pointer",
        job_id="governed_remote_job",
    )
    assert pointer_id

    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.store import save_job, update_job_status
    from openprogram.runtime.jobs.types import Job, JobStatus
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.models.usage.ledger import UsageLedger

    governor = ResourceGovernor(UsageLedger(tmp_path / "worker-loss.db"))
    runner = JobRunner(max_workers=1, governor=governor)
    runner.shutdown(wait=True)
    job = Job(
        id="governed_remote_job",
        parent_session_id="target",
        caller_session_id="source",
        caller_msg_id="source_a",
        origin_turn_id="source_a",
        relation="linked",
        creates_agent=True,
        prompt="worker will disappear",
        agent_id="main",
        attach_pointer_id=pointer_id,
    )
    decision = governor.admit_job(
        job,
        persist=lambda accepted: save_job("target", accepted),
        creates_agent=True,
        caller_session_id="source",
        caller_turn_id="source_a",
    )
    assert decision.accepted
    owner = f"worker_{os.getpid()}_lost"
    assert governor.try_start(job.id, owner_instance_id=owner)
    update_job_status("target", job.id, JobStatus.RUNNING)
    monkeypatch.setattr(runner, "_owner_holds_worker_lock", lambda _owner: False)

    runner._reconcile_resources()

    recovered = runner.get_job(job.id)
    assert recovered is not None
    assert recovered.status == JobStatus.ERRORED
    source_pair = cross_session_store._open("source")
    assert source_pair is not None
    pointer = source_pair[1].nodes_by_id[pointer_id]
    attach = _attach_payload(pointer)
    assert attach["status"] == "errored"
    assert attach["session_id"] == "target"
    assert "worker died" in pointer.output


def _sync_spawn(store, monkeypatch, outcome):
    """Run a real synchronous spawn; ``outcome`` plays the model turn."""
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.execution.run_control.binding import _current_session_id
    from openprogram.state.store import _current_turn_id

    seen: dict = {}

    def run_turn(*, request, **_kwargs):
        node = next(n for n in store.get_nodes("source") if (n.metadata or {}).get("function") == "attach")
        seen.update(attach=_attach_payload(node), head=(store.get_session("source") or {})["head_id"])
        return outcome(request)

    monkeypatch.setattr(AgentProductionDriver, "_default_turn_runner", staticmethod(run_turn))
    monkeypatch.setattr("openprogram.programs._host.runtime.current_tool_call_id", lambda: "call-1")
    session_token = _current_session_id.set("source")
    turn_token = _current_turn_id.set("source_a")
    try:
        seen["reply"] = _agent_impl(prompt="do work", description="worker")
    finally:
        _current_session_id.reset(session_token)
        _current_turn_id.reset(turn_token)
    attach_nodes = [n for n in store.get_nodes("source") if (n.metadata or {}).get("function") == "attach"]
    assert len(attach_nodes) == 1
    return seen, attach_nodes[0]


def test_sync_spawn_card_runs_with_its_job_and_settles(cross_session_store, monkeypatch):
    store = cross_session_store
    head = (store.get_session("source") or {})["head_id"]

    def outcome(request):
        from openprogram.runtime.conversation.sub_agent_run import AgentTurnResult

        store.append_message("source", {
            "id": "spawned", "role": "assistant", "content": "done",
            "timestamp": time.time(), "predecessor": "source_u",
        }, advance_head=False)
        return AgentTurnResult(head_id="spawned", final_text="done")

    seen, node = _sync_spawn(store, monkeypatch, outcome)

    assert seen["head"] == head
    running = seen["attach"]
    assert running["status"] == "running" and running["head_id"] is None
    assert running["job_id"] and running["tool_call_id"] == "call-1"
    assert running["delivery"] == "tool_result"
    final = _attach_payload(node)
    assert final["status"] == "completed" and final["head_id"] == "spawned"
    assert final["job_id"] == running["job_id"] and final["tool_call_id"] == "call-1"
    assert node.output == "done" and node.metadata["is_error"] is False
    assert (store.get_session("source") or {})["head_id"] == head


def test_sync_spawn_card_settles_when_the_run_fails(cross_session_store, monkeypatch):
    from openprogram.runtime.conversation.sub_agent_run import AgentTurnResult

    _seen, node = _sync_spawn(
        cross_session_store, monkeypatch,
        lambda request: AgentTurnResult(failed=True, error="provider exploded"),
    )

    final = _attach_payload(node)
    assert final["status"] == "errored" and final["job_id"]
    assert node.metadata["is_error"] is True
    assert "provider exploded" in node.output


def test_a_failed_placeholder_is_replaced_by_the_end_write(cross_session_store, monkeypatch):
    from openprogram.runtime.conversation import sub_agent_run as module

    monkeypatch.setattr(module, "write_attach_placeholder_for_spawn", lambda **_k: None)
    module.write_attach_pointer_for_spawn(
        session_id="source", caller_msg_id="source_a",
        result=module.AgentTurnResult(head_id="source_a", final_text="ok"),
        label="x", prompt="p", chosen_agent="main", node_id="card", tool_call_id="call-2",
    )

    node = next(n for n in cross_session_store.get_nodes("source") if n.id == "card")
    attach = _attach_payload(node)
    assert attach["status"] == "completed" and attach["tool_call_id"] == "call-2"


def test_a_result_with_no_head_and_no_placeholder_writes_no_row(cross_session_store):
    from openprogram.runtime.conversation import sub_agent_run as module

    assert module.write_attach_pointer_for_spawn(
        session_id="source", caller_msg_id="source_a",
        result=module.AgentTurnResult(failed=True, error="no"),
        label=None, prompt="p", chosen_agent="main", node_id="card",
    ) is None
    assert not cross_session_store.message_exists("source", "card")
