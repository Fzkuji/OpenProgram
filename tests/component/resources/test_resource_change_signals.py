"""A conversation's resources list is read again when told, never on a timer:
each change signals ``conversation.resources`` for the conversations that
list it."""
from __future__ import annotations

import pytest

from openprogram.host.resources import conversation_changes
from openprogram.host._platform import changes

TOPIC = conversation_changes.TOPIC


@pytest.fixture
def heard():
    listener = changes.listen(TOPIC, "system_access.waits")
    try:
        yield lambda: listener.wait(0)
    finally:
        listener.close()


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)


def test_a_browser_page_change_names_the_conversations_that_list_it(tmp_path, heard):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    store = BrowserResourceStore(tmp_path / "session-resources.db")
    heard()
    store.retain(page_key="page-1", window_id="w", tab_id="t", title="A", target="https://a.test",
                 connection_generation=1, session_id="child", conversation_session_id="parent",
                 execution_id="exec-1")
    assert heard() == {f"{TOPIC}:child", f"{TOPIC}:parent"}

    # An operation's row is pushed to the windows as it happens.
    store.record_operation("page-1", {"id": "op-1", "phase": "dispatched"})
    store.bump_sequence("page-1")
    assert heard() == set()

    store.set_control_state("page-1", "active")
    assert heard() == {f"{TOPIC}:child", f"{TOPIC}:parent"}
    # The same state again changes nothing, so it tells nobody.
    store.set_control_state("page-1", "active", bump=False)
    assert heard() == set()

    store.mark_closed("page-1")
    assert heard() == {f"{TOPIC}:child", f"{TOPIC}:parent"}

    # A read writes nothing and tells nobody.
    store.get_resource("page-1")
    store.associations_for_page("page-1")
    assert heard() == set()


def test_a_page_no_conversation_lists_tells_nobody(tmp_path, heard):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    store = BrowserResourceStore(tmp_path / "session-resources.db")
    heard()
    store.retain(page_key="loose", window_id="w", tab_id="t", title="A", target="https://a.test",
                 connection_generation=1)
    assert heard() == set()


def test_a_machine_in_use_names_its_conversation(tmp_path, monkeypatch, heard):
    from openprogram.host.resources.session import ResourceUseStore

    monkeypatch.setattr("openprogram.host.resources.session.process_identity", lambda pid: "birth")
    store = ResourceUseStore(tmp_path / "resources.db")
    record = store.add("session-a", None, "machine", "Lab VM", "vm.test")
    assert heard() == {f"{TOPIC}:session-a"}
    store.remove(record)
    assert heard() == {f"{TOPIC}:session-a"}
    assert store.list("session-a") == []
    assert heard() == set()


def test_a_run_change_reaches_its_conversation(tmp_path, monkeypatch, heard):
    from openprogram.runtime.execution import CapabilitySet, ExecutionEventEffects, ExecutionStatus, ExecutionStore

    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", lambda frame: None)
    store = ExecutionStore(tmp_path / "runtime" / "executions.sqlite3")
    revision = store.create_revision(revision_id="revision-1", manifest={"entrypoint": "workflow.run"})
    execution = store.admit_execution(
        execution_id="run-1", run_id="run-1", session_id="conversation",
        revision_id=revision.revision_id, input_ref="blob:input", input_hash="hash",
        entrypoint="workflow.run", trusted_actor={"subject": "user-1", "session_id": "conversation"},
        config_snapshot_ref="blob:config", user_message_id="msg-1",
        capabilities=CapabilitySet(pause=True),
    )
    effects = ExecutionEventEffects(store)
    heard()
    store.transition_execution(execution.execution_id, expected_version=execution.status_version,
                               target=ExecutionStatus.RUNNING)
    effects.drain()
    assert heard() == {f"{TOPIC}:conversation", "system_access.waits:conversation"}
    # Only the Worker follows runs, so no mark is left for it.
    assert not (tmp_path / conversation_changes.MARKER).exists()


def test_a_called_jobs_change_names_the_sessions_above_it():
    from types import SimpleNamespace as Run

    runs = {
        "top": Run(execution_id="top", session_id="conversation", parent_execution_id=None),
        "agent": Run(execution_id="agent", session_id="helper", parent_execution_id=None),
        "agent-step": Run(execution_id="agent-step", session_id="helper", parent_execution_id="agent"),
        "job": Run(execution_id="job", session_id="job-session", parent_execution_id=None),
    }
    callers = {"agent": "conversation", "job": "helper"}

    class Store:
        def get_execution(self, execution_id):
            return runs.get(execution_id)

        def get_job_agent_input(self, execution_id):
            caller = callers.get(execution_id)
            return {"job_context": {"caller": {"session_id": caller}}} if caller else None

        def list_nonterminal(self, *, session_id=None):
            return [run for run in runs.values() if run.session_id == session_id]

    from openprogram.runtime.execution.conversation_scope import sessions_above_execution

    found = sessions_above_execution(Store(), runs["job"])
    assert found == ["job-session", "helper", "conversation"]


def test_another_process_leaves_the_mark_the_worker_watches(tmp_path, monkeypatch):
    monkeypatch.setattr(changes, "file_events_available", lambda: False)
    conversation_changes.changed("session-a")
    assert (tmp_path / conversation_changes.MARKER).is_file()

    (tmp_path / conversation_changes.MARKER).unlink()
    monkeypatch.setattr(changes, "file_events_available", lambda: True)
    conversation_changes.changed("session-a")
    assert not (tmp_path / conversation_changes.MARKER).exists()
