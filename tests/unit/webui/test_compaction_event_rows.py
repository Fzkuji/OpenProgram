"""UI-only compaction records spliced into session_loaded.shown."""
from openprogram.webui.ws_actions.session import splice_compaction_event_rows


def test_splices_after_last_covered():
    shown = [
        {"id": "u0", "role": "user"},
        {"id": "a0", "role": "assistant"},
        {"id": "u1", "role": "user"},
        {"id": "a1", "role": "assistant"},
    ]
    graph = [{
        "id": "sum1",
        "covers_ids": ["u0", "a0"],
        "created_at": 100.0,
    }]
    out = splice_compaction_event_rows(shown, graph)
    assert [m["id"] for m in out] == ["u0", "a0", "sum1_ui", "u1", "a1"]
    row = out[2]
    assert row["role"] == "system"
    assert row["kind"] == "compaction"
    assert row["summarised_count"] == 2
    assert row["timestamp"] == 100.0
    assert "covered 2" in row["content"]
    assert shown[2]["id"] == "u1"  # original list unchanged


def test_skips_superseded_and_empty():
    shown = [{"id": "u0"}, {"id": "a0"}]
    graph = [
        {"id": "old", "covers_ids": ["u0"], "superseded_summary": True},
        {"id": "bare"},
    ]
    assert splice_compaction_event_rows(shown, graph) is shown


def test_uses_message_timestamp_when_graph_has_none():
    shown = [{"id": "u0"}, {"id": "a0", "timestamp": 9}, {"id": "u1"}]
    graph = [{"id": "sum1", "covers_ids": ["u0", "a0"]}]
    all_msgs = [{"id": "sum1", "timestamp": 42}]
    out = splice_compaction_event_rows(shown, graph, all_msgs)
    assert out[2]["id"] == "sum1_ui"
    assert out[2]["timestamp"] == 42
