"""压缩后 Context tab 的明暗集合不能和 DAG 画的节点脱节。

压缩把保留尾部复制成新的 ``k_`` 行、挂到 ``summary_`` 节点下。DAG 的
graph_layout/filter.py 把这两类 id 全过滤掉（画出来会是同一段对话的第
二份），于是 /context-range 若原样返回分支 id，前端拿到的集合和图上节
点完全不相交——整图全暗。

修法是 ``k_`` 行落库时记 ``original_id`` 回指，端点按它翻译回去。
"""
from __future__ import annotations

from openprogram.context.persistence import _original_id_of, original_ids


def test_original_id_prefers_backref_over_own_id():
    assert _original_id_of({"id": "k_ab12", "original_id": "u1"}) == "u1"


def test_original_id_falls_back_to_own_id_for_legacy_rows():
    """回指字段是后加的，老数据没有 → 原样返回，退回修复前的行为。"""
    assert _original_id_of({"id": "u1"}) == "u1"


def test_original_ids_translates_kept_tail_and_drops_summary():
    branch = [
        {"id": "summary_ff00", "role": "system"},
        {"id": "k_aa11", "role": "user", "original_id": "u2"},
        {"id": "k_bb22", "role": "assistant", "original_id": "u2_reply"},
    ]
    # summary 节点图上不存在 → 剔除；k_ 行翻译回真实节点
    assert original_ids(branch) == ["u2", "u2_reply"]


def test_original_ids_intersects_the_drawn_node_set():
    """回归点：翻译后的集合必须和 DAG 可见集有交集，否则整图变暗。"""
    from openprogram.webui.graph_layout import annotate_graph

    drawn = {
        n["id"] for n in annotate_graph([
            {"id": "ROOT", "role": "user", "display": "root", "created_at": 0},
            {"id": "u1", "role": "user", "caller": "ROOT", "created_at": 1},
            {"id": "u1_reply", "role": "assistant",
             "predecessor": "u1", "created_at": 2},
            {"id": "u2", "role": "user", "caller": "ROOT",
             "predecessor": "u1_reply", "created_at": 3},
            {"id": "u2_reply", "role": "assistant",
             "predecessor": "u2", "created_at": 4},
            # 压缩产物：图上被过滤，不该出现在 drawn 里
            {"id": "summary_ff00", "role": "system", "created_at": 5},
            {"id": "k_aa11", "role": "user",
             "predecessor": "summary_ff00", "original_id": "u2",
             "created_at": 6},
        ], None)
    }
    assert "summary_ff00" not in drawn and "k_aa11" not in drawn

    compacted_branch = [
        {"id": "summary_ff00", "role": "system"},
        {"id": "k_aa11", "role": "user", "original_id": "u2"},
        {"id": "k_bb22", "role": "assistant", "original_id": "u2_reply"},
    ]
    lit = set(original_ids(compacted_branch))
    assert lit <= drawn          # 翻译后每个 id 都是图上真有的节点
    assert lit == {"u2", "u2_reply"}
    # 被压缩掉的前缀不在集合里 → 前端把它们画暗，正是想要的效果
    assert "u1" not in lit


def test_persister_stamps_original_id_on_kept_tail():
    """insert_summary_node 复制保留尾部时必须写回指。"""
    from openprogram.context.persistence import Persister

    written: list[dict] = []

    class _FakeDB:
        def append_message(self, _sid, row):
            written.append(dict(row))

        def set_head(self, _sid, _hid):
            pass

    import openprogram.agent.session_db as _sdb
    orig = _sdb.default_db
    # 只在本用例内替换，结束即还原——default_db 背后是进程级单例，
    # 泄漏出去会污染同批次其它测试。
    _sdb.default_db = lambda: _FakeDB()  # type: ignore[assignment]
    try:
        history = [
            {"id": "u1", "role": "user", "content": "a", "timestamp": 1.0},
            {"id": "u2", "role": "user", "content": "b", "timestamp": 2.0},
            {"id": "u3", "role": "user", "content": "c", "timestamp": 3.0},
        ]
        sid = Persister().insert_summary_node(
            "s1", summary_text="sum", cut_idx=1, history=history,
        )
    finally:
        _sdb.default_db = orig  # type: ignore[assignment]

    assert sid and sid.startswith("summary_")
    kept = [r for r in written if r["id"].startswith("k_")]
    assert [r["original_id"] for r in kept] == ["u2", "u3"]


def test_recompaction_keeps_pointing_at_the_real_node():
    """二次压缩复制的是 k_ 行本身，回指不能退化成中间那份副本。"""
    assert _original_id_of({"id": "k_new", "original_id": "u2"}) == "u2"
