"""agent tool — same-session spawn from inside a turn."""

from __future__ import annotations

import atexit
from contextvars import copy_context

import pytest


@pytest.fixture(autouse=True)
def _runner_lifecycle():
    """Close the singleton pool created by synchronous agent turns."""
    from openprogram.runtime.jobs import runner as runner_mod

    runner_mod.shutdown_runner()
    yield
    runner_mod.shutdown_runner()


@pytest.fixture
def store(tmp_path, monkeypatch):
    from openprogram.state.store.session.session_store import SessionStore
    from openprogram.runtime.context import session_db as sdb_mod
    from openprogram.runtime.jobs import runner as runner_mod
    from openprogram.state import store as store_mod

    runner_mod.shutdown_runner()
    # Pair the canonical execution database with this test's session store;
    # runner startup must not recover executions left by unrelated tests.
    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path",
        lambda: tmp_path / "executions.db",
    )
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr(store_mod, "default_store", lambda: s)
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.default_store",
        lambda: s,
    )
    s.create_session("p1", "main", title="parent")
    s.append_message(
        "p1",
        {
            "id": "u1",
            "role": "user",
            "content": "hi",
            "timestamp": 0,
            "predecessor": None,
        },
    )
    s.append_message(
        "p1",
        {
            "id": "a1",
            "role": "assistant",
            "content": "ok",
            "timestamp": 0,
            "predecessor": "u1",
        },
    )
    s.commit_turn("p1", "init")
    try:
        yield s
    finally:
        runner_mod.shutdown_runner()
        timer = s._index_timer
        try:
            s._flush_index()
        finally:
            if timer is not None:
                timer.join(timeout=1)
            atexit.unregister(s._flush_index)


@pytest.fixture
def fake_dispatcher(monkeypatch):
    from openprogram.runtime.conversation import turn_runtime as disp

    class _R:
        def __init__(self, text, failed=False, error=None):
            self.final_text = text
            self.user_msg_id = "u"
            self.assistant_msg_id = "a"
            self.tool_calls = []
            self.usage = {}
            self.duration_ms = 1
            self.failed = failed
            self.error = error

    captured = {}

    def fake_run(req, *, on_event=None, cancel_event=None):
        captured["prompt"] = req.user_text
        captured["agent_id"] = req.agent_id
        captured["predecessor"] = req.branch_from
        captured["history_override"] = req.history_override
        from openprogram.runtime.context.session_db import default_db

        s = default_db()
        u_id = "u_" + str(len(captured))
        a_id = "a_" + str(len(captured))
        if req.branch_from is None and req.spawn_caller:
            s.spawn_branch(
                req.session_id,
                req.spawn_caller,
                source=req.source,
                node_id=u_id,
                prompt=req.user_text,
                created_at=0,
                register_head=req.advance_head,
            )
        else:
            s.append_message(
                req.session_id,
                {
                    "id": u_id,
                    "role": "user",
                    "content": req.user_text,
                    "timestamp": 0,
                    "predecessor": req.branch_from,
                    "source": req.source,
                    "agent_id": req.agent_id,
                },
            )
        s.append_message(
            req.session_id,
            {
                "id": a_id,
                "role": "assistant",
                "content": "(spawned reply)",
                "timestamp": 0,
                "predecessor": u_id,
                "agent_id": req.agent_id,
            },
        )
        return _R("(spawned reply)")

    monkeypatch.setattr(disp, "process_user_turn", fake_run)
    return captured


def _call_agent(
    *,
    prompt: str,
    description: str = "",
    agent_id: str = "",
    start_from: str = "inherit",
    session_id: str | None = None,
    turn_id: str | None = None,
):
    """Invoke the agent tool's underlying Python (skipping the @function
    wrapper which is for LLM-facing dispatch). ContextVars must be set
    so _resolve_parent finds them."""
    from openprogram.runtime.execution.run_control.binding import _current_session_id
    from openprogram.state.store import _current_turn_id
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
    from openprogram.runtime.permissions.refusal import Refused

    def _go():
        tok1 = _current_session_id.set(session_id)
        tok2 = _current_turn_id.set(turn_id)
        try:
            return _agent_impl(
                prompt=prompt,
                description=description,
                agent_id=agent_id,
                start_from=start_from,
            )
        except Refused as refused:
            return refused.text
        finally:
            _current_session_id.reset(tok1)
            _current_turn_id.reset(tok2)

    return copy_context().run(_go)


def test_agent_inherit_default(store, fake_dispatcher):
    """Default start_from=inherit: forks off the caller turn, history
    inherited. Result string carries ``branch=<sid>:<head_id>``."""
    out = _call_agent(
        prompt="find the answer",
        description="finder",
        session_id="p1",
        turn_id="a1",
    )
    assert "(spawned reply)" in out
    assert "[spawned agent branch=p1:" in out
    assert fake_dispatcher["prompt"] == "find the answer"
    # inherit → caller pinned to the caller turn.
    assert fake_dispatcher["predecessor"] == "a1"
    # inherit → history_override left at default (None) so dispatcher
    # walks the conv chain ending at a1.
    assert fake_dispatcher["history_override"] is None


def test_agent_clean_mode_starts_new_root(store, fake_dispatcher):
    """start_from=clean: new root (caller=None) in the same session.
    Result string still carries branch=<sid>:<head_id> — same session."""
    out = _call_agent(
        prompt="find the answer",
        description="finder",
        start_from="clean",
        session_id="p1",
        turn_id="a1",
    )
    assert "(spawned reply)" in out
    assert "[spawned agent branch=p1:" in out
    assert fake_dispatcher["predecessor"] is None
    assert fake_dispatcher["history_override"] == []  # empty start


def test_agent_resolves_parent_agent_when_not_supplied(store, fake_dispatcher):
    _call_agent(prompt="x", session_id="p1", turn_id="a1")
    assert fake_dispatcher["agent_id"] == "main"


def test_agent_explicit_agent_id_wins(store, fake_dispatcher, monkeypatch):
    # A saved Agent other agents may start (the registry is not shared
    # state between tests).
    from openprogram.runtime.agent_registry.manager import AgentSpec
    monkeypatch.setattr("openprogram.runtime.agent_registry.manager.list_all",
                        lambda: [AgentSpec(id="researcher", name="Researcher")])
    _call_agent(
        prompt="x",
        agent_id="researcher",
        session_id="p1",
        turn_id="a1",
    )
    assert fake_dispatcher["agent_id"] == "researcher"


def test_agent_unknown_agent_lists_the_roster(store, fake_dispatcher, monkeypatch):
    from openprogram.runtime.agent_registry.manager import AgentSpec
    monkeypatch.setattr("openprogram.runtime.agent_registry.manager.list_all",
                        lambda: [AgentSpec(id="explorer", name="Explorer")])
    out = _call_agent(prompt="x", agent_id="nobody", session_id="p1", turn_id="a1")
    assert "[agent error]" in out and "Explorer" in out
    assert "agent_id" not in fake_dispatcher


def test_agent_without_session_returns_error(store, fake_dispatcher):
    out = _call_agent(prompt="x", session_id=None, turn_id="a1")
    assert "[agent error]" in out
    assert "no active parent turn" in out


def test_registered_agent_reports_a_spawn_that_never_started_as_failed():
    import asyncio
    from openprogram.programs.tools.agents.agent.agent.agent import agent
    result = asyncio.run(agent.execute("call-1", {"prompt": "x"}, None, None))
    assert result.is_error
    assert result.content[0].text.startswith("[agent error] no active parent turn")


def test_agent_without_turn_returns_error(store, fake_dispatcher):
    out = _call_agent(prompt="x", session_id="p1", turn_id=None)
    assert "[agent error]" in out
    assert "no active parent turn" in out


def test_agent_fork_off_node_address(store, fake_dispatcher):
    """start_from="SID:MSG_ID" forks the new branch off that exact node."""
    out = _call_agent(
        prompt="continue from there",
        description="forker",
        start_from="p1:u1",
        session_id="p1",
        turn_id="a1",
    )
    assert "(spawned reply)" in out
    assert "[spawned agent branch=p1:" in out
    assert fake_dispatcher["predecessor"] == "u1"


def test_agent_fork_unknown_session_errors(store, fake_dispatcher):
    out = _call_agent(
        prompt="x",
        start_from="nosuch:u1",
        session_id="p1",
        turn_id="a1",
    )
    assert "[agent error]" in out and "not found" in out


def test_agent_unknown_start_from_returns_error(store, fake_dispatcher):
    out = _call_agent(
        prompt="x",
        start_from="weird",
        session_id="p1",
        turn_id="a1",
    )
    assert "[agent error]" in out
    assert "unknown start_from" in out


# --- tool_call_id propagation --------------------------------------------


def test_current_tool_call_id_visible_inside_tool_body():
    """``_execute`` binds the LLM's tool_call_id so a tool body can
    correlate what it emits with the UI block drawn for its call —
    agent() uses it to anchor the spawn card on the right timeline row.
    Covers the sync body path, which runs in an executor thread and only
    sees the value because ``_invoke`` copies the context across."""
    import asyncio
    from openprogram.programs._host.runtime import (
        current_tool_call_id,
        function,
    )

    seen: list = []

    @function(name="_probe_call_id", register_globally=False)
    def _probe() -> str:
        """Probe.

        Returns:
            the bound id
        """
        seen.append(current_tool_call_id())
        return "ok"

    asyncio.run(_probe.execute("tc_abc", {}, None, None))
    assert seen == ["tc_abc"]


def test_current_tool_call_id_is_none_outside_a_tool_call():
    from openprogram.programs._host.runtime import current_tool_call_id

    assert current_tool_call_id() is None


# --- LLM-facing schema ----------------------------------------------------


def test_agent_exposes_start_from_to_the_llm():
    """The spawn-point parameter must survive into the tool schema.

    ``_build_parameters_schema`` drops framework-injected kwargs by name,
    and ``context`` is on that list — so while this parameter was named
    ``context`` no model could ever set it: it silently kept its default
    and every ``clean`` / ``inherit`` / ``SID:MSG_ID`` choice documented
    in the description was unreachable. ``start_from`` avoids the
    reserved name; this test keeps it that way.
    """
    from openprogram.programs._host.runtime import all_tools

    tool = next(t for t in all_tools() if t.name == "agent")
    props = (tool.parameters or {}).get("properties", {})
    assert "start_from" in props, sorted(props)
    assert "context" not in props


def test_no_function_tool_declares_a_parameter_the_runtime_drops():
    """Repo-wide guard for the same class of bug.

    ``ctx`` and ``context`` are stripped from every ``@function`` schema
    and never injected back, so a tool parameter with either name is dead
    on arrival: the LLM cannot set it and the framework never fills it in.
    (``cancel`` / ``on_update`` are stripped *and* injected, so they stay
    legal.) ``Agent method`` uses a separate schema generator and
    intentionally exposes ordinary parameters with these names.

    Scope is "the .py files this repo owns", which git already answers
    exactly: tracked files plus untracked ones .gitignore does not hide.
    Walking the directory instead would sweep in code we do not control
    and cannot fix — ``openprogram/programs/applications/`` is where
    ``openprogram programs install`` drops independent harness checkouts
    (each with its own ``.git``, some with a ``.venv`` carrying a whole
    site-packages, including stale copies of openprogram itself), and
    .gitignore line "openprogram/programs/applications/*" is what marks them
    as not ours. Untracked-but-unignored files stay in scope so a tool
    written and not yet committed is still guarded. Checking out this
    repo as a git worktree leaves those installs behind, so a directory
    walk also gives different answers in a worktree than in the main
    checkout; asking git gives the same answer in both.
    """
    import ast
    import pathlib
    import subprocess

    repo = pathlib.Path(__file__).resolve().parents[3]
    try:
        listing = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "openprogram/*.py",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        pytest.skip(f"git unavailable, cannot scope the sweep: {exc}")
    if listing.returncode != 0:  # pragma: no cover
        pytest.skip(f"not a git checkout: {listing.stderr.strip()}")
    paths = [repo / p for p in listing.stdout.split("\0") if p]
    assert paths, "pathspec matched nothing — the sweep would be vacuous"

    dead = {"ctx", "context"}
    offenders: list[str] = []
    swept = 0
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = set()
            for d in node.decorator_list:
                f = d.func if isinstance(d, ast.Call) else d
                decorators.add(getattr(f, "id", None) or getattr(f, "attr", None))
            if "function" not in decorators:
                continue
            swept += 1
            args = {a.arg for a in node.args.args + node.args.kwonlyargs}
            for bad in sorted(args & dead):
                offenders.append(
                    f"{path.relative_to(repo)}:{node.lineno} {node.name}({bad}=…)"
                )
    assert swept, "no decorated definitions found — the sweep is broken"
    assert not offenders, (
        "these tool parameters never reach the model and are never "
        f"injected by the framework — rename them: {offenders}"
    )


def test_agent_method_ctx_and_context_parameters_are_llm_controllable():
    import asyncio

    from openprogram import Agent

    class SampleAgent(Agent):
        method_options = {
            "sample": {"register_globally": False, "name": "sample", "tool": True},
        }

        def sample(self, ctx: str, context: str) -> str:
            return f"{ctx}:{context}"

    sample = SampleAgent().sample

    for parameters in (sample.spec["parameters"], sample._agent_tool.parameters):
        for name in ("ctx", "context"):
            assert parameters["properties"][name] == {"type": "string"}
            assert name in parameters["required"]

    result = asyncio.run(
        sample._agent_tool.execute(
            "probe", {"ctx": "left", "context": "right"}, None, None
        )
    )
    assert result.content[0].text == "left:right"


# --------------------------------------------------------------------------
# Fan-out budget (agent.max_spawn_fanout) — how many agents ONE turn may
# create. The chain counter bounds generations downward; this bounds
# siblings, which nothing else counts.
# --------------------------------------------------------------------------


def test_fanout_refuses_the_spawn_past_the_limit(store, fake_dispatcher, monkeypatch):
    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"agent": {"max_spawn_fanout": 8}},
    )
    for i in range(8):
        out = _call_agent(prompt=f"task {i}", session_id="p1", turn_id="a1")
        assert "[blocked by setting]" not in out, f"spawn {i} was refused"
    out = _call_agent(prompt="one too many", session_id="p1", turn_id="a1")
    assert "[blocked by setting]" in out
    assert "reached 8 agents" in out
    assert "send_message" in out  # points at reusing the agents it has


def test_fanout_is_counted_per_turn(store, fake_dispatcher, monkeypatch):
    """A new turn spawns with a fresh budget — the cap stops one runaway
    turn, it is not a session-lifetime quota."""
    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"agent": {"max_spawn_fanout": 2}},
    )
    for i in range(3):
        _call_agent(prompt=f"task {i}", session_id="p1", turn_id="a1")
    out = _call_agent(prompt="next turn", session_id="p1", turn_id="a2")
    assert "[blocked by setting]" not in out


def test_no_fanout_cap_by_default(store, fake_dispatcher):
    for i in range(12):
        out = _call_agent(prompt=f"task {i}", session_id="p1", turn_id="a1")
        assert "[blocked by setting]" not in out


def test_fanout_slot_is_not_spent_by_a_refused_spawn(store, fake_dispatcher, monkeypatch):
    """The generation guard runs first, so a chain that is out of
    generations never burns its turn's fan-out slots."""
    from openprogram.programs.tools.agents.agent.agent.agent import _fanout_used
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        set_chain_generations,
    )

    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"agent": {"max_spawn_depth": 1, "max_spawn_fanout": 8}},
    )
    tok = set_chain_generations(1)
    try:
        out = _call_agent(prompt="nope", session_id="p1", turn_id="a1")
    finally:
        tok.var.reset(tok)
    assert "generations" in out
    assert _fanout_used.get(("p1", "a1"), 0) == 0


def test_a_waited_agent_stopped_by_the_person_reports_where_its_work_is(monkeypatch):
    """A foreground helper stopped on its own row has no final text: its
    caller reads the same "(no reply)" pointer a stopped background helper's
    notice carries, not an error line with ``None`` in it."""
    from openprogram.programs.tools.agents.agent.agent.agent import _sync_result
    from openprogram.runtime.conversation.peer_agents.sub_agent_run import AgentTurnResult
    from openprogram.runtime.jobs.types import Job, JobStatus

    job = Job(id="j_stop", parent_session_id="s1", prompt="sleep", agent_id="main",
              label="sleeper", status=JobStatus.CANCELLED, reason_code="cancel.user",
              head_id="j_stop_user_reply")
    monkeypatch.setattr("openprogram.runtime.jobs.store.load_job", lambda _sid, _jid: job)

    out = _sync_result(
        AgentTurnResult(head_id=None, final_text="", failed=True, error=None),
        run_session="s1", job_id="j_stop", label="sleeper", chosen_agent="main",
        worktree=None,
    )

    assert 'status="stopped" reason="user"' in out
    assert '(no reply) Its work so far: read_conversation("j_stop").' in out
    assert "agent error" not in out and "None" not in out
    assert out.endswith("[spawned agent branch=s1:j_stop_user_reply]")


def test_agent_name_is_the_description_as_written(store, fake_dispatcher):
    """The name people and the model read is the description, spaces
    and all, not an identifier form of it ("Spell_the_word_harbor")."""
    from openprogram.runtime.jobs.store import list_jobs
    out = _call_agent(prompt="spell it", description="  Spell the word harbor ",
                      session_id="p1", turn_id="a1")
    assert 'name="Spell the word harbor"' in out
    jobs = list_jobs("p1")
    assert [j.label for j in jobs] == ["Spell the word harbor"]
    names = {b.get("name") for b in store.list_branches("p1")}
    assert "Spell the word harbor" in names or "Spell the word harbor" in {
        store.get_branch_meta("p1", jobs[0].head_id).get("name")}


def test_agent_name_is_trimmed_at_a_word():
    from openprogram.programs.tools.agents.agent.agent.agent import AGENT_NAME_MAX, agent_name
    long = "Check every tide table along the northern coast for spring tides"
    name = agent_name(long)
    assert len(name) <= AGENT_NAME_MAX and long.startswith(name) and long[len(name)] == " "
    assert agent_name("x" * 80) == "x" * AGENT_NAME_MAX
    assert agent_name("two\nlines") == "two lines"
    assert agent_name("indented\n    next line") == "indented next line"
    assert agent_name("one\u2028two\x85three") == "one two three"
    assert agent_name("keeps  two") == "keeps  two"


def test_an_agent_name_keeps_two_spaces_as_written(store, fake_dispatcher):
    """Claude Code keeps an Agent call's description as typed; so does the
    name here, so the card, the job and the branch show the spacing the
    description had, and send_message reaches it by that text."""
    from openprogram.runtime.jobs.store import list_jobs
    out = _call_agent(prompt="fix it", description="Fix  the  login",
                      session_id="p1", turn_id="a1")
    assert 'name="Fix  the  login"' in out
    jobs = list_jobs("p1")
    assert [j.label for j in jobs] == ["Fix  the  login"]
    names = {b.get("name") for b in store.list_branches("p1")}
    assert "Fix  the  login" in names or "Fix  the  login" in {
        store.get_branch_meta("p1", jobs[0].head_id).get("name")}


def test_agent_default_starts_fresh(store, fake_dispatcher):
    """C1: with no start_from the helper sees only the brief."""
    _call_agent(prompt="find the answer", session_id="p1", turn_id="a1", start_from="")
    assert fake_dispatcher["predecessor"] is None
    assert fake_dispatcher["history_override"] == []


def test_agent_fork_with_another_agent_starts_fresh_with_a_note(store, fake_dispatcher, monkeypatch):
    from openprogram.runtime.agent_registry.manager import AgentSpec
    monkeypatch.setattr("openprogram.runtime.agent_registry.manager.list_all",
                        lambda: [AgentSpec(id="researcher", name="Researcher")])
    out = _call_agent(prompt="x", agent_id="researcher", start_from="fork",
                      session_id="p1", turn_id="a1")
    assert fake_dispatcher["predecessor"] is None
    assert "start_from='fork' needs your own Agent" in out
