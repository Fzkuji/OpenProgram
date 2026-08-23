"""UI-only compaction card + event rows spliced into session_loaded.shown."""
from openprogram.webui.ws_actions.session import splice_compaction_event_rows


def test_active_summary_emits_card_at_boundary_and_event_at_exec():
    shown = [
        {"id": "u0", "role": "user", "timestamp": 1},
        {"id": "a0", "role": "assistant", "timestamp": 2},
        {"id": "u1", "role": "user", "timestamp": 3},
        {"id": "a1", "role": "assistant", "timestamp": 4},
    ]
    graph = [{
        "id": "sum1",
        "covers_ids": ["u0", "a0"],
        "created_at": 1.0,
        "compacted_at": 10.0,
        "tokens_before": 90,
        "tokens_after": 20,
        "summarised_count": 2,
    }]
    all_msgs = [{
        "id": "sum1",
        "covers_ids": ["u0", "a0"],
        "content": "[Previous conversation summary]\nrecap",
        "compacted_at": 10.0,
        "tokens_before": 90,
        "tokens_after": 20,
        "summarised_count": 2,
    }]
    out = splice_compaction_event_rows(shown, graph, all_msgs)
    ids = [m["id"] for m in out]
    assert ids[0] == "sum1_card"
    assert ids[-1] == "sum1_ui"
    assert ids == ["sum1_card", "u0", "a0", "u1", "a1", "sum1_ui"]
    card = out[0]
    assert card["slot"] == "card"
    assert card["summarised_count"] == 2
    assert "recap" in card["content"]
    ev = out[-1]
    assert ev["slot"] == "event"
    assert ev["tokens_before"] == 90
    assert ev["tokens_after"] == 20
    assert "90 → 20" in ev["content"]
    assert shown[0]["id"] == "u0"


def test_skips_non_summary_rows():
    shown = [{"id": "u0"}, {"id": "a0"}]
    graph = [{"id": "bare"}]
    assert splice_compaction_event_rows(shown, graph) is shown


def test_superseded_keeps_event_drops_card():
    shown = [
        {"id": "u0", "timestamp": 1},
        {"id": "a0", "timestamp": 2},
        {"id": "u1", "timestamp": 3},
    ]
    graph = [
        {"id": "sum_old", "superseded_summary": True,
         "compacted_at": 5, "summarised_count": 2},
        {"id": "sum_new", "covers_ids": ["u0", "a0"],
         "compacted_at": 8, "summarised_count": 2},
    ]
    all_msgs = [
        {"id": "sum_old", "covers_ids": ["u0", "a0"],
         "compacted_at": 5, "summarised_count": 2},
        {"id": "sum_new", "covers_ids": ["u0", "a0"],
         "content": "new recap", "compacted_at": 8, "summarised_count": 2},
    ]
    out = splice_compaction_event_rows(shown, graph, all_msgs)
    ids = [m["id"] for m in out]
    assert "sum_new_card" in ids
    assert "sum_old_card" not in ids
    assert "sum_old_ui" in ids
    assert "sum_new_ui" in ids


def test_count_prefers_original_covers_not_expanded_graph():
    shown = [{"id": "u0", "timestamp": 1}, {"id": "a0", "timestamp": 2},
             {"id": "u1", "timestamp": 3}]
    graph = [{"id": "sum1", "covers_ids": ["u0", "a0", "tool_a", "tool_b"]}]
    all_msgs = [{"id": "sum1", "covers_ids": ["u0", "a0"]}]
    out = splice_compaction_event_rows(shown, graph, all_msgs)
    card = next(m for m in out if m["id"] == "sum1_card")
    ev = next(m for m in out if m["id"] == "sum1_ui")
    assert card["summarised_count"] == 2
    assert ev["summarised_count"] == 2
    assert "tokens" not in ev["content"]


def test_old_node_without_compacted_at_uses_append_order():
    shown = [
        {"id": "u0", "role": "user"},
        {"id": "a0", "role": "assistant"},
        {"id": "u1", "role": "user"},
        {"id": "a1", "role": "assistant"},
    ]
    graph = [{"id": "sum1", "covers_ids": ["u0", "a0"], "created_at": 0.1}]
    all_msgs = [
        {"id": "u0"}, {"id": "a0"}, {"id": "u1"}, {"id": "a1"},
        {"id": "sum1", "covers_ids": ["u0", "a0"], "content": "r"},
    ]
    out = splice_compaction_event_rows(shown, graph, all_msgs)
    ids = [m["id"] for m in out]
    assert ids[0] == "sum1_card"
    assert ids[-1] == "sum1_ui"
