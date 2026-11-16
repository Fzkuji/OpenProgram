"""send_message as a tracked Job (it took over ``agent(to=…)``).

All fake — no real LLM, no live task pool run (deliveries are captured
at run_agent_turn_async, same technique as test_send_message.py):

  * addressing reuse (SID:HEAD → current tip, branch-name resolution)
  * idle target → immediate delivery with the sender header + caller
    routing + depth inheritance
  * ``agent(to=…)`` is refused with a pointer to send_message
  * self-target guard, message budget, hop limit, hourly send limit
  * busy target → pending Job pre-created + inbox entry carrying its id
  * drain: entries deliver with the sender header and reuse the Job id;
    withdrawn entries are dropped without delivering

See docs/reference/design/runtime/collaboration/agent-collaboration.html.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
from openprogram.programs.tools.agents.send_message.send_message.send_message import (
    _send_message_impl as send,
)


@pytest.fixture
def parent_turn(tmp_path, monkeypatch, request):
    """Isolated store, two sessions, parent turn bound on p1:a1.
    Deliveries are captured at run_agent_turn_async; captured kwargs are
    exposed as ``store.async_calls``."""
    from openprogram.state.store.session.session_store import SessionStore
    from openprogram.runtime.context import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    # Register immediately so setup errors also join deferred index writers.
    # Runner shutdown below happens first; the unit boundary must not race a
    # timer still flushing the fixture's store on slower native ARM64 disks.
    request.addfinalizer(s.close)
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: s)
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {"id": "u1", "role": "user", "content": "hi",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p1", {"id": "a1", "role": "assistant", "content": "ok",
                            "timestamp": 0, "predecessor": "u1"})
    # A second, earlier branch tip (not the current turn) as a target.
    s.append_message("p1", {"id": "u0", "role": "user", "content": "older",
                            "timestamp": 0, "predecessor": "ROOT"})
    s.append_message("p1", {"id": "a0", "role": "assistant", "content": "older reply",
                            "timestamp": 0, "predecessor": "u0"})
    s.commit_turn("p1", "init")
    # A second session as a cross-session target.
    s.create_session("p2", "main", title="other")
    s.append_message("p2", {"id": "u9", "role": "user", "content": "x",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p2", {"id": "a9", "role": "assistant", "content": "y",
                            "timestamp": 0, "predecessor": "u9"})
    s.commit_turn("p2", "init")

    from openprogram.runtime.execution.run_control import binding as run_control_binding
    from openprogram.state import store as store_mod
    sid_tok = run_control_binding._current_session_id.set("p1")
    turn_tok = store_mod._current_turn_id.set("a1")

    calls: list[dict] = []
    from openprogram.runtime.conversation.peer_agents.sub_agent_run import run_agent_turn_async as real_async

    def fake_async(**kw):
        if kw.get("defer_dispatch"):
            return real_async(**kw)
        calls.append(kw)
        return "t_fake"

    monkeypatch.setattr(
        "openprogram.runtime.conversation.peer_agents.sub_agent_run.run_agent_turn_async", fake_async)
    monkeypatch.setattr(
        'openprogram.runtime.jobs.runner.shared._broadcast', lambda *a, **k: None)
    s.async_calls = calls

    # Build the runner NOW (empty store) so its one-shot orphan
    # reconciliation doesn't flip tasks the tests save later.
    from openprogram.runtime.jobs import runner as runner_mod
    from openprogram.runtime.execution.run_control import binding as run_control_binding
    runner = runner_mod.get_runner()

    def close_runner():
        runner_mod.shutdown_runner()
        # The singleton shutdown is deliberately non-waiting. Drain this
        # fixture's management threads before its store and monkeypatches end.
        for thread in (
            runner._dispatcher_thread, runner._reconciler_thread, runner._budget_thread,
        ):
            thread.join()

    request.addfinalizer(close_runner)

    yield s
    run_control_binding._current_session_id.reset(sid_tok)
    store_mod._current_turn_id.reset(turn_tok)


# --- idle target ---

def test_send_runs_immediately_on_target_tip(parent_turn):
    out = send("summarize the log", to="p1:a0")
    assert out.startswith("[delivered]")
    assert "execution_id=t_fake" in out
    kw = parent_turn.async_calls[-1]
    assert kw["session_id"] == "p1"
    assert kw["branch_from"] == "a0"
    assert kw["context_mode"] == "inherit"
    # Receipt header + reply routing back to the sender.
    assert kw["prompt"].startswith("[message from p1:a1")
    assert "summarize the log" in kw["prompt"]
    assert kw["caller_session_id"] == "p1"
    assert kw["caller_msg_id"] == "a1"
    assert kw["chain_messages"] == 1


def test_agent_to_is_refused_with_a_pointer_to_send_message(parent_turn):
    out = _agent_impl("summarize the log", to="p1:a0")
    assert "send_message" in out
    assert not parent_turn.async_calls


def test_send_creates_no_generation(parent_turn):
    """A message reaches an agent that already exists, so it spends a
    message and leaves the generation count alone — the target and the
    reply turn both run at the sender's count."""
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        set_chain_generations,
    )
    tok = set_chain_generations(1)
    try:
        send("summarize the log", to="p1:a0")
    finally:
        tok.var.reset(tok)
    kw = parent_turn.async_calls[-1]
    assert kw["chain_generations"] == 1
    assert kw["caller_chain_generations"] == 1


def test_same_session_dispatch_result_reaches_the_dispatcher(parent_turn, monkeypatch):
    """A message onto a branch in the SAME session creates no attach
    pointer (it spawns nothing), so its followup has to carry the reply
    inline rather than say "the whole transcript is attached above"."""
    from openprogram.runtime.jobs import runner as runner_mod
    from openprogram.runtime.jobs.types import Job, JobStatus

    send("summarize the log", to="p1:a0")
    kw = parent_turn.async_calls[-1]
    task = Job(
        id="t_same1",
        parent_session_id=kw["session_id"],
        prompt=kw["prompt"],
        agent_id=kw["agent_id"],
        caller_session_id=kw["caller_session_id"],
        caller_msg_id=kw["caller_msg_id"],
        label=kw.get("label"),
        status=JobStatus.COMPLETED,
        head_id="h1",
        result_text="the summary",
    )
    assert task.caller_session_id == task.parent_session_id
    assert task.attach_pointer_id is None

    seen: dict = {}

    def fake_process(req, **_kw):
        seen["session_id"] = req.session_id
        seen["text"] = req.user_text
        return type("_R", (), {})()

    import openprogram.runtime.conversation.turn_runtime as disp
    monkeypatch.setattr(disp, "process_user_turn", fake_process)

    def run_inline(target=None, daemon=None, **_kw):
        return type("_T", (), {"start": lambda self_: target()})()

    monkeypatch.setattr(
        runner_mod.shared,
        "threading",
        SimpleNamespace(Thread=run_inline, Lock=runner_mod.shared.threading.Lock),
    )
    runner_mod.get_runner()._dispatch_followup(task)

    assert seen["session_id"] == "p1"
    assert "the summary" in seen["text"]
    assert "attached above" not in seen["text"]


def test_addressing_by_branch_name(parent_turn):
    parent_turn.set_branch_name("p1", "a0", "research")
    out = send("dig deeper", to="research")
    assert out.startswith("[delivered]")
    assert parent_turn.async_calls[-1]["branch_from"] == "a0"


def test_addressing_by_conversation_title(parent_turn):
    out = send("dig deeper", to="other")
    assert out.startswith("[delivered]")
    assert parent_turn.async_calls[-1]["session_id"] == "p2"


def test_stale_head_snaps_to_current_tip(parent_turn):
    s = parent_turn
    s.append_message("p1", {"id": "u2", "role": "user", "content": "later",
                            "timestamp": 0, "predecessor": "a0"})
    s.append_message("p1", {"id": "a2", "role": "assistant", "content": "later reply",
                            "timestamp": 0, "predecessor": "u2"})
    s.commit_turn("p1", "advance")
    send("more", to="p1:a0")  # stale head
    assert s.async_calls[-1]["branch_from"] == "a2"


def test_unknown_target_errors(parent_turn):
    out = send("hi", to="nosuchname")
    assert out.startswith("[send_message error]")
    assert not parent_turn.async_calls


def test_self_target_refused(parent_turn):
    out = send("loop", to="p1:a1")
    assert "[send_message refused]" in out and "own current turn" in out


def test_message_budget_guard(parent_turn, monkeypatch):
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        set_chain_messages, _chain_messages,
    )
    from openprogram.runtime.permissions.refusal import Refused
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {"agent": {"max_messages": 8}})
    tok = set_chain_messages(8)
    try:
        with pytest.raises(Refused) as refused:
            send("deeper", to="p1:a0")
    finally:
        _chain_messages.reset(tok)
    assert refused.value.text.startswith("[blocked by setting]") and "messages" in refused.value.text


def test_hop_limit_refuses_the_fifth_hop(parent_turn):
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        set_chain_messages, _chain_messages,
    )
    from openprogram.runtime.permissions.refusal import Refused
    tok = set_chain_messages(4)
    try:
        with pytest.raises(Refused) as refused:
            send("deeper", to="p1:a0")
    finally:
        _chain_messages.reset(tok)
    assert "hops" in refused.value.text
    assert not parent_turn.async_calls


def test_hourly_send_limit(parent_turn, monkeypatch):
    from openprogram.runtime.permissions.refusal import Refused
    monkeypatch.setattr("openprogram.state.config.read_config",
                        lambda: {"agent": {"max_sends_per_hour": 2}})
    send("one", to="p1:a0")
    send("two", to="p1:a0")
    with pytest.raises(Refused) as refused:
        send("three", to="p1:a0")
    assert "sends this hour" in refused.value.text
    assert len(parent_turn.async_calls) == 2


# --- busy target → pending Job + inbox entry ---

def _dispatch_queued(parent_turn, monkeypatch, prompt="audit the config"):
    monkeypatch.setattr(
        "openprogram.runtime.conversation.run_control.is_turn_running",
        lambda sid: sid == "p2",
    )
    out = send(prompt, to="p2:a9")
    assert out.startswith("[queued]"), out
    tid = out.split("execution_id=")[1].split()[0]
    return tid, out


def test_busy_target_precreates_job_and_queues(parent_turn, monkeypatch):
    from openprogram.runtime.conversation.peer_agents import inbox
    from openprogram.runtime.jobs.store import load_job
    from openprogram.runtime.jobs.types import JobStatus
    tid, _ = _dispatch_queued(parent_turn, monkeypatch)
    t = load_job("p2", tid)
    assert t is not None
    assert t.status == JobStatus.QUEUED
    assert t.admission_id
    assert t.budget_scope_id
    assert t.caller_session_id == "p1"
    assert t.caller_msg_id == "a1"
    assert t.parent_msg_id == "a9"
    assert t.chain_messages == 1
    entries = inbox._load(inbox._inbox_path("p2"))
    assert len(entries) == 1
    assert entries[0]["job_id"] == tid
    assert not parent_turn.async_calls


def test_busy_inbox_failure_cancels_canonical_admission(parent_turn, monkeypatch):
    """A failed inbox write must cancel the admitted execution itself."""
    calls: list[tuple[str, str | None]] = []

    class _Runner:
        def admit_job_entity(self, *args, **kwargs):
            return None

        def cancel_execution(self, execution_id, *, reason=None):
            calls.append((execution_id, reason))
            return None

    monkeypatch.setattr("openprogram.runtime.jobs.get_runner", lambda: _Runner())
    monkeypatch.setattr(
        "openprogram.runtime.conversation.run_control.is_turn_running",
        lambda sid: sid == "p2",
    )

    def fail_enqueue(*args, **kwargs):
        raise OSError("inbox unavailable")

    monkeypatch.setattr("openprogram.runtime.conversation.peer_agents.inbox.enqueue", fail_enqueue)
    out = send("audit", to="p2:a9")

    assert "inbox unavailable" in out
    assert len(calls) == 1
    assert calls[0][0]
    assert "inbox enqueue failed" in (calls[0][1] or "")


def test_busy_duplicate_cancels_duplicate_admission(parent_turn, monkeypatch):
    """A duplicate inbox result must not leave its admission runnable."""
    calls: list[tuple[str, str | None]] = []

    class _Runner:
        def admit_job_entity(self, *args, **kwargs):
            return None

        def cancel_execution(self, execution_id, *, reason=None):
            calls.append((execution_id, reason))
            return None

    monkeypatch.setattr("openprogram.runtime.jobs.get_runner", lambda: _Runner())
    monkeypatch.setattr(
        "openprogram.runtime.conversation.run_control.is_turn_running",
        lambda sid: sid == "p2",
    )
    monkeypatch.setattr("openprogram.runtime.conversation.peer_agents.inbox.enqueue", lambda *a, **k: "duplicate")
    out = send("audit", to="p2:a9")

    assert "duplicate message ignored" in out
    assert len(calls) == 1
    assert calls[0][0]
    assert calls[0][1] == "duplicate message"


# --- inbox drain ---

def test_drain_delivers_queued_entry_on_its_job(parent_turn, monkeypatch):
    from openprogram.runtime.conversation.peer_agents import inbox
    tid, _ = _dispatch_queued(parent_turn, monkeypatch)
    monkeypatch.setattr(
        "openprogram.runtime.conversation.run_control.is_turn_running", lambda sid: False)
    delivered = inbox.drain("p2")
    assert delivered == 1
    kw = parent_turn.async_calls[-1]
    assert kw["job_id"] == tid  # SAME Job id — the sender's handle
    assert kw["prompt"].startswith("[message from p1:a1")
    assert kw["caller_session_id"] == "p1"
    assert inbox.pending_count("p2") == 0


def test_drain_skips_withdrawn_entry(parent_turn, monkeypatch):
    from openprogram.runtime.conversation.peer_agents import inbox
    from openprogram.runtime.jobs import get_runner
    tid, _ = _dispatch_queued(parent_turn, monkeypatch)
    get_runner().cancel_execution(tid)
    inbox.enqueue("p2", message="audit the config", sender_session_id="p1",
                  sender_msg_id="a1", sender_agent_id="main", agent_id="main",
                  chain_messages=0, target_head_id="a9", job_id=tid)
    monkeypatch.setattr(
        "openprogram.runtime.conversation.run_control.is_turn_running", lambda sid: False)
    delivered = inbox.drain("p2")
    assert delivered == 0
    assert not parent_turn.async_calls
    assert inbox.pending_count("p2") == 0


def test_spawn_job_does_not_resurrect_terminal_precreated(parent_turn):
    from openprogram.runtime.jobs import get_runner, JobStatus
    from openprogram.runtime.jobs.store import load_job, save_job
    from openprogram.runtime.jobs.types import Job
    save_job("p2", Job(id="t_gone1", parent_session_id="p2", prompt="x",
                         agent_id="main", status=JobStatus.CANCELLED))
    tid = get_runner().spawn_job(
        session_id="p2", prompt="x", agent_id="main", job_id="t_gone1")
    assert tid == "t_gone1"
    assert load_job("p2", "t_gone1").status == JobStatus.CANCELLED


# --- the specialist's model -----------------------------------------------------

def test_a_specialist_runs_on_its_own_model_not_the_conversations(parent_turn, monkeypatch):
    """The helper runs in the caller's conversation, whose model would win;
    a saved Agent's own model (Explorer: the utility tier) goes with the call.
    The caller's own Agent keeps the conversation's model."""
    from openprogram.runtime.agent_registry import builtin_agents, delegation, manager

    explorer = manager.AgentSpec(id="explorer", name="Explorer", description="scout",
                                 tools=builtin_agents.builtin("explorer")["tools"])
    utility = manager.AgentSpec(id="utility", name="Lightweight helper",
                                model=manager.AgentModelRef(provider="p", id="small"))
    reviewer = manager.AgentSpec(id="rev", name="Reviewer", description="reviews",
                                 tools={"mode": "selected", "allowed": ["read"]},
                                 model=manager.AgentModelRef(provider="q", id="big"))
    main = manager.AgentSpec(id="main", name="Assistant", model=manager.AgentModelRef(provider="q", id="big"))
    rows = {spec.id: spec for spec in (explorer, utility, reviewer, main)}
    monkeypatch.setattr(manager, "get", lambda agent_id: rows.get(agent_id))
    monkeypatch.setattr(delegation, "delegatable", lambda: list(rows.values()))

    for ref, expected in (("Explorer", "p/small"), ("Reviewer", "q/big"), ("", None)):
        out = _agent_impl("find the config loader", description="look", agent=ref,
                          run_in_background=True)
        assert out.startswith("[agent started in background]"), (ref, out)
        assert parent_turn.async_calls[-1]["model_override"] == expected, ref
