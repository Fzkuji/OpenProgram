"""A view follows new rows without a rebuild, and is the view a rebuild would give."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from openprogram.interfaces.server.transcript import reader, turn_index
from openprogram.interfaces.server.transcript.turn_index import build_view, get_view
from tests.support.transcript.live_server import live_chat
from tests.support.transcript.old_shapes import OLD_SHAPES, Rows

_COMPARED = [field.name for field in dataclasses.fields(turn_index.TranscriptView) if field.name not in ("revision", "lock", "fingerprint")]


def _facts(view) -> dict:
    return {name: getattr(view, name) for name in _COMPARED}


@pytest.fixture
def chat(tmp_path: Path):
    with live_chat(tmp_path) as live:
        yield live


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real = turn_index.build_view
    monkeypatch.setattr(turn_index, "build_view", lambda db, session_id, *rest: seen.append(session_id) or real(db, session_id, *rest))
    return seen


def _view(chat):
    return get_view(chat.sessions, chat.session_id)


def _same_as_rebuild(chat) -> None:
    view = _view(chat)
    state = chat.sessions.transcript_state(chat.session_id)
    assert _facts(view) == _facts(build_view(chat.sessions, chat.session_id, state, None))


def _opened(chat) -> Rows:
    rows = Rows(chat)
    rows.turn("a")
    rows.turn("b")
    rows.finish()
    _view(chat)
    return rows


def test_a_new_turn_extends_the_view(chat, builds) -> None:
    rows = _opened(chat)
    before = _view(chat)
    builds.clear()
    rows.turn("c")
    rows.finish()
    view = _view(chat)
    assert builds == [] and view is before
    assert [turn.id for turn in view.turns] == ["a", "b", "c"]
    assert view.head_id == "c_reply"
    _same_as_rebuild(chat)


def test_an_extension_keeps_the_number_and_earlier_positions(chat, builds) -> None:
    rows = _opened(chat)
    first = _view(chat).revision
    positions = dict(_view(chat).positions)
    builds.clear()
    rows.add("c", "user", "c prompt")
    rows.finish()
    view = _view(chat)
    assert view.revision == first and builds == []
    assert all(view.positions[row_id] == at for row_id, at in positions.items())


def test_a_tool_row_folds_into_the_last_reply_without_a_rebuild(chat, builds) -> None:
    rows = _opened(chat)
    builds.clear()
    rows.add("t1", "tool", "out", chain=False, caller="b_reply", function="lookup", predecessor="b_reply", tool_call_id="c1")
    view = _view(chat)
    assert builds == [] and "t1" in view.turns[-1].raw_ids and view.positions["t1"] == 1
    _same_as_rebuild(chat)


def test_a_steer_joins_the_running_turn_and_a_page_reads_it(chat, builds) -> None:
    rows = _opened(chat)
    builds.clear()
    rows.add("steer", "user", "a steer", steering=True)
    rows.add("steered", "assistant", "answer to the steer")
    rows.finish()
    view = _view(chat)
    assert builds == [] and [turn.id for turn in view.turns] == ["a", "b"]
    _same_as_rebuild(chat)
    turns, window = reader.page(chat.sessions, view)
    assert window["total"] == 2 and "steered" in [row["id"] for row in turns[-1]["rows"]]


def test_rows_on_a_branch_this_view_never_touches_change_nothing(chat, builds) -> None:
    rows = Rows(chat)
    rows.turn("a")
    rows.finish()
    rows.add("job", "user", "elsewhere", chain=False, predecessor=None, caller="a_reply", source="agent_spawn")
    _view(chat)
    builds.clear()
    rows.add("job_reply", "assistant", "there", chain=False, predecessor="job")
    view = _view(chat)
    assert builds == [] and "job_reply" not in view.positions
    _same_as_rebuild(chat)


@pytest.mark.parametrize("change", ["version", "head_back", "earlier_turn", "summary", "removed"])
def test_what_reaches_further_back_rebuilds(chat, builds, change: str) -> None:
    rows = _opened(chat)
    builds.clear()
    if change == "version":
        rows.add("b_again", "assistant", "another", chain=False, predecessor="b")
        chat.sessions.set_head(chat.session_id, "b_again")
    elif change == "head_back":
        chat.sessions.set_head(chat.session_id, "a_reply")
    elif change == "earlier_turn":
        rows.add("late", "tool", "out", chain=False, caller="a_reply", function="lookup", predecessor="a_reply", tool_call_id="c1")
    elif change == "summary":
        rows.add("sum", "system", "recap", chain=False, predecessor="a", covers_ids=["a_reply"], kind="compaction")
    else:
        chat.sessions.delete_nodes(chat.session_id, ["b_reply"])
        rows.add("c", "user", "next", predecessor="b")
    view = _view(chat)
    assert builds == [chat.session_id], "only a rebuild can place this"
    assert view is not None


def test_an_attach_row_joins_its_parents_turn_without_a_rebuild(chat, builds) -> None:
    rows = _opened(chat)
    builds.clear()
    rows.add("old_card", "assistant", "agent", chain=False, predecessor="a_reply", display="runtime", function="attach", timestamp=1.0)
    rows.add("card", "assistant", "agent", chain=False, predecessor="b_reply", display="runtime", function="attach")
    view = _view(chat)
    assert builds == [] and view.positions["card"] == view.positions["b_reply"]
    _same_as_rebuild(chat)


def test_a_reparented_row_rebuilds(chat, builds) -> None:
    rows = _opened(chat)
    builds.clear()
    rows.add("steer", "user", "a steer", chain=False, predecessor="b", steering=True)
    chat.sessions.update_node(chat.session_id, "b_reply", predecessor="steer")
    assert chat.sessions.transcript_state(chat.session_id)[4] == 1
    _view(chat)
    assert builds == [chat.session_id]
    _same_as_rebuild(chat)


def test_a_version_arrow_keeps_following_its_branch(chat, builds) -> None:
    rows = _opened(chat)
    rows.add("b_two", "assistant", "second version", chain=False, predecessor="b")
    chat.sessions.set_head(chat.session_id, "b_two")
    _view(chat)
    builds.clear()
    rows.add("other", "user", "elsewhere", chain=False, predecessor="b_reply")
    _view(chat)
    assert builds == [chat.session_id], "the arrow to the other version must see its new leaf"
    _same_as_rebuild(chat)


@pytest.mark.parametrize("name", sorted(OLD_SHAPES))
def test_following_every_old_shape_row_by_row_equals_a_rebuild(chat, name: str) -> None:
    real = chat.sessions.append_message

    def appended(session_id, message, **options):
        written = real(session_id, message, **options)
        if session_id == chat.session_id:
            _same_as_rebuild(chat)
        return written

    chat.sessions.append_message = appended
    OLD_SHAPES[name](Rows(chat))
    _same_as_rebuild(chat)
