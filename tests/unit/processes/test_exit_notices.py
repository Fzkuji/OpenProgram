"""A background command that ends unseen starts a notice turn once its conversation is idle."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.runtime.conversation import process_notices as notices
from openprogram.host.processes.store import ProcessStore


@pytest.fixture
def store(tmp_path):
    return ProcessStore(tmp_path / "processes.db")


def _exited(store, **fields):
    record = store.create(session_id="conversation", execution_id=None, tool_call_id=None,
                          command="npm run build", cwd="/work", backend_id="local")
    store.append_output(record["id"], b"compiled 12 files\n")
    return store.update(record["id"], status="exited", exit_code=0, ended_at=record["started_at"] + 42,
                        **{"background": True, **fields})


class _Executions:
    def __init__(self, *, busy=False, chat=True):
        self.busy, self.chat = busy, chat

    def list_nonterminal(self, *, session_id):
        return [object()] if self.busy else []

    def list_for_session(self, session_id):
        return [SimpleNamespace(execution_id="previous")]

    def get_agent_turn_input(self, execution_id):
        return {"kind": "chat" if self.chat else "job", "request": {"user_text": "build it", "source": "web",
                                                                     "branch_from": "x"}}

    def get_execution_input(self, execution_id):
        return SimpleNamespace(trusted_actor={"speaker_kind": "human"}, config_snapshot_ref="session:conversation")

    def admission_execution_id(self, session_id, key):
        return "exec-" + key.replace(":", "-")


@pytest.fixture
def runtime(monkeypatch):
    import openprogram.runtime.conversation.production_driver as driver
    import openprogram.runtime.conversation.run_control as run_control
    import openprogram.runtime.execution as execution

    admitted = []

    class Adapter:
        def __init__(self, **_):
            pass

        def admit_payload(self, **kwargs):
            admitted.append(kwargs)
            return SimpleNamespace(execution_id="new")

        async def activate(self, admission):
            return None

    state = SimpleNamespace(executions=_Executions(), running=False, admitted=admitted)
    monkeypatch.setattr(driver, "CanonicalAgentAdapter", Adapter)
    monkeypatch.setattr(run_control, "is_turn_running", lambda session_id: state.running)
    monkeypatch.setattr(run_control, "is_worker_stopping", lambda: False)
    monkeypatch.setattr(execution, "default_store", lambda: state.executions)
    return state


def test_only_unseen_background_exits_are_due(store):
    due = _exited(store)
    _exited(store, reported=True)
    _exited(store, background=False)
    stopped = _exited(store)
    store.update(stopped["id"], status="stopped")
    assert [item["id"] for item in store.unreported_exits()] == [due["id"]]


def test_a_busy_conversation_gets_the_notice_after_its_turn(store, runtime):
    record = _exited(store)
    runtime.running = True
    notices.sweep(store)
    assert runtime.admitted == [] and store.get(record["id"])["reported"] is False
    runtime.running = False
    runtime.executions.busy = True
    notices.sweep(store)
    assert runtime.admitted == []
    runtime.executions.busy = False
    notices.sweep(store)
    assert len(runtime.admitted) == 1
    assert store.get(record["id"])["reported"] is True
    notices.sweep(store)
    assert len(runtime.admitted) == 1


def test_the_notice_turn_reuses_the_last_chat_request(store, runtime):
    record = _exited(store)
    notices.sweep(store)
    (call,) = runtime.admitted
    request = call["payload"]["request"]
    assert request["source"] == "process_exit"
    assert "branch_from" not in request
    assert record["id"] in request["user_text"]
    assert "npm run build" in request["user_text"]
    assert "exited with code 0 after 42 s" in request["user_text"]
    assert "compiled 12 files" in request["user_text"]
    assert call["admission_key"] == f"process_exit:{record['id']}"
    assert call["trusted_actor"] == {"speaker_kind": "human"}


def test_a_conversation_without_a_chat_is_not_notified(store, runtime):
    record = _exited(store)
    runtime.executions.chat = False
    notices.sweep(store)
    assert runtime.admitted == []
    assert store.get(record["id"])["reported"] is True
