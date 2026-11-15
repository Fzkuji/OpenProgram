"""Branch settings survive real HTTP checkout and metadata writers."""
import asyncio
from tests.unit.dag.test_rewind_modes import Session, store, _append, _apply
from tests.unit.dag.test_shared_workspace_rewind import _checkout_server
from openprogram.interfaces.server.routes.chat_branches import post_chat_checkout


def checkout(head, branch_id=None):
    response = asyncio.run(post_chat_checkout({"session_id": "s", "msg_id": head, "branch_id": branch_id}))
    assert not hasattr(response, "status_code") or response.status_code == 200


def test_branch_settings_restore_and_inherit_once(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    session = Session(store, "s"); session.turn_edits()
    store.update_session("s", agent_id="alpha", provider_override="p", model_override="a",
                         thinking_effort="high", web_search=True, tools_override=["read"],
                         permission_mode="ask", additional_working_dirs=["/shared"])
    _append(store, "s", "ub", "user", "ROOT"); _append(store, "s", "ab", "assistant", "ub")
    checkout("ab")
    assert store.get_session("s")["agent_id"] == "alpha"
    store.update_session("s", agent_id="beta", model_override="b", thinking_effort="low",
                         web_search=False, tools_override=[], permission_mode="bypass")
    store.update_session_dict("s", "permission_state", lambda _: {"mode": "bypass", "version": 1})
    checkout("a1")
    row = store.get_session("s")
    assert row["agent_id"] == "alpha"
    assert row["model_override"] == "a"
    assert row["thinking_effort"] == "high"
    assert row["web_search"] is True
    assert row["tools_override"] == ["read"]
    assert row.get("permission_state") is None
    assert row["permission_mode"] == "ask"
    checkout("ab")
    row = store.get_session("s")
    assert row["agent_id"] == "beta"
    assert row["tools_override"] == []
    assert row["permission_state"]["mode"] == "bypass"
    assert row["additional_working_dirs"] == ["/shared"]


def test_sibling_creation_and_reload_keep_config(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    session = Session(store, "s"); session.turn_edits()
    store.update_session("s", agent_id="alpha", tools_override=["read"])
    a = store.get_session("s")["active_branch_id"]
    _append(store, "s", "ub", "user", "ROOT")
    store.set_head("s", "ub")
    b = store.get_session("s")["active_branch_id"]
    assert a != b
    assert store.get_session("s")["agent_id"] == "alpha"
    store.update_session("s", agent_id="beta", tools_override=[])
    _append(store, "s", "ab", "assistant", "ub")
    store.set_head("s", "ab")
    assert store.get_session("s")["active_branch_id"] == b
    store.invalidate_cache("s")
    checkout("a1", a)
    assert store.get_session("s")["agent_id"] == "alpha"
    checkout("ab", b)
    assert store.get_session("s")["agent_id"] == "beta"
    assert store.get_session("s")["tools_override"] == []


def test_same_head_rewind_keeps_separate_settings(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    session = Session(store, "s"); session.turn_edits(); session.turn_edits()
    store.update_session("s", agent_id="alpha")
    _append(store, "s", "ub", "user", "a1"); _append(store, "s", "ab", "assistant", "ub")
    _apply("s", "u2", "conversation")
    a = store.get_session("s")["active_branch_id"]
    checkout("ab")
    store.update_session("s", agent_id="beta")
    _apply("s", "ub", "conversation")
    b = store.get_session("s")["active_branch_id"]
    assert a != b
    checkout("a1", a)
    assert store.get_session("s")["agent_id"] == "alpha"
    checkout("a1", b)
    assert store.get_session("s")["agent_id"] == "beta"


def test_stale_branch_setting_writes_are_atomic(store, monkeypatch):
    import pytest
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    _checkout_server(monkeypatch, store)
    session = Session(store, "s"); session.turn_edits()
    store.update_session("s", agent_id="alpha")
    a = store.get_session("s")["active_branch_id"]
    _append(store, "s", "ub", "user", "ROOT"); _append(store, "s", "ab", "assistant", "ub")
    checkout("ab")
    with pytest.raises(BranchConfigConflict):
        store.update_session("s", expected_branch_id=a, agent_id="bad")
    with pytest.raises(BranchConfigConflict):
        store.update_session_dict("s", "permission_state", lambda _: {"mode": "bypass"}, expected_branch_id=a)
    assert store.get_session("s")["agent_id"] == "alpha"
    assert store.get_session("s").get("permission_state") is None


def test_placeholder_fork_uses_first_user_identity(store):
    from openprogram.state.store.session.branch_config import branch_identity
    from openprogram.state.store.session.session_node_writer import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call
    session = Session(store, "s"); session.turn_edits()
    store.update_session("s", agent_id="alpha")
    a = store.get_session("s")["active_branch_id"]
    writer = SessionNodeWriter(store, "s")
    writer.append(Call(id="ub", role="user", output="fork", caller="ROOT", predecessor="ROOT"))
    writer.append(Call(id="ab", role="assistant", output="", predecessor="ub"))
    row = store.get_session("s")
    assert row["active_branch_id"] == branch_identity("s", "ub")
    assert row["branch_refs"][a]["head_id"] == "a1"
    assert row["agent_id"] == "alpha"


def test_background_append_cannot_restore_stale_selection(store, monkeypatch):
    from openprogram.state.store.session.session_node_writer import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call
    _checkout_server(monkeypatch, store)
    session = Session(store, "s"); session.turn_edits()
    store.update_session("s", agent_id="alpha")
    _append(store, "s", "ub", "user", "ROOT"); _append(store, "s", "ab", "assistant", "ub")
    checkout("ab")
    store.update_session("s", agent_id="beta")
    checkout("a1")
    original = store.spill_large_node
    def interleave(sid, node):
        original(sid, node)
        checkout("ab")
    monkeypatch.setattr(store, "spill_large_node", interleave)
    SessionNodeWriter(store, "s", advance_head=False).append(
        Call(id="background", role="assistant", output="done", predecessor="a1"))
    row = store.get_session("s")
    assert row["head_id"] == "ab"
    assert row["agent_id"] == "beta"
    assert store.list_sessions()[0]["agent_id"] == "beta"


def test_checkout_cannot_overwrite_concurrent_settings(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    session = Session(store, "s"); session.turn_edits()
    store.update_session("s", agent_id="alpha")
    _append(store, "s", "ub", "user", "ROOT"); _append(store, "s", "ab", "assistant", "ub")
    cas = store.compare_and_set_head
    def interleave(*args, **kwargs):
        store.update_session("s", agent_id="new-alpha")
        return cas(*args, **kwargs)
    monkeypatch.setattr(store, "compare_and_set_head", interleave)
    response = asyncio.run(post_chat_checkout({"session_id": "s", "msg_id": "ab"}))
    assert response.status_code == 409
    assert store.get_session("s")["head_id"] == "a1"
    assert store.get_session("s")["agent_id"] == "new-alpha"


def test_checkout_mirror_reads_current_selection_inside_lock(store, monkeypatch):
    from openprogram.interfaces.server import server
    from openprogram.state.store.session.rewind_view import checkout_meta
    from openprogram.interfaces.server.server_runtime.session_persistence import _set_active_head
    _checkout_server(monkeypatch, store)
    session = Session(store, "s"); session.turn_edits()
    store.update_session("s", agent_id="alpha")
    _append(store, "s", "ub", "user", "ROOT"); _append(store, "s", "ab", "assistant", "ub")
    server._sessions["s"] = {"id": "s"}
    class InterleaveLock:
        def __enter__(self):
            update = checkout_meta(store, "s", "a1")
            assert store.compare_and_set_head("s", "ab", "a1", meta_update=update)
            store.update_session("s", agent_id="new-alpha")
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(server, "_sessions_lock", InterleaveLock())
    assert _set_active_head("s", "ab")
    mirror = server._sessions["s"]
    assert mirror["agent_id"] == "new-alpha"
    assert mirror["head_id"] == "a1"
    assert [row["id"] for row in mirror["messages"]] == ["u1", "a1"]
