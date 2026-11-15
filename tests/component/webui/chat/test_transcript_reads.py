"""Protocol 3 reads: turns and a window, straight from the store."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openprogram.interfaces.server.transcript import reader
from openprogram.interfaces.server.transcript.rows import project_rows
from openprogram.interfaces.server.transcript.turn_index import get_view
from openprogram.interfaces.server.ws_errors import OperationError
from tests.support.transcript.live_server import live_chat
from tests.support.transcript.old_shapes import OLD_SHAPES, Rows, build_case


def _turns(chat, count: int) -> list[str]:
    head = "ROOT"
    ids = []
    for at in range(count):
        user, reply = f"u{at}", f"u{at}_reply"
        chat.sessions.append_messages(chat.session_id, [
            {"id": user, "role": "user", "content": f"question {at}", "timestamp": 1000.0 + at, "predecessor": head, "source": "web"},
            {"id": reply, "role": "assistant", "content": f"answer {at}", "timestamp": 1000.5 + at, "predecessor": user},
        ])
        head = reply
        ids.append(user)
    chat.sessions.set_head(chat.session_id, head)
    return ids


def _loaded(chat, **request) -> dict:
    return next(f["data"] for f in chat.read(**request) if f["type"] == "session_loaded")


def _page(chat, **request) -> dict:
    return next(f["data"] for f in chat.read(**request) if f["type"] == "session_history_page")


def _row(data: dict, row_id: str) -> dict:
    return next(row for turn in data["turns"] for row in turn["rows"] if row["id"] == row_id)


@pytest.mark.parametrize("name", sorted(OLD_SHAPES))
@pytest.mark.parametrize("first,later", [(1, 1), (2, 3)])
def test_paging_every_old_shape_from_every_edge_equals_the_whole_read(
    name: str, first: int, later: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reader, "INITIAL_PAGE_TURNS", first)
    monkeypatch.setattr(reader, "PAGE_TURNS", later)
    with live_chat(tmp_path) as chat:
        whole = build_case(name, chat)["frames"][0]["data"]["turns"]
        data = _loaded(chat)
        turns, window = data["turns"], data["window"]
        while window["before"]:
            page = _page(chat, history_before=window["before"])
            turns, window = page["turns"] + turns, page["window"]
    strip = lambda value: json.loads(json.dumps(value))  # noqa: E731
    assert strip(turns) == strip(whole)


def test_a_read_is_turns_and_a_window_with_no_graph_or_token(tmp_path: Path) -> None:
    with live_chat(tmp_path) as chat:
        _turns(chat, 8)
        data = _loaded(chat)
    assert [turn["id"] for turn in data["turns"]] == [f"u{at}" for at in range(2, 8)]
    assert data["graph"] == [] and "messages" not in data and "history" not in data
    window = data["window"]
    assert {key: window[key] for key in ("before", "after", "start", "end", "total")} == {
        "before": "u2", "after": None, "start": 2, "end": 8, "total": 8}
    assert window["revision"] > 0 and window["head_id"] == "u7_reply"


def test_a_row_changed_after_open_reads_fresh(tmp_path: Path) -> None:
    with live_chat(tmp_path) as chat:
        _turns(chat, 40)
        window = _loaded(chat)["window"]
        chat.sessions.update_node(chat.session_id, "u5_reply", output="changed after open")
        page = _page(chat, history_around="u5")
    assert _row(page, "u5_reply")["content"] == "changed after open"
    assert page["window"]["head_id"] == window["head_id"], "an edit moved no structure"
    assert page["window"]["total"] == window["total"]


def test_a_first_read_stops_at_the_byte_limit_but_keeps_one_whole_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader, "INITIAL_PAGE_BYTES", 1)
    with live_chat(tmp_path) as chat:
        _turns(chat, 3)
        data = _loaded(chat)
    assert [turn["id"] for turn in data["turns"]] == ["u2"]


def test_any_row_is_a_cursor_and_an_unknown_one_says_so(tmp_path: Path) -> None:
    with live_chat(tmp_path) as chat:
        _turns(chat, 30)
        assert _page(chat, history_around="u3_reply")["window"]["start"] <= 3
        assert [turn["id"] for turn in _page(chat, history_turn="u9_reply")["turns"]] == ["u9"]
        with pytest.raises(OperationError) as failure:
            chat.read(history_before="never-existed")
    assert failure.value.code == "cursor_not_on_branch" and failure.value.retryable


def test_a_head_that_only_advanced_keeps_positions_and_cursors(tmp_path: Path) -> None:
    with live_chat(tmp_path) as chat:
        ids = _turns(chat, 30)
        first = _loaded(chat)["window"]
        chat.filler(2)
        page = _page(chat, history_before=first["before"])
    assert page["window"]["end"] == first["start"] and page["window"]["total"] == 32
    assert page["turns"][-1]["id"] == ids[first["start"] - 1]


def test_a_failed_read_is_retryable_and_substitutes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with live_chat(tmp_path) as chat:
        _turns(chat, 2)
        monkeypatch.setattr(reader, "first_read", lambda *_a: (_ for _ in ()).throw(RuntimeError("store")))
        with pytest.raises(OperationError) as failure:
            chat.read()
    assert failure.value.code == "session_read_failed" and failure.value.retryable


def test_a_broken_chain_is_the_named_case_and_still_reads(tmp_path: Path) -> None:
    with live_chat(tmp_path) as chat:
        data = build_case("broken_predecessor_chain", chat)["frames"][0]["data"]
    assert data["turns"] and data["window"]["total"] == len(data["turns"])


def test_find_searches_the_shown_text_of_the_branch(tmp_path: Path) -> None:
    with live_chat(tmp_path) as chat:
        _turns(chat, 3)
        rows = Rows(chat)
        rows.add("tool-1", "tool", "needle in a tool", caller="u1_reply", function="lookup")
        view = get_view(chat.sessions, chat.session_id)
        matches = reader.search(chat.sessions, view, "ANSWER 1")
        assert reader.search(chat.sessions, view, "needle") == []
    assert [(m["id"], m["count"]) for m in matches] == [("u1_reply", 1)]
    assert matches[0]["snippet"] == "answer 1"


def test_a_compaction_card_and_what_it_covers_keep_their_kinds(tmp_path: Path) -> None:
    with live_chat(tmp_path) as chat:
        data = build_case("summary_covers_ids", chat)["frames"][0]["data"]
    rows = [row for turn in data["turns"] for row in turn["rows"]]
    card = next(row for row in rows if row.get("slot") == "card")
    event = next(row for row in rows if row.get("slot") == "event")
    assert (card["kind"], card["rowKind"]) == ("compaction", "compaction_card") and "coversIds" not in card
    assert (event["kind"], event["rowKind"]) == ("compaction", "system_event")
    assert [row["id"] for row in rows if row.get("coveredBy") == card["id"]] == ["a", "a_reply"]


def test_a_stored_row_with_no_id_keeps_one_key_wherever_it_is_read() -> None:
    row = {"role": "user", "content": "old", "timestamp": 5.0}
    first = project_rows([row])[0]["id"]
    assert first == project_rows([row])[0]["id"] and first.startswith("hist_user_5.0")


def test_block_ids_stay_put_when_the_final_write_splits_a_run() -> None:
    def ids(*texts: str) -> list[str]:
        reply = {"id": "r", "role": "assistant", "content": "", "blocks": [{"type": "text", "text": text} for text in texts]}
        return [block["id"] for block in project_rows([reply])[0]["blocks"]]

    assert ids("whole") == ["text:0"]
    assert ids("wh", "ole") == ["text:0", "text:0.1"]


def test_every_block_has_its_stored_position_and_a_legacy_steer_sorts_first() -> None:
    steer = {"id": "s", "role": "user", "content": "go left", "steering": True, "predecessor": "u"}
    user = {"id": "u", "role": "user", "content": "go"}
    reply = {"id": "r", "role": "assistant", "content": "ok", "predecessor": "s", "blocks": [
        {"type": "text", "text": "ok"}, {"type": "tool", "tool": "x", "input": "{}"}, {"type": "tool", "tool": "x", "tool_call_id": "c"}]}
    blocks = next(row for row in project_rows([user, steer, reply]) if row["id"] == "r")["blocks"]
    assert [(block["index"], block["id"]) for block in blocks] == [
        (-1, "steering:s"), (0, "text:0"), (1, "tool:1"), (2, "c")]


def test_an_always_on_report_keeps_what_the_chat_labels_it_by(tmp_path: Path) -> None:
    """A report posted as the Agent's message (always_on/runtime.py
    post_report_message) reads with its id, kind, source and title, which
    the chat draws its label and time break from (bot-view.ts)."""
    with live_chat(tmp_path) as chat:
        (user,) = _turns(chat, 1)
        chat.sessions.append_message(chat.session_id, {
            "id": "report_abc", "role": "assistant", "content": "The build broke.", "timestamp": 2000.0,
            "predecessor": f"{user}_reply", "agent_id": "main", "status": "done",
            "always_on_report": "abc", "kind": "approval", "report_title": "Build broke",
            "report_source": "check_in",
        })
        chat.sessions.set_head(chat.session_id, "report_abc")
        row = _row(_loaded(chat), "report_abc")
    assert (row["reportId"], row["reportKind"], row["reportTitle"], row["reportSource"]) == (
        "abc", "approval", "Build broke", "check_in")
    plain = project_rows([{"id": "r", "role": "assistant", "content": "ok", "kind": "approval"}])[0]
    assert not {"reportId", "reportKind", "reportTitle", "reportSource"} & set(plain)


def test_goal_verifier_turns_read_their_mark_without_scanning_executions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openprogram.programs.tools.planning.goal import presentation

    seen = []
    real = presentation.annotate_messages
    monkeypatch.setattr(presentation, "annotate_messages", lambda *a, **k: seen.append(k) or real(*a, **k))
    with live_chat(tmp_path) as chat:
        _turns(chat, 2)
        _loaded(chat)
    assert seen == [], "a chat with no verifier turn pays for none"
