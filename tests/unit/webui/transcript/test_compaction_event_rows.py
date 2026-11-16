"""The UI-only compaction card spliced into the shown rows."""
from openprogram.interfaces.server.ws_actions.session import splice_compaction_event_rows


def test_active_summary_emits_only_its_card_at_the_boundary():
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
    # Nothing marks where the compaction ran; the card is the only row.
    assert [m["id"] for m in out] == ["u0", "a0", "sum1_card", "u1", "a1"]
    card = out[2]
    assert card["slot"] == "card"
    assert card["summarised_count"] == 2
    assert card["timestamp"] == 10.0
    assert "recap" in card["content"]
    assert not any("Context compacted here" in str(m.get("content")) for m in out)
    assert shown[0]["id"] == "u0"


def test_skips_non_summary_rows():
    shown = [{"id": "u0"}, {"id": "a0"}]
    graph = [{"id": "bare"}]
    assert splice_compaction_event_rows(shown, graph) is shown


def test_superseded_summary_gets_no_row():
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
    assert [m["id"] for m in out] == ["u0", "a0", "sum_new_card", "u1"]


def test_count_prefers_original_covers_not_expanded_graph():
    shown = [{"id": "u0", "timestamp": 1}, {"id": "a0", "timestamp": 2},
             {"id": "u1", "timestamp": 3}]
    graph = [{"id": "sum1", "covers_ids": ["u0", "a0", "tool_a", "tool_b"]}]
    all_msgs = [{"id": "sum1", "covers_ids": ["u0", "a0"]}]
    out = splice_compaction_event_rows(shown, graph, all_msgs)
    card = next(m for m in out if m["id"] == "sum1_card")
    assert card["summarised_count"] == 2


def test_old_node_without_compacted_at_still_gets_its_card():
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
    assert [m["id"] for m in out] == ["u0", "a0", "sum1_card", "u1", "a1"]


def test_a_branch_the_summary_does_not_cover_gets_no_rows():
    """A branch forked inside the covered range was never compacted: that
    summary's card is not shown on it, while the compacted branch keeps it."""
    rows = [
        {"id": "u0", "predecessor": None},
        {"id": "a0", "predecessor": "u0"},
        {"id": "u1", "predecessor": "a0"},
        {"id": "a1", "predecessor": "u1"},
        {"id": "u2", "predecessor": "a1"},
        {"id": "a1b", "predecessor": "u1"},
        {"id": "sum1", "predecessor": "ROOT", "covers_ids": ["u0", "a0", "u1", "a1"],
         "content": "recap", "compacted_at": 9, "summarised_count": 4},
        {"id": "sum0", "predecessor": "ROOT", "covers_ids": ["u0", "a0", "u1", "a1"],
         "compacted_at": 8, "summarised_count": 4},
    ]
    graph = [
        {"id": "sum1", "covers_ids": ["u0", "a0", "u1", "a1"], "compacted_at": 9},
        {"id": "sum0", "superseded_summary": True, "compacted_at": 8},
    ]
    by = {r["id"]: r for r in rows}
    fork = [dict(by[i], timestamp=t) for t, i in enumerate(["u0", "a0", "u1", "a1b"], 1)]
    main = [dict(by[i], timestamp=t) for t, i in enumerate(["u0", "a0", "u1", "a1", "u2"], 1)]

    assert [m["id"] for m in splice_compaction_event_rows(fork, graph, rows)] == [
        "u0", "a0", "u1", "a1b"]
    assert [m["id"] for m in splice_compaction_event_rows(main, graph, rows)] == [
        "u0", "a0", "u1", "a1", "sum1_card", "u2"]
