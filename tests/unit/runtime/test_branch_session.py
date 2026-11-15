"""``branch_session``: a conversation branched into a NEW session.

The store primitive copies the chain root→msg_id (with each reply's tool
and execution subtree) under fresh ids, sets the new HEAD, records the
lineage and the run configuration, and never consults or writes the
source session. The WS action and the REST route return the same
``branch_session_result`` data.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from openprogram.runtime.context.branch_session import (
    BranchSessionError,
    RUN_CONFIG_FIELDS,
    branch_session,
    copy_tip,
)
from openprogram.state.store import SessionNodeWriter
from openprogram.state.store.session.nodes import Call, ROLE_CODE, ROLE_LLM, ROLE_USER
from openprogram.state.store.session.session_store import SessionStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: s)
    yield s
    s.close()


def _seed(store: SessionStore, sid: str = "src") -> dict[str, str]:
    """Two turns on the main line, a retry sibling of turn 2, a tool call
    with a nested exec node, a spawn branch, an attach pointer onto the
    sibling, and a context snapshot anchored on turn 1's reply."""
    store.create_session(sid, "researcher", title="Trip plan")
    store.update_session(
        sid,
        provider_override="openai-codex", model_override="gpt-6.1-sol",
        thinking_effort="high", permission_mode="bypass",
        tools_override=["bash", "read"], web_search=True, sandbox_enabled=True,
        environment_id="local", additional_working_dirs=["/tmp/extra"],
        _title_gen_count=2, parent_session_id="not-copied",
    )
    w = SessionNodeWriter(store, sid)
    w.append(Call(id="ROOT", role=ROLE_USER, output="", metadata={"display": "root"}))
    w.append(Call(id="u1", role=ROLE_USER, output="hello", caller="ROOT", predecessor="ROOT"))
    w.append(Call(id="u1_reply", role=ROLE_LLM, output="hi", predecessor="u1",
                  metadata={"predecessor": "u1", "status": "completed"}))
    w.append(Call(id="ctxsp_aaaa", role=ROLE_CODE, name="context/system_prompt",
                  output="SYSTEM", caller="ROOT",
                  metadata={"display": "runtime", "anchor_head_id": "u1_reply"}))
    w.append(Call(id="u2", role=ROLE_USER, output="plan it", caller="ROOT",
                  predecessor="u1_reply"))
    w.append(Call(id="u2_reply", role=ROLE_LLM, output="planned", predecessor="u2",
                  reads=["u1", "u1_reply"],
                  metadata={"status": "completed",
                            "blocks": [{"type": "tool", "ref_node_id": "u2_reply_t_c1"}],
                            "extra": json.dumps({"blocks": [{"message_id": "u2_reply"}]})}))
    w.append(Call(id="u2_reply_t_c1", role=ROLE_CODE, name="agent", input={"task": "x"},
                  output="ok", caller="u2_reply", metadata={"tool_call_id": "c1"}))
    w.append(Call(id="exec1", role=ROLE_LLM, output="inner", caller="u2_reply_t_c1"))
    # A sub-agent branch spawned by the agent tool: not copied.
    w.append(Call(id="spawn1", role=ROLE_USER, output="sub task", caller="u2_reply_t_c1",
                  metadata={"source": "agent_spawn", "spawn_branch_root": True}))
    w.append(Call(id="spawn1_reply", role=ROLE_LLM, output="sub done", predecessor="spawn1"))
    # A retry sibling of turn 2 (another branch of the source).
    w.append(Call(id="u2b", role=ROLE_USER, output="plan it again", caller="ROOT",
                  predecessor="u1_reply", metadata={"forked_from": "u2"}))
    w.append(Call(id="u2b_reply", role=ROLE_LLM, output="planned again", predecessor="u2b",
                  metadata={"status": "completed"}))
    # Attach pointers hanging off u2_reply: one onto the sibling (still
    # readable in the source), one onto a head that does not exist.
    w.append(Call(id="att_ok", role=ROLE_LLM, output="preview", caller="u2_reply",
                  predecessor="u2_reply",
                  metadata={"function": "attach", "display": "runtime",
                            "attach": {"session_id": sid, "head_id": "u2b_reply"}}))
    w.append(Call(id="att_gone", role=ROLE_LLM, output="preview", caller="u2_reply",
                  predecessor="u2_reply",
                  metadata={"function": "attach", "display": "runtime",
                            "extra": json.dumps({"attach": {"session_id": sid,
                                                            "head_id": "missing"}})}))
    w.append(Call(id="u3", role=ROLE_USER, output="after", caller="ROOT",
                  predecessor="u2_reply"))
    w.append(Call(id="u3_reply", role=ROLE_LLM, output="later", predecessor="u3",
                  metadata={"status": "completed"}))
    store.set_head(sid, "u3_reply")
    return {"head": "u3_reply"}


def _by_output(store, sid):
    return {n.output: n for n in store.get_nodes(sid)}


def test_copies_chain_with_fresh_ids_and_remapped_edges(store):
    _seed(store)
    new = branch_session("src", "u2_reply", store=store)
    assert new != "src"

    chain = store.get_branch(new)
    assert [m["content"] for m in chain] == ["hello", "hi", "plan it", "planned"]
    # HEAD is the copy of the target.
    assert store.get_session(new)["head_id"] == chain[-1]["id"]

    nodes = _by_output(store, new)
    old_ids = {"u1", "u1_reply", "u2", "u2_reply", "u2_reply_t_c1", "exec1"}
    assert not old_ids & {n.id for n in nodes.values()}, "ids must be fresh"
    u1, r1, u2, r2 = nodes["hello"], nodes["hi"], nodes["plan it"], nodes["planned"]
    tool, inner = nodes["ok"], nodes["inner"]
    assert (u1.caller, u1.predecessor) == ("ROOT", "ROOT")
    assert r1.predecessor == u1.id and r1.id == u1.id + "_reply"
    assert u2.predecessor == r1.id and r2.predecessor == u2.id
    assert tool.caller == r2.id and tool.id == r2.id + "_t_c1"
    assert inner.caller == tool.id
    # References inside metadata / reads follow the new ids.
    assert r2.reads == [u1.id, r1.id]
    assert r2.metadata["blocks"][0]["ref_node_id"] == tool.id
    assert json.loads(r2.metadata["extra"])["blocks"][0]["message_id"] == r2.id
    assert r1.metadata["predecessor"] == u1.id
    # The context snapshot anchored on a copied reply comes along.
    ctx = nodes["SYSTEM"]
    assert ctx.metadata["anchor_head_id"] == r1.id and ctx.id.startswith("ctxsp_")

    # Not copied: later turns, the sibling branch, the spawn branch.
    outputs = set(nodes)
    for absent in ("after", "later", "plan it again", "planned again", "sub task", "sub done"):
        assert absent not in outputs
    # The attach pointer onto a readable branch stays, pointing at the
    # source; the one onto a missing head is dropped.
    attaches = [n for n in nodes.values() if (n.metadata or {}).get("function") == "attach"]
    assert len(attaches) == 1
    assert attaches[0].metadata["attach"] == {"session_id": "src", "head_id": "u2b_reply"}
    assert attaches[0].caller == r2.id


def test_lineage_title_and_run_config(store):
    _seed(store)
    new = branch_session("src", "u2_reply", store=store)
    row = store.get_session(new)
    assert row["branched_from"] == {"session_id": "src", "msg_id": "u2_reply"}
    assert row["title"] == "Trip plan (copy)"
    assert row["agent_id"] == "researcher"
    src = store.get_session("src")
    for key in ("provider_override", "model_override", "thinking_effort",
                "permission_mode", "tools_override", "web_search",
                "sandbox_enabled", "environment_id", "additional_working_dirs"):
        assert key in RUN_CONFIG_FIELDS
        assert row.get(key) == src.get(key), key
    # Sub-session wiring of the source is not inherited.
    assert row.get("parent_session_id") is None
    # The auto-titler may still rename it later, but not on its first turn.
    assert row.get("_auto_titled") is True and not row.get("_user_titled")

    from openprogram.runtime.conversation.session_config import (
        load_session_run_config, tools_override_from_config,
    )
    assert tools_override_from_config(load_session_run_config(new)) == \
        tools_override_from_config(load_session_run_config("src"))

    named = branch_session("src", "u1_reply", title="Plan B", store=store)
    named_row = store.get_session(named)
    assert named_row["title"] == "Plan B" and named_row.get("_user_titled") is True


def test_source_is_untouched(store):
    _seed(store)
    before_nodes = [(n.id, n.caller, n.predecessor) for n in store.get_nodes("src")]
    before_head = store.get_session("src")["head_id"]
    branch_session("src", "u2_reply", store=store)
    assert [(n.id, n.caller, n.predecessor) for n in store.get_nodes("src")] == before_nodes
    assert store.get_session("src")["head_id"] == before_head


def test_never_consults_run_state(store, monkeypatch):
    """A running turn in the source does not block a branch: the primitive
    must not ask the server whether a run is active."""
    _seed(store)
    from openprogram.interfaces.server import server as _srv

    def _boom(_sid):
        raise AssertionError("branch_session consulted the run state")

    monkeypatch.setattr(_srv, "_is_run_active", _boom)
    # The source's live reply being written: a further turn in flight.
    w = SessionNodeWriter(store, "src")
    w.append(Call(id="u4", role=ROLE_USER, output="more", caller="ROOT", predecessor="u3_reply"))
    w.append(Call(id="u4_reply", role=ROLE_LLM, output="", predecessor="u4",
                  metadata={"status": "running"}))
    new = branch_session("src", "u3_reply", store=store)
    assert [m["content"] for m in store.get_branch(new)][-1] == "later"


def test_branch_from_a_sub_agent_branch_starts_at_root(store):
    _seed(store)
    new = branch_session("src", "spawn1_reply", store=store)
    chain = store.get_branch(new)
    assert [m["content"] for m in chain] == ["sub task", "sub done"]
    first = _by_output(store, new)["sub task"]
    assert (first.caller, first.predecessor) == ("ROOT", "ROOT")
    assert not (first.metadata or {}).get("spawn_branch_root")


@pytest.mark.parametrize("sid,mid,code", [
    ("nope", "u1_reply", "unknown_session"),
    ("src", "missing", "unknown_message"),
    ("src", "ROOT", "unknown_message"),
    ("src", "u2_reply_t_c1", "not_a_turn"),
    ("src", "exec1", "not_a_turn"),
])
def test_rejections(store, sid, mid, code):
    _seed(store)
    count = len(store.list_sessions(limit=100, include_archived=True))
    with pytest.raises(BranchSessionError) as err:
        branch_session(sid, mid, store=store)
    assert err.value.code == code
    assert len(store.list_sessions(limit=100, include_archived=True)) == count


def test_rejects_a_reply_still_being_written(store):
    _seed(store)
    w = SessionNodeWriter(store, "src")
    w.append(Call(id="u4", role=ROLE_USER, output="more", caller="ROOT", predecessor="u3_reply"))
    w.append(Call(id="u4_reply", role=ROLE_LLM, output="", predecessor="u4",
                  metadata={"status": "running"}))
    with pytest.raises(BranchSessionError) as err:
        branch_session("src", "u4_reply", store=store)
    assert err.value.code == "reply_in_progress"


# ---- the tip of "Copy conversation" ----------------------------------------

def _running_turn(store):
    w = SessionNodeWriter(store, "src")
    w.append(Call(id="u4", role=ROLE_USER, output="more", caller="ROOT", predecessor="u3_reply"))
    w.append(Call(id="u4_reply", role=ROLE_LLM, output="", predecessor="u4",
                  metadata={"status": "running"}))
    store.set_head("src", "u4_reply")


def test_copy_tip_is_head_when_idle(store):
    _seed(store)
    assert copy_tip("src", running=False, store=store) == "u3_reply"


def test_copy_tip_skips_the_running_turn(store):
    _seed(store)
    _running_turn(store)
    assert copy_tip("src", running=True, store=store) == "u3_reply"
    store.set_head("src", "u4")  # the reply not written yet
    assert copy_tip("src", running=True, store=store) == "u3_reply"


def test_copy_tip_of_an_empty_conversation(store):
    store.create_session("empty", "main")
    with pytest.raises(BranchSessionError) as err:
        copy_tip("empty", running=False, store=store)
    assert err.value.code == "unknown_message"
    with pytest.raises(BranchSessionError) as err:
        copy_tip("nope", running=False, store=store)
    assert err.value.code == "unknown_session"


# ---- WS action + REST route ------------------------------------------------

class FakeWS:
    def __init__(self):
        self.frames: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))


@pytest.fixture
def broadcasts(monkeypatch):
    from openprogram.interfaces.server import server as _srv
    sent: list[dict] = []
    monkeypatch.setattr(_srv, "_broadcast", lambda text, *a, **k: sent.append(json.loads(text)))
    monkeypatch.setattr(_srv, "_is_run_active", lambda _sid: True)  # must not matter
    return sent


def test_ws_action_result_frame(store, broadcasts):
    from openprogram.interfaces.server.ws_actions import branch as branch_actions

    _seed(store)
    assert branch_actions.ACTIONS["branch_session"] is branch_actions.handle_branch_session
    ws = FakeWS()
    asyncio.run(branch_actions.handle_branch_session(
        ws, {"action": "branch_session", "session_id": "src", "msg_id": "u2_reply"}))
    frame = ws.frames[-1]
    assert frame["type"] == "branch_session_result"
    data = frame["data"]
    assert data["ok"] is True and data["session_id"] == "src" and data["msg_id"] == "u2_reply"
    assert data["new_session_id"] and data["title"] == "Trip plan (copy)"
    assert "error" not in data
    lists = [b for b in broadcasts if b.get("type") == "sessions_list"]
    assert lists and any(row["id"] == data["new_session_id"] for row in lists[-1]["data"])

    ws = FakeWS()
    asyncio.run(branch_actions.handle_branch_session(
        ws, {"action": "branch_session", "session_id": "src", "msg_id": "exec1"}))
    bad = ws.frames[-1]["data"]
    assert bad["ok"] is False and bad["new_session_id"] is None
    assert bad["code"] == "not_a_turn" and bad["error"]


def test_rest_route(store, broadcasts):
    from openprogram.interfaces.server.routes.chat_branches import post_chat_branch_session

    _seed(store)
    ok = asyncio.run(post_chat_branch_session({"session_id": "src", "msg_id": "u1_reply"}))
    body = json.loads(ok.body)
    assert ok.status_code == 200 and body["ok"] and body["new_session_id"]
    assert [m["content"] for m in store.get_branch(body["new_session_id"])] == ["hello", "hi"]

    missing = asyncio.run(post_chat_branch_session({"session_id": "src", "msg_id": "zzz"}))
    assert missing.status_code == 404 and json.loads(missing.body)["code"] == "unknown_message"
    internal = asyncio.run(post_chat_branch_session({"session_id": "src", "msg_id": "exec1"}))
    assert internal.status_code == 400
    empty = asyncio.run(post_chat_branch_session({}))
    assert empty.status_code == 400 and json.loads(empty.body)["code"] == "bad_request"


def test_rest_route_without_a_message_copies_up_to_head(store, broadcasts):
    """"Copy conversation": no msg_id. The fixture reports a running turn,
    so the copy stops before the turn in flight."""
    from openprogram.interfaces.server.routes.chat_branches import post_chat_branch_session

    _seed(store)
    _running_turn(store)
    response = asyncio.run(post_chat_branch_session({"session_id": "src"}))
    body = json.loads(response.body)
    assert response.status_code == 200 and body["ok"] and body["msg_id"] == "u3_reply"
    assert [m["content"] for m in store.get_branch(body["new_session_id"])][-1] == "later"
    assert body["title"] == "Trip plan (copy)"


def test_session_load_reports_lineage(store):
    from openprogram.interfaces.server.ws_actions.session.load import _branched_from

    _seed(store)
    new = branch_session("src", "u2_reply", store=store)
    assert _branched_from(store.get_session(new)) == {
        "session_id": "src", "msg_id": "u2_reply", "title": "Trip plan", "exists": True,
    }
    assert _branched_from(store.get_session("src")) is None
    store.delete_session("src")
    gone = _branched_from(store.get_session(new))
    assert gone["exists"] is False and gone["title"] == ""
