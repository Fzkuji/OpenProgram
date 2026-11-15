"""Cross-session history search: what it finds and in which order."""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.permissions.identity import CHECK_IN_SPEAKER


@pytest.fixture
def db(tmp_path: Path) -> SessionDB:
    return SessionDB(tmp_path / "sessions.sqlite")


def _add(db: SessionDB, session_id: str, node_id: str, role: str, content: str, at: float,
         predecessor: str | None, **extra) -> None:
    db.append_message(session_id, {"id": node_id, "role": role, "content": content, "timestamp": at,
                                   "predecessor": predecessor, **extra})


def _ids(hits: list[dict]) -> list[str]:
    return [hit["id"] for hit in hits]


def test_newest_hits_win_across_sessions_before_the_limit(db):
    db.create_session("old", "main")
    db.create_session("new", "main")
    previous = None
    for index in range(1, 4):
        _add(db, "old", f"o{index}", "user", "zircon", float(index), previous)
        previous = f"o{index}"
    _add(db, "new", "n1", "user", "zircon", 2.5, None)

    assert _ids(db.search_messages("zircon", limit=2)) == ["o3", "n1"]


def test_matches_the_message_text_not_its_stored_fields(db):
    db.create_session("c", "main")
    _add(db, "c", "m1", "user", "hello", 1.0, None, memory_prefetch="zircon", peer_display="zircon")
    _add(db, "c", "m2", "assistant", "a zircon", 2.0, "m1")

    assert _ids(db.search_messages("zircon")) == ["m2"]


def test_prompts_the_chat_does_not_draw_are_left_out(db):
    db.create_session("c", "main")
    _add(db, "c", "m1", "user", "zircon notice", 1.0, None, display="runtime")
    _add(db, "c", "m2", "assistant", "zircon reply", 2.0, "m1")

    assert _ids(db.search_messages("zircon")) == ["m2"]


def test_a_scheduled_tasks_branch_is_left_out(db):
    db.create_session("c", "main")
    _add(db, "c", "m1", "user", "zircon here", 1.0, None)
    _add(db, "c", "t1", "user", "zircon task prompt", 2.0, "m1", source="scheduler")
    _add(db, "c", "t2", "assistant", "zircon task result", 3.0, "t1")

    assert _ids(db.search_messages("zircon")) == ["m1"]


def test_a_merged_branch_is_left_out_but_the_line_it_left_stays(db):
    db.create_session("c", "main")
    _add(db, "c", "m1", "user", "zircon start", 1.0, None)
    _add(db, "c", "m2", "assistant", "zircon answer", 2.0, "m1")
    db.set_head("c", "m2")
    _add(db, "c", "b1", "user", "zircon check-in prompt", 3.0, "m2")
    _add(db, "c", "b2", "assistant", "zircon check-in note", 4.0, "b1")
    db.set_head("c", "m2")
    assert "b1" in _ids(db.search_messages("zircon"))

    db.mark_merged("c", ["b2"])

    assert _ids(db.search_messages("zircon")) == ["m2", "m1"]


def test_tool_output_on_a_merged_branch_is_left_out(db):
    db.create_session("c", "main")
    _add(db, "c", "m1", "user", "start", 1.0, None)
    db.set_head("c", "m1")
    _add(db, "c", "b1", "user", "prompt", 2.0, "m1")
    _add(db, "c", "b2", "assistant", "note", 3.0, "b1")
    _add(db, "c", "tool", "tool", "zircon output", 2.5, None, caller="b2")
    db.set_head("c", "m1")
    assert _ids(db.search_messages("zircon")) == ["tool"]

    db.mark_merged("c", ["b2"])

    assert db.search_messages("zircon") == []


def test_a_check_in_is_left_out_before_it_finishes(db):
    db.create_session("c", "main")
    _add(db, "c", "m1", "user", "zircon start", 1.0, None)
    db.set_head("c", "m1")
    _add(db, "c", "b1", "user", "zircon check-in prompt", 2.0, "m1", speaker_id=CHECK_IN_SPEAKER)
    _add(db, "c", "b2", "assistant", "zircon partial note", 3.0, "b1")
    db.set_head("c", "m1")

    assert _ids(db.search_messages("zircon")) == ["m1"]


def test_a_finished_check_in_does_not_hide_a_line_the_conversation_moved_off(db):
    db.create_session("c", "main")
    _add(db, "c", "m1", "user", "zircon start", 1.0, None)
    _add(db, "c", "m2", "assistant", "zircon answer", 2.0, "m1")
    db.set_head("c", "m2")
    _add(db, "c", "b1", "user", "prompt", 3.0, "m2", speaker_id=CHECK_IN_SPEAKER)
    _add(db, "c", "b2", "assistant", "note", 4.0, "b1")
    db.set_head("c", "m2")
    db.mark_merged("c", ["b2"])
    _add(db, "c", "n1", "user", "another try", 5.0, "m1")
    db.set_head("c", "n1")

    assert _ids(db.search_messages("zircon")) == ["m2", "m1"]


def test_the_conversation_stays_findable_while_a_merged_branch_is_open(db):
    db.create_session("c", "main")
    _add(db, "c", "m1", "user", "zircon start", 1.0, None)
    _add(db, "c", "b1", "user", "zircon side", 2.0, "m1")
    db.mark_merged("c", ["b1"])
    db.set_head("c", "b1")

    assert _ids(db.search_messages("zircon")) == ["b1", "m1"]


def test_a_reply_on_a_scheduled_tasks_branch_is_a_chat(db):
    db.create_session("c", "main")
    _add(db, "c", "m1", "user", "start", 1.0, None)
    db.set_head("c", "m1")
    _add(db, "c", "t1", "user", "zircon task prompt", 2.0, "m1", source="scheduler")
    _add(db, "c", "t2", "assistant", "zircon task result", 3.0, "t1")
    _add(db, "c", "s1", "user", "zircon helper prompt", 3.5, "t2", caller="t2")
    _add(db, "c", "s2", "user", "zircon runtime turn", 3.6, "t2", speaker_kind="runtime")
    db.set_head("c", "t2")
    _add(db, "c", "o1", "user", "zircon owner reply", 4.0, "t2", source="web")
    _add(db, "c", "o2", "assistant", "zircon answer", 5.0, "o1")
    db.set_head("c", "o2")
    db.mark_merged("c", ["unrelated"])

    assert _ids(db.search_messages("zircon")) == ["o2", "o1"]
