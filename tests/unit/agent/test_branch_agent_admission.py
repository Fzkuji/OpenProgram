"""Each branch of a conversation runs and stores its own Agent configuration.

A turn runs with the Agent, model and permission of the branch it is on,
whatever branch the page has selected; a settings write names its branch;
a setting can go back to inherit; Plan and a /skill whitelist stay where
they belong.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from tests.unit.dag.test_rewind_modes import Session, store, _append  # noqa: F401 - fixture
from tests.unit.dag.test_shared_workspace_rewind import _checkout_server


def endpoint(app, path, method):
    return next(r.endpoint for r in app.routes if r.path == path and method in r.methods)


def two_branches(store):
    """Branch ``a`` (first line) and branch ``b`` (a sibling, selected)."""
    Session(store, "s").turn_edits()
    store.update_session("s", agent_id="alpha", thinking_effort="high", sandbox_enabled=True,
                         permission_state={"mode": "bypass", "version": 1})
    a = store.get_session("s")["active_branch_id"]
    _append(store, "s", "ub", "user", "ROOT")
    store.set_head("s", "ub")
    _append(store, "s", "ab", "assistant", "ub")
    store.set_head("s", "ab")
    store.update_session("s", agent_id="beta", thinking_effort="low",
                         permission_state={"mode": "ask", "version": 1})
    b = store.get_session("s")["active_branch_id"]
    assert a != b
    return a, b


def test_a_write_to_a_branch_that_is_not_selected_changes_only_that_branch(store):
    a, b = two_branches(store)
    settings = store.update_branch_config("s", a, {"thinking_effort": "max", "sandbox_enabled": None})
    assert settings["thinking_effort"] == "max"
    assert settings["sandbox_enabled"] is None
    row = store.get_session("s")
    assert row["active_branch_id"] == b
    assert (row["agent_id"], row["thinking_effort"]) == ("beta", "low")
    assert row["branch_refs"][b]["config"]["thinking_effort"] == "low"
    assert row["branch_refs"][a]["config"]["thinking_effort"] == "max"
    assert row["branch_refs"][a]["config"]["agent_id"] == "alpha"


def test_a_write_to_a_branch_with_no_record_is_refused_as_gone(store):
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    two_branches(store)
    with pytest.raises(BranchConfigConflict) as caught:
        store.update_branch_config("s", "branch_missing", {"thinking_effort": "max"})
    assert caught.value.code == "branch_gone"


def test_branch_settings_route_targets_the_named_branch_and_clears_to_inherit(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    a, b = two_branches(store)
    from openprogram.interfaces.server.routes.execution.session_agent import register
    app = FastAPI(); register(app)
    save = endpoint(app, "/api/sessions/{session_id}/branch_settings", "POST")
    # Clearing has one spelling: ``reset_fields``. A null value is refused.
    refused = asyncio.run(save("s", {"branch_id": a, "thinking_effort": None}))
    assert refused.status_code == 400
    assert store.get_session("s")["branch_refs"][a]["config"]["thinking_effort"] == "high"
    result = asyncio.run(save("s", {"branch_id": a, "reset_fields": ["thinking_effort", "sandbox_enabled"],
                                    "service_tier": "priority"}))
    assert result.status_code == 200
    data = json.loads(result.body)
    assert (data["branch_id"], data["active_branch_id"]) == (a, b)
    assert data["settings"]["thinking_effort"] is None
    assert data["settings"]["sandbox_enabled"] is None
    assert data["settings"]["service_tier"] == "priority"
    row = store.get_session("s")
    assert (row["thinking_effort"], row.get("service_tier")) == ("low", None)
    # An old client's guard still refuses a write meant for another selection.
    stale = asyncio.run(save("s", {"expected_branch_id": a, "thinking_effort": "max"}))
    assert stale.status_code == 409
    assert json.loads(stale.body)["code"] == "branch_changed"


def test_an_agent_switch_on_another_branch_leaves_the_selected_one_running(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    a, b = two_branches(store)
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.routes.execution import session_agent
    from openprogram.runtime.conversation import always_on
    joined = []
    live = SimpleNamespace(close=lambda: joined.append("closed"))
    server._sessions["s"] = {"id": "s", "runtime": live}
    monkeypatch.setattr(always_on, "join", lambda sid: joined.append(sid))
    monkeypatch.setattr("openprogram.runtime.agent_registry.manager.get",
                        lambda agent_id: SimpleNamespace(id=agent_id, model=SimpleNamespace(provider="p", id="m")))
    status, payload = session_agent._switch("s", "gamma", {"agent_id": "gamma", "branch_id": a})
    assert status == 200
    assert (payload["branch_id"], payload["agent_id"]) == (a, "gamma")
    row = store.get_session("s")
    assert row["agent_id"] == "beta"
    assert row["branch_refs"][a]["config"]["agent_id"] == "gamma"
    # The branch follows its new Agent's model; nothing is copied onto it.
    assert row["branch_refs"][a]["config"].get("model_override") is None
    assert payload["model"] == {"provider": "p", "id": "m"}
    assert server._sessions["s"]["runtime"] is live
    assert joined == []


def test_a_turn_admitted_on_a_branch_that_is_not_selected_runs_its_agent_and_permission(store, monkeypatch):
    a, b = two_branches(store)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules",
                        lambda sid, **kwargs: ("rules", kwargs.get("branch_id")))
    from openprogram.runtime.conversation.branch_config import freeze_branch_request
    head = store.get_session("s")["branch_refs"][a]["head_id"]
    # What a conversation-level caller reads: the selected branch's.
    values = {"session_id": "s", "agent_id": "beta", "permission_mode": "ask",
              "permission_rules": ("rules", None), "branch_from": head, "user_msg_id": "new"}
    frozen = freeze_branch_request(values, branch_authority=True)
    assert frozen["branch_id"] == a
    assert frozen["agent_id"] == "alpha"
    assert frozen["permission_mode"] == "bypass"
    assert frozen["permission_rules"] == ("rules", a)
    assert frozen["thinking_effort"] == "high"
    # A Job keeps the Agent and permission it was given.
    kept = freeze_branch_request(values)
    assert (kept["agent_id"], kept["permission_mode"]) == ("beta", "ask")


def test_an_omitted_agent_and_permission_come_from_the_turns_branch(store, monkeypatch):
    a, b = two_branches(store)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules",
                        lambda sid, **kwargs: ("rules", kwargs.get("branch_id")))
    from openprogram.runtime.conversation.branch_config import freeze_branch_request
    frozen = freeze_branch_request({"session_id": "s", "agent_id": None, "permission_mode": None,
                                    "branch_id": a})
    assert (frozen["agent_id"], frozen["permission_mode"]) == ("alpha", "bypass")
    selected = freeze_branch_request({"session_id": "s", "agent_id": None, "permission_mode": None})
    assert (selected["branch_id"], selected["agent_id"], selected["permission_mode"]) == (b, "beta", "ask")


def test_a_scheduled_run_gets_its_own_branch_record_with_the_tasks_agent(store, monkeypatch):
    a, b = two_branches(store)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules",
                        lambda sid, **kwargs: ("rules", kwargs.get("branch_id")))
    from openprogram.runtime.scheduler import task_branch
    from openprogram.runtime.conversation.branch_config import freeze_branch_request
    task = {"id": "t1", "title": "Watch", "execution": {"agent_id": "watcher"}}
    first = task_branch.prepare("s", task, db=store)
    refs = store.get_session("s")["branch_refs"]
    owned = [key for key, ref in refs.items() if ref.get("scheduled_task_id") == "t1"]
    assert len(owned) == 1
    ref = refs[owned[0]]
    assert ref["head_id"] == first.msg_id
    assert ref["parent_branch_id"] == b
    assert ref["config"]["agent_id"] == "watcher"
    assert ref["config"]["thinking_effort"] == "low"
    # The user moves on: the selected branch's settings change.
    store.update_session("s", agent_id="beta2", thinking_effort="minimal",
                         permission_state={"mode": "plan", "version": 2})
    frozen = freeze_branch_request({"session_id": "s", "agent_id": "watcher", "permission_mode": "plan",
                                    "branch_from": first.parent, "user_msg_id": first.msg_id},
                                   branch_authority=True)
    assert frozen["branch_id"] == owned[0]
    assert (frozen["agent_id"], frozen["thinking_effort"], frozen["permission_mode"]) == ("watcher", "low", "ask")
    # The run's messages carry the record; the next run continues the same one.
    store.append_message("s", {"id": first.msg_id, "role": "user", "content": "go",
                               "predecessor": first.parent, "caller": "ROOT"}, advance_head=False)
    store.append_message("s", {"id": f"{first.msg_id}_reply", "role": "assistant", "content": "ok",
                               "predecessor": first.msg_id}, advance_head=False)
    assert store.get_session("s")["branch_refs"][owned[0]]["head_id"] == f"{first.msg_id}_reply"
    task_branch.finish("s", task, first, f"{first.msg_id}_reply", db=store)
    second = task_branch.prepare("s", task, db=store)
    assert second.parent == f"{first.msg_id}_reply"
    refs = store.get_session("s")["branch_refs"]
    assert [key for key, ref in refs.items() if ref.get("scheduled_task_id") == "t1"] == owned
    assert refs[owned[0]]["head_id"] == second.msg_id
    assert refs[owned[0]]["config"]["thinking_effort"] == "low"
    assert store.get_session("s")["active_branch_id"] == b


def test_plan_is_kept_per_branch():
    from openprogram.runtime.agent.turn_request import TurnRequest, set_turn_request, reset_turn_request
    from openprogram.runtime.conversation import plan_mode
    plan_mode.enter("plan-s", "a")
    try:
        assert plan_mode.is_plan_mode("plan-s", "a")
        assert not plan_mode.is_plan_mode("plan-s", "b")
        token = set_turn_request(TurnRequest("plan-s", "", "main", "web", branch_id="a"))
        try:
            assert plan_mode.is_plan_mode("plan-s")
        finally:
            reset_turn_request(token)
        token = set_turn_request(TurnRequest("plan-s", "", "main", "web", branch_id="b"))
        try:
            assert not plan_mode.is_plan_mode("plan-s")
        finally:
            reset_turn_request(token)
    finally:
        plan_mode.exit("plan-s", "a")


def chat_request(session_id, branch_id, **fields):
    from openprogram.interfaces.server.ws_actions.chat.turn_request import _ChatRequest
    values = dict(cmd={"expected_branch_id": branch_id}, text="hello", session_id=session_id,
                  agent_id=None, thinking_effort="high", tools_flag=None, web_search_flag=True,
                  permission_mode="ask", sandbox_flag=None, surface_ref=None, service_tier=None,
                  response_format=None, attachments=None)
    values.update(fields)
    return _ChatRequest(**values)


def test_a_send_stores_fast_and_not_the_skill_whitelist_or_the_agent(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    a, b = two_branches(store)
    monkeypatch.setattr("openprogram.host.environments.assign_new_session_environment", lambda *a: "local")
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.server_runtime.session_persistence import _save_session
    from openprogram.interfaces.server.ws_actions.chat.turn import _save_run_config
    # The live mirror and the real meta save that runs when the turn ends.
    monkeypatch.setattr(server, "_save_session", _save_session)
    conv = server._sessions["s"] = {"id": "s", "messages": [{"id": "ab"}]}
    req = chat_request("s", b, tools_flag=["read"], service_tier="priority")
    cfg, _ = _save_run_config(server, req, conv, "skill-turn")
    assert cfg.tools_override == ["read"]
    server._save_session("s")
    row = store.get_session("s")
    assert row.get("tools_override") is None
    assert row["branch_refs"][b]["config"].get("tools_override") is None
    assert row["service_tier"] == "priority"
    assert row["agent_id"] == "beta"
    # The versioned permission is kept; the composer's legacy mode is not written over it.
    assert row["permission_state"]["mode"] == "ask"
    cfg, _ = _save_run_config(server, chat_request("s", b, service_tier="default"), {"id": "s"}, "plain-turn")
    assert cfg.tools_override is None
    assert store.get_session("s")["service_tier"] == "default"


def test_a_permission_change_names_its_branch(store, monkeypatch):
    a, b = two_branches(store)
    from openprogram.runtime.permissions.authority import local_owner_authority
    from openprogram.runtime.permissions.state import permission_state, update_permission
    owner = {**local_owner_authority(), "interaction": "interactive"}
    store.update_branch_config("s", a, {"permission_state": {"mode": "bypass", "version": 1,
                                                             "principal_id": owner["principal_id"]}})
    update_permission("s", "acceptEdits", 1, owner, db=store, branch_id=a)
    assert permission_state("s", db=store, branch_id=a)["mode"] == "acceptEdits"
    assert permission_state("s", db=store)["mode"] == "ask"


def test_an_always_allow_grant_belongs_to_the_turns_branch(store, monkeypatch):
    a, b = two_branches(store)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    from openprogram.runtime.agent.turn_request import TurnRequest, set_turn_request, reset_turn_request
    from openprogram.runtime.permissions.session_facts import facts
    token = set_turn_request(TurnRequest("s", "", "alpha", "web", branch_id=a))
    try:
        facts().save_session_rules("s", "alpha", {"allow": ["read"], "deny": [], "ask": []})
        assert facts().session_rules("s").allow == ["read"]
    finally:
        reset_turn_request(token)
    assert not (store.get_session("s").get("permission_rules") or {}).get("allow")
    assert facts().session_rules("s") is None or facts().session_rules("s").allow == []


def test_sandbox_goes_back_to_inherit_on_the_named_branch(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    a, b = two_branches(store)
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions.session.run_config import handle_set_sandbox
    monkeypatch.setattr(server, "_broadcast", lambda *_: None)
    monkeypatch.setattr("openprogram.host.sandbox.ui_state", lambda override: {"sandbox": override})

    class Socket:
        frames = []

        async def send_text(self, frame):
            self.frames.append(json.loads(frame))

    ws = Socket()
    asyncio.run(handle_set_sandbox(ws, {"session_id": "s", "branch_id": a, "sandbox_enabled": None}))
    data = ws.frames[-1]["data"]
    assert (data["branch_id"], data["active_branch_id"], data["sandbox"]) == (a, b, None)
    row = store.get_session("s")
    assert row["branch_refs"][a]["config"]["sandbox_enabled"] is None
    assert row["sandbox_enabled"] is True


class FrameSocket:
    def __init__(self):
        self.frames = []

    async def send_text(self, frame):
        self.frames.append(json.loads(frame))


def test_a_drafts_sandbox_is_stored_once_its_model_is_picked(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions.session.run_config import handle_set_sandbox
    monkeypatch.setattr(server, "_broadcast", lambda *_: None)
    # The product default is on, so switching it off is a choice of the
    # branch's own (a value equal to the inherited one is stored as unset).
    monkeypatch.setattr("openprogram.host.sandbox.ui_state",
                        lambda override: {"sandbox": True if override is None else override})
    # A model pick gives the unsent draft a row and a branch, not a creation.
    store.update_branch_config("local_draft", None, {"provider_override": "p", "model_override": "m"})
    assert not store.is_created("local_draft")
    branch = store.get_session("local_draft")["active_branch_id"]
    ws = FrameSocket()
    asyncio.run(handle_set_sandbox(ws, {"session_id": "local_draft", "branch_id": branch,
                                        "sandbox_enabled": False}))
    assert ws.frames[-1]["data"]["sandbox"] is False
    assert store.get_session("local_draft")["sandbox_enabled"] is False
    # A draft with no row yet is echoed and gets no row.
    asyncio.run(handle_set_sandbox(ws, {"session_id": "local_new", "expected_branch_id": None,
                                        "sandbox_enabled": True}))
    assert ws.frames[-1]["data"]["sandbox"] is True
    assert store.get_session("local_new") is None


def test_a_permission_request_for_a_branch_with_no_record_is_answered_as_gone(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    a, b = two_branches(store)
    from openprogram.runtime.permissions.authority import local_owner_authority
    from openprogram.interfaces.server.ws_actions.permissions import handle_set_permission
    monkeypatch.setattr("openprogram.interfaces.server.ws_actions.runtime._trusted_runtime_actor",
                        lambda _: {**local_owner_authority(), "interaction": "interactive"})
    ws = FrameSocket()
    ws.scope = {"state": {}}
    for cmd in ({}, {"mode": "plan", "expected_version": 1}, {"mode": "bypass", "expected_version": 1}):
        asyncio.run(handle_set_permission(ws, {"session_id": "s", "branch_id": "branch_missing",
                                               "request_id": "r", **cmd}))
        data = ws.frames[-1]["data"]
        assert (data["error"], data["code"], data["active_branch_id"]) == ("branch_config_conflict", "branch_gone", b)
    assert len(ws.frames) == 3
    assert store.get_session("s")["permission_state"]["mode"] == "ask"


def test_a_model_pick_on_a_branch_that_is_not_selected_reports_that_branchs_model(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    a, b = two_branches(store)
    store.update_session("s", provider_override="p", model_override="selected-model")
    monkeypatch.setattr("openprogram.models.providers.catalog.last_used.record_last_used_model", lambda *_: None)
    from openprogram.interfaces.server.branch_settings import commit_model
    conv = {"id": "s"}
    row = commit_model("s", {"branch_id": a}, "p", "picked", conv)
    assert (row["branch_id"], row["active_branch_id"], row["model_override"]) == (a, b, "picked")
    assert conv["model_override"] == "selected-model"
    assert store.get_session("s")["model_override"] == "selected-model"
    row = commit_model("s", {"branch_id": b}, "p", "other", conv)
    assert (row["branch_id"], row["model_override"]) == (b, "other")
