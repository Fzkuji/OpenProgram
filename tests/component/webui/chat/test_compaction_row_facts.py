"""A compaction row shows the facts compaction stamps on its summary after the insert."""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.interfaces.server.transcript.turn_index import get_view
from openprogram.runtime.context.persistence import Persister
from tests.support.transcript.live_server import live_chat
from tests.support.transcript.old_shapes import Rows

STATS = {"tokens_before": 900, "tokens_after": 300, "summarised_count": 4, "compacted_at": 50.0}


@pytest.fixture
def chat(tmp_path: Path):
    with live_chat(tmp_path) as live:
        yield live


def _event_row(chat) -> dict:
    read = next(step["read"] for step in reversed(chat.timeline) if "read" in step)
    loaded = next(frame["data"] for frame in read["frames"] if frame["type"] == "session_loaded")
    return next(row for turn in loaded["turns"] for row in turn["rows"]
                if row.get("kind") == "compaction" and row.get("slot") == "event")


def _compacted(chat) -> str:
    """Three turns, the first two covered by a summary written as compaction writes it."""
    rows = Rows(chat)
    for name in ("a", "b", "c"):
        rows.turn(name)
    rows.finish()
    history = chat.sessions.get_branch(chat.session_id) or []
    summary_id = Persister().insert_summary_node(
        chat.session_id, summary_text="recap", cut_idx=4, history=history,
    )
    assert summary_id
    return summary_id


def test_stats_stamped_after_a_read_reach_the_next_read(chat) -> None:
    summary_id = _compacted(chat)
    chat.client_read("between insert and stamp")
    assert _event_row(chat).get("tokensBefore") is None
    chat.sessions.merge_node_metadata(chat.session_id, summary_id, STATS)
    chat.client_read("after the stamp")
    row = _event_row(chat)
    assert (row["tokensBefore"], row["tokensAfter"], row["timestamp"]) == (900, 300, 50.0)


def test_an_open_page_is_sent_to_read_again_when_the_stats_land(chat) -> None:
    summary_id = _compacted(chat)
    chat.client_read("open")
    chat.wait_published()
    chat.timeline.clear()
    chat.sessions.merge_node_metadata(chat.session_id, summary_id, STATS)
    chat.wait_published()
    stale = [change["reason"] for step in chat.timeline
             if step.get("frame", {}).get("type") == "transcript_update"
             for change in step["frame"]["data"]["changes"] if change["kind"] == "window_stale"]
    assert stale == ["structure"]


def test_a_patch_to_an_ordinary_row_keeps_the_view(chat) -> None:
    rows = Rows(chat)
    rows.turn("a")
    rows.finish()
    view = get_view(chat.sessions, chat.session_id)
    chat.sessions.merge_node_metadata(chat.session_id, "a", {"memory_map": "note"})
    assert get_view(chat.sessions, chat.session_id) is view
