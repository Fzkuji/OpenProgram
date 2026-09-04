"""agent tool — same-session spawn from inside a turn."""
from __future__ import annotations

import atexit
from contextvars import copy_context

import pytest


@pytest.fixture(autouse=True)
def _runner_lifecycle():
    """Close the singleton pool created by synchronous agent turns."""
    from openprogram.agent.job import runner as runner_mod

    runner_mod.shutdown_runner()
    yield
    runner_mod.shutdown_runner()


@pytest.fixture
def store(tmp_path, monkeypatch):
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    # Pair the canonical execution database with this test's session store;
    # runner startup must not recover executions left by unrelated tests.
    monkeypatch.setattr(
        "openprogram.paths.get_execution_db_path",
        lambda: tmp_path / "executions.db",
    )
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store", lambda: s,
    )
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {
        "id": "u1", "role": "user", "content": "hi",
        "timestamp": 0, "predecessor": None,
    })
    s.append_message("p1", {
        "id": "a1", "role": "assistant", "content": "ok",
        "timestamp": 0, "predecessor": "u1",
    })
    s.commit_turn("p1", "init")
    try:
        yield s
    finally:
        timer = s._index_timer
        try:
            s._flush_index()
        finally:
            if timer is not None:
                timer.join(timeout=1)
            atexit.unregister(s._flush_index)


@pytest.fixture
def fake_dispatcher(monkeypatch):
    from openprogram.agent import dispatcher as disp

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
        from openprogram.agent.session_db import default_db
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
            s.append_message(req.session_id, {
                "id": u_id, "role": "user", "content": req.user_text,
                "timestamp": 0, "predecessor": req.branch_from,
                "source": req.source,
                "agent_id": req.agent_id,
            })
        s.append_message(req.session_id, {
            "id": a_id, "role": "assistant", "content": "(spawned reply)",
            "timestamp": 0, "predecessor": u_id,
            "agent_id": req.agent_id,
        })
        return _R("(spawned reply)")

    monkeypatch.setattr(disp, "process_user_turn", fake_run)
    return captured


def _call_agent(*, prompt: str, description: str = "", agent_id: str = "",
               start_from: str = "inherit",
               session_id: str | None = None, turn_id: str | None = None):
    """Invoke the agent tool's underlying Python (skipping the @function
    wrapper which is for LLM-facing dispatch). ContextVars must be set
    so _resolve_parent finds them."""
    from openprogram.agent.run_control import _current_session_id
    from openprogram.store import _current_turn_id
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl

    def _go():
        tok1 = _current_session_id.set(session_id)
        tok2 = _current_turn_id.set(turn_id)
        try:
            return _agent_impl(
                prompt=prompt, description=description, agent_id=agent_id,
                start_from=start_from,
            )
        finally:
            _current_session_id.reset(tok1)
            _current_turn_id.reset(tok2)

    return copy_context().run(_go)


def test_agent_inherit_default(store, fake_dispatcher):
    """Default start_from=inherit: forks off the caller turn, history
    inherited. Result string carries ``branch=<sid>:<head_id>``."""
    out = _call_agent(
        prompt="find the answer", description="finder",
        session_id="p1", turn_id="a1",
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
        prompt="find the answer", description="finder", start_from="clean",
        session_id="p1", turn_id="a1",
    )
    assert "(spawned reply)" in out
    assert "[spawned agent branch=p1:" in out
    assert fake_dispatcher["predecessor"] is None
    assert fake_dispatcher["history_override"] == []   # empty start


def test_agent_resolves_parent_agent_when_not_supplied(store, fake_dispatcher):
    _call_agent(prompt="x", session_id="p1", turn_id="a1")
    assert fake_dispatcher["agent_id"] == "main"


def test_agent_explicit_agent_id_wins(store, fake_dispatcher):
    _call_agent(
        prompt="x", agent_id="researcher",
        session_id="p1", turn_id="a1",
    )
    assert fake_dispatcher["agent_id"] == "researcher"


def test_agent_without_session_returns_error(store, fake_dispatcher):
    out = _call_agent(prompt="x", session_id=None, turn_id="a1")
    assert "[agent error]" in out
    assert "no active parent turn" in out


def test_agent_without_turn_returns_error(store, fake_dispatcher):
    out = _call_agent(prompt="x", session_id="p1", turn_id=None)
    assert "[agent error]" in out
    assert "no active parent turn" in out


def test_agent_fork_off_node_address(store, fake_dispatcher):
    """start_from="SID:MSG_ID" forks the new branch off that exact node."""
    out = _call_agent(
        prompt="continue from there", description="forker",
        start_from="p1:u1", session_id="p1", turn_id="a1",
    )
    assert "(spawned reply)" in out
    assert "[spawned agent branch=p1:" in out
    assert fake_dispatcher["predecessor"] == "u1"


def test_agent_fork_unknown_session_errors(store, fake_dispatcher):
    out = _call_agent(
        prompt="x", start_from="nosuch:u1",
        session_id="p1", turn_id="a1",
    )
    assert "[agent error]" in out and "not found" in out


def test_agent_unknown_start_from_returns_error(store, fake_dispatcher):
    out = _call_agent(
        prompt="x", start_from="weird",
        session_id="p1", turn_id="a1",
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
    from openprogram.programs._runtime import (
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
    from openprogram.programs._runtime import current_tool_call_id
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
    from openprogram.programs._runtime import all_tools
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
    legal.) ``@agentic_function`` uses a separate schema generator and
    intentionally exposes ordinary parameters with these names.

    Scope is "the .py files this repo owns", which git already answers
    exactly: tracked files plus untracked ones .gitignore does not hide.
    Walking the directory instead would sweep in code we do not control
    and cannot fix — ``openprogram/programs/workflow/`` is where
    ``openprogram programs install`` drops independent harness checkouts
    (each with its own ``.git``, some with a ``.venv`` carrying a whole
    site-packages, including stale copies of openprogram itself), and
    .gitignore line "openprogram/programs/workflow/*" is what marks them
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
            ["git", "-C", str(repo), "ls-files", "-z",
             "--cached", "--others", "--exclude-standard",
             "--", "openprogram/*.py"],
            capture_output=True, text=True, timeout=60,
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
                    f"{path.relative_to(repo)}:{node.lineno} "
                    f"{node.name}({bad}=…)"
                )
    assert swept, "no decorated definitions found — the sweep is broken"
    assert not offenders, (
        "these tool parameters never reach the model and are never "
        f"injected by the framework — rename them: {offenders}"
    )


def test_agentic_function_ctx_and_context_parameters_are_llm_controllable():
    import asyncio

    from openprogram.agentic_programming.function import agentic_function

    @agentic_function(register_globally=False)
    def sample(ctx: str, context: str) -> str:
        return f"{ctx}:{context}"

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

def test_fanout_refuses_the_spawn_past_the_limit(store, fake_dispatcher):
    from openprogram.programs.tools.agents.agent.agent.agent import MAX_SPAWN_FANOUT

    for i in range(MAX_SPAWN_FANOUT):
        out = _call_agent(prompt=f"task {i}", session_id="p1", turn_id="a1")
        assert "[agent refused]" not in out, f"spawn {i} was refused"
    out = _call_agent(prompt="one too many", session_id="p1", turn_id="a1")
    assert "[agent refused]" in out
    assert f"already created {MAX_SPAWN_FANOUT} agents" in out
    assert "agent(to=" in out  # points at reusing the agents it has


def test_fanout_is_counted_per_turn(store, fake_dispatcher):
    """A new turn spawns with a fresh budget — the cap stops one runaway
    turn, it is not a session-lifetime quota."""
    from openprogram.programs.tools.agents.agent.agent.agent import MAX_SPAWN_FANOUT

    for i in range(MAX_SPAWN_FANOUT + 1):
        _call_agent(prompt=f"task {i}", session_id="p1", turn_id="a1")
    out = _call_agent(prompt="next turn", session_id="p1", turn_id="a2")
    assert "[agent refused]" not in out


def test_fanout_zero_disables_the_cap(store, fake_dispatcher, monkeypatch):
    from openprogram.programs.tools.agents.agent.agent.agent import MAX_SPAWN_FANOUT
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"agent": {"max_spawn_fanout": 0}},
    )
    for i in range(MAX_SPAWN_FANOUT + 2):
        out = _call_agent(prompt=f"task {i}", session_id="p1", turn_id="a1")
        assert "[agent refused]" not in out


def test_fanout_slot_is_not_spent_by_a_refused_spawn(store, fake_dispatcher):
    """The generation guard runs first, so a chain that is out of
    generations never burns its turn's fan-out slots."""
    from openprogram.programs.tools.agents.agent.agent.agent import (
        MAX_SPAWN_DEPTH, _fanout_used,
    )
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        set_chain_generations,
    )
    tok = set_chain_generations(MAX_SPAWN_DEPTH)
    try:
        out = _call_agent(prompt="nope", session_id="p1", turn_id="a1")
    finally:
        tok.var.reset(tok)
    assert "generations of agents" in out
    assert _fanout_used.get(("p1", "a1"), 0) == 0
