"""A sub-agent may do only what both its caller's turn and its own Agent allow,
and a question it raises is asked where someone waits on it.

Design: docs/reference/design/runtime/collaboration/agent-collaboration.html#permissions
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.runtime.agent_registry.manager import AgentSpec, merge_patch, validate_permissions
from openprogram.runtime.jobs.input import JobAgentInputV1
from openprogram.runtime.jobs.types import Job, JobStatus
from openprogram.runtime.permissions import delegation


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from openprogram.runtime.permissions import authority
    from openprogram.state import paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    authority._reset_owner_cache_for_tests()
    yield authority.local_owner_authority()
    authority._reset_owner_cache_for_tests()


def _own_agent(monkeypatch, permissions):
    from openprogram.runtime.agent_registry import manager

    spec = AgentSpec.from_dict({"id": "coder", "name": "Coder", "permissions": permissions})
    monkeypatch.setattr(manager, "get", lambda agent_id: spec if agent_id == "coder" else None)


# The three ways a sub-agent starts: waited on, in the background, forked
# from its caller's node.
STARTS = {
    "foreground": dict(wait=True),
    "background": dict(wait=False),
    "fork": dict(wait=True, parent_msg_id="m1", context_mode="inherit"),
}


def _spawn(owner, **start):
    return Job(id="j1", parent_session_id="p1", prompt="go", agent_id="coder",
               source="agent_spawn", **start, **owner)


@pytest.mark.parametrize("start", list(STARTS))
def test_a_stricter_caller_holds_back_an_agent_that_allows_more(owner, monkeypatch, start):
    _own_agent(monkeypatch, {"mode": "bypass", "allow": ["read", "bash"]})
    snapshot = {"mode": "ask", "rules": {"allow": ["read"], "deny": ["write"], "ask": []},
                "additional_working_dirs": ["/work/docs"]}
    request = JobAgentInputV1.from_job(_spawn(owner, **STARTS[start]),
                                       permission_snapshot=snapshot).turn_request
    assert request["permission_mode"] == "ask"
    # The Agent's own allow rule for bash allows nothing its caller did not.
    assert request["permission_rules"] == {"allow": ["read"], "deny": ["write"], "ask": []}
    assert request["additional_working_dirs"] == ["/work/docs"]
    assert request.get("sandbox_enabled") is None


@pytest.mark.parametrize("start", list(STARTS))
def test_a_stricter_agent_narrows_a_caller_that_allows_more(owner, monkeypatch, start):
    _own_agent(monkeypatch, {"mode": "plan", "deny": ["bash"], "ask": ["web_fetch"],
                             "additional_directories": ["/work/a"], "sandbox": True})
    snapshot = {"mode": "bypass", "rules": {"allow": ["read"], "deny": [], "ask": []},
                "additional_working_dirs": ["/work/a", "/work/b"], "sandbox_enabled": False}
    request = JobAgentInputV1.from_job(_spawn(owner, **STARTS[start]),
                                       permission_snapshot=snapshot).turn_request
    assert request["permission_mode"] == "plan"
    assert request["permission_rules"] == {"allow": ["read"], "deny": ["bash"], "ask": ["web_fetch"]}
    assert request["additional_working_dirs"] == ["/work/a"]
    assert (request["sandbox_enabled"], request["sandbox_config_frozen"]) == (True, True)
    turn = JobAgentInputV1.parse(JobAgentInputV1.from_job(
        _spawn(owner, **STARTS[start]), permission_snapshot=snapshot).to_dict()).to_turn_request()
    assert turn.permission_mode == "plan" and turn.permission_rules.deny == ["bash"]


def test_an_agent_that_sets_nothing_runs_as_its_caller(owner, monkeypatch):
    _own_agent(monkeypatch, {})
    snapshot = {"mode": "acceptEdits", "rules": {"allow": ["read"], "deny": [], "ask": []},
                "additional_working_dirs": ["/work"], "sandbox_enabled": True}
    request = JobAgentInputV1.from_job(_spawn(owner, wait=False), permission_snapshot=snapshot).turn_request
    assert request["permission_mode"] == "acceptEdits"
    assert request["permission_rules"] == snapshot["rules"]
    assert request["additional_working_dirs"] == ["/work"]
    assert request["sandbox_enabled"] is True


def test_the_mode_order_and_a_mode_nobody_set():
    assert delegation.MODE_ORDER == ("plan", "ask", "acceptEdits", "auto", "bypass")
    assert delegation.stricter_mode("bypass", "acceptEdits") == "acceptEdits"
    assert delegation.stricter_mode("ask", "auto") == "ask"
    assert delegation.stricter_mode("auto", None) == "auto"
    assert delegation.stricter_mode("", "nonsense") is None


def test_an_agents_permissions_are_validated_and_replaced_whole():
    assert validate_permissions(None) == {}
    assert validate_permissions({"mode": "acceptedits", "deny": [" bash ", "bash"], "sandbox": False}) == {
        "mode": "acceptEdits", "deny": ["bash"], "sandbox": False}
    for bad in ({"mode": "yolo"}, {"deny": "bash"}, {"sandbox": "on"}, {"model": "x"}, []):
        with pytest.raises(ValueError):
            validate_permissions(bad)
    spec = AgentSpec.from_dict({"id": "coder", "permissions": {"mode": "plan", "deny": ["bash"]}})
    assert AgentSpec.from_dict(spec.to_dict()).permissions == {"mode": "plan", "deny": ["bash"]}
    assert merge_patch(spec, {"permissions": {"deny": ["write"]}}).permissions == {"deny": ["write"]}
    assert AgentSpec.from_dict({"id": "old"}).permissions == {}


# --- questions --------------------------------------------------------------

def _sub_agent_request(owner, *, asks_through=None, mode="ask"):
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.permissions.authority import runtime_authority

    return TurnRequest(session_id="s1", user_text="go", agent_id="main", source="agent_spawn",
                       permission_mode=mode, asks_through=asks_through,
                       **runtime_authority(owner, "agent_spawn"))


def _tool(name="bash"):
    from openprogram.models.providers.types import AgentTool, AgentToolResult, TextContent

    async def execute(_call_id, _args, _cancel, _on_update):
        return AgentToolResult(content=[TextContent(text="RAN")])

    return AgentTool(name=name, description=name, parameters={}, label=name, execute=execute)


ROUTE = {"session_id": "s1", "execution_id": "e-parent", "label": "scout"}


def test_a_foreground_sub_agent_asks_on_its_callers_card_named(owner):
    from openprogram.runtime.permissions.approvals.approval import wrap_with_approval
    from openprogram.runtime.permissions.policy import can_approve, permission_decision

    request = _sub_agent_request(owner, asks_through=ROUTE)
    assert can_approve(request)
    assert permission_decision(_tool(), request, {"command": "make"}).outcome == "ask"
    manifest = wrap_with_approval(_tool(), request, lambda _event: None, _live=False)._interaction_manifest(
        "c1", {"command": "make"})
    assert "scout" not in manifest["prompt"], "the card names the asker beside the request, not in its text"
    assert manifest["request_metadata"]["asked_by"] == "scout"


def test_a_background_sub_agent_is_refused_at_once_with_what_to_do(owner):
    from openprogram.runtime.permissions.approvals.approval import wrap_with_approval
    from openprogram.runtime.permissions.policy import can_approve, permission_decision

    request = _sub_agent_request(owner)
    assert not can_approve(request)
    decision = permission_decision(_tool(), request, {"command": "make"})
    assert (decision.outcome, decision.code) == ("deny", "HELPER_CANNOT_ASK")
    assert "background" in decision.refusal.text and "final reply" in decision.refusal.text
    # No card is opened, so nothing waits.
    assert wrap_with_approval(_tool(), request, lambda _event: None, _live=False)._interaction_manifest(
        "c1", {"command": "make"}) is None
    # A route on a turn that is not a sub-agent's grants nothing.
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.permissions.authority import runtime_authority
    other = TurnRequest(session_id="s1", user_text="", agent_id="main", source="cron",
                        asks_through=ROUTE, **runtime_authority(owner, "cron"))
    assert not can_approve(other)


def test_the_mode_decides_before_any_question(owner):
    from openprogram.runtime.permissions.policy import permission_decision

    # Bypass asks nothing, so a background sub-agent runs as its caller would.
    assert permission_decision(_tool(), _sub_agent_request(owner, mode="bypass"),
                               {"command": "make"}).outcome == "allow"


class _Inputs:
    def __init__(self, values, kind="chat"):
        self.values, self.kind = values, kind

    def get_job_agent_input(self, _execution_id):
        return None

    def get_agent_turn_input(self, _execution_id):
        return {"kind": self.kind, "request": self.values}


PARENT = SimpleNamespace(session_id="p1", execution_id="e-parent")
SNAPSHOT = {"mode": "ask", "rules": {}}


def _child(**fields):
    base = dict(id="j1", parent_session_id="p1", prompt="go", agent_id="main", source="agent_spawn",
                label="scout", wait=True)
    return Job(**{**base, **fields})


def test_only_a_waited_on_sub_agent_of_a_person_asks_through_its_caller():
    from openprogram.runtime.permissions.approvals.lifecycle import spawn_question_route

    person = _Inputs({"interaction": "interactive", "speaker_kind": "owner"})
    assert spawn_question_route(person, PARENT, _child(), SNAPSHOT) == {
        "session_id": "p1", "execution_id": "e-parent", "label": "scout"}
    assert spawn_question_route(person, PARENT, _child(parent_msg_id="m1"), SNAPSHOT)["label"] == "scout"
    # Forked into another conversation: it asks in its caller's, where the person waits.
    assert spawn_question_route(person, PARENT, _child(parent_session_id="other", caller_session_id="p1"),
                                SNAPSHOT) == {"session_id": "p1", "execution_id": "e-parent", "label": "scout"}
    # Background, a delivered message, a caller in another conversation, no
    # frozen policy: nobody there waits on it.
    for child in (_child(wait=False), _child(message_origin="agent_delivery"),
                  _child(parent_session_id="other", caller_session_id="elsewhere")):
        assert spawn_question_route(person, PARENT, child, SNAPSHOT) is None
    assert spawn_question_route(person, PARENT, _child(), None) is None
    # A caller no person answers for (a channel, a scheduled run) passes nobody on.
    for values in ({"interaction": "non-interactive", "speaker_kind": "runtime"},
                   {"interaction": "interactive", "speaker_kind": "owner", "detached": True}):
        assert spawn_question_route(_Inputs(values), PARENT, _child(), SNAPSHOT) is None


def test_the_route_is_kept_in_the_stored_input(owner, monkeypatch):
    _own_agent(monkeypatch, {})
    stored = JobAgentInputV1.from_job(_spawn(owner, wait=True), permission_snapshot=SNAPSHOT,
                                      asks_through=ROUTE)
    assert JobAgentInputV1.parse(stored.to_dict()).to_turn_request().asks_through == ROUTE


class _Runner:
    """A waited-on sub-agent that stops at a question, is answered, and finishes."""

    def __init__(self, states):
        self.states = list(states)
        self._execution_store = SimpleNamespace(
            get_job_agent_input=lambda _id: {"turn_request": {"asks_through": ROUTE}})

    def _now(self):
        return self.states[0]

    def get_job(self, _job_id):
        if len(self.states) > 1:
            self.states.pop(0)
        status = JobStatus.COMPLETED if self._now() == "done" else JobStatus.RUNNING
        return Job(id="j1", parent_session_id="p1", prompt="go", agent_id="main", status=status)

    def stopped_execution(self, _job_id):
        if self._now() != "asking":
            return None
        return SimpleNamespace(status=SimpleNamespace(value="paused"), reason_code="wait_open")

    def await_job_durable(self, job_id, on_poll=None):
        while self._now() == "running":
            self.get_job(job_id)
        return self.get_job(job_id)


def test_the_caller_waits_through_its_sub_agents_question(monkeypatch):
    from openprogram.runtime.conversation.peer_agents import sub_agent_run

    monkeypatch.setattr(sub_agent_run, "ANSWER_POLL_S", 0)
    runner = _Runner(["asking", "asking", "asking", "running", "done"])
    job = runner.get_job("j1")
    assert sub_agent_run._asking_the_person(runner, job)
    while sub_agent_run._asking_the_person(runner, job):
        job = sub_agent_run._await_answer(runner, "j1")
    assert job.status == JobStatus.COMPLETED


def test_a_pause_with_no_question_route_ends_the_wait():
    from openprogram.runtime.conversation.peer_agents import sub_agent_run

    runner = _Runner(["asking"])
    runner._execution_store = SimpleNamespace(get_job_agent_input=lambda _id: {"turn_request": {}})
    assert not sub_agent_run._asking_the_person(runner, runner.get_job("j1"))


def test_an_unreadable_permissions_block_in_a_trial_profile_refuses_the_start(owner):
    from openprogram.runtime.jobs.input import JobAgentInputError

    job = _spawn(owner, wait=True, profile_snapshot={"id": "coder", "permissions": {"mode": "yolo"}})
    with pytest.raises(JobAgentInputError):
        JobAgentInputV1.from_job(job, permission_snapshot=SNAPSHOT)


def test_a_sandbox_the_callers_project_turns_on_travels_with_the_snapshot(monkeypatch):
    from types import SimpleNamespace

    from openprogram.host.sandbox import MODE_WORKSPACE_WRITE
    from openprogram.runtime.permissions.approvals import lifecycle
    from openprogram.state.store.project import project_store

    calls = []

    def project_for_session(session_id):
        calls.append(session_id)
        return SimpleNamespace(id="project-1")

    monkeypatch.setattr(project_store, "project_for_session", project_for_session)
    monkeypatch.setattr(project_store, "load_project_settings",
                        lambda project_id: {"sandbox_mode": MODE_WORKSPACE_WRITE})
    values = {"sandbox_config_frozen": True, "sandbox_enabled": None}
    snapshot = lifecycle._caller_sandbox(values, "p1")
    assert snapshot is True and calls == ["p1"]
    assert lifecycle._caller_sandbox({"sandbox_config_frozen": True, "sandbox_enabled": False}, "p1") is False


def test_a_forked_helpers_card_is_shown_in_its_callers_conversation():
    from openprogram.runtime.permissions.approvals.approval import _asker_metadata
    elsewhere = SimpleNamespace(source="agent_spawn", session_id="fork-target", asks_through=ROUTE)
    assert _asker_metadata(elsewhere)["asked_in_session"] == "s1"
    here = SimpleNamespace(source="agent_spawn", session_id="s1", asks_through=ROUTE)
    assert "asked_in_session" not in _asker_metadata(here)


# ---- what a helper never gets ---------------------------------------------------

def _tools(*names):
    return [SimpleNamespace(name=name) for name in names]


def test_a_helper_loses_the_scheduling_channel_and_memory_writing_tools():
    from openprogram.programs.tools.agents.send_message.send_message import depth
    from openprogram.runtime.conversation.turn_runtime.loop_runner import DELEGATE_BLOCKED, delegate_blocked
    # Every registered scheduling, channel-sending and memory-writing tool,
    # old names included (``cron`` is the scheduler's alias).
    assert {"scheduler", "cron", "remind", "save_routine", "always_on_schedule",
            "chat_send", "memory_update"} <= DELEGATE_BLOCKED
    tools = _tools("read", "edit", *sorted(DELEGATE_BLOCKED))
    token = depth.set_helper_turn(True)
    try:
        kept, unavailable = delegate_blocked(tools, {})
        assert [t.name for t in kept] == ["read", "edit"] and unavailable == {}
        # A forked helper keeps its caller's list (shared prompt cache): the
        # same tools stay listed and every call is refused.
        forked = depth.set_forked_turn(True)
        try:
            kept, unavailable = delegate_blocked(tools, {})
        finally:
            depth._forked.reset(forked)
        assert len(kept) == len(tools) and set(unavailable) == DELEGATE_BLOCKED
    finally:
        depth._helper.reset(token)
    # The conversation's own turn, or a message delivered to it, keeps them.
    assert delegate_blocked(tools, {}) == (tools, {})
