"""Regression guard: the web chat view must show ONLY the active branch.

The bug: ``handle_load_session`` built the displayed chat list from
``all_msgs`` (every node in the DAG, all branches) and then tried to
*widen* it toward the active branch with a ROOT-prepend gap-fill. That
gap-fill used "ROOT-parented + timestamp earlier than the current
chain" as its heuristic, which cannot tell a genuinely-missing earlier
turn of THIS branch apart from the root of a DIFFERENT sibling branch —
so forked/retried sessions leaked other branches' turns into the chat.

``active_branch_chain`` fixes it by making the active-branch node-id set
(from ``get_branch(head)``) authoritative: keep only ``all_msgs`` rows
whose id is on the active branch, in seq order, with tool_calls already
folded into their parent assistant. These tests pin that contract using
the exact message shapes seen in real branched sessions.
"""
from __future__ import annotations

from openprogram.contextgit import active_branch_chain


def _msg(mid: str, role: str, pred: str | None, ts: int, content: str = ""):
    return {
        "id": mid,
        "role": role,
        "predecessor": pred if pred is not None else "ROOT",
        "timestamp": ts,
        "content": content or mid,
    }


def test_mid_conversation_retry_excludes_other_branch():
    """u1→a1→u2→a2  plus a retry sibling a2b under u2, plus a totally
    separate ROOT turn u0→a0. Active head = a2b. Only the a2b branch
    must show; u0/a0 (different branch) must NOT leak in."""
    all_msgs = [
        _msg("u0", "user", None, 100, "OTHER-BRANCH root"),
        _msg("a0", "assistant", "u0", 101, "other reply"),
        _msg("u1", "user", None, 200, "hi"),
        _msg("a1", "assistant", "u1", 201, "hello"),
        _msg("u2", "user", "a1", 202, "q"),
        _msg("a2", "assistant", "u2", 203, "ANSWER-A"),
        _msg("a2b", "assistant", "u2", 300, "ANSWER-B retry"),
    ]
    # get_branch(head=a2b) walks a2b→u2→a1→u1 (root-first after reverse).
    branch_ids = {"u1", "a1", "u2", "a2b"}
    chain = active_branch_chain(all_msgs, branch_ids, head="a2b")
    ids = [m["id"] for m in chain]
    assert ids == ["u1", "a1", "u2", "a2b"], ids
    # The other branch's root and the sibling answer are gone.
    assert "u0" not in ids and "a0" not in ids
    assert "a2" not in ids


def test_active_branch_is_oldest_first():
    all_msgs = [
        _msg("u1", "user", None, 100),
        _msg("a1", "assistant", "u1", 101),
        _msg("u2", "user", "a1", 102),
        _msg("a2", "assistant", "u2", 103),
    ]
    branch_ids = {"u1", "a1", "u2", "a2"}
    chain = active_branch_chain(all_msgs, branch_ids, head="a2")
    assert [m["id"] for m in chain] == ["u1", "a1", "u2", "a2"]


def test_tool_calls_survive_the_branch_filter():
    """aggregate_tool_messages folds tool rows into the parent assistant
    BEFORE we filter. The assistant is on the branch, so its tool_calls
    ride along even though the standalone tool node id is not a branch
    node (tool nodes hang off ``caller``, never on the conv path)."""
    a1 = _msg("a1", "assistant", "u1", 101)
    a1["tool_calls"] = [{"tool_call_id": "t1", "tool": "bash",
                          "input": "ls", "result": "ok", "is_error": False}]
    all_msgs = [_msg("u1", "user", None, 100), a1]
    branch_ids = {"u1", "a1"}  # tool node t1 is NOT here — it's a caller-child
    chain = active_branch_chain(all_msgs, branch_ids, head="a1")
    assert [m["id"] for m in chain] == ["u1", "a1"]
    assert chain[1]["tool_calls"][0]["tool"] == "bash"


def test_empty_branch_ids_falls_back_to_linear_history():
    """If get_branch returned nothing (stale/None head after a mid-turn
    crash), don't blank the page — fall back to the head's predecessor
    walk over all_msgs."""
    all_msgs = [
        _msg("u1", "user", None, 100),
        _msg("a1", "assistant", "u1", 101),
    ]
    chain = active_branch_chain(all_msgs, set(), head="a1")
    assert [m["id"] for m in chain] == ["u1", "a1"]


def test_no_head_returns_all_msgs():
    all_msgs = [_msg("u1", "user", None, 100)]
    chain = active_branch_chain(all_msgs, set(), head=None)
    assert [m["id"] for m in chain] == ["u1"]
