"""Framework helpers inherit an execution without owning standalone entries."""
from openprogram import Agent
from openprogram.runtime.context.scope import _call_id
from openprogram.state.store.session.nodes import Call, ROLE_USER
from openprogram.state.store import SessionNodeWriter, SessionStore, _store


def test_builtin_helper_without_an_execution_does_not_create_a_session(monkeypatch):
    from openprogram.programs.tools.planning.goal import state

    def forbidden_store(*args, **kwargs):
        raise AssertionError("A framework helper created a standalone session")

    monkeypatch.setattr("openprogram.state.store.SessionStore", forbidden_store)
    store_token = _store.set(None)
    call_token = _call_id.set("")
    try:
        assert state.normalize_goal({"status": "active"})["status"] == "active"
        assert _store.get() is None
        assert _call_id.get() == ""
    finally:
        _call_id.reset(call_token)
        _store.reset(store_token)


def test_builtin_helper_does_not_record_under_a_public_agent_method(tmp_path):
    from openprogram.programs.tools.planning.goal import state

    class InspectGoal(Agent):
        def inspect(self):
            return state.normalize_goal({"status": "active"})

    store = SessionStore(tmp_path / "sessions")
    store.create_session("owned", "main")
    writer = SessionNodeWriter(store, "owned")
    writer.append(Call(id="user", role=ROLE_USER, output="request"))
    store_token = _store.set(writer)
    call_token = _call_id.set("")
    try:
        # A writer alone does not make a framework lifecycle call an entry.
        state.normalize_goal({"status": "active"})
        assert len(writer.load().nodes) == 1
        assert InspectGoal().inspect()["status"] == "active"
        nodes = writer.load().nodes.values()
        parent = next(node for node in nodes if node.name.endswith("InspectGoal.inspect"))
        assert not any(node.name.endswith("state.normalize_goal") for node in nodes)
        assert parent.metadata["structural"] is True
        assert store.get_session("owned")["head_id"] == "user"
        assert [message["id"] for message in store.get_messages("owned")] == ["user"]
        assert [branch["head_msg_id"] for branch in store.list_branches("owned")] == ["user"]
        assert store.get_deepest_leaf("owned") == "user"
        assert _call_id.get() == ""
    finally:
        _call_id.reset(call_token)
        _store.reset(store_token)
        store.close()
