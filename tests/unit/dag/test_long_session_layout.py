"""Long conversations must retain complete layout without recursive walks."""
from openprogram.webui.graph_layout import annotate_graph
from openprogram.webui.graph_layout import depth as depth_module


def test_long_conversation_layout_is_stack_safe_and_visits_parents_once(monkeypatch):
    count = 10_000
    rows = [{"id": str(i), "predecessor": str(i - 1) if i else None,
             "role": "assistant" if i % 2 else "user", "created_at": i}
            for i in range(count)]
    visits = 0
    original = depth_module.predecessor_of

    def counted(*args):
        nonlocal visits
        visits += 1
        return original(*args)

    monkeypatch.setattr(depth_module, "predecessor_of", counted)
    result = annotate_graph(rows, str(count - 1))
    assert [row["_depth"] for row in result] == list(range(count))
    assert {row["_lane"] for row in result} == {0}
    assert visits <= count * 3


def test_cycle_and_nodes_leading_into_cycle_remain_independent_roots():
    rows = [{"id": "a", "predecessor": "b", "created_at": 0},
            {"id": "b", "predecessor": "a", "created_at": 1},
            {"id": "c", "predecessor": "a", "created_at": 2},
            {"id": "d", "created_at": 3}]
    by_id = {row['id']: row for row in rows}
    assert depth_module.compute_depth(by_id, {}) == {"a": 0, "b": 1, "c": 2, "d": 3}


def test_wide_forks_place_columns_without_rescanning_previous_lanes(monkeypatch):
    import openprogram.webui.graph_layout as layout

    count = 2_000
    rows = [{'id': 'ROOT', 'display': 'root', 'created_at': 0}]
    rows += [{'id': str(i), 'predecessor': 'ROOT', 'role': 'user',
              'created_at': i + 1} for i in range(count)]
    visits = 0
    original = layout.compute_tier

    class Tiers(dict):
        def get(self, *args):
            nonlocal visits
            visits += 1
            return super().get(*args)

    monkeypatch.setattr(layout, 'compute_tier', lambda by_id: Tiers(original(by_id)))
    graph = annotate_graph(rows, '0')
    assert len({row['_lane'] for row in graph[1:]}) == count
    assert {row['_depth'] for row in graph[1:]} == {1}
    assert visits < 10 * count


def test_followup_reply_normalization_preserves_chained_rewrites():
    from openprogram.webui.graph_layout.filter import normalize_followup

    rows = [{'id': 'root'},
            {'id': 'u1', 'role': 'user', 'source': 'job_followup', 'predecessor': 'u2'},
            {'id': 'u2', 'role': 'user', 'source': 'job_followup', 'predecessor': 'root'},
            {'id': 'a', 'role': 'assistant', 'source': 'job_followup', 'predecessor': 'u1'}]
    normalize_followup(rows)
    assert rows[-1]['predecessor'] == 'root'
