"""send_message — existing-branch delivery (the only send_message use).

Covers, with a fake run_agent_turn_async (no real LLM, no job runner):
  * target parsing (existing / removed spawn syntax)
  * async delivery → submitted to the runner with the right target
    branch, sender receipt header, and caller routing
  * branch.message_sent event emitted
  * no active parent turn → clear error
  * spawn syntax (to="new"/"new:…") → error pointing to the agent tool

See docs/reference/design/runtime/agent-collaboration.md.
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
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    from openprogram.programs.tools.agents.send_message.send_message.send_message import _resolve_parent

    s = SessionStore(tmp_path / "g")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.session.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)
    s.create_session("px", "main", title="t")
    s.append_message("px", {"id": "ux", "role": "user", "content": "hi",
                            "timestamp": 0, "predecessor": None})
    s.append_message("px", {"id": "ax", "role": "assistant", "content": "ok",
                            "timestamp": 0, "predecessor": "ux"})
    s.commit_turn("px", "init")

    from openprogram.agent import run_control
    from openprogram import store as store_mod
    sid_tok = run_control._current_session_id.set("px")
    turn_tok = store_mod._current_turn_id.set(None)  # turn id NOT bound
    try:
        sid, aid, agent = _resolve_parent()
    finally:
        run_control._current_session_id.reset(sid_tok)
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
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.session.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)
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
    from openprogram.agent import run_control
    from openprogram import store as store_mod
    sid_tok = run_control._current_session_id.set("p1")
    turn_tok = store_mod._current_turn_id.set("a1")

    # Fake run_agent_turn_async — capture the submission, no runner.
    calls: list[dict] = []

    def fake_async(**kw):
        calls.append(kw)
        return "task_fake"

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn_async", fake_async)
    s.async_calls = calls

    yield s
    run_control._current_session_id.reset(sid_tok)
    store_mod._current_turn_id.reset(turn_tok)


def _collect_events():
    """Subscribe to the bus, return (events_list, unsubscribe)."""
    from openprogram.events import get_event_bus
    got = []
    unsub = get_event_bus().subscribe(
        lambda ev: got.append(ev),
        types={"branch.message_sent"},
    )
    return got, unsub


def test_no_active_turn_errors():
    """Outside a parent turn → clear error, not a crash."""
    from openprogram.agent import run_control
    from openprogram import store as store_mod
    sid_tok = run_control._current_session_id.set(None)
    turn_tok = store_mod._current_turn_id.set(None)
    try:
        out = _send_message_impl("hello", to="p1:a0")
    finally:
        run_control._current_session_id.reset(sid_tok)
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
    """to=SID:HEAD submits one turn continuing that head and returns a
    delivery id immediately — the sender is never blocked."""
    out = _send_message_impl("more", to="p1:a0")
    assert "[delivered, running async]" in out
    assert "delivery_id=task_fake" in out
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
    ([message from SID:HEAD] + reply-is-optional note)."""
    _send_message_impl("ping", to="p1:a0")
    prompt = parent_turn.async_calls[-1]["prompt"]
    assert prompt.startswith("[message from p1:a1]")
    assert 'send_message(to="p1:a1")' in prompt
    assert "Replying is optional" in prompt


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
    out = _send_message_impl("hi", to="p1")
    assert "needs the branch head" in out


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
    assert "[delivered, running async]" in out
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

def test_message_budget_refuses(parent_turn):
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        set_chain_messages, _chain_messages, MAX_MESSAGES,
    )
    tok = set_chain_messages(MAX_MESSAGES)
    try:
        out = _send_message_impl("go deeper", to="p1:a0")
    finally:
        _chain_messages.reset(tok)
    assert "[send_message refused]" in out and "messages" in out


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
    assert "[delivered, running async]" in out
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
