"""message_branch — C1 core path (target="new" spawn usage).

Covers, with a fake run_agent_turn (no real LLM):
  * target parsing (new / fork / existing)
  * sync delivery → run → reply text returned to caller
  * branch.message_sent / branch.message_replied events emitted
  * no active parent turn → clear error
  * target=existing → not-yet-implemented error (C3 placeholder)

See docs/design/runtime/agent-collaboration.md.
"""
from __future__ import annotations

import pytest

from openprogram.functions.tools.agent_collab.message_branch import (
    _message_branch_impl,
    _parse_target,
)


def test_parse_target_new():
    assert _parse_target("new") == ("new", None, None)


def test_parse_target_fork():
    assert _parse_target("new:sess1:msg9") == ("fork", "sess1", "msg9")


def test_parse_target_existing():
    assert _parse_target("sess1:head7") == ("existing", "sess1", "head7")


def test_resolve_parent_falls_back_to_head(tmp_path, monkeypatch):
    """When _current_turn_id is unbound but a session is active, the parent
    anchor falls back to the session head (fixes 'no active parent turn')."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    from openprogram.functions.tools.agent_collab.message_branch import _resolve_parent

    s = SessionStore(tmp_path / "g")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)
    s.create_session("px", "main", title="t")
    s.append_message("px", {"id": "ux", "role": "user", "content": "hi",
                            "timestamp": 0, "predecessor": None})
    s.append_message("px", {"id": "ax", "role": "assistant", "content": "ok",
                            "timestamp": 0, "predecessor": "ux"})
    s.commit_turn("px", "init")

    from openprogram.webui import _pause_stop
    from openprogram import store as store_mod
    sid_tok = _pause_stop._current_session_id.set("px")
    turn_tok = store_mod._current_turn_id.set(None)  # turn id NOT bound
    try:
        sid, aid, agent = _resolve_parent()
    finally:
        _pause_stop._current_session_id.reset(sid_tok)
        store_mod._current_turn_id.reset(turn_tok)
    assert sid == "px"
    assert aid is not None  # fell back to the session head, not None
    assert agent == "main"


@pytest.fixture
def parent_turn(tmp_path, monkeypatch):
    """Isolated store + a parent session/turn bound on the ContextVars,
    same shape the dispatcher sets up."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {"id": "u1", "role": "user", "content": "hi",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p1", {"id": "a1", "role": "assistant", "content": "ok",
                            "timestamp": 0, "predecessor": "u1"})
    # A second, earlier branch tip (not the current turn) for existing-target
    # tests — messaging the current turn (a1) trips the self-target guard.
    s.append_message("p1", {"id": "u0", "role": "user", "content": "older",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p1", {"id": "a0", "role": "assistant", "content": "older reply",
                            "timestamp": 0, "predecessor": "u0"})
    s.commit_turn("p1", "init")

    # Bind parent session + turn on the ContextVars message_branch reads.
    from openprogram.webui import _pause_stop
    from openprogram import store as store_mod
    sid_tok = _pause_stop._current_session_id.set("p1")
    turn_tok = store_mod._current_turn_id.set("a1")

    # Fake run_agent_turn — deterministic reply, no LLM.
    def fake_run(*, session_id, prompt, agent_id, branch_from=None, label=None, spawn_caller=None):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        s.append_message(session_id, {
            "id": "head_x", "role": "assistant",
            "content": f"reply to: {prompt} (from={branch_from})",
            "predecessor": branch_from, "timestamp": 0,
        })
        s.commit_turn(session_id, "fake turn")
        return AgentTurnResult(
            head_id="head_x",
            final_text=f"reply to: {prompt} (from={branch_from})",
            failed=False, error=None)

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", fake_run)
    # Skip the attach-pointer write (its own integration test covers it).
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.write_attach_pointer_for_spawn",
        lambda **kw: None)

    yield s
    _pause_stop._current_session_id.reset(sid_tok)
    store_mod._current_turn_id.reset(turn_tok)


def _collect_events():
    """Subscribe to the bus, return (events_list, unsubscribe)."""
    from openprogram.agent.event_bus import get_event_bus
    got = []
    unsub = get_event_bus().subscribe(
        lambda ev: got.append(ev),
        types={"branch.message_sent", "branch.message_replied"},
    )
    return got, unsub


def test_no_active_turn_errors():
    """Outside a parent turn → clear error, not a crash."""
    from openprogram.webui import _pause_stop
    from openprogram import store as store_mod
    sid_tok = _pause_stop._current_session_id.set(None)
    turn_tok = store_mod._current_turn_id.set(None)
    try:
        out = _message_branch_impl("hello", target="new", wait=True)
    finally:
        _pause_stop._current_session_id.reset(sid_tok)
        store_mod._current_turn_id.reset(turn_tok)
    assert "no active parent turn" in out


def test_spawn_new_sync_returns_reply(parent_turn):
    got, unsub = _collect_events()
    try:
        out = _message_branch_impl("do the thing", target="new", wait=True)
    finally:
        unsub()
    assert "reply to: do the thing" in out
    assert "branch p1:head_x" in out
    types = [e.type for e in got]
    assert "branch.message_sent" in types
    assert "branch.message_replied" in types


# --- C3: target = existing branch (same session) ---

def test_existing_branch_continues_from_head(parent_turn):
    """target=SID:HEAD runs one turn forked off that head (branch_from=HEAD)."""
    out = _message_branch_impl("more", target="p1:a0", wait=True)
    assert "reply to: more" in out
    assert "(from=a0)" in out  # fake_run saw branch_from = the branch head


def test_existing_branch_is_not_new_event(parent_turn):
    got, unsub = _collect_events()
    try:
        _message_branch_impl("more", target="p1:a0", wait=True)
    finally:
        unsub()
    sent = next(e for e in got if e.type == "branch.message_sent")
    assert sent.payload["is_new"] is False
    assert sent.payload["to"] == "p1:a0"


def test_existing_missing_session_errors(parent_turn):
    out = _message_branch_impl("hi", target="nope:a1", wait=True)
    assert "not found" in out


def test_existing_missing_head_errors(parent_turn):
    out = _message_branch_impl("hi", target="p1", wait=True)
    assert "needs the branch head" in out


# --- C5: sources synthesis ---

def test_sources_prepended_to_delivery(parent_turn):
    """sources branch content is wrapped in a block and prepended to the
    message delivered to the target model."""
    out = _message_branch_impl(
        "synthesize these", target="new", sources=["p1:a1"], wait=True)
    # fake_run echoes the prompt it received, so the source block shows up
    assert "<branch source=\"p1:a1\">" in out
    assert "synthesize these" in out


def test_sources_event_records_them(parent_turn):
    got, unsub = _collect_events()
    try:
        _message_branch_impl("go", target="new", sources=["p1:a1"], wait=True)
    finally:
        unsub()
    sent = next(e for e in got if e.type == "branch.message_sent")
    assert sent.payload["sources"] == ["p1:a1"]


def test_no_sources_no_block(parent_turn):
    out = _message_branch_impl("plain", target="new", wait=True)
    assert "<branch source" not in out


# --- C6: robustness ---

def test_depth_guard_refuses(parent_turn):
    from openprogram.functions.tools.agent_collab.message_branch import (
        set_spawn_depth, _spawn_depth, MAX_SPAWN_DEPTH,
    )
    tok = set_spawn_depth(MAX_SPAWN_DEPTH)
    try:
        out = _message_branch_impl("go deeper", target="new", wait=True)
    finally:
        _spawn_depth.reset(tok)
    assert "spawn depth" in out and "max" in out


def test_self_target_refused(parent_turn):
    # parent turn is p1:a1 — messaging it is a direct loop
    out = _message_branch_impl("loop me", target="p1:a1", wait=True)
    # note: a1 is the parent turn id (aid) in the fixture
    # (the fixture binds _current_turn_id = "a1")
    assert "your own current turn" in out


def test_result_truncated_when_huge(parent_turn, monkeypatch):
    from openprogram.agent.sub_agent_run import AgentTurnResult
    big = "x" * 40_000

    def fake_big(*, session_id, prompt, agent_id, branch_from=None, label=None, spawn_caller=None):
        return AgentTurnResult(head_id="h", final_text=big,
                               failed=False, error=None)
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", fake_big)
    out = _message_branch_impl("big", target="new", wait=True)
    assert "truncated" in out
    assert "full reply saved to" in out
    assert len(out) < 40_000


def test_spawn_sent_event_payload(parent_turn):
    got, unsub = _collect_events()
    try:
        _message_branch_impl("x", target="new", wait=True)
    finally:
        unsub()
    sent = next(e for e in got if e.type == "branch.message_sent")
    assert sent.payload["is_new"] is True
    assert sent.payload["from"] == "p1:a1"
    assert sent.origin == "agent"
