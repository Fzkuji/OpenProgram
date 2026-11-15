"""Which branch an operation acts on.

Starting again from an old point (edit, retry) opens a new branch beside the
original, also while another branch runs. A Goal's check-ins, a merge and a
queued delivery act on the branch they belong to, never on whichever branch
is selected when they run.
"""
import asyncio
from types import SimpleNamespace

import pytest

from openprogram.interfaces.server.routes import chat_branches as routes


@pytest.fixture
def two_branches(tmp_path, monkeypatch):
    """Branch A (hi -> hello, selected) and branch B (other -> o)."""
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call, ROLE_USER
    from openprogram.state.store.session.session_store import SessionStore
    import openprogram.runtime.context.session_db as session_db

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(session_db, "default_db", lambda: store)
    monkeypatch.setattr("openprogram.state.store.session.session_store.shared._default_store", store)
    sid = "s"
    store.create_session(sid, "main")
    writer = SessionNodeWriter(store, sid)
    writer.append(Call(id="ROOT", role=ROLE_USER, output="", metadata={"display": "root"}))
    writer.append(Call(id="u1", role=ROLE_USER, output="hi", caller="ROOT", predecessor="ROOT"))
    store.set_head(sid, "u1")
    store.append_message(sid, {"id": "r1", "role": "assistant", "content": "hello", "predecessor": "u1"})
    writer.append(Call(id="u2", role=ROLE_USER, output="other", caller="ROOT", predecessor="ROOT"))
    store.set_head(sid, "u2")
    store.append_message(sid, {"id": "r2", "role": "assistant", "content": "o", "predecessor": "u2"})
    store.set_head(sid, "r1")
    row = store.get_session(sid)
    branch_a = row["active_branch_id"]
    branch_b = next(key for key, ref in row["branch_refs"].items() if ref["head_id"] == "r2")
    return SimpleNamespace(store=store, sid=sid, a=branch_a, b=branch_b)


# ── (2) edit and retry open a new branch, beside a running one ─────────────

def _fork_harness(monkeypatch, store, sid, *, selected_running=False):
    from openprogram.interfaces.server import server
    import openprogram.runtime.conversation.production_driver as driver
    import openprogram.runtime.conversation.session_config as config
    from openprogram.runtime.conversation.session_config import SessionRunConfig

    captured, appended = [], []

    class Adapter:
        def __init__(self, **_kw):
            pass

        def admit(self, request, **_kw):
            captured.append(request)
            return SimpleNamespace(execution_id="exec", status_version=0)

    monkeypatch.setattr(server, "_sessions", {sid: {"id": sid, "messages": []}})
    monkeypatch.setattr(server, "_is_selected_run_active", lambda _sid: selected_running)
    # Another branch's turn is running the whole time.
    monkeypatch.setattr(server, "_is_run_active", lambda _sid: True)
    monkeypatch.setattr(server, "reserve_operation", lambda *a, **kw: None)
    monkeypatch.setattr(server, "_activate_run_reservation", lambda *a: True)
    monkeypatch.setattr(server, "_emit_running_task_event", lambda *a, **kw: None)
    monkeypatch.setattr(server, "_save_session", lambda *a: None)
    monkeypatch.setattr(server, "_append_msg", lambda _conv, message, **_kw: appended.append(message))
    monkeypatch.setattr(driver, "CanonicalAgentAdapter", Adapter)
    monkeypatch.setattr(config, "load_session_run_config", lambda *_a, **_kw: SessionRunConfig())
    monkeypatch.setattr(routes, "threading", SimpleNamespace(Thread=lambda **_kw: SimpleNamespace(start=lambda: None)))
    return captured, appended


@pytest.mark.parametrize("content", ["edited", None])
def test_edit_and_retry_open_a_new_branch_while_another_branch_runs(two_branches, monkeypatch, content):
    from openprogram.state.store.session.branch_config import branch_identity

    h = two_branches
    captured, appended = _fork_harness(monkeypatch, h.store, h.sid)
    result = routes._fork_user_turn_and_run(h.sid, "r1", content)

    assert "__error__" not in result, result
    new_id = result["msg_id"]
    assert new_id != "u1" and appended[0]["predecessor"] == "ROOT"
    request = captured[0]
    assert request.branch_id == branch_identity(h.sid, new_id)
    assert request.branch_id not in {h.a, h.b}
    assert request.branch_from == "ROOT"
    # The original stays as it was, a version beside the new one.
    assert [row["id"] for row in h.store.get_branch(h.sid, "r1")] == ["u1", "r1"]


def test_edit_waits_while_its_own_branch_runs(two_branches, monkeypatch):
    h = two_branches
    _fork_harness(monkeypatch, h.store, h.sid, selected_running=True)
    assert routes._fork_user_turn_and_run(h.sid, "r1", "edited")["__error__"][1] == 409


# ── Goals ──────────────────────────────────────────────────────────────────

def test_a_goal_reads_and_checks_in_on_its_own_branch(two_branches, monkeypatch):
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.programs.tools.planning.goal import chat, verification

    h = two_branches
    goal = {"execution_mode": "chat", "status": "active", "text": "ship it", "branch_id": h.b}
    monkeypatch.setattr(goals, "load_goal", lambda _sid: dict(goal))

    # Branch A is selected; the Goal still works on B.
    assert chat.goal_branch_tip(h.sid, goal) == "r2"
    assert [row["id"] for row in verification._goal_branch(h.sid, goal)] == ["u2", "r2"]
    assert chat.instructions(h.sid, h.a) == ""
    assert "ship it" in chat.instructions(h.sid, h.b)
    assert chat.on_goal_branch(goal, h.a) is False
    # A Goal saved before Goals recorded their branch reads the selected one.
    assert chat.goal_branch_tip(h.sid, {}) is None
    assert chat.on_goal_branch({}, h.a) is True


def test_a_goal_records_the_branch_it_was_started_on(two_branches):
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.runtime.agent.turn_request import TurnRequest, reset_turn_request, set_turn_request

    h = two_branches
    assert chat._starting_branch(h.sid) == h.a
    request = TurnRequest(session_id=h.sid, user_text="go", agent_id="main", source="web", branch_id=h.b)
    token = set_turn_request(request)
    try:
        assert chat._starting_branch(h.sid) == h.b
    finally:
        reset_turn_request(token)


def test_a_message_to_another_branch_than_the_goals_is_not_refused(two_branches, monkeypatch):
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.interfaces.server.server_runtime import session_lifecycle
    from openprogram.interfaces.server.ws_actions.chat import turn

    h = two_branches
    goal = {"execution_mode": "chat", "status": "active", "branch_id": h.a}
    monkeypatch.setattr(goals, "load_goal", lambda _sid: goal)
    monkeypatch.setattr(session_lifecycle, "side_branch_target", lambda _sid, _cmd: h.b)

    class WS:
        frames = []

        async def send_text(self, value):
            self.frames.append(value)

    req = SimpleNamespace(session_id=h.sid, cmd={}, text="hello", agent_invocation=None, side_branch_id=None)
    assert asyncio.run(turn._resolve_side_branch(WS(), req)) is True
    # A branch's Goal is its own: a message to the Goal's branch, selected
    # or not, is admitted too.
    goal["branch_id"] = h.b
    assert asyncio.run(turn._resolve_side_branch(WS(), req)) is True
    assert req.side_branch_id == h.b


# ── /merge ─────────────────────────────────────────────────────────────────

def test_a_merge_writes_to_the_branch_it_was_started_on(two_branches, monkeypatch):
    import openprogram.runtime.conversation.production_driver as driver
    from openprogram.runtime.conversation import merge

    h = two_branches
    captured = []

    class Adapter:
        def __init__(self, **_kw):
            pass

        def admit(self, request, **_kw):
            captured.append(request)
            raise RuntimeError("stop here")

    monkeypatch.setattr(driver, "CanonicalAgentAdapter", Adapter)
    # Started on A; the person has selected B since.
    h.store.update_session(h.sid, head_id="r2", active_branch_id=h.b)
    result = merge.process_merge_turn(h.sid, peers=[{"session_id": h.sid, "head_id": "r2"}],
                                      message="merge", target_branch_id=h.a)
    assert result.failed and captured[0].branch_id == h.a
    assert merge._target_branch(h.store.get_session(h.sid), h.a) == (h.a, "r1")
    assert merge._target_branch(h.store.get_session(h.sid), None) == (h.b, "r2")
    assert merge.still_selected(h.sid, h.a) is False
    assert merge.still_selected(h.sid, h.b) is True
