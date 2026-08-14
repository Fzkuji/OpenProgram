"""DAG layout ↔ dag-layout-spec.html 13 个场景的逐场景核对。

权威是 docs/reference/design/runtime/dag/dag-layout-spec.html 里的 SVG 坐标：
每个节点画在 (PADX + x*COL, PADY + y*ROW)，所以 SVG 的 x = 后端的
``_lane + _tier``（列号），SVG 的 y = 后端的 ``_depth``（行号）。本文件把
每张图的 (x, y) 逐个抄成断言。

不属于后端布局的场景（12 状态描边 / 13 分支名 badge 位置）只断言它们
依赖的布局前提——描边和 badge 由前端画，后端只保证行列。
"""
from __future__ import annotations

from openprogram.webui.graph_layout import annotate_graph


def _annotate(nodes):
    out = annotate_graph([dict(n) for n in nodes], None)
    return {n["id"]: n for n in out}


def _col(node) -> int:
    return (node.get("_lane", 0) or 0) + (node.get("_tier", 0) or 0)


def _pos(by_id, nid) -> tuple[int, float]:
    """(列, 行) —— 直接对应 spec.html 的 node(null, x, y, ...)。"""
    n = by_id[nid]
    return _col(n), n.get("_depth", 0) or 0


def _assert_grid(by_id, expected: dict[str, tuple[int, float]]):
    actual = {nid: _pos(by_id, nid) for nid in expected}
    assert actual == expected


def _no_overlap(by_id) -> bool:
    seen = set()
    for n in by_id.values():
        cell = (_col(n), n.get("_depth", 0) or 0)
        if cell in seen:
            return False
        seen.add(cell)
    return True


def _root():
    return {"id": "ROOT", "role": "user", "display": "root", "created_at": 0}


# ── 场景 1：单条对话 ──────────────────────────────────────────────

def test_scene1_single_turn():
    """ROOT(0,0) / user(1,1) / llm(2,2)，全在 lane 0。"""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
    ])
    _assert_grid(by, {"ROOT": (0, 0), "u1": (1, 1), "l1": (2, 2)})
    assert _no_overlap(by)


# ── 场景 2：多轮对话 ──────────────────────────────────────────────

def test_scene2_multi_turn():
    """user 永远 tier=1、llm 永远 tier=2，第二轮回到列 1。行逐轮递增。"""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
        {"id": "u2", "role": "user", "caller": "ROOT",
         "predecessor": "l1", "created_at": 3},
        {"id": "l2", "role": "assistant", "caller": "u2",
         "predecessor": "u2", "created_at": 4},
    ])
    _assert_grid(by, {
        "ROOT": (0, 0), "u1": (1, 1), "l1": (2, 2),
        "u2": (1, 3), "l2": (2, 4),
    })
    assert _no_overlap(by)


# ── 场景 3：retry 多分支 ──────────────────────────────────────────

def test_scene3_retry_fork_two_columns():
    """fork 占两列：竖线列 3（不放节点）+ 节点列 4~5。fork 首节点与分叉点同行。"""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
        {"id": "u2", "role": "user", "caller": "ROOT",
         "predecessor": "l1", "created_at": 3},
        {"id": "l2", "role": "assistant", "caller": "u2",
         "predecessor": "u2", "created_at": 4},
        # u1 的改写：与 u1 共享 predecessor(ROOT) → 新分支
        {"id": "fu", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 5},
        {"id": "fl", "role": "assistant", "caller": "fu",
         "predecessor": "fu", "created_at": 6},
    ])
    _assert_grid(by, {
        "ROOT": (0, 0), "u1": (1, 1), "l1": (2, 2),
        "u2": (1, 3), "l2": (2, 4),
        "fu": (4, 1), "fl": (5, 2),
    })
    assert _no_overlap(by)


# ── 场景 4：AI 调用工具 ───────────────────────────────────────────

def test_scene4_tool_call_tier3():
    """工具是 llm 的子调用 → tier=3、lane 仍 0，落在 (3,3)。"""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
        {"id": "t1", "role": "tool", "caller": "l1", "created_at": 3},
    ])
    _assert_grid(by, {
        "ROOT": (0, 0), "u1": (1, 1), "l1": (2, 2), "t1": (3, 3),
    })
    assert _no_overlap(by)


# ── 场景 5：手动调用函数（带内部子调用）─────────────────────────

def test_scene5_manual_function_with_internals():
    """手动调函数挂在当前对话链上（tier=1，不另开 lane），内部子调用逐层 +1。"""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
        # gui_agent：手动调，接在当前 head(l1) 后，caller=ROOT
        {"id": "gui", "role": "code", "caller": "ROOT",
         "predecessor": "l1", "created_at": 3},
        {"id": "step", "role": "code", "caller": "gui", "created_at": 4},
        {"id": "ilm1", "role": "assistant", "caller": "step", "created_at": 5},
        {"id": "concl", "role": "code", "caller": "gui", "created_at": 6},
        {"id": "ilm2", "role": "assistant", "caller": "concl", "created_at": 7},
    ])
    _assert_grid(by, {
        "ROOT": (0, 0), "u1": (1, 1), "l1": (2, 2),
        "gui": (1, 3), "step": (2, 4), "ilm1": (3, 5),
        "concl": (2, 6), "ilm2": (3, 7),
    })
    assert _no_overlap(by)


# ── 场景 6：综合（多轮 + retry + 工具）──────────────────────────

def _scene6_nodes(with_tool: bool):
    nodes = [
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
    ]
    if with_tool:
        nodes.append(
            {"id": "t1", "role": "tool", "caller": "l1", "created_at": 3})
    nodes += [
        {"id": "u2", "role": "user", "caller": "ROOT",
         "predecessor": "l1", "created_at": 4},
        {"id": "l2", "role": "assistant", "caller": "u2",
         "predecessor": "u2", "created_at": 5},
        {"id": "fu", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 6},
        {"id": "fl", "role": "assistant", "caller": "fu",
         "predecessor": "fu", "created_at": 7},
    ]
    return nodes


def test_scene6_combined_expanded():
    """lane0 占列 0~3（含 tier3 工具）→ fork 竖线列 4 → fork 节点列 5~6。"""
    by = _annotate(_scene6_nodes(with_tool=True))
    _assert_grid(by, {
        "ROOT": (0, 0), "u1": (1, 1), "l1": (2, 2), "t1": (3, 3),
        "u2": (1, 4), "l2": (2, 5),
        "fu": (5, 1), "fl": (6, 2),
    })
    assert _no_overlap(by)


# ── 场景 7：收起子调用 → 后面分支左移 ───────────────────────────

def test_scene7_collapsed_shifts_left_and_up():
    """同一棵树收起工具：fork 从列 5 左移到列 4，追问/回复2 从行 4/5 上移到 3/4。"""
    by = _annotate(_scene6_nodes(with_tool=False))
    _assert_grid(by, {
        "ROOT": (0, 0), "u1": (1, 1), "l1": (2, 2),
        "u2": (1, 3), "l2": (2, 4),
        "fu": (4, 1), "fl": (5, 2),
    })
    assert _no_overlap(by)


def test_scene7_vs_scene6_delta():
    """收起相对展开：横向左移一列、纵向上移一行——两个方向都紧凑。"""
    expanded = _annotate(_scene6_nodes(with_tool=True))
    collapsed = _annotate(_scene6_nodes(with_tool=False))
    assert _col(collapsed["fu"]) == _col(expanded["fu"]) - 1
    assert collapsed["u2"]["_depth"] == expanded["u2"]["_depth"] - 1


# ── 场景 8：分支合并（merge 节点）───────────────────────────────

def test_scene8_merge_lands_in_base_lane():
    """merge 落在 base lane、tier=2（对话层），peer 分支不因合并改列。"""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 1},
        {"id": "la", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
        {"id": "fu", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 3},
        {"id": "lb", "role": "assistant", "caller": "fu",
         "predecessor": "fu", "created_at": 4},
        # merge：base 主线延续（predecessor = base tip），peer 靠汇入线（前端画）
        {"id": "mg", "role": "assistant", "caller": "u1",
         "predecessor": "la", "created_at": 5},
    ])
    assert by["mg"]["_lane"] == by["la"]["_lane"]  # merge 落 base lane
    assert by["mg"]["_tier"] == 2                  # 对话层
    _assert_grid(by, {
        "ROOT": (0, 0), "u1": (1, 1), "la": (2, 2),
        "fu": (4, 1), "lb": (5, 2),
    })
    assert _no_overlap(by)


# ── 场景 9：分支间发消息（send_to_branch）───────────────────────

def test_scene9_inter_branch_message_appends_to_target():
    """通信在目标分支末尾追加一个 user 节点：留在目标 lane，tier=1，行号往下。

    通信线本身是前端画的（点线 1 5，hover 才显示），后端只负责这个新节点
    的落位。
    """
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 1},
        {"id": "la", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
        {"id": "bu", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 3},
        {"id": "bl", "role": "assistant", "caller": "bu",
         "predecessor": "bu", "created_at": 4},
        # A 发给 B：B 末尾多一个 user（metadata.from_branch 标来源）
        {"id": "msg", "role": "user", "caller": "ROOT", "predecessor": "bl",
         "metadata": {"from_branch": "A"}, "created_at": 5},
    ])
    assert by["msg"]["_lane"] == by["bu"]["_lane"]   # 留在目标分支 lane
    assert by["msg"]["_tier"] == 1                    # 对话层 user
    assert by["msg"]["_depth"] == by["bl"]["_depth"] + 1
    assert _no_overlap(by)


# ── 场景 10：spawn → attach 回流 ────────────────────────────────

def _scene10_nodes():
    return [
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 1},
        {"id": "la", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
        {"id": "sp", "role": "code", "caller": "la", "created_at": 3},
        # 子分支 B 的根：caller = spawn 节点，无 predecessor
        {"id": "bu", "role": "user", "caller": "sp", "predecessor": None,
         "source": "agent_spawn", "created_at": 4},
        {"id": "bl", "role": "assistant", "caller": "bu",
         "predecessor": "bu", "created_at": 5},
        # A 收到回流后继续
        {"id": "la2", "role": "assistant", "caller": "u1",
         "predecessor": "la", "created_at": 6},
    ]


def test_scene10_spawn_root_same_row_as_spawn_node():
    """关键规则：子分支首节点与 spawn_branch 节点【同一行】。"""
    by = _annotate(_scene10_nodes())
    assert by["bu"]["_depth"] == by["sp"]["_depth"]


def test_scene10_spawn_branch_columns():
    """spawn 分支自成 lane、tier 按对话层（user=1 / llm=2），在 lane0 右边。"""
    by = _annotate(_scene10_nodes())
    assert by["bu"]["_tier"] == 1
    assert by["bl"]["_tier"] == 2
    assert by["bu"]["_lane"] != by["u1"]["_lane"]
    assert by["bl"]["_lane"] == by["bu"]["_lane"]
    # 子分支整条在 lane0 实际占用列（含 tier3 的 spawn 节点）右边
    assert _col(by["bu"]) > _col(by["sp"])
    _assert_grid(by, {
        "ROOT": (0, 0), "u1": (1, 1), "la": (2, 2), "sp": (3, 3),
        "bu": (5, 3), "bl": (6, 4),
    })
    assert _no_overlap(by)


def test_scene10_attach_return_stays_in_base_lane():
    """attach 回流后 A 继续的那个节点仍在 base lane、tier=2、行在下面。"""
    by = _annotate(_scene10_nodes())
    assert by["la2"]["_lane"] == by["la"]["_lane"]
    assert by["la2"]["_tier"] == 2
    assert by["la2"]["_depth"] > by["la"]["_depth"]


# ── 场景 11：执行子树默认聚合（⚒N 徽标 ↔ 展开）─────────────────

def _scene11_nodes(expanded: bool):
    nodes = [
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
        {"id": "u2", "role": "user", "caller": "ROOT",
         "predecessor": "l1", "created_at": 3},
        {"id": "l2", "role": "assistant", "caller": "u2",
         "predecessor": "u2", "created_at": 4},
    ]
    if expanded:
        # 展开后三个工具都是 l2 的直接子调用：tier=3 平级，逐行往下
        nodes += [
            {"id": "bash", "role": "tool", "caller": "l2", "created_at": 5},
            {"id": "fetch", "role": "tool", "caller": "l2", "created_at": 6},
            {"id": "meteo", "role": "tool", "caller": "l2", "created_at": 7},
        ]
    return nodes


def test_scene11_default_collapsed_is_conversation_layer_only():
    """默认（执行子树不在可见集）：图只有对话层骨架，工具不占任何行列。"""
    by = _annotate(_scene11_nodes(expanded=False))
    _assert_grid(by, {
        "ROOT": (0, 0), "u1": (1, 1), "l1": (2, 2),
        "u2": (1, 3), "l2": (2, 4),
    })
    assert _no_overlap(by)


def test_scene11_expanded_tools_are_flat_siblings():
    """展开后工具全是该轮 llm 的 tier=3 平级兄弟：同列、逐行排，不互相嵌套。"""
    by = _annotate(_scene11_nodes(expanded=True))
    for nid in ("bash", "fetch", "meteo"):
        assert by[nid]["_tier"] == 3, nid
        assert by[nid]["_lane"] == by["l2"]["_lane"], nid
    cols = {_col(by[n]) for n in ("bash", "fetch", "meteo")}
    assert len(cols) == 1                       # 同一列
    rows = [by[n]["_depth"] for n in ("bash", "fetch", "meteo")]
    assert rows == sorted(rows) and len(set(rows)) == 3   # 逐行往下
    assert _no_overlap(by)


def test_scene11_expand_only_affects_that_turn():
    """展开一轮的执行子树不移动其它对话层节点的行列。"""
    collapsed = _annotate(_scene11_nodes(expanded=False))
    expanded = _annotate(_scene11_nodes(expanded=True))
    for nid in ("ROOT", "u1", "l1", "u2", "l2"):
        assert _pos(collapsed, nid) == _pos(expanded, nid), nid


# ── 场景 12：状态与徽标（描边/徽标是前端画的，后端只保证不占格）──

def test_scene12_status_does_not_consume_grid():
    """running/error/cancelled 只改描边：带 status 的节点与不带的落位相同，
    且不产生任何额外占位节点（规则② 推论：占位框占格即违规）。"""
    plain = [
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
    ]
    styled = [dict(n) for n in plain]
    styled[2]["status"] = "running"
    a, b = _annotate(plain), _annotate(styled)
    assert len(a) == len(b)
    for nid in a:
        assert _pos(a, nid) == _pos(b, nid), nid


def test_scene12_loop_siblings_each_keep_a_row():
    """×N 循环折叠是纯显示（前端合并）：后端仍给 N 个同构兄弟各一行、同一列。"""
    nodes = [
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT", "created_at": 1},
        {"id": "l1", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
    ]
    nodes += [
        {"id": f"m{i}", "role": "tool", "caller": "l1",
         "function": "open_meteo", "created_at": 3 + i}
        for i in range(19)
    ]
    by = _annotate(nodes)
    loop = [by[f"m{i}"] for i in range(19)]
    assert {_col(n) for n in loop} == {_col(by["l1"]) + 1}   # 同一列 tier3
    assert len({n["_depth"] for n in loop}) == 19            # 各占一行
    assert _no_overlap(by)


def test_scene12_cross_session_spawn_root_hangs_off_root():
    """跨会话 spawn：目标会话里分支根挂 ROOT（↗ 徽标是前端画的），
    tier=1、lane 0——它在自己会话里就是第一条分支。"""
    by = _annotate([
        _root(),
        {"id": "bu", "role": "user", "caller": "ROOT", "predecessor": None,
         "source": "agent_spawn",
         "metadata": {"spawned_from_session": "X"}, "created_at": 1},
        {"id": "bl", "role": "assistant", "caller": "bu",
         "predecessor": "bu", "created_at": 2},
    ])
    assert by["bu"]["_tier"] == 1
    assert by["bu"]["_lane"] == 0
    assert _no_overlap(by)


# ── 场景 13：分支名 badge 的布局前提（badge 本体由前端画）──────

def test_scene13_badge_anchor_row_is_free():
    """badge 锚在分支末节点正下方一行——后端必须保证那一格没有节点，
    否则 badge 会压到别的节点上。"""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 1},
        {"id": "la", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
        {"id": "fu", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 3},
        {"id": "lb", "role": "assistant", "caller": "fu",
         "predecessor": "fu", "created_at": 4},
    ])
    occupied = {(_col(n), n["_depth"]) for n in by.values()}
    for tip in ("la", "lb"):
        anchor = (_col(by[tip]), by[tip]["_depth"] + 1)
        assert anchor not in occupied, tip


def test_scene13_two_branch_tips_share_a_row():
    """两条分支的末节点同行时 badge 才可能横向碰撞（前端据此下移一行）——
    后端要真的把它们放在同一行、不同列。"""
    by = _annotate([
        _root(),
        {"id": "u1", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 1},
        {"id": "la", "role": "assistant", "caller": "u1",
         "predecessor": "u1", "created_at": 2},
        {"id": "fu", "role": "user", "caller": "ROOT",
         "predecessor": "ROOT", "created_at": 3},
        {"id": "lb", "role": "assistant", "caller": "fu",
         "predecessor": "fu", "created_at": 4},
    ])
    assert by["la"]["_depth"] == by["lb"]["_depth"]
    assert _col(by["la"]) != _col(by["lb"])
