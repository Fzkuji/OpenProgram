"""End-to-end DAG mutation scenarios against the real SessionStore.

Static review keeps missing cross-module side effects (a handler that
is locally correct but breaks an invariant another module maintains —
the compaction/head bug was exactly that class). These tests run the
actual user flows — chat, fork, checkout, compact, rewind, branch
delete — on a real on-disk store and check the structural invariants
after every step.

Invariants checked by ``_check``:
  * the session head exists in the graph,
  * ``get_branch`` walks without raising and ends at the head,
  * every branch node's predecessor is present (or a legal terminus).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.store.session.session_store import SessionStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionStore:
    st = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db", lambda: st)
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store", lambda: st)
    return st


def _turn(store: SessionStore, sid: str, i: int,
          pred: str | None) -> str:
    """One user+assistant turn; returns the new tip (assistant id)."""
    uid, aid = f"u{i}", f"u{i}_reply"
    store.append_message(sid, {"id": uid, "role": "user",
                               "content": f"question {i}",
                               "predecessor": pred})
    store.append_message(sid, {"id": aid, "role": "assistant",
                               "content": f"answer {i}",
                               "predecessor": uid})
    return aid


def _seed(store: SessionStore, sid: str, n: int) -> str:
    store.create_session(sid, "main", title="t")
    tip = None
    for i in range(n):
        tip = _turn(store, sid, i, tip)
    return tip


def _check(store: SessionStore, sid: str) -> list[dict]:
    """Assert the structural invariants; return the active branch."""
    head = (store.get_session(sid) or {}).get("head_id")
    assert head, "session lost its head"
    branch = store.get_branch(sid)   # raises on a broken chain
    assert branch, "active branch is empty"
    assert branch[-1]["id"] == head, "branch tip diverged from head"
    return branch


# --- plain chat ------------------------------------------------------

def test_chat_appends_keep_the_branch_walkable(store):
    tip = _seed(store, "s", 5)
    branch = _check(store, "s")
    assert branch[-1]["id"] == tip
    assert len(branch) == 10


# --- fork + checkout -------------------------------------------------

def test_fork_and_checkout_between_branches(store):
    _seed(store, "s", 3)               # tip u2_reply
    # Fork off turn 0's reply — a second branch from mid-chain.
    store.append_message("s", {"id": "f0", "role": "user",
                               "content": "fork question",
                               "predecessor": "u0_reply"})
    store.append_message("s", {"id": "f0_reply", "role": "assistant",
                               "content": "fork answer",
                               "predecessor": "f0"})
    branch = _check(store, "s")
    assert branch[-1]["id"] == "f0_reply"

    # Back to the original branch and forth again.
    store.set_head("s", "u2_reply")
    assert [m["id"] for m in _check(store, "s")][-1] == "u2_reply"
    store.set_head("s", "f0_reply")
    assert [m["id"] for m in _check(store, "s")] == [
        "u0", "u0_reply", "f0", "f0_reply"]


# --- compaction ------------------------------------------------------

def test_chat_continues_after_compaction(store):
    from openprogram.context.persistence import Persister

    tip = _seed(store, "s", 5)
    msgs = store.get_messages("s")
    sid = Persister().insert_summary_node(
        "s", summary_text="recap", cut_idx=6, history=msgs)
    assert sid
    branch = _check(store, "s")
    assert branch[-1]["id"] == tip, "kept tail must stay the active tip"

    # Next turn chains off the old tip, not the summary.
    new_tip = _turn(store, "s", 99, tip)
    assert _check(store, "s")[-1]["id"] == new_tip


# --- rewind ----------------------------------------------------------

def test_rewind_restores_an_earlier_tip(store):
    _seed(store, "s", 4)
    from openprogram.agent._rewind import rewind_to

    res = rewind_to("s", "u2")          # undo turns 2 and 3
    assert not res.get("error"), res
    branch = _check(store, "s")
    assert branch[-1]["id"] == "u1_reply"
    assert {m["id"] for m in branch} == {
        "u0", "u0_reply", "u1", "u1_reply"}


def test_rewind_leaves_sibling_branches_alone(store):
    """rewind walks by seq, but a sibling branch forked earlier may
    hold larger seqs — rewinding one branch must not mark or reparent
    nodes that live on another."""
    _seed(store, "s", 2)               # u0..u1_reply
    # A sibling branch appended AFTER (larger seqs than) the main chain.
    store.append_message("s", {"id": "f0", "role": "user",
                               "content": "fork", "predecessor": "u0_reply"})
    store.append_message("s", {"id": "f0_reply", "role": "assistant",
                               "content": "fork answer", "predecessor": "f0"})
    # Work on the MAIN branch again and rewind its last turn.
    store.set_head("s", "u1_reply")
    from openprogram.agent._rewind import rewind_to
    res = rewind_to("s", "u1")
    assert not res.get("error"), res
    _check(store, "s")

    # The sibling branch must still be fully checkout-able.
    store.set_head("s", "f0_reply")
    sibling = _check(store, "s")
    assert [m["id"] for m in sibling] == ["u0", "u0_reply", "f0", "f0_reply"]


# --- branch delete ---------------------------------------------------

def test_delete_side_branch_keeps_active_branch_intact(store):
    _seed(store, "s", 3)
    store.append_message("s", {"id": "f0", "role": "user",
                               "content": "fork", "predecessor": "u0_reply"})
    store.append_message("s", {"id": "f0_reply", "role": "assistant",
                               "content": "fork answer", "predecessor": "f0"})
    store.set_head("s", "u2_reply")

    deleted = store.delete_branch_tail("s", "f0")
    assert deleted == 2
    branch = _check(store, "s")
    assert len(branch) == 6
    assert not store.message_exists("s", "f0_reply")


def test_delete_the_active_branch_must_not_leave_a_dangling_head(store):
    """Deleting the branch the head sits on: the store must move the
    head somewhere real (or refuse), never leave it pointing at a
    deleted node — a dangling head renders the whole session empty."""
    _seed(store, "s", 2)
    store.append_message("s", {"id": "f0", "role": "user",
                               "content": "fork", "predecessor": "u0_reply"})
    store.append_message("s", {"id": "f0_reply", "role": "assistant",
                               "content": "fork answer", "predecessor": "f0"})
    assert (store.get_session("s") or {}).get("head_id") == "f0_reply"

    store.delete_branch_tail("s", "f0")

    head = (store.get_session("s") or {}).get("head_id")
    assert head and store.message_exists("s", head), (
        f"head {head!r} dangles after deleting its branch")
    _check(store, "s")
