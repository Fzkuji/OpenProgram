"""send_message busy-target inbox — queue, drain, limits.

Covers (design: agent-collaboration.md §5.4):
  * busy target → message queued (not delivered, not dropped)
  * drain at turn end → each entry delivered through run_agent_turn_async
    with the sender receipt header + caller routing
  * queued delivery inherits the spawn depth recorded at enqueue (+1)
  * per-target cap (50): oldest dropped + notice in the sender session
  * duplicate suppression: identical message from the same sender within
    the 60s window is rejected
"""
from __future__ import annotations

import json
import threading

import pytest

from openprogram.agent import inbox
from openprogram.functions.tools.send_message.send_message.send_message import (
    _send_message_impl,
)


@pytest.fixture
def two_sessions(tmp_path, monkeypatch):
    """Sender session p1 (bound on the ContextVars) + target session t1."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)

    s.create_session("p1", "main", title="sender")
    s.append_message("p1", {"id": "u1", "role": "user", "content": "hi",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p1", {"id": "a1", "role": "assistant", "content": "ok",
                            "timestamp": 0, "predecessor": "u1"})
    s.commit_turn("p1", "init")

    s.create_session("t1", "main", title="target")
    s.append_message("t1", {"id": "u2", "role": "user", "content": "yo",
                            "timestamp": 0, "predecessor": None})
    s.append_message("t1", {"id": "b1", "role": "assistant", "content": "sup",
                            "timestamp": 0, "predecessor": "u2"})
    s.commit_turn("t1", "init")

    from openprogram.agent import run_control
    from openprogram import store as store_mod
    sid_tok = run_control._current_session_id.set("p1")
    turn_tok = store_mod._current_turn_id.set("a1")
    yield s
    run_control._current_session_id.reset(sid_tok)
    store_mod._current_turn_id.reset(turn_tok)


@pytest.fixture
def busy_target(two_sessions):
    """Mark t1 as running a turn (registered cancel token = busy)."""
    from openprogram.agent import run_control
    ev = threading.Event()
    run_control.register_cancel_event("t1", ev)
    yield ev
    run_control.unregister_cancel_event("t1", ev)


def _capture_async(monkeypatch):
    """Patch run_agent_turn_async at its definition site (inbox and
    send_message both import it lazily from there) and capture kwargs."""
    calls: list[dict] = []

    def fake_async(**kw):
        calls.append(kw)
        return "task_fake"

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn_async", fake_async)
    return calls


def test_busy_send_enqueues(two_sessions, busy_target, monkeypatch):
    calls = _capture_async(monkeypatch)
    out = _send_message_impl("hello t1", to="t1:b1")
    assert "[queued]" in out
    assert "busy" in out
    assert calls == []                      # nothing delivered yet
    assert inbox.pending_count("t1") == 1


def test_drain_delivers_then_removes(two_sessions, busy_target, monkeypatch):
    calls = _capture_async(monkeypatch)
    _send_message_impl("hello t1", to="t1:b1")
    assert inbox.pending_count("t1") == 1

    # Release the busy state before draining (turn ended).
    from openprogram.agent import run_control
    run_control.unregister_cancel_event("t1", busy_target)

    delivered = inbox.drain("t1")
    assert delivered == 1
    assert inbox.pending_count("t1") == 0
    kw = calls[0]
    assert kw["session_id"] == "t1"
    assert kw["caller_session_id"] == "p1"   # reply routes back to sender
    assert kw["caller_msg_id"] == "a1"
    assert kw["prompt"].startswith("[message from p1:a1]")
    assert "hello t1" in kw["prompt"]
    # Delivered onto the target's current head.
    head = (two_sessions.get_session("t1") or {}).get("head_id")
    assert kw["branch_from"] == head


def test_drain_leaves_entry_on_failed_delivery(two_sessions, busy_target, monkeypatch):
    """Deliver-then-delete: a failed submission keeps the entry queued."""
    _send_message_impl("keep me", to="t1:b1")

    def boom(**kw):
        raise RuntimeError("runner down")
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn_async", boom)
    assert inbox.drain("t1") == 0
    assert inbox.pending_count("t1") == 1


def test_queued_delivery_inherits_spawn_depth(two_sessions, busy_target, monkeypatch):
    from openprogram.functions.tools.send_message.send_message.depth import (
        set_spawn_depth, _spawn_depth,
    )
    calls = _capture_async(monkeypatch)
    tok = set_spawn_depth(3)
    try:
        _send_message_impl("deep hello", to="t1:b1")
    finally:
        _spawn_depth.reset(tok)
    delivered = inbox.drain("t1")
    assert delivered == 1
    assert calls[0]["spawn_depth"] == 4      # recorded depth 3, child +1


def test_cap_drops_oldest_and_notifies_sender(two_sessions):
    for i in range(inbox.MAX_PENDING):
        assert inbox.enqueue(
            "t1", message=f"msg {i}", sender_session_id="p1",
            sender_msg_id="a1", sender_agent_id="main", agent_id="main",
            spawn_depth=0, target_head_id="b1",
        ) == "queued"
    assert inbox.pending_count("t1") == inbox.MAX_PENDING

    assert inbox.enqueue(
        "t1", message="one too many", sender_session_id="p1",
        sender_msg_id="a1", sender_agent_id="main", agent_id="main",
        spawn_depth=0, target_head_id="b1",
    ) == "queued"
    assert inbox.pending_count("t1") == inbox.MAX_PENDING
    path = inbox._inbox_path("t1")
    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    messages = [e["message"] for e in entries]
    assert "msg 0" not in messages           # oldest dropped
    assert "one too many" in messages
    # The dropped message's sender got a system notice.
    notes = [m for m in (two_sessions.get_messages("p1") or [])
             if "inbox is full" in str(m.get("content") or "")]
    assert notes
    assert "msg 0" in notes[0]["content"]


def test_duplicate_within_window_rejected(two_sessions, busy_target, monkeypatch):
    _capture_async(monkeypatch)
    out1 = _send_message_impl("same words", to="t1:b1")
    out2 = _send_message_impl("same words", to="t1:b1")
    assert "[queued]" in out1
    assert "duplicate message ignored" in out2
    assert inbox.pending_count("t1") == 1


def test_duplicate_outside_window_accepted(two_sessions):
    assert inbox.enqueue(
        "t1", message="same words", sender_session_id="p1",
        sender_msg_id="a1", sender_agent_id="main", agent_id="main",
        spawn_depth=0, target_head_id="b1",
    ) == "queued"
    # Age the queued copy past the dedup window.
    path = inbox._inbox_path("t1")
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["entries"][0]["enqueued_at"] -= inbox.DEDUP_WINDOW_SECS + 1
    path.write_text(json.dumps(blob), encoding="utf-8")
    assert inbox.enqueue(
        "t1", message="same words", sender_session_id="p1",
        sender_msg_id="a1", sender_agent_id="main", agent_id="main",
        spawn_depth=0, target_head_id="b1",
    ) == "queued"
    assert inbox.pending_count("t1") == 2
