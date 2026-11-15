"""send_message — existing-branch delivery (the only send_message use).

Covers, with a fake run_agent_turn_async (no real LLM, no job runner):
  * target parsing (existing / removed spawn syntax)
  * async delivery → submitted to the runner with the right target
    branch, sender receipt header, and caller routing
  * branch.message_sent event emitted
  * no active parent turn → clear error
  * spawn syntax (to="new"/"new:…") → error pointing to the agent tool

See docs/reference/design/runtime/collaboration/agent-collaboration.html.
"""
from __future__ import annotations

import pytest

from openprogram.programs.tools.agents.send_message.send_message.send_message import (
    _send_message_impl,
    _parse_to,
)


def test_parse_to_existing():
    assert _parse_to("sess1:head7") == ("existing", "sess1", "head7")


def test_parse_to_spawn_syntax_flagged():
    assert _parse_to("new") == ("spawn_syntax", None, None)
    assert _parse_to("new:sess1:msg9") == ("spawn_syntax", None, None)


def test_resolve_parent_falls_back_to_head(tmp_path, monkeypatch):
    """When _current_turn_id is unbound but a session is active, the parent
    anchor falls back to the session head (fixes 'no active parent turn')."""
    from openprogram.state.store.session.session_store import SessionStore
    from openprogram.runtime.context import session_db as sdb_mod
    from openprogram.programs.tools.agents.send_message.send_message.send_message import _resolve_parent

    s = SessionStore(tmp_path / "g")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: s)
    s.create_session("px", "main", title="t")
    s.append_message("px", {"id": "ux", "role": "user", "content": "hi",
                            "timestamp": 0, "predecessor": None})
    s.append_message("px", {"id": "ax", "role": "assistant", "content": "ok",
                            "timestamp": 0, "predecessor": "ux"})
    s.commit_turn("px", "init")

    from openprogram.runtime.execution.run_control import binding as run_control_binding
    from openprogram.state import store as store_mod
    sid_tok = run_control_binding._current_session_id.set("px")
    turn_tok = store_mod._current_turn_id.set(None)  # turn id NOT bound
    try:
        sid, aid, agent = _resolve_parent()
    finally:
        run_control_binding._current_session_id.reset(sid_tok)
        store_mod._current_turn_id.reset(turn_tok)
    assert sid == "px"
    assert aid is not None  # fell back to the session head, not None
    assert agent == "main"


@pytest.fixture
def parent_turn(tmp_path, monkeypatch):
    """Isolated store + a parent session/turn bound on the ContextVars,
    same shape the dispatcher sets up. Deliveries are captured at
    run_agent_turn_async (the only dispatch path); captured kwargs are
    exposed as ``store.async_calls``."""
    from openprogram.state.store.session.session_store import SessionStore
    from openprogram.runtime.context import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: s)
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {"id": "u1", "role": "user", "content": "hi",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p1", {"id": "a1", "role": "assistant", "content": "ok",
                            "timestamp": 0, "predecessor": "u1"})
    # A second, earlier branch tip (not the current turn) for existing-target
    # tests — messaging the current turn (a1) trips the self-target guard.
    s.append_message("p1", {"id": "u0", "role": "user", "content": "older",
                            "timestamp": 0, "predecessor": "ROOT"})
    s.append_message("p1", {"id": "a0", "role": "assistant", "content": "older reply",
                            "timestamp": 0, "predecessor": "u0"})
    s.commit_turn("p1", "init")

    # Bind parent session + turn on the ContextVars send_message reads.
    from openprogram.runtime.execution.run_control import binding as run_control_binding
    from openprogram.state import store as store_mod
    sid_tok = run_control_binding._current_session_id.set("p1")
    turn_tok = store_mod._current_turn_id.set("a1")

    # Fake run_agent_turn_async — capture the submission, no runner.
    calls: list[dict] = []

    def fake_async(**kw):
        calls.append(kw)
        return "task_fake"

    monkeypatch.setattr(
        "openprogram.runtime.conversation.sub_agent_run.run_agent_turn_async", fake_async)
    s.async_calls = calls

    yield s
    run_control_binding._current_session_id.reset(sid_tok)
    store_mod._current_turn_id.reset(turn_tok)


def _collect_events():
    """Subscribe to the bus, return (events_list, unsubscribe)."""
    from openprogram.runtime.events import get_event_bus
    got = []
    unsub = get_event_bus().subscribe(
        lambda ev: got.append(ev),
        types={"branch.message_sent"},
    )
    return got, unsub


def test_no_active_turn_errors():
    """Outside a parent turn → clear error, not a crash."""
    from openprogram.runtime.execution.run_control import binding as run_control_binding
    from openprogram.state import store as store_mod
    sid_tok = run_control_binding._current_session_id.set(None)
    turn_tok = store_mod._current_turn_id.set(None)
    try:
        out = _send_message_impl("hello", to="p1:a0")
    finally:
        run_control_binding._current_session_id.reset(sid_tok)
        store_mod._current_turn_id.reset(turn_tok)
    assert "no active parent turn" in out


def test_spawn_syntax_errors_and_points_to_agent_tool(parent_turn):
    """The removed spawn addressing gets a clear redirect, not a spawn."""
    for target in ("new", "new:p1:a0"):
        out = _send_message_impl("do the thing", to=target)
        assert "not a valid target" in out
        assert "`agent` tool" in out


def test_empty_to_errors(parent_turn):
    out = _send_message_impl("hello", to="")
    assert "`to` is required" in out


# --- target = existing branch (same session) ---

def test_existing_branch_delivers_async_onto_head(parent_turn):
    """to=SID:HEAD submits one tracked turn continuing that head and
    returns its execution id immediately — the sender is never blocked;
    the reply comes back as a notice."""
    out = _send_message_impl("more", to="p1:a0")
    assert out.startswith("[delivered]")
    assert "execution_id=task_fake" in out
    assert "comes back to you automatically" in out
    kw = parent_turn.async_calls[-1]
    assert kw["session_id"] == "p1"
    assert kw["branch_from"] == "a0"
    assert "more" in kw["prompt"]
    # The reply routes back to the sender via the runner's followup.
    assert kw["caller_session_id"] == "p1"
    assert kw["caller_msg_id"] == "a1"
    assert kw["creates_agent"] is False


def test_existing_delivery_carries_receipt_header(parent_turn):
    """Deliveries are prefixed with the sender receipt header
    ([message from SID:HEAD (sender)] + a note that the reply returns
    on its own, so the receiver does not send_message it back)."""
    _send_message_impl("ping", to="p1:a0")
    prompt = parent_turn.async_calls[-1]["prompt"]
    assert prompt.startswith("[message from p1:a1 (main conversation)]")
    assert "not from your user" not in prompt
    assert "returned to the sender automatically" in prompt
    assert 'send_message(to=' not in prompt


def test_reply_from_unnamed_own_branch_is_the_main_conversation(parent_turn):
    """A message to an unnamed branch of the sender's session names its
    reply after the main conversation, not a bare address."""
    _send_message_impl("ping", to="p1:a0")
    assert parent_turn.async_calls[-1]["label"] == "main conversation"


def test_reply_from_named_branch_keeps_its_name(parent_turn):
    parent_turn.set_branch_name("p1", "a0", "research")
    _send_message_impl("ping", to="p1:a0")
    assert parent_turn.async_calls[-1]["label"] == "research"


def test_existing_branch_events(parent_turn):
    got, unsub = _collect_events()
    try:
        _send_message_impl("more", to="p1:a0")
    finally:
        unsub()
    sent = next(e for e in got if e.type == "branch.message_sent")
    assert sent.payload["to"] == "p1:a0"
    assert sent.payload["from"] == "p1:a1"
    assert sent.origin == "agent"


def test_existing_missing_session_errors(parent_turn):
    out = _send_message_impl("hi", to="nope:a1")
    assert "not found" in out


def test_existing_missing_head_errors(parent_turn):
    # A bare id names the conversation; this one's tip is the sender's own
    # turn, so the message would loop back to it.
    out = _send_message_impl("hi", to="p1")
    assert "[send_message refused]" in out and "own current turn" in out


# --- Existing-target normalization: SID:HEAD names the branch, not a
# fork point — delivery snaps onto the branch's current tip. ---

def test_existing_stale_head_lands_on_current_tip(parent_turn):
    """The target branch ran more turns since the sender saw its head:
    delivering to the old head continues from the CURRENT tip instead of
    forking a new branch off history."""
    s = parent_turn
    # Advance the a0 branch: its tip moves a0 → a2.
    s.append_message("p1", {"id": "u2", "role": "user", "content": "later",
                            "timestamp": 0, "predecessor": "a0"})
    s.append_message("p1", {"id": "a2", "role": "assistant", "content": "later reply",
                            "timestamp": 0, "predecessor": "u2"})
    s.commit_turn("p1", "advance")

    out = _send_message_impl("more", to="p1:a0")  # stale head
    assert out.startswith("[delivered]")
    # The submission continued from the CURRENT tip, not the stale head.
    assert s.async_calls[-1]["branch_from"] == "a2"


def test_existing_shared_ancestor_errors_with_candidates(parent_turn):
    """A node that several branches share is an ambiguous address — the
    error lists each candidate branch's current tip."""
    s = parent_turn
    # Two branches forking off a0: tips b1 and b2, a0/u0 shared ancestors.
    s.append_message("p1", {"id": "b1", "role": "assistant", "content": "x",
                            "timestamp": 0, "predecessor": "a0"})
    s.append_message("p1", {"id": "b2", "role": "assistant", "content": "y",
                            "timestamp": 0, "predecessor": "a0"})
    s.commit_turn("p1", "split")
    out = _send_message_impl("hi", to="p1:u0")
    assert "shared ancestor" in out
    assert "p1:b1" in out and "p1:b2" in out


def test_existing_unknown_node_errors(parent_turn):
    out = _send_message_impl("hi", to="p1:nosuchnode")
    assert "not found" in out and "list_agents" in out


# --- C6: robustness ---

def test_message_budget_refuses(parent_turn, monkeypatch):
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        set_chain_messages, _chain_messages,
    )
    from openprogram.runtime.permissions.refusal import Refused
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {"agent": {"max_messages": 8}})
    tok = set_chain_messages(8)
    try:
        with pytest.raises(Refused) as refused:
            _send_message_impl("go deeper", to="p1:a0")
    finally:
        _chain_messages.reset(tok)
    assert refused.value.text.startswith("[blocked by setting]") and "messages" in refused.value.text


def test_self_target_refused(parent_turn):
    # parent turn is p1:a1 — messaging it is a direct loop
    out = _send_message_impl("loop me", to="p1:a1")
    # note: a1 is the parent turn id (aid) in the fixture
    # (the fixture binds _current_turn_id = "a1")
    assert "your own current turn" in out


def test_delivery_inherits_chain_messages(parent_turn):
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        set_chain_messages, _chain_messages,
    )
    tok = set_chain_messages(2)
    try:
        _send_message_impl("deep", to="p1:a0")
    finally:
        _chain_messages.reset(tok)
    assert parent_turn.async_calls[-1]["chain_messages"] == 3  # child = depth+1


# --- Name addressing (to="<branch name>") ---

def test_name_addressing_resolves_unique_branch(parent_turn):
    parent_turn.set_branch_name("p1", "a0", "research")
    out = _send_message_impl("hi there", to="research")
    assert out.startswith("[delivered]")
    assert parent_turn.async_calls[-1]["branch_from"] == "a0"


def test_name_addressing_unique_prefix(parent_turn):
    parent_turn.set_branch_name("p1", "a0", "research-fox")
    _send_message_impl("hi", to="research")
    assert parent_turn.async_calls[-1]["branch_from"] == "a0"


def test_name_addressing_ambiguous_lists_candidates(parent_turn):
    parent_turn.set_branch_name("p1", "a0", "research")
    parent_turn.create_session("p2", "main", title="other")
    parent_turn.append_message("p2", {"id": "u9", "role": "user", "content": "x",
                                      "timestamp": 0, "predecessor": None})
    parent_turn.append_message("p2", {"id": "a9", "role": "assistant", "content": "y",
                                      "timestamp": 0, "predecessor": "u9"})
    parent_turn.commit_turn("p2", "init")
    parent_turn.set_branch_name("p2", "a9", "research")
    out = _send_message_impl("hi", to="research")
    assert "matches several branches" in out
    assert "p1:a0" in out and "p2:a9" in out


def test_name_addressing_zero_hits_errors(parent_turn):
    out = _send_message_impl("hi", to="nosuchname")
    assert "not found" in out
    assert "list_agents" in out


def test_a_finished_agent_answers_to_the_name_it_was_spawned_with(parent_turn):
    """The auto-namer renamed its branch and its end retired the branch
    from the tip list; the name from the agent call still reaches it."""
    from openprogram.runtime.jobs.store import save_job
    from openprogram.runtime.jobs.types import Job, JobStatus
    save_job("p1", Job(id="j1", parent_session_id="p1", prompt="2+3?", agent_id="main",
                       status=JobStatus.COMPLETED, head_id="a0", label="scout"))
    parent_turn.set_branch_name("p1", "a0", "2加3的计算结果", auto_named=True)
    parent_turn.mark_merged("p1", ["a0"])
    out = _send_message_impl("and 4*5?", to="scout")
    assert out.startswith("[delivered]")
    assert parent_turn.async_calls[-1]["branch_from"] == "a0"
    # The reply notice names the agent by the name it was spawned with.
    assert parent_turn.async_calls[-1]["label"] == "scout"
    from openprogram.programs.tools.agents.send_message.list_agents.list_agents import (
        _list_agents_impl,
    )
    listing = _list_agents_impl()
    assert "p1:a0 «2加3的计算结果» (agent «scout»)" in listing


def test_an_agent_head_left_behind_by_a_running_message_keeps_its_name(parent_turn, monkeypatch):
    """A watch addresses the head the spawn ended on while a message to the
    agent runs and its tip has moved on: the agent's name, not the
    auto-namer's branch name."""
    from openprogram.runtime.jobs.store import save_job
    from openprogram.runtime.jobs.types import Job, JobStatus
    from openprogram.programs.tools.agents.send_message.send_message import delivery
    save_job("p1", Job(id="j1", parent_session_id="p1", prompt="2+3?", agent_id="main",
                       status=JobStatus.COMPLETED, head_id="a0", label="scout", creates_agent=True))
    parent_turn.set_branch_name("p1", "a0", "2加3的计算结果", auto_named=True)
    import importlib
    addressing = importlib.import_module(
        "openprogram.programs.tools.agents.send_message.send_message.addressing")
    monkeypatch.setattr(addressing, "agent_tips", lambda session_id: [("scout", "a0_running")])
    assert delivery.branch_name("p1", "a0", "p1") == "scout"


def test_an_agent_named_with_spaces_is_reached_as_written_or_with_underscores(parent_turn):
    """A name is addressed as written; the identifier spelling older
    agents were stored with reaches the same agent, and the reverse."""
    from openprogram.runtime.jobs.store import save_job
    from openprogram.runtime.jobs.types import Job, JobStatus
    save_job("p1", Job(id="j1", parent_session_id="p1", prompt="spell", agent_id="main",
                       status=JobStatus.COMPLETED, head_id="a0", label="Spell the word harbor",
                       creates_agent=True))
    for to in ("Spell the word harbor", "Spell_the_word_harbor", "spell the word HARBOR"):
        out = _send_message_impl("again", to=to)
        assert out.startswith("[delivered]"), (to, out)
        assert parent_turn.async_calls[-1]["branch_from"] == "a0"
        assert parent_turn.async_calls[-1]["label"] == "Spell the word harbor"


def test_an_agent_named_with_two_spaces_keeps_them_and_answers_either_spelling(parent_turn):
    """The name keeps the spacing its description had; it is reached as
    written and with the spaces folded, and the reply names it as written."""
    from openprogram.runtime.jobs.store import save_job
    from openprogram.runtime.jobs.types import Job, JobStatus
    save_job("p1", Job(id="j1", parent_session_id="p1", prompt="fix", agent_id="main",
                       status=JobStatus.COMPLETED, head_id="a0", label="Fix  the  login",
                       creates_agent=True))
    for to in ("Fix  the  login", "Fix the login", "fix_the_login"):
        out = _send_message_impl("again", to=to)
        assert out.startswith("[delivered]"), (to, out)
        assert parent_turn.async_calls[-1]["branch_from"] == "a0"
        assert parent_turn.async_calls[-1]["label"] == "Fix  the  login"
