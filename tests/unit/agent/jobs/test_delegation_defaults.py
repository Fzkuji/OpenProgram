"""The default bounds on delegated agents and the one result shape.

All fake: Jobs are built in memory, the ledger is a stub, the clock is a
number. See docs/reference/design/runtime/collaboration/agent-collaboration.html.
"""
from __future__ import annotations


import pytest

from openprogram.runtime.jobs import agent_result, delegation_watch
from openprogram.runtime.jobs.types import Job, JobStatus


def _job(job_id: str, **kw) -> Job:
    base = dict(id=job_id, parent_session_id="p1", prompt="task", agent_id="main",
                status=JobStatus.RUNNING, caller_session_id="p1", caller_msg_id="a1",
                origin_turn_id="a1")
    base.update(kw)
    return Job(**base)


class _Ledger:
    def __init__(self, costs: dict[str, list[str]], unknown: set[str] = frozenset()):
        self.costs, self.unknown = costs, unknown

    def job_resource_usage(self, job_id):
        return {"cost_values": self.costs.get(job_id, []), "total_tokens": 1200,
                "unknown_cost_events": 1 if job_id in self.unknown else 0}


@pytest.fixture
def config(monkeypatch):
    values: dict = {}
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {"agent": values})
    return values


def _check(jobs, *, now, ledger=None, entries=None, already=None):
    by_id = {j.id: j for j in jobs}
    cancelled, stalled = [], []
    delegation_watch.check_running(
        entries if entries is not None else [(j.id, j.parent_session_id, 0.0, 0.0) for j in jobs],
        now=now, load=lambda _sid, jid: by_id.get(jid), ledger=ledger or _Ledger({}),
        cancel=lambda jid, reason: cancelled.append((jid, reason)),
        stalled=lambda job: stalled.append(job.id),
        already=already if already is not None else {},
    )
    return cancelled, stalled


def test_defaults_are_the_documented_ones(config):
    from openprogram.programs.tools.agents.send_message.send_message import depth
    from openprogram.runtime.jobs.resource_governance.limits import running_agents_default

    assert delegation_watch.tree_cap_usd() == 2.0
    assert delegation_watch.stall_seconds() == 600
    assert delegation_watch.max_run_seconds() == 7200
    assert depth.max_spawn_depth() == 1
    assert depth.max_hops() == 4 and depth.max_sends_per_hour() == 20
    assert running_agents_default() == 6


def test_a_quiet_helper_is_reported_stalled_once_and_keeps_running(config):
    helper = _job("j1")
    already: dict = {}
    cancelled, stalled = _check([helper], now=601, already=already)
    assert stalled == ["j1"] and cancelled == []
    _, again = _check([helper], now=1200, already=already)
    assert again == []


def test_a_helper_past_the_hard_stop_is_stopped(config):
    cancelled, _ = _check([_job("j1")], now=7200)
    assert cancelled == [("j1", delegation_watch.RUNTIME_REASON)]


def test_settings_change_the_bounds_and_zero_turns_one_off(config):
    config.update(max_agent_hours=0, stall_minutes=1)
    cancelled, stalled = _check([_job("j1")], now=10 * 3600)
    assert cancelled == [] and stalled == ["j1"]


def test_a_tree_past_its_spend_stops_its_running_helpers(config, monkeypatch):
    jobs = [_job("j1"), _job("j2")]
    monkeypatch.setattr(delegation_watch, "tree_jobs", lambda sid, turn: jobs)
    ledger = _Ledger({"j1": ["1.50"], "j2": ["0.60"]})
    entries = [(j.id, "p1", 0.0, 100.0) for j in jobs]
    cancelled, _ = _check(jobs, now=101, ledger=ledger, entries=entries)
    assert sorted(cancelled) == [("j1", delegation_watch.TREE_COST_REASON),
                                 ("j2", delegation_watch.TREE_COST_REASON)]


def test_a_tree_under_its_spend_keeps_running(config, monkeypatch):
    jobs = [_job("j1")]
    monkeypatch.setattr(delegation_watch, "tree_jobs", lambda sid, turn: jobs)
    cancelled, _ = _check(jobs, now=1, ledger=_Ledger({"j1": ["1.99"]}),
                          entries=[("j1", "p1", 0.0, 1.0)])
    assert cancelled == []


def test_a_spawn_past_the_tree_spend_is_refused(config, monkeypatch):
    monkeypatch.setattr(delegation_watch, "tree_jobs", lambda sid, turn: [_job("j1")])
    assert delegation_watch.spawn_refusal("p1", "a1", ledger=_Ledger({"j1": ["2.00"]})) is not None
    assert delegation_watch.spawn_refusal("p1", "a1", ledger=_Ledger({"j1": ["0.10"]})) is None


def test_only_agent_jobs_are_watched():
    assert delegation_watch.watched(_job("j1"))
    assert not delegation_watch.watched(_job("j2", source="self_update"))


# ---- the one result shape -------------------------------------------------

def test_every_result_has_reply_and_usage(monkeypatch):
    monkeypatch.setattr(agent_result, "_tool_calls", lambda job: 3)
    job = _job("j1", status=JobStatus.COMPLETED, label="scout", result_text="found it",
               started_at=100.0, completed_at=142.0)
    out = agent_result.render(job, usage=agent_result.usage_of(job, ledger=_Ledger({"j1": ["0.012"]})))
    assert out.splitlines()[0] == '<agent name="scout" id="j1" status="completed">'
    assert "found it" in out
    assert "42 s" in out and "3 tool calls" in out and "1.2k tokens" in out and "$0.012" in out
    assert out.endswith("</agent>")


def test_a_card_names_what_an_isolated_helper_changed(monkeypatch):
    from types import SimpleNamespace

    changed = {"files": ["a.py", "b.py"], "dirty": True}

    class _Manager:
        def get_worktree(self, worktree_id):
            return SimpleNamespace(branch_name="scout-7f2a") if worktree_id == "wt1" else None

        def summarize(self, worktree):
            return changed

    monkeypatch.setattr("openprogram.state.worktree.manager.get_manager", lambda: _Manager())
    assert agent_result.files_of(_job("j1", worktree_id="wt1")) == {"count": 2, "branch": "scout-7f2a", "dirty": True}
    assert agent_result.files_of(_job("j2")) is None
    assert agent_result.files_of(_job("j3", worktree_id="gone")) is None
    changed["files"] = []
    assert agent_result.files_of(_job("j4", worktree_id="wt1")) is None


def test_an_unknown_price_shows_no_made_up_cost():
    job = _job("j1", status=JobStatus.COMPLETED, started_at=0.0, completed_at=1.0)
    usage = agent_result.usage_of(job, ledger=_Ledger({}, unknown={"j1"}))
    assert usage["cost_usd"] is None


def test_a_limit_stop_names_its_reason():
    job = _job("j1", status=JobStatus.CANCELLED, label="scout",
               reason_code=delegation_watch.TREE_COST_REASON)
    first = agent_result.render(job, reply="", usage={}).splitlines()[0]
    assert 'status="stopped" reason="spend cap"' in first


def test_a_long_reply_is_clipped_with_a_pointer():
    text = agent_result.clip_reply("x" * 30000, "j1")
    assert len(text) < 30000 and "read_conversation" in text and "j1" in text


# ---- saved Agent settings ---------------------------------------------------

def _spec(**kw):
    from openprogram.runtime.agent_registry.manager import AgentSpec
    base = dict(id="a", name="A", description="does a")
    base.update(kw)
    return AgentSpec(**base)


def test_delegation_defaults_follow_tools():
    from openprogram.runtime.agent_registry import delegation
    assert delegation.delegation_enabled(_spec())
    assert not delegation.delegation_enabled(_spec(tools={"mode": "none"}))
    assert not delegation.delegation_enabled(_spec(delegation="off"))
    assert delegation.delegation_enabled(_spec(tools={"mode": "none"}, delegation="on"))


def test_isolation_rule():
    from openprogram.runtime.agent_registry import delegation
    writer = _spec()
    reader = _spec(tools={"mode": "selected", "allowed": ["read", "grep"]})
    assert delegation.isolation_for(writer, requested="", background=True) == ("worktree", True)
    assert delegation.isolation_for(writer, requested="", background=False) == ("", False)
    assert delegation.isolation_for(reader, requested="", background=True) == ("", False)
    assert delegation.isolation_for(_spec(isolation="none"), requested="", background=True) == ("", False)
    assert delegation.isolation_for(reader, requested="worktree", background=False) == ("worktree", False)


def test_roster_lists_only_callable_agents_by_id():
    from openprogram.programs.tools.agents.agent.agent.prompt import roster_text
    from openprogram.runtime.agent_registry import delegation
    rows = delegation.roster([_spec(id="z", name="Zed"), _spec(id="b", name="Bee"),
                              _spec(id="off", delegation="off")])
    assert [r[0] for r in rows] == ["b", "z"]
    text = roster_text(rows)
    assert "- Bee (b) — does a" in text and "off" not in text


def test_find_by_name_or_id():
    from openprogram.runtime.agent_registry import delegation
    rows = [_spec(id="explorer", name="Explorer")]
    assert delegation.find("explorer", rows).id == "explorer"
    assert delegation.find("EXPLORER", rows).id == "explorer"
    assert delegation.find("nobody", rows) is None


def test_explorer_is_read_only():
    from openprogram.runtime.agent_registry import builtin_agents, delegation
    explorer = builtin_agents.builtin("explorer")
    allowed = set(explorer["tools"]["allowed"])
    assert not allowed & delegation.WRITE_TOOLS


def test_model_facing_collaboration_tools_are_five():
    from openprogram.programs import get_agent_tool
    for name in ("agent", "send_message", "list_agents", "read_conversation", "close_agent"):
        assert get_agent_tool(name) is not None, name
    for name in ("handoff", "job_output", "list_jobs", "archive_agent"):
        assert get_agent_tool(name) is None, name


def test_agent_tool_has_no_model_override():
    from openprogram.programs import get_agent_tool
    tool = get_agent_tool("agent")
    props = (getattr(tool, "parameters", None) or {}).get("properties", {})
    assert "model" not in props and "to" not in props
    assert props["start_from"]


# ---- close_agent ------------------------------------------------------------

def test_close_agent_stops_then_archives_its_own_helper(monkeypatch):
    import importlib
    module = importlib.import_module(
        "openprogram.programs.tools.agents.send_message.close_agent.close_agent")

    running = _job("j1", label="scout", head_id="h1")
    calls: list = []

    class _Runner:
        def get_job(self, job_id):
            return running if job_id == "j1" else None

        def _cancel_cascade(self, job_id, *, reason, root_reason_code):
            calls.append(("cancel", job_id, root_reason_code))
            running.status = JobStatus.CANCELLED

    monkeypatch.setattr("openprogram.runtime.jobs.get_runner", lambda: _Runner())
    monkeypatch.setattr("openprogram.programs.tools.agents.agent._ownership.check_job_ownership",
                        lambda job_id, tool: None)
    archive = importlib.import_module(
        "openprogram.programs.tools.agents.send_message.archive_agent.archive_agent")
    monkeypatch.setattr(archive, "_archive_agent_impl",
                        lambda address, reason="": calls.append(("archive", address)) or "[archive_agent] done")
    monkeypatch.setattr("openprogram.runtime.jobs.notices.consume", lambda job: calls.append(("consume", job.id)))

    out = module._close_agent_impl("j1")
    assert calls == [("cancel", "j1", "cancel.parent"), ("archive", "p1:h1"), ("consume", "j1")]
    assert out.startswith("[close_agent] scout: stopped and archived.")


def test_close_agent_refuses_work_it_did_not_start(monkeypatch):
    import importlib
    module = importlib.import_module(
        "openprogram.programs.tools.agents.send_message.close_agent.close_agent")

    class _Runner:
        def get_job(self, job_id):
            return _job("j1")

        def _cancel_cascade(self, *a, **k):
            raise AssertionError("must not stop another conversation's work")

    monkeypatch.setattr("openprogram.runtime.jobs.get_runner", lambda: _Runner())
    monkeypatch.setattr("openprogram.programs.tools.agents.agent._ownership.check_job_ownership",
                        lambda job_id, tool: "[close_agent refused] not yours")
    assert module._close_agent_impl("j1") == "[close_agent refused] not yours"


def test_close_agent_archives_a_helper_stopped_mid_turn(monkeypatch):
    """A job stopped mid-turn has no head_id yet; its branch ends at the
    reply admission named for it, and that tip is what gets archived."""
    import importlib
    from types import SimpleNamespace
    module = importlib.import_module(
        "openprogram.programs.tools.agents.send_message.close_agent.close_agent")

    running = _job("j1", label="scout", head_id=None)
    calls: list = []

    class _Store:
        def get_execution_input(self, job_id):
            return SimpleNamespace(assistant_message_id=f"{job_id}_user_reply")

    class _Runner:
        _execution_store = _Store()

        def get_job(self, job_id):
            return running if job_id == "j1" else None

        def _cancel_cascade(self, job_id, *, reason, root_reason_code):
            running.status = JobStatus.CANCELLED

    monkeypatch.setattr("openprogram.runtime.jobs.get_runner", lambda: _Runner())
    monkeypatch.setattr("openprogram.programs.tools.agents.agent._ownership.check_job_ownership",
                        lambda job_id, tool: None)
    archive = importlib.import_module(
        "openprogram.programs.tools.agents.send_message.archive_agent.archive_agent")
    monkeypatch.setattr(archive, "_archive_agent_impl",
                        lambda address, reason="": calls.append(address) or "[archive_agent] done")
    monkeypatch.setattr("openprogram.runtime.jobs.notices.consume", lambda job: None)

    out = module._close_agent_impl("j1")
    assert calls == ["p1:j1_user_reply"]
    assert out.startswith("[close_agent] scout: stopped and archived.")


# ---- the roster in the agent tool's prompt ------------------------------------

def test_roster_render_is_stable():
    from openprogram.programs.tools.agents.agent.agent.prompt import roster_text
    from openprogram.runtime.agent_registry import delegation
    rows = delegation.roster([
        _spec(id="explorer", name="Explorer", description="Read-only scout.\n  Changes nothing."),
        _spec(id="main", name="Assistant", description="x" * 200),
        _spec(id="same", name="same", description=""),
    ])
    assert roster_text(rows) == (
        "\n\nAgents you can start with agent=…:\n"
        "- Explorer (explorer) — Read-only scout. Changes nothing.\n"
        "- Assistant (main) — " + "x" * 159 + "…\n"
        "- same"
    )
    assert roster_text([]) == ""


@pytest.mark.parametrize("change", [
    {"name": "Renamed"},
    {"description": "now does b"},
    {"delegation": "off"},
])
def test_a_roster_edit_announces_the_prefix_break(monkeypatch, change):
    from openprogram.runtime.agent_registry import manager
    breaks: list[str] = []
    monkeypatch.setattr("openprogram.models.usage.prefix_fingerprint.expect_break_everywhere",
                        lambda reason: breaks.append(reason))
    manager._roster_changed(_spec(), _spec(**change))
    assert breaks == ["agent_roster"]


def test_an_edit_outside_the_roster_breaks_nothing(monkeypatch):
    from openprogram.runtime.agent_registry import manager
    breaks: list[str] = []
    monkeypatch.setattr("openprogram.models.usage.prefix_fingerprint.expect_break_everywhere",
                        lambda reason: breaks.append(reason))
    manager._roster_changed(_spec(), _spec(system_prompt="other", thinking_effort="high"))
    assert breaks == []


# ---- the utility model tier ---------------------------------------------------

def test_explorer_runs_on_the_lightweight_helpers_model(monkeypatch):
    from openprogram.runtime.agent_registry import builtin_agents, manager
    from openprogram.runtime.conversation.session_settings import session_model
    helper = _spec(id="utility", model=manager.AgentModelRef(provider="p", id="small"))
    explorer = _spec(id="explorer")
    rows = {"utility": helper, "explorer": explorer}
    monkeypatch.setattr(manager, "get", lambda agent_id: rows.get(agent_id))
    assert builtin_agents.utility_tier_model(explorer) == ("p", "small")
    assert session_model.new_session_chat_model("explorer") == ("p", "small")
    # An Explorer given its own model keeps it; other Agents are not in the tier.
    rows["explorer"] = _spec(id="explorer", model=manager.AgentModelRef(provider="q", id="big"))
    assert session_model.new_session_chat_model("explorer") == ("q", "big")
    assert builtin_agents.utility_tier_model(_spec(id="main")) is None
    # No helper model: the usual fallback (the model run last).
    rows["explorer"] = explorer
    rows["utility"] = _spec(id="utility")
    monkeypatch.setattr("openprogram.models.providers.catalog.last_used.read_last_used_model",
                        lambda: ("last", "used"))
    assert session_model.new_session_chat_model("explorer") == ("last", "used")


def test_usage_counts_the_helpers_a_helper_started(monkeypatch):
    """C2: a helper's tokens and cost include the helpers under it."""
    root = _job("j1", status=JobStatus.COMPLETED, started_at=0.0, completed_at=1.0)
    child = _job("j2", parent_job_id="j1")
    grandchild = _job("j3", parent_job_id="j2")
    stranger = _job("j9", parent_job_id="other")
    by_session = {"p1": [root, child, grandchild, stranger]}
    monkeypatch.setattr("openprogram.runtime.jobs.store.list_jobs", lambda sid: by_session.get(sid, []))
    monkeypatch.setattr(agent_result, "_tool_calls", lambda job: 0)
    assert agent_result.subtree_job_ids(root) == ["j1", "j2", "j3"]
    usage = agent_result.usage_of(root, ledger=_Ledger({"j1": ["0.01"], "j2": ["0.02"], "j3": ["0.005"],
                                                         "j9": ["9"]}))
    assert usage["tokens"] == 3600 and usage["cost_usd"] == "0.035000"


# ---- a fork sends its caller's recorded prompt --------------------------------

def test_a_forked_helper_sends_the_prompt_its_caller_recorded(monkeypatch):
    """C1: the same bytes as the caller's prompt, so the prompt cache is shared;
    a fresh helper or the conversation's own turn builds its own."""
    import hashlib
    from types import SimpleNamespace
    from openprogram.programs.tools.agents.send_message.send_message import depth
    from openprogram.runtime.conversation.turn_runtime import loop_runner

    caller_prompt = "You are Assistant.\n\n# Environment\ncwd: /repo"
    seen: list = []

    def recorded(store, session_id, head_id=None):
        seen.append((session_id, head_id))
        return caller_prompt

    monkeypatch.setattr("openprogram.runtime.context.prompt_assembly.system_prompt_node.latest_recorded_prompt", recorded)
    monkeypatch.setattr("openprogram.runtime.context.Context.turn_store", staticmethod(lambda sid: object()))
    req = SimpleNamespace(session_id="p1", branch_from="a1")

    tokens = [depth.set_helper_turn(True), depth.set_forked_turn(True)]
    try:
        sent = loop_runner.forked_prompt(req)
    finally:
        depth._forked.reset(tokens[1])
        depth._helper.reset(tokens[0])
    assert hashlib.sha256(sent.encode()).digest() == hashlib.sha256(caller_prompt.encode()).digest()
    assert seen == [("p1", "a1")]

    token = depth.set_helper_turn(True)
    try:
        assert loop_runner.forked_prompt(req) is None  # fresh: built for the helper
    finally:
        depth._helper.reset(token)
    assert loop_runner.forked_prompt(req) is None  # the conversation's own turn
