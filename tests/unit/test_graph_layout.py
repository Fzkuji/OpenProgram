"""DAG viewport layout — annotate_graph lane/tier/depth assignment.

Locks the layout rules from docs/design/runtime/dag/dag-layout-algorithm.md
(+ the 7-scene dag-layout-spec.html):

  * lane: count branches, number in appearance order from 0
  * tier: ROOT=0, user=1, llm=2, tool/code=3, sub-call +1 (follows caller)
  * depth: conversation order (follows predecessor + seq)
  * a fork lane clears the entire base lane (no overlap); multiple fork
    siblings each get their own column
  * a fork-branch continuation inherits the fork's lane (not lane 0)

These were previously verified only by hand via tools/dag_dump.py; this
suite makes them regression-proof.
"""
from __future__ import annotations

from openprogram.webui.graph_layout import annotate_graph


def _annotate(nodes):
    """Run annotate_graph on a list of node dicts, return {id: node}."""
    out = annotate_graph([dict(n) for n in nodes], None)
    return {n["id"]: n for n in out}


def _col(node) -> int:
    """Final column a node renders at = lane offset + tier indent."""
    return (node.get("_lane", 0) or 0) + (node.get("_tier", 0) or 0)


def _no_overlap(by_id) -> bool:
    """No two nodes share the same (column, depth) grid cell."""
    seen = set()
    for n in by_id.values():
        cell = (_col(n), n.get("_depth", 0) or 0)
        if cell in seen:
            return False
        seen.add(cell)
    return True


# Scenario builders (mirror dag-layout-spec.html). Tools/sub-calls have
# NO predecessor — they hang off their caller only.

def _root():
    return {"id": "ROOT", "role": "user", "display": "root", "created_at": 0}


def test_single_turn_all_lane_0():
    """One turn: ROOT/user/llm all lane 0, tier ladder 0/1/2."""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "predecessor": None, "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1", "predecessor": "u1", "created_at": 2},
    ])
    assert by["ROOT"]["_lane"] == 0 and by["ROOT"]["_tier"] == 0
    assert by["u1"]["_lane"] == 0 and by["u1"]["_tier"] == 1
    assert by["l1"]["_lane"] == 0 and by["l1"]["_tier"] == 2
    assert _no_overlap(by)


def test_multi_turn_all_lane_0():
    """Multiple turns stay on lane 0; each user back to tier 1."""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "predecessor": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1", "predecessor": "u1", "created_at": 2},
        {"id": "u2", "role": "user", "caller": "ROOT", "predecessor": "l1", "created_at": 3},
        {"id": "l2", "role": "assistant", "caller": "u2", "predecessor": "u2", "created_at": 4},
    ])
    for nid in ("u1", "u2"):
        assert by[nid]["_lane"] == 0 and by[nid]["_tier"] == 1
    for nid in ("l1", "l2"):
        assert by[nid]["_lane"] == 0 and by[nid]["_tier"] == 2
    assert _no_overlap(by)


def test_tool_call_indents_no_new_lane():
    """A tool (sub-call) indents to tier 3, stays on lane 0."""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "predecessor": None, "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1", "predecessor": "u1", "created_at": 2},
        {"id": "t1", "role": "tool", "caller": "l1", "created_at": 3},
    ])
    assert by["t1"]["_lane"] == 0
    assert by["t1"]["_tier"] == 3
    assert _no_overlap(by)


def test_manual_function_hangs_off_root():
    """Manual fn call stays on lane 0; its internals indent by tier.

    A NEW function run chains off the session's current head (here the
    prior assistant reply ``l1``), exactly like a new chat turn — so it
    carries a real ``predecessor`` and continues the trunk lane rather
    than forking. Only a retry (sharing a fork point) is predecessor-less
    off ROOT and gets its own lane; see ``run_agentic_function_call``."""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "predecessor": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1", "predecessor": "u1", "created_at": 2},
        {"id": "gui", "role": "code", "caller": "ROOT", "predecessor": "l1", "created_at": 3},
        {"id": "step", "role": "code", "caller": "gui", "created_at": 4},
        {"id": "ilm", "role": "assistant", "caller": "step", "created_at": 5},
    ])
    assert by["gui"]["_lane"] == 0
    assert by["step"]["_tier"] == by["gui"]["_tier"] + 1
    assert by["ilm"]["_tier"] == by["step"]["_tier"] + 1
    assert _no_overlap(by)


def test_root_function_call_same_spacing_as_chat():
    """Regression: a function call chained off the current head sits ONE
    unit from root — same horizontal spacing as a top-level chat message,
    not two units further out. Nested sub-calls keep relative +1 steps.

    A new run chains off the head (``u1`` here) like a new chat turn, so
    it stays on the trunk lane. (Only retries fork; see the retry test.)"""
    by = _annotate([
        _root(),
        # top-level chat message off ROOT
        {"id": "u1", "role": "user", "caller": "ROOT", "predecessor": "ROOT", "created_at": 1},
        # top-level function call chained off the head (u1), like a new turn
        {"id": "fn", "role": "tool", "caller": "ROOT", "predecessor": "u1", "created_at": 2},
        {"id": "sub", "role": "tool", "caller": "fn", "created_at": 3},
    ])
    # both are one unit from root, and equal to each other
    assert _col(by["fn"]) == _col(by["u1"])
    assert _col(by["fn"]) == _col(by["ROOT"]) + 1
    # nested sub-call keeps its relative one-unit step
    assert by["sub"]["_tier"] == by["fn"]["_tier"] + 1
    assert _no_overlap(by)


def test_retry_fork_clears_base_lane():
    """Retry → 2nd branch starts past the entire base lane (no overlap)."""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "predecessor": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1", "predecessor": "u1", "created_at": 2},
        {"id": "u1b", "role": "user", "caller": "ROOT", "predecessor": "ROOT", "created_at": 3},
        {"id": "l1b", "role": "assistant", "caller": "u1b", "predecessor": "u1b", "created_at": 4},
    ])
    # base lane occupies cols 0..2 (ROOT/user/llm); fork is to the right
    assert _col(by["u1b"]) > _col(by["l1"])
    assert by["u1b"]["_lane"] != 0
    assert _no_overlap(by)


def test_multiple_fork_siblings_distinct_columns():
    """Three forks off the same point → three distinct columns, no overlap."""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "predecessor": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1", "predecessor": "u1", "created_at": 2},
        {"id": "u1b", "role": "user", "caller": "ROOT", "predecessor": "ROOT", "created_at": 3},
        {"id": "l1b", "role": "assistant", "caller": "u1b", "predecessor": "u1b", "created_at": 4},
        {"id": "u1c", "role": "user", "caller": "ROOT", "predecessor": "ROOT", "created_at": 5},
        {"id": "l1c", "role": "assistant", "caller": "u1c", "predecessor": "u1c", "created_at": 6},
    ])
    cols = {_col(by["u1"]), _col(by["u1b"]), _col(by["u1c"])}
    assert len(cols) == 3  # all distinct
    assert _no_overlap(by)


def test_fork_continuation_inherits_fork_lane():
    """Chatting on a fork branch stays on the fork's lane, not lane 0."""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "predecessor": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1", "predecessor": "u1", "created_at": 2},
        {"id": "fk", "role": "user", "caller": "ROOT", "predecessor": "ROOT", "created_at": 3},
        {"id": "fkr", "role": "assistant", "caller": "fk", "predecessor": "fk", "created_at": 4},
        # continue on the fork branch (predecessor = fork reply)
        {"id": "fk2", "role": "user", "caller": "ROOT", "predecessor": "fkr", "created_at": 5},
        {"id": "fk2r", "role": "assistant", "caller": "fk2", "predecessor": "fk2", "created_at": 6},
    ])
    assert by["fk"]["_lane"] != 0
    assert by["fk2"]["_lane"] == by["fk"]["_lane"]  # continuation = same lane
    assert by["fk2r"]["_lane"] == by["fk"]["_lane"]
    assert _no_overlap(by)


def test_tool_plus_fork_no_overlap():
    """Base lane has a tool (tier 3); fork must clear it."""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "predecessor": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1", "predecessor": "u1", "created_at": 2},
        {"id": "t1", "role": "tool", "caller": "l1", "created_at": 3},
        {"id": "u1b", "role": "user", "caller": "ROOT", "predecessor": "ROOT", "created_at": 4},
        {"id": "l1b", "role": "assistant", "caller": "u1b", "predecessor": "u1b", "created_at": 5},
    ])
    assert _col(by["u1b"]) > _col(by["t1"])  # fork past the tool
    assert _no_overlap(by)


# Edge cases — must not crash, must not overlap.

def test_empty_graph():
    assert annotate_graph([], None) == []


def test_root_only():
    by = _annotate([_root()])
    assert by["ROOT"]["_lane"] == 0 and by["ROOT"]["_tier"] == 0


def test_deep_nesting_no_crash():
    """Function calling function … 10 levels deep — tier keeps climbing,
    no recursion blow-up, no overlap."""
    nodes = [_root()]
    prev = "ROOT"
    for i in range(10):
        nodes.append({"id": f"c{i}", "role": "code", "caller": prev, "created_at": i + 1})
        prev = f"c{i}"
    by = _annotate(nodes)
    assert by["c9"]["_tier"] > by["c0"]["_tier"]  # tier climbs with depth
    assert _no_overlap(by)


def test_self_referencing_caller_no_crash():
    """A node whose caller points at itself must not loop forever."""
    by = _annotate([
        _root(),
        {"id": "x", "role": "code", "caller": "x", "created_at": 1},
    ])
    assert "x" in by  # produced a result, didn't hang


def test_orphan_no_root():
    """A graph with no display=root node still lays out without overlap."""
    by = _annotate([
        {"id": "a", "role": "user", "created_at": 0},
        {"id": "b", "role": "assistant", "caller": "a", "predecessor": "a", "created_at": 1},
    ])
    assert _no_overlap(by)


# ── spawn 分支根：对话层裁决（dag-rendering.md 第一节 / 场景 10） ──

def _spawn_turn():
    """One turn whose llm spawned a sub-agent branch (clean mode)."""
    return [
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "predecessor": None, "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1", "predecessor": "u1", "created_at": 2},
        # spawn branch root: caller = the spawning turn, no predecessor
        {"id": "s1", "role": "user", "caller": "l1", "predecessor": None,
         "source": "agent_spawn", "created_at": 3},
        {"id": "sl1", "role": "assistant", "caller": "s1", "predecessor": "s1",
         "created_at": 4},
    ]


def test_spawn_root_tier_is_conversation_layer():
    """spawn 根 tier=1（对话层 user），不吃 caller 的执行层缩进。"""
    by = _annotate(_spawn_turn())
    assert by["s1"]["_tier"] == 1
    assert by["sl1"]["_tier"] == 2


def test_spawn_root_same_row_as_spawning_turn():
    """spawn 根与发起 spawn 的那轮同一行（spawn 边是水平的）。"""
    by = _annotate(_spawn_turn())
    assert by["s1"]["_depth"] == by["l1"]["_depth"]
    assert _no_overlap(by)


def test_spawn_branch_gets_own_lane():
    by = _annotate(_spawn_turn())
    assert by["s1"]["_lane"] != by["u1"]["_lane"]
    assert by["sl1"]["_lane"] == by["s1"]["_lane"]


def test_followup_reply_without_attach_reparents_to_receiver():
    """task_followup 回流没写 attach 指针时，reply 挂回收到回流的那轮，
    不能变成 depth=0 的孤儿根（dag-rendering.md 场景 10 回流语义）。"""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "predecessor": None, "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1", "predecessor": "u1", "created_at": 2},
        # 合成 followup user（display=runtime 被过滤）+ 它的 reply
        {"id": "fu", "role": "user", "source": "task_followup",
         "predecessor": "l1", "display": "runtime", "created_at": 3},
        {"id": "fu_reply", "role": "assistant", "source": "task_followup",
         "predecessor": "fu", "created_at": 4},
    ])
    assert "fu" not in by                      # 合成桥被过滤
    assert by["fu_reply"]["predecessor"] == "l1"  # 挂回收到回流的那轮
    assert by["fu_reply"]["_depth"] > by["l1"]["_depth"]
    assert by["fu_reply"]["_lane"] == by["l1"]["_lane"]
