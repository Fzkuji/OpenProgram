"""Behaviour of session logs and run tables after a profile uses them."""


def _finished_turn(store, session_id):
    from openprogram.runtime.execution.model import ExecutionStatus

    revision = store.create_revision(manifest={"entrypoint": "chat"})
    record = store.create_execution(session_id=session_id, revision_id=revision.revision_id)
    store.transition_execution(record.execution_id, expected_version=record.status_version,
                               target=ExecutionStatus.INTERRUPTED, reason_code="test")
    return record.execution_id


def test_a_quiet_finished_session_leaves_memory_and_comes_back_from_its_log(tmp_path):
    from openprogram.runtime.execution.store import ExecutionStore

    store = ExecutionStore(tmp_path / "tables.sqlite3")
    finished = _finished_turn(store, "quiet")
    assert store.tables.forget_finished_sessions(60) == 0
    assert store.tables.forget_finished_sessions(-1) == 1
    assert store.tables.handle.count("executions") == 0
    assert store.get_execution(finished).status.value == "interrupted"


def test_a_session_with_unfinished_work_stays_in_memory(tmp_path):
    from openprogram.runtime.execution.store import ExecutionStore

    store = ExecutionStore(tmp_path / "tables.sqlite3")
    revision = store.create_revision(manifest={"entrypoint": "chat"})
    store.create_execution(session_id="busy", revision_id=revision.revision_id)
    assert store.tables.forget_finished_sessions(-1) == 0


def test_a_fresh_view_sees_what_the_cached_tables_cannot(tmp_path):
    from openprogram.runtime.execution.store import ExecutionStore

    store = ExecutionStore(tmp_path / "tables.sqlite3")
    revision = store.create_revision(manifest={"entrypoint": "chat"})
    created = store.create_execution(session_id="busy", revision_id=revision.revision_id)
    view = store.fresh_view()
    assert [item.execution_id for item in view.list_nonterminal()] == [created.execution_id]
    assert view.tables is not store.tables


def test_a_run_closed_by_the_move_needs_no_admission_to_finish_its_job(tmp_path):
    from openprogram.models.usage.ledger import UsageLedger
    from openprogram.runtime.execution.model import UNFINISHED_BEFORE_SESSION_LOGS, ExecutionStatus
    from openprogram.runtime.execution.store import ExecutionStore
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.runtime.jobs.runner.service import JobRunner

    store = ExecutionStore(tmp_path / "state.sqlite3")
    revision = store.create_revision(manifest={"entrypoint": "imported"})
    record = store.create_execution(
        execution_id="old-job", run_id="old-run", session_id="old-session",
        revision_id=revision.revision_id,
    )
    with store._transaction() as connection:
        store._transition_execution(
            connection, record.execution_id, expected_version=record.status_version,
            target=ExecutionStatus.INTERRUPTED, reason_code=UNFINISHED_BEFORE_SESSION_LOGS,
        )
    closed = store.get_execution("old-job")
    runner = object.__new__(JobRunner)
    runner._execution_store = store
    runner._governor = ResourceGovernor(UsageLedger(tmp_path / "usage.sqlite3"))
    runner._project_canonical_terminal(closed)


def test_a_field_without_a_column_survives_a_node_update(tmp_path):
    from openprogram.state.store.session.session_store.service import SessionStore

    store = SessionStore(tmp_path / "agent")
    store.create_session("s1", "main")
    store.append_message("s1", {"id": "a1", "role": "assistant", "content": "hi"})
    store.update_node("s1", "a1", loaded_deferred_tools=["web_search"])

    reopened = SessionStore(tmp_path / "agent")
    assert reopened.get_branch("s1")[-1]["loaded_deferred_tools"] == ["web_search"]
