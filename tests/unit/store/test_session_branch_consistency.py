"""Backend session/branch consistency: the in-memory mirror must never
outlive the store write that moved HEAD.

``openprogram/webui/server.py`` keeps a per-session mirror in
``_sessions[sid]`` (``head_id`` + ``messages``) and ``_save_session``
flushes it straight back into the store. So any path that moves HEAD in
the store but forgets the mirror is not merely stale — the next save
actively REVERTS the move. ``_set_active_head`` is the one correct way
to move HEAD; these tests lock that every mutating path uses it, and
that branch mutations are refused while a run is in flight.

Covered:
  1. rewind syncs the mirror (else _save_session undoes the rewind)
  2. attach/delete refresh the cached branch + mirror
  3. checkout / delete / attach / merge / rewind reject during a run
  4. no dispatcher event is silently dropped by the chat event chain
  5. reconcile_interrupted_runs clears a stuck session-row status
"""
from __future__ import annotations

import asyncio
import json

import pytest

from openprogram.store.session.session_store import SessionStore


class FakeWS:
    """Collects the frames a handler sends."""

    def __init__(self):
        self.frames: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))

    def of_type(self, t: str) -> list[dict]:
        return [f["data"] for f in self.frames if f.get("type") == t]


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: s)
    import openprogram.store.session.session_store as ss_mod
    monkeypatch.setattr(ss_mod, "_default_store", s)
    return s


@pytest.fixture
def srv(monkeypatch):
    """The real server module with its global state isolated per test."""
    from openprogram.webui import server as _s
    monkeypatch.setattr(_s, "_sessions", {})
    monkeypatch.setattr(_s, "_running_tasks", {})
    monkeypatch.setattr(_s, "_msg_cache", type(_s._msg_cache)())
    monkeypatch.setattr(_s, "refresh_context_stats", lambda *a, **k: None)
    monkeypatch.setattr(_s, "_broadcast", lambda *a, **k: None)
    return _s


def _two_turns(store: SessionStore, sid: str = "s1") -> list[str]:
    """u1 -> a1 -> u2 -> a2, head at a2. Returns the ids in order."""
    store.create_session(sid, "main", title="t")
    ids = []
    prev = ""
    for i, role in enumerate(["user", "assistant", "user", "assistant"]):
        mid = f"m{i}"
        store.append_message(sid, {
            "id": mid, "role": role, "content": f"c{i}",
            "predecessor": prev, "agent_id": "main",
        })
        prev = mid
        ids.append(mid)
    store.set_head(sid, ids[-1])
    return ids


# ---- 1. rewind must sync the in-memory mirror -------------------------

def test_rewind_syncs_mirror_so_save_session_cannot_revert_it(
    store, srv, monkeypatch,
):
    """THE defect: rewind_to wrote the store head directly, leaving
    _sessions[sid]["head_id"] on the rewound node. _save_session then
    flushed that stale head back and silently undid the rewind."""
    from openprogram.webui.ws_actions import chat as chat_actions

    ids = _two_turns(store)
    srv._sessions["s1"] = {
        "id": "s1", "head_id": ids[-1], "messages": [{"id": i} for i in ids],
    }

    # rewind to the second user turn (m2) -> head lands on m1
    monkeypatch.setattr(
        "openprogram.agent._rewind.rewind_to",
        lambda sid, tid, **_kwargs: {
            "session_id": sid, "target_msg_id": tid, "user_text": "c2",
            "turns_reverted": 1, "nodes_rewound": 2,
            "total_restored_paths": [], "new_head_id": ids[1],
            "head_changed": True, "status": "committed", "errors": [],
        },
    )
    ws = FakeWS()
    _run(chat_actions.handle_rewind(
        ws, {
            "session_id": "s1", "target_msg_id": ids[2],
            "phase": "apply", "idempotency_key": "rewind-test",
            "plan_hash": "sha256:test",
        }))

    assert ws.of_type("rewind_result")[0]["errors"] == []
    # The mirror followed the rewind...
    assert srv._sessions["s1"]["head_id"] == ids[1]
    # ...so a save flushes the REWOUND head, not the pre-rewind one.
    assert store.get_session("s1")["head_id"] == ids[1]


def test_rewind_to_returns_new_head_for_mirror_sync(store):
    """rewind_to must report where it left HEAD — without this the webui
    caller has nothing to write into its mirror."""
    from openprogram.agent._rewind import rewind_to

    ids = _two_turns(store)
    out = rewind_to("s1", ids[2])
    assert "new_head_id" in out
    assert out["new_head_id"] == store.get_session("s1")["head_id"]


def test_rewind_ws_defaults_to_read_only_plan(srv, monkeypatch):
    from openprogram.webui.ws_actions import chat as chat_actions

    monkeypatch.setattr(srv, "_is_run_active", lambda _sid: False)
    monkeypatch.setattr(
        "openprogram.agent._rewind.plan_rewind",
        lambda sid, target, **_kwargs: {
            "status": "ready", "phase": "plan", "session_id": sid,
            "target_msg_id": target, "plan_hash": "sha256:plan",
            "idempotency_key": "request-key", "head_changed": False,
        },
    )
    monkeypatch.setattr(
        "openprogram.agent._rewind.rewind_to",
        lambda *_args, **_kwargs: pytest.fail("plan request applied files"),
    )

    ws = FakeWS()
    _run(chat_actions.handle_rewind(
        ws, {"session_id": "s1", "target_msg_id": "u2"},
    ))

    result = ws.of_type("rewind_result")[0]
    assert result["status"] == "ready"
    assert result["phase"] == "plan"
    assert result["plan_hash"] == "sha256:plan"


def test_rewind_ws_apply_requires_plan_identity(srv, monkeypatch):
    from openprogram.webui.ws_actions import chat as chat_actions

    monkeypatch.setattr(srv, "_is_run_active", lambda _sid: False)
    ws = FakeWS()
    _run(chat_actions.handle_rewind(ws, {
        "session_id": "s1", "target_msg_id": "u2", "phase": "apply",
    }))

    result = ws.of_type("rewind_result")[0]
    assert result["status"] == "error"
    assert "idempotency_key and plan_hash" in result["error"]


def test_set_active_head_refreshes_messages_mirror(store, srv):
    """_set_active_head is the shared primitive; it must refresh
    conv["messages"] too, since _save_session flushes that list."""
    ids = _two_turns(store)
    srv._sessions["s1"] = {
        "id": "s1", "head_id": ids[-1], "messages": [{"id": i} for i in ids],
    }
    srv._set_active_head("s1", ids[1])
    got = [m.get("id") for m in srv._sessions["s1"]["messages"]]
    assert got == ids[:2], "messages must follow the new head"


def test_conversation_checkout_marks_workspace_mismatch(store, srv, monkeypatch):
    from openprogram.webui.ws_actions import branch as branch_actions

    ids = _two_turns(store)
    monkeypatch.setattr(srv, "_is_run_active", lambda _sid: False)
    ws = FakeWS()
    _run(branch_actions.handle_checkout_branch(ws, {
        "session_id": "s1", "head_msg_id": ids[1],
    }))

    result = ws.of_type("branch_checked_out")[0]
    alignment = store.get_session("s1")["workspace_alignment"]
    assert result["ok"] is True
    assert alignment["status"] == "mismatch"
    assert alignment["source_head_id"] == ids[-1]
    assert alignment["target_head_id"] == ids[1]


def test_checkout_head_race_keeps_head_and_alignment_unchanged(
    store, srv, monkeypatch,
):
    from openprogram.webui.ws_actions import branch as branch_actions

    ids = _two_turns(store)
    monkeypatch.setattr(srv, "_is_run_active", lambda _sid: False)
    monkeypatch.setattr(store, "compare_and_set_head", lambda *_args, **_kwargs: False)
    ws = FakeWS()

    _run(branch_actions.handle_checkout_branch(ws, {
        "session_id": "s1", "head_msg_id": ids[1],
    }))

    result = ws.of_type("branch_checked_out")[0]
    assert result["ok"] is False
    assert result["error"] == "conversation head changed during checkout"
    assert store.get_session("s1")["head_id"] == ids[-1]
    assert "workspace_alignment" not in store.get_session("s1")


def test_mismatched_workspace_blocks_chat_until_explicit_adoption(
    store, srv, monkeypatch,
):
    from openprogram.agent.workspace_alignment import mark_conversation_checkout
    from openprogram.webui.ws_actions import chat as chat_actions

    ids = _two_turns(store)
    mark_conversation_checkout("s1", ids[-1], ids[1], store=store)
    monkeypatch.setattr(
        srv, "_get_or_create_session",
        lambda sid, **_kwargs: {"id": sid},
    )
    ws = FakeWS()
    _run(chat_actions.handle_chat(ws, {"session_id": "s1", "text": "edit file"}))

    result = ws.of_type("chat_response")[0]
    assert result["code"] == "workspace_alignment_required"
    assert store.get_session("s1")["head_id"] == ids[-1]

    resolved = FakeWS()
    from openprogram.webui.ws_actions import branch as branch_actions
    _run(branch_actions.handle_resolve_workspace_alignment(resolved, {
        "session_id": "s1", "decision": "keep_current_files",
    }))
    assert resolved.of_type("workspace_alignment_resolved")[0]["ok"] is True
    assert store.get_session("s1")["workspace_alignment"]["status"] == "aligned"


# ---- 2. attach / delete must refresh cache + mirror --------------------

def test_attach_branch_invalidates_message_cache(store, srv):
    """The attach row lands in the DAG, so the cached branch list taken
    before it is stale. The attach handler previously left that cache
    (and the conv mirror) untouched, so readers kept the pre-attach view
    until a refresh."""
    from openprogram.webui.ws_actions import branch as branch_actions

    ids = _two_turns(store)
    # A second branch off m1 to attach.
    store.append_message("s1", {
        "id": "b1", "role": "assistant", "content": "side",
        "predecessor": ids[1], "agent_id": "main",
    })
    store.set_head("s1", ids[-1])
    srv._sessions["s1"] = {"id": "s1", "head_id": ids[-1], "messages": []}
    # Warm the cache so we can prove the attach drops it.
    srv._get_messages("s1")
    assert "s1" in srv._msg_cache

    ws = FakeWS()
    _run(branch_actions.handle_attach_branch(ws, {
        "session_id": "s1", "target_head_msg_id": "b1",
    }))
    res = ws.of_type("attach_branch_result")[0]
    assert res["ok"], res.get("error")
    assert "s1" not in srv._msg_cache, "attach must invalidate the cache"
    # The mirror was re-read from the store rather than left empty, so a
    # later _save_session can't flush a blank transcript over the branch.
    assert [m.get("id") for m in srv._sessions["s1"]["messages"]] == ids
    # HEAD is unchanged by an attach.
    assert store.get_session("s1")["head_id"] == ids[-1]
    # The attach row itself hangs off HEAD as a child (it is not on the
    # HEAD->root path, so get_branch correctly omits it); the chat panel
    # picks it up via the session_reload broadcast.
    assert any(m.get("id") == res["attach_node_id"]
               for m in store.get_messages("s1"))


def test_delete_branch_drops_deleted_rows_from_mirror(store, srv):
    from openprogram.webui.ws_actions import branch as branch_actions

    ids = _two_turns(store)
    srv._sessions["s1"] = {
        "id": "s1", "head_id": ids[-1], "messages": [{"id": i} for i in ids],
    }
    ws = FakeWS()
    _run(branch_actions.handle_delete_branch(
        ws, {"session_id": "s1", "head_msg_id": ids[2]}))
    res = ws.of_type("branch_deleted")[0]
    assert res["ok"], res.get("error")
    mirror_ids = {m.get("id") for m in srv._sessions["s1"]["messages"]}
    assert ids[2] not in mirror_ids and ids[3] not in mirror_ids


# ---- 3. run-active protection on every branch mutation -----------------

@pytest.mark.parametrize("action,cmd,frame,errkey", [
    ("checkout_branch", {"head_msg_id": "m1"}, "branch_checked_out", "error"),
    ("delete_branch", {"head_msg_id": "m1"}, "branch_deleted", "error"),
    ("attach_branch", {"target_head_msg_id": "m1"},
     "attach_branch_result", "error"),
])
def test_branch_actions_refuse_while_run_active(
    store, srv, monkeypatch, action, cmd, frame, errkey,
):
    from openprogram.webui.ws_actions import branch as branch_actions

    _two_turns(store)
    monkeypatch.setattr(srv, "_is_run_active", lambda sid: True)
    ws = FakeWS()
    _run(branch_actions.ACTIONS[action](ws, {"session_id": "s1", **cmd}))
    data = ws.of_type(frame)[0]
    assert data.get(errkey), f"{action} must report an error while running"
    assert "run is currently active" in data[errkey]


def test_merge_refuses_while_run_active(store, srv, monkeypatch):
    from openprogram.webui.ws_actions import merge as merge_actions

    _two_turns(store)
    monkeypatch.setattr(srv, "_is_run_active", lambda sid: True)
    ws = FakeWS()
    _run(merge_actions.handle_merge_branches(ws, {
        "session_id": "s1", "sub_sessions": ["s2"],
    }))
    data = ws.of_type("merge_branches_result")[0]
    assert data["failed"] and "run is currently active" in data["error"]


def test_merge_refuses_when_a_peer_is_running(store, srv, monkeypatch):
    """The guard covers peers, not just the target — a merge consumes the
    peer branches too."""
    from openprogram.webui.ws_actions import merge as merge_actions

    _two_turns(store)
    monkeypatch.setattr(srv, "_is_run_active", lambda sid: sid == "peer")
    ws = FakeWS()
    _run(merge_actions.handle_merge_branches(ws, {
        "session_id": "s1", "peers": [{"session_id": "peer"}],
    }))
    data = ws.of_type("merge_branches_result")[0]
    assert data["failed"] and data.get("code") == "run_active"


def test_rewind_refuses_while_run_active(store, srv, monkeypatch):
    from openprogram.webui.ws_actions import chat as chat_actions

    ids = _two_turns(store)
    monkeypatch.setattr(srv, "_is_run_active", lambda sid: True)
    ws = FakeWS()
    _run(chat_actions.handle_rewind(
        ws, {"session_id": "s1", "target_msg_id": ids[2]}))
    data = ws.of_type("rewind_result")[0]
    assert data.get("code") == "run_active"
    # HEAD untouched.
    assert store.get_session("s1")["head_id"] == ids[-1]


def test_branch_actions_still_work_when_no_run_active(store, srv, monkeypatch):
    """The guard must not block the normal path."""
    from openprogram.webui.ws_actions import branch as branch_actions

    ids = _two_turns(store)
    monkeypatch.setattr(srv, "_is_run_active", lambda sid: False)
    ws = FakeWS()
    _run(branch_actions.handle_checkout_branch(
        ws, {"session_id": "s1", "head_msg_id": ids[1]}))
    assert ws.of_type("branch_checked_out")[0]["ok"]
    assert store.get_session("s1")["head_id"] == ids[1]


# ---- 4. no dispatcher event is silently dropped ------------------------

@pytest.mark.parametrize("evt_type", [
    "compaction_started", "compaction_failed", "compaction_finished",
    "reactive_compact_started", "reactive_compact_done",
    "reactive_compact_failed", "reactive_snip",
    "some_future_event_nobody_whitelisted",
])
def test_chat_event_chain_forwards_every_envelope(monkeypatch, evt_type):
    """The if/elif chain had no else, so any envelope missing from the
    whitelist was dropped — the user saw the context ring move with no
    explanation. A catch-all forward is the fix."""
    import inspect
    from openprogram.webui._execute import chat as exec_chat

    src = inspect.getsource(exec_chat)
    assert "_on_dispatcher_event" in src
    # Structural check: the handler must end in a bare ``else`` that
    # forwards, not a final ``elif`` that drops the remainder.
    body = src.split("def _on_dispatcher_event", 1)[1].split("\n    # Carry")[0]
    assert "\n        else:\n" in body, (
        "the dispatcher-event chain needs an else branch or events get "
        "silently dropped"
    )
    forward_after_else = body.split("\n        else:\n", 1)[1]
    assert "_broadcast_chat_response" in forward_after_else


# ---- 5. worker restart must clear a stuck session-row status -----------

def test_reconcile_clears_stuck_session_row_status(store, monkeypatch):
    """A SIGKILLed worker never clears sessions.status, so the row stays
    "running" and the chat container is pinned run-active forever."""
    from openprogram.webui import _exec_dag

    _two_turns(store)
    store.update_session("s1", status="running")
    assert store.get_session("s1")["status"] == "running"

    _exec_dag.reconcile_interrupted_runs()
    assert store.get_session("s1")["status"] != "running"


def test_reconcile_leaves_idle_sessions_alone(store):
    from openprogram.webui import _exec_dag

    _two_turns(store)
    store.update_session("s1", status="idle")
    _exec_dag.reconcile_interrupted_runs()
    assert store.get_session("s1")["status"] == "idle"


def test_reconcile_finishes_a_durable_cancellation_intent(store):
    """A restart after cancel intent must finish cancelled, not interrupted."""
    from openprogram.context.nodes import Call, ROLE_CODE
    from openprogram.store import SessionNodeWriter
    from openprogram.webui import _exec_dag

    ids = _two_turns(store)
    SessionNodeWriter(store, "s1").append(Call(
        id="execution-cancelling",
        role=ROLE_CODE,
        name="cancellation_probe",
        output="partial output",
        caller=ids[-1],
        metadata={
            "status": "cancelling",
            "reason_code": "cancel.user",
            "execution_kind": "agentic_function",
        },
    ))
    store.update_session("s1", status="cancelling")

    fixed = _exec_dag.reconcile_interrupted_runs()

    node = next(
        node for node in store.get_nodes("s1")
        if node.id == "execution-cancelling"
    )
    assert fixed == 2
    assert node.metadata["status"] == "cancelled"
    assert node.metadata["reason_code"] == "cancel.user"
    assert node.output == "partial output"
    assert store.get_session("s1")["status"] == "cancelled"


def test_interrupted_exec_tree_keeps_the_restart_reason():
    from openprogram.context.nodes import Call, ROLE_CODE
    from openprogram.webui import _exec_dag

    node = Call(
        role=ROLE_CODE,
        name="agentic_workflow",
        output="[interrupted] worker restarted mid-turn",
        metadata={
            "status": "interrupted",
            "error": "Worker restarted before this turn finished",
        },
    )

    tree = _exec_dag._exec_tnode(node, {})
    assert tree["status"] == "interrupted"
    assert tree["error"] == "Worker restarted before this turn finished"


# ---- 6. failures are diagnosable, never silent -------------------------

def test_attach_failure_reports_error_and_logs(store, srv, monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(srv, "_log", lambda m: logged.append(m))
    from openprogram.webui.ws_actions import branch as branch_actions

    _two_turns(store)
    ws = FakeWS()
    # Attaching a branch onto itself is rejected.
    head = store.get_session("s1")["head_id"]
    _run(branch_actions.handle_attach_branch(ws, {
        "session_id": "s1", "target_head_msg_id": head,
    }))
    data = ws.of_type("attach_branch_result")[0]
    assert not data["ok"] and data["error"]
    assert any("attach_branch] FAILED" in m for m in logged)


def test_merge_failure_is_logged(store, srv, monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(srv, "_log", lambda m: logged.append(m))
    from openprogram.webui.ws_actions import merge as merge_actions

    ws = FakeWS()
    _run(merge_actions.handle_merge_branches(ws, {"session_id": "s1"}))
    data = ws.of_type("merge_branches_result")[0]
    assert data["failed"]
    assert any("merge_branches] FAILED" in m for m in logged)
