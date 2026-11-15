"""Large tool outputs are loaded lazily over the session WebSocket."""

from __future__ import annotations

import asyncio
import json

import pytest

from openprogram.state.store import SessionStore
from openprogram.interfaces.server.ws_actions import session as ws_session
from openprogram.interfaces.server.ws_actions.session.tool_output import CALL_TREE_MAX_BYTES, call_tree_rows


class FakeWS:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))


class WebWS(FakeWS):
    """A client that reads turns (history protocol 3), as the web client does."""

    _history_protocol = 3


def _turn_rows(ws, cmd: dict | None = None) -> list[dict]:
    """The rows of every turn one web read returns."""
    from openprogram.interfaces.server.transcript import publisher

    asyncio.run(ws_session.handle_load_session(ws, {"session_id": "session-1", **(cmd or {})}))
    publisher.release(ws)
    loaded = next(frame for frame in ws.frames if frame["type"] in ("session_loaded", "session_history_page"))
    return [row for turn in loaded["data"]["turns"] for row in turn["rows"]]


def _reply(rows: list[dict]) -> dict:
    return next(row for row in rows if row["id"] == "assistant-1")


@pytest.fixture
def session_with_tool_outputs(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    store.create_session("session-1", "main")
    large = 'x"\\\n' * (ws_session.TOOL_OUTPUT_INLINE_MAX_BYTES // 2)
    store.append_message(
        "session-1",
        {
            "id": "user-1",
            "role": "user",
            "content": "inspect",
        },
    )
    store.append_message(
        "session-1",
        {
            "id": "assistant-1",
            "role": "assistant",
            "content": "done",
            "predecessor": "user-1",
            "extra": json.dumps(
                {
                    "blocks": [
                        {
                            "type": "tool",
                            "tool": "read",
                            "tool_call_id": "tool-large",
                            "input": '{"path": "x"}',
                            "result": large,
                            "is_error": False,
                        }
                    ]
                }
            ),
        },
    )
    for node_id, result in (("tool-large", large), ("tool-small", "small")):
        store.append_message(
            "session-1",
            {
                "id": node_id,
                "role": "tool",
                "content": result,
                "function": "read",
                "caller": "assistant-1",
                "extra": json.dumps(
                    {
                        "tool_use": {"name": "read", "arguments": {"path": "x"}},
                    }
                ),
            },
        )

    from openprogram.interfaces.server import server

    with server._sessions_lock:
        server._sessions["session-1"] = {"id": "session-1"}
    monkeypatch.setattr(server, "_get_provider_info", lambda _sid=None: {})
    monkeypatch.setattr(server, "_is_run_active", lambda _sid: False)
    monkeypatch.setattr(server, "refresh_context_stats", lambda _sid: None)
    yield store, large
    with server._sessions_lock:
        server._sessions.pop("session-1", None)


def test_session_loaded_truncates_only_oversized_tool_output(
    session_with_tool_outputs,
):
    store, large = session_with_tool_outputs
    ws = WebWS()

    assistant = _reply(_turn_rows(ws, {"request_id": "history-read-1"}))

    loaded = next(frame for frame in ws.frames if frame["type"] == "session_loaded")
    assert loaded["data"]["request_id"] == "history-read-1"
    assert loaded["data"]["action"] == "load_session"
    tools = {tool["id"]: tool for tool in assistant["tools"]}
    truncated = tools["tool-large"]
    assert truncated["truncated"] is True
    assert truncated["totalBytes"] == len(json.dumps(large, ensure_ascii=False).encode("utf-8"))
    assert truncated["messageId"] == "assistant-1"
    assert truncated["nodeId"] == "tool-large"
    assert tools["tool-small"]["truncated"] is False
    block = assistant["blocks"][0]
    assert block["truncated"] is True
    assert block["total_bytes"] == truncated["totalBytes"]
    assert block["message_id"] == "assistant-1"
    assert block["node_id"] == "tool-large"
    assert large.startswith(block["result"])
    assert len(json.dumps(block["result"], ensure_ascii=False).encode("utf-8")) <= ws_session.TOOL_OUTPUT_INLINE_MAX_BYTES
    persisted = {message["id"]: message for message in store.get_messages("session-1")}
    assert persisted["tool-large"]["content"] == large
    assert persisted["assistant-1"]["blocks"][0]["result"] == large


def _append_call_row(store, row_id: str, caller: str, content: str, *, role: str = "tool", function: str | None = None) -> None:
    row = {"id": row_id, "role": role, "content": content, "caller": caller}
    if role == "tool":
        row |= {"function": function or row_id, "extra": json.dumps({"tool_use": {"name": function or row_id, "arguments": {"body": "x" * 70000}}})}
    store.append_message("session-1", row)


def test_session_load_sends_a_nested_call_tree_with_capped_bodies(
    session_with_tool_outputs,
):
    store, large = session_with_tool_outputs
    _append_call_row(store, "tree-root", "assistant-1", "root output")
    _append_call_row(store, "tree-step", "tree-root", large)
    _append_call_row(store, "tree-llm", "tree-step", "model said", role="assistant")

    reply = _reply(_turn_rows(WebWS()))

    [root] = reply["callRoots"]
    [step] = root["children"]
    [llm] = step["children"]
    assert (root["path"], step["path"], llm["path"]) == ("tree-root", "tree-step", "tree-llm")
    assert root["blockId"] == "tree-root"
    assert large.startswith(step["output"])
    assert len(json.dumps(step["output"], ensure_ascii=False).encode("utf-8")) <= ws_session.TOOL_OUTPUT_INLINE_MAX_BYTES
    assert llm["output"] == "model said"
    # The store keeps the whole body.
    assert next(m for m in store.get_messages("session-1") if m["id"] == "tree-step")["content"] == large


def _load_rows(ws) -> list[dict]:
    asyncio.run(ws_session.handle_load_session(ws, {"session_id": "session-1"}))
    return next(frame for frame in ws.frames if frame["type"] == "session_loaded")["data"]["messages"]


def test_a_client_that_does_not_fold_call_trees_gets_only_the_conversation(
    session_with_tool_outputs,
):
    # The terminal CLI sends no history_version and turns every row into a turn.
    store, _ = session_with_tool_outputs
    _append_call_row(store, "tree-root", "assistant-1", "root output")
    _append_call_row(store, "tree-step", "tree-root", "step output")
    _append_call_row(store, "tree-llm", "tree-step", "model said", role="assistant")

    assert [row["id"] for row in _load_rows(FakeWS())] == ["user-1", "assistant-1"]


def test_every_same_named_call_of_a_reply_is_sent_when_one_of_them_has_a_tree(
    session_with_tool_outputs,
):
    # Each root names the block it belongs to, so the first "deep" block
    # never takes the second one's tree.
    store, _ = session_with_tool_outputs
    _append_call_row(store, "deep-first", "assistant-1", "first", function="deep")
    _append_call_row(store, "deep-second", "assistant-1", "second", function="deep")
    _append_call_row(store, "deep-step", "deep-second", "step")
    _append_call_row(store, "shallow", "assistant-1", "alone", function="other")

    roots = {root["blockId"]: root for root in _reply(_turn_rows(WebWS()))["callRoots"]}

    assert not roots["deep-first"].get("children")
    assert [child["path"] for child in roots["deep-second"]["children"]] == ["deep-step"]


def _tree_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row["id"].startswith("tree-")]


def test_a_huge_call_tree_keeps_its_shape_and_stays_inside_the_page_budget(
    session_with_tool_outputs,
):
    from openprogram.interfaces.server.transcript.reader import INITIAL_PAGE_BYTES

    store, _ = session_with_tool_outputs
    _append_call_row(store, "tree-root", "assistant-1", "root output")
    for index in range(140):
        _append_call_row(store, f"tree-{index}", "tree-root", "y" * 32000)

    [root] = _reply(_turn_rows(WebWS()))["callRoots"]

    assert len(root["children"]) == 140
    assert len(json.dumps(root, ensure_ascii=False).encode("utf-8")) <= CALL_TREE_MAX_BYTES
    assert root["children"][0]["output"] == "y" * 32000
    assert not root["children"][-1].get("output")
    assert CALL_TREE_MAX_BYTES < INITIAL_PAGE_BYTES


def test_a_tree_too_wide_to_describe_loses_its_last_rows_and_never_an_ancestor():
    rows = [{"id": "tree-root", "role": "tool", "function": "f", "content": "r", "caller": "reply"}]
    for index in range(5000):
        rows.append({
            "id": f"tree-{index}", "role": "tool", "function": "f" * 40, "content": "z" * 100,
            "caller": f"tree-{index - 1}" if index else "tree-root",
        })

    kept = call_tree_rows(rows)

    assert 0 < len(kept) < len(rows)
    assert len(json.dumps(kept, ensure_ascii=False).encode("utf-8")) <= CALL_TREE_MAX_BYTES
    assert [row["id"] for row in kept] == [row["id"] for row in rows[: len(kept)]]


def test_a_compaction_line_without_a_stamp_stays_among_the_chat_rows(
    session_with_tool_outputs,
):
    from openprogram.runtime.context.persistence import default_persister

    store, _ = session_with_tool_outputs
    _append_call_row(store, "tree-root", "assistant-1", "root output")
    _append_call_row(store, "tree-step", "tree-root", "step output")

    def turn(user: str, reply: str, after: str) -> None:
        store.append_message("session-1", {"id": user, "role": "user", "content": user, "predecessor": after})
        store.append_message("session-1", {"id": reply, "role": "assistant", "content": reply, "predecessor": user})

    turn("user-2", "assistant-2", "assistant-1")
    turn("user-3", "assistant-3", "assistant-2")
    covered = [row for row in store.get_messages("session-1") if row["id"] in ("user-2", "assistant-2", "user-3")]
    default_persister.insert_summary_node("session-1", summary_text="recap", cut_idx=2, history=covered)
    turn("user-4", "assistant-4", "assistant-3")

    order = [row["id"] for row in _turn_rows(WebWS())]

    assert order == [
        "user-1", "assistant-1", "user-2", "assistant-2", order[4],
        "user-3", "assistant-3", order[7], "user-4", "assistant-4",
    ]
    assert order[4].endswith("_card") and order[7].endswith("_ui")


def test_session_load_sends_no_row_for_a_call_with_nothing_under_it(
    session_with_tool_outputs,
):
    ws = FakeWS()

    asyncio.run(ws_session.handle_load_session(ws, {"session_id": "session-1"}))

    loaded = next(frame for frame in ws.frames if frame["type"] == "session_loaded")
    assert [message["id"] for message in loaded["data"]["messages"]] == ["user-1", "assistant-1"]


@pytest.mark.parametrize("failing", ["get_messages", "get_branch"])
def test_a_failed_read_is_a_retryable_error_and_never_the_server_mirror(
    session_with_tool_outputs, monkeypatch, failing,
):
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_errors import OperationError

    store, _ = session_with_tool_outputs
    server._sessions["session-1"]["messages"] = [{"id": "stale", "role": "user", "content": "stale"}]

    def boom(*_args, **_kwargs):
        raise RuntimeError("disk went away")

    monkeypatch.setattr(store, failing, boom)
    ws = FakeWS()

    with pytest.raises(OperationError, match="session_read_failed") as failure:
        asyncio.run(ws_session.handle_load_session(ws, {"session_id": "session-1"}))

    assert failure.value.retryable is True
    assert failure.value.scope == "session"
    assert [frame for frame in ws.frames if frame["type"] == "session_loaded"] == []


def test_a_broken_chain_is_still_walked_over_the_same_rows_and_logged(
    session_with_tool_outputs, caplog,
):
    from openprogram.state.store.session.nodes import Call, ROLE_USER

    store, _ = session_with_tool_outputs
    # A later chat row with no edge at all is what the store refuses to walk.
    # Its append path rejects that, so the row goes straight into the index.
    _, index = store._open("session-1")
    index.append(Call(id="cut-off", role=ROLE_USER, output="newer"), predecessor=None, caller=None)
    store.set_head("session-1", "cut-off")
    ws = FakeWS()

    with caplog.at_level("WARNING", logger="openprogram.interfaces.server.ws_actions.session.load"):
        asyncio.run(ws_session.handle_load_session(ws, {"session_id": "session-1"}))

    loaded = next(frame for frame in ws.frames if frame["type"] == "session_loaded")
    assert [message["id"] for message in loaded["data"]["messages"]] == ["cut-off"]
    assert "broken predecessor chain" in caplog.text
    assert "session-1" in caplog.text


def test_session_load_repairs_stale_restart_marker_from_canonical_pause(
    session_with_tool_outputs,
    monkeypatch,
):
    store, _ = session_with_tool_outputs
    store.update_session("session-1", status="running")
    from openprogram.state.store.session.nodes import Call, ROLE_CODE
    from openprogram.runtime.execution import CapabilitySet, ExecutionStore, ExecutionStatus
    from openprogram.state.store import SessionNodeWriter

    node_id = "system-access-node"
    SessionNodeWriter(store, "session-1").append(
        Call(
            id=node_id,
            role=ROLE_CODE,
            name="agentic_workflow",
            output="",
            metadata={
                "status": "interrupted",
                "error": "Worker restarted before this turn finished",
                "interrupted_at": 1.0,
                "execution_kind": "agent_method",
            },
        )
    )
    executions = ExecutionStore(store.root_path.parent / "executions.sqlite")
    revision = executions.create_revision(manifest={"entrypoint": "agent"})
    execution = executions.admit_execution(
        execution_id="execution-system-access-load",
        run_id="run-system-access-load",
        session_id="session-1",
        revision_id=revision.revision_id,
        input_ref="input:system-access-load",
        input_hash="hash:system-access-load",
        entrypoint="openprogram.runtime.conversation.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "owner"},
        config_snapshot_ref="config:system-access-load",
        assistant_message_id=node_id,
        capabilities=CapabilitySet(pause=True),
    )
    executions.transition_execution(
        execution.execution_id,
        expected_version=execution.status_version,
        target=ExecutionStatus.PAUSED,
        reason_code="system_access_required",
    )
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: executions)

    ws = WebWS()
    from openprogram.interfaces.server import server

    monkeypatch.setattr(server, "_get_provider_info", lambda _sid=None: {})
    monkeypatch.setattr(server, "_is_run_active", lambda _sid: False)
    node = next(row for row in _turn_rows(ws) if row["id"] == node_id)

    loaded = next(frame for frame in ws.frames if frame["type"] == "session_loaded")
    assert node["status"] == "paused"
    assert "Worker restarted" not in str(node)
    assert loaded["data"]["status"] == "idle"
    assert loaded["data"]["run_active"] is False


def test_get_full_tool_output_returns_original_content(
    session_with_tool_outputs,
):
    _, large = session_with_tool_outputs
    ws = FakeWS()

    asyncio.run(
        ws_session.handle_get_full_tool_output(
            ws,
            {
                "session_id": "session-1",
                "message_id": "assistant-1",
                "node_id": "tool-large",
                "request_id": "request-1",
            },
        )
    )

    assert ws.frames == [
        {
            "type": "full_tool_output",
            "data": {
                "session_id": "session-1",
                "message_id": "assistant-1",
                "node_id": "tool-large",
                "request_id": "request-1",
                "result": large,
            },
        }
    ]


def test_get_full_tool_output_finds_a_row_named_after_its_reply(
    session_with_tool_outputs,
):
    """A block names the provider's call id; the row is ``<reply>_t_<id>``."""
    store, large = session_with_tool_outputs
    store.append_message(
        "session-1",
        {
            "id": "assistant-1_t_call-9",
            "role": "tool",
            "content": large,
            "function": "read",
            "caller": "assistant-1",
            "extra": json.dumps({"tool_use": {"name": "read", "arguments": {}, "tool_call_id": "call-9"}}),
        },
    )
    ws = FakeWS()

    asyncio.run(
        ws_session.handle_get_full_tool_output(
            ws,
            {"session_id": "session-1", "message_id": "assistant-1", "node_id": "call-9", "request_id": "r"},
        )
    )

    assert ws.frames[0]["data"]["result"] == large


def test_get_full_tool_output_is_scoped_to_the_named_reply(
    session_with_tool_outputs,
):
    store, large = session_with_tool_outputs
    store.append_message(
        "session-1",
        {"id": "other_t_call-9", "role": "tool", "content": large, "function": "read", "caller": "other"},
    )
    ws = FakeWS()

    asyncio.run(
        ws_session.handle_get_full_tool_output(
            ws,
            {"session_id": "session-1", "message_id": "assistant-1", "node_id": "call-9", "request_id": "r"},
        )
    )

    assert ws.frames[0]["data"]["error"] == "tool output not found"


def test_paged_load_preserves_all_rows_and_does_not_move_head_or_focus(
    session_with_tool_outputs,
):
    store, _ = session_with_tool_outputs
    from openprogram.interfaces.server import server

    prev = "assistant-1"
    for i in range(65):
        mid = f"history-{i}"
        store.append_message(
            "session-1",
            {
                "id": mid,
                "role": "user",
                "content": "中" * 9000,
                "predecessor": prev,
            },
        )
        prev = mid
    store.update_session("session-1", head_id=prev)
    ws = WebWS()

    first_rows = _turn_rows(ws)
    first = next(f["data"] for f in ws.frames if f["type"] == "session_loaded")
    assert len(first_rows) < 50
    assert first["window"]["before"]
    ids = [row["id"] for row in first_rows]
    cursor = first["window"]["before"]
    ws._focused_session_id = "another-session"
    server._sessions["session-1"]["head_id"] = "new-live-head"
    while cursor:
        ws.frames.clear()
        rows = _turn_rows(ws, {"history_before": cursor, "request_id": "request"})
        assert [f["type"] for f in ws.frames] == ["session_history_page"]
        page = ws.frames[0]["data"]
        assert page["action"] == "load_session"
        assert page["request_id"] == "request"
        ids = [row["id"] for row in rows] + ids
        assert page["window"]["before"] != cursor
        cursor = page["window"]["before"]
    assert len(ids) == len(set(ids))
    assert all(f"history-{i}" in ids for i in range(65))
    assert "assistant-1" in ids and "user-1" in ids
    assert ws._focused_session_id == "another-session"
    assert server._sessions["session-1"]["head_id"] == "new-live-head"


def test_deleted_session_history_request_does_not_rehydrate_empty_session(
    session_with_tool_outputs,
):
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_errors import OperationError

    store, _ = session_with_tool_outputs
    store.delete_session("session-1")
    server._sessions.pop("session-1", None)
    ws = WebWS()
    ws._focused_session_id = "another-session"
    with pytest.raises(OperationError, match="invalid_request"):
        asyncio.run(
            ws_session.handle_load_session(
                ws,
                {
                    "session_id": "session-1",
                    "history_before": "assistant-1",
                    "request_id": "stale-page",
                },
            )
        )
    assert ws.frames == []
    assert ws._focused_session_id == "another-session"


def test_ten_thousand_node_session_loads_recent_page_without_recursion(
    session_with_tool_outputs,
):
    from openprogram.state.store.session.nodes import Call, ROLE_USER, ROLE_LLM
    from openprogram.interfaces.server.transcript import publisher

    store, _ = session_with_tool_outputs
    sid = "long-session"
    store.create_session(sid, "main")
    _, index = store._open(sid)
    count = 10_000
    # Exercise the production store's warm index without 10,000 unrelated
    # filesystem writes; storage persistence has separate component coverage.
    for i in range(count):
        pred = str(i - 1) if i else None
        node = Call(
            id=str(i),
            role=ROLE_LLM if i % 2 else ROLE_USER,
            output=f"message {i}",
            predecessor=pred,
            created_at=float(i + 1),
        )
        index.append(node, predecessor=pred, caller=None)
    store.set_head(sid, str(count - 1))
    from openprogram.interfaces.server import server

    server._sessions[sid] = {"id": sid, "messages": [], "head_id": str(count - 1)}
    ws = WebWS()
    try:
        asyncio.run(ws_session.handle_load_session(ws, {"session_id": sid}))
        data = next(f["data"] for f in ws.frames if f["type"] == "session_loaded")
        assert [row["id"] for turn in data["turns"] for row in turn["rows"]] == [
            str(i) for i in range(count - 12, count)
        ]
        assert data["window"]["before"] == str(count - 12)
        # A user row and its reply are one turn: the first read holds six.
        assert data["window"]["start"] == count // 2 - 6
        assert data["window"]["end"] == data["window"]["total"] == count // 2
        ws.frames.clear()
        asyncio.run(
            ws_session.handle_load_session(
                ws, {"session_id": sid, "history_before": data["window"]["before"]},
            )
        )
        assert [f["type"] for f in ws.frames] == ["session_history_page"]
        older = ws.frames[0]["data"]
        assert [row["id"] for turn in older["turns"] for row in turn["rows"]] == [
            str(i) for i in range(count - 62, count - 12)
        ]
        assert older["window"]["before"] == str(count - 62)
        assert older["window"]["end"] == data["window"]["start"]
    finally:
        publisher.release(ws)
        server._sessions.pop(sid, None)


def test_pages_read_only_their_own_rows(
    session_with_tool_outputs, monkeypatch
):
    store, _ = session_with_tool_outputs
    previous = "assistant-1"
    for i in range(120):
        mid = f"page-{i}"
        store.append_message(
            "session-1",
            {"id": mid, "role": "user", "content": str(i), "predecessor": previous},
        )
        previous = mid
    store.update_session("session-1", head_id=previous)
    ws = WebWS()

    first_rows = _turn_rows(ws)
    first = next(f["data"] for f in ws.frames if f["type"] == "session_loaded")

    def forbidden(*args, **kwargs):
        raise AssertionError("Paging must not rebuild the transcript or graph")

    monkeypatch.setattr(store, "get_messages", forbidden)
    import openprogram.state.store.session.graph.builder as graph_builder

    monkeypatch.setattr(graph_builder, "build_session_graph", forbidden)
    ws.frames.clear()
    _turn_rows(ws, {"history_before": first["window"]["before"]})
    older = ws.frames[0]["data"]
    assert older["window"]["end"] == first["window"]["start"]
    ws.frames.clear()
    newer = _turn_rows(ws, {"history_after": older["window"]["after"]})
    assert newer == first_rows


def test_search_session_finds_text_on_the_loaded_branch(
    session_with_tool_outputs,
):
    from openprogram.interfaces.server.ws_errors import OperationError

    store, _ = session_with_tool_outputs
    store.append_message(
        "session-1",
        {"id": "user-2", "role": "user", "content": "Where is the Needle?", "predecessor": "assistant-1"},
    )
    store.append_message(
        "session-1",
        {"id": "assistant-2", "role": "assistant", "content": "needle here, and another needle", "predecessor": "user-2"},
    )
    store.update_session("session-1", head_id="assistant-2")
    ws = WebWS()

    async def scenario():
        await ws_session.handle_search_session(ws, {
            "session_id": "session-1", "query": " NEEDLE ", "request_id": "find-1",
        })
        [frame] = ws.frames
        assert frame["type"] == "session_search_results"
        data = frame["data"]
        assert data["action"] == "search_session" and data["request_id"] == "find-1"
        assert [(m["message_id"], m["count"]) for m in data["matches"]] == [("user-2", 1), ("assistant-2", 2)]
        assert data["total"] == 3
        # The tool row's output (x"\\...) is never searched as conversation text.
        ws.frames.clear()
        await ws_session.handle_search_session(ws, {"session_id": "session-1", "query": 'x"'})
        assert ws.frames[0]["data"]["matches"] == []
        with pytest.raises(OperationError, match="invalid_request"):
            await ws_session.handle_search_session(ws, {"session_id": "no-such-session", "query": "needle"})

    asyncio.run(scenario())
