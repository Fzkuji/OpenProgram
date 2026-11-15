"""CallStreamState owns merge, revision, and preview isolation."""
from __future__ import annotations

from openprogram.runtime.agent.runtime.execution_stream.state import (
    CallStreamState,
    StreamIdentity,
)
from openprogram.runtime.agent.runtime.execution_stream.adapter import (
    project_provider_event,
)
from openprogram.runtime.agent.runtime.execution_stream.protocol import (
    MAX_INLINE_PREVIEW_BYTES,
)


def _state(**kwargs):
    ident = StreamIdentity(
        session_id="sess",
        execution_id="exec",
        node_id="node",
        generation=1,
        display_msg_id="assistant",
        ephemeral=True,
    )
    emitted: list[dict] = []
    st = CallStreamState(ident, emit=emitted.append, **kwargs)
    return st, emitted


def test_block_delta_advances_revision_and_merges_preview():
    st, emitted = _state()
    st.start_attempt()
    st.append_delta(kind="text", delta="A")
    st.append_delta(kind="text", delta="B")
    st.flush_checkpoint()
    assert "AB" in st.text_preview()
    ops = [e["data"]["op"] for e in emitted]
    assert "attempt_started" in ops
    assert "block_started" in ops
    assert "block_delta" in ops
    # revisions are contiguous on change events
    deltas = [e["data"] for e in emitted if e["data"]["op"] == "block_delta"]
    assert deltas
    assert all("base_revision" in d and "revision" in d for d in deltas)


def test_retry_isolates_attempts():
    st, emitted = _state()
    st.start_attempt(reason="initial")
    st.append_delta(kind="text", delta="old half")
    st.finish_attempt(status="failed", validation="failed")
    st.start_attempt(reason="structured_output_repair")
    st.append_delta(kind="text", delta="new full")
    assert st.text_preview() == "new full"
    assert len(st.attempt_order) == 2
    first = st.attempts[st.attempt_order[0]]
    assert "old half" in first.blocks[list(first.blocks)[0]].content


def test_cancel_stops_accepting_and_keeps_partial():
    st, _ = _state()
    st.start_attempt()
    st.append_delta(kind="text", delta="partial")
    st.stop_accepting()
    st.append_delta(kind="text", delta=" after cancel")
    assert st.text_preview() == "partial"
    meta = st.finish_node(status="cancelled")
    assert meta["phase"] == "cancelled"
    assert meta["partial"] is False
    assert "partial" in meta["preview_text"]


def test_node_finished_snapshot_recoverable_without_deltas():
    st, emitted = _state()
    st.start_attempt()
    st.append_delta(kind="text", delta="hello")
    st.finish_node(status="completed", result="hello")
    finished = [e["data"] for e in emitted if e["data"]["op"] == "node_finished"]
    assert len(finished) == 1
    snap = finished[0]["snapshot"]
    assert snap["preview_text"].endswith("hello")
    assert snap["phase"] == "completed"


def test_opaque_reasoning_not_in_preview():
    st, _ = _state()
    st.start_attempt()
    bid = st.start_block(kind="reasoning_summary", visibility="opaque")
    st.append_delta(kind="reasoning_summary", delta="secret-sig", block_id=bid)
    assert st.text_preview(kind="reasoning_summary") == ""


def test_envelope_schema():
    st, emitted = _state()
    st.start_attempt()
    st.append_delta(kind="text", delta="x")
    env = emitted[-1]
    assert env["type"] == "chat_response"
    data = env["data"]
    assert data["type"] == "execution_stream"
    assert data["schema_version"] == 1
    assert data["session_id"] == "sess"
    assert data["execution_id"] == "exec"
    assert data["node_id"] == "node"


def test_tool_ref_emits_started_and_preserves_metadata():
    st, emitted = _state()
    st.start_attempt()
    bid = st.add_tool_ref(
        tool_call_id="call_1",
        tool_name="search",
        ref_node_id="node/search#1",
    )
    assert bid
    started = [e["data"] for e in emitted if e["data"]["op"] == "block_started"]
    assert started
    last = started[-1]
    assert last["kind"] == "tool_ref"
    assert last["tool_call_id"] == "call_1"
    assert last["tool_name"] == "search"
    assert last["ref_node_id"] == "node/search#1"
    # text preview ignores tool_ref
    assert st.text_preview() == ""


def test_tool_ref_idempotent_and_finish_sets_ref():
    st, emitted = _state()
    st.start_attempt()
    b1 = st.add_tool_ref(tool_call_id="call_2", tool_name="read")
    b2 = st.add_tool_ref(tool_call_id="call_2", ref_node_id="node/read#1")
    assert b1 == b2
    refs = [
        blk
        for aid in st.attempt_order
        for blk in st.attempts[aid].blocks.values()
        if blk.kind == "tool_ref"
    ]
    assert len(refs) == 1
    assert refs[0].ref_node_id == "node/read#1"
    st.finish_tool_ref("call_2", ref_node_id="node/read#1")
    emitted_count = len(emitted)
    st.finish_tool_ref("call_2", ref_node_id="node/read#1")
    assert len(emitted) == emitted_count
    assert refs[0].status == "finished"
    finished = [e["data"] for e in emitted if e["data"]["op"] == "block_finished"]
    assert finished
    assert finished[-1]["tool_call_id"] == "call_2"
    assert finished[-1]["ref_node_id"] == "node/read#1"


def test_parallel_group_requires_explicit_group_id():
    st, _ = _state()
    st.start_attempt()
    st.add_tool_ref(tool_call_id="a", tool_name="t1", group_id="parallel-1")
    st.add_tool_ref(tool_call_id="b", tool_name="t2", group_id="parallel-1")
    refs = [
        blk
        for aid in st.attempt_order
        for blk in st.attempts[aid].blocks.values()
        if blk.kind == "tool_ref"
    ]
    assert len(refs) == 2
    assert refs[0].group_id
    assert refs[0].group_id == refs[1].group_id
    # Adjacent calls without an explicit batch id remain sequential.
    st.add_tool_ref(tool_call_id="sequential", tool_name="t3")
    assert refs[1].group_id == "parallel-1"
    assert st.attempts[st.attempt_order[0]].blocks[st.attempts[st.attempt_order[0]].block_order[-1]].group_id == ""

    # An intervening text block does not cause a later call to inherit the
    # previous group's identity either.
    st.append_delta(kind="text", delta="mid")
    st.add_tool_ref(tool_call_id="c", tool_name="t4")
    refs2 = [
        blk
        for aid in st.attempt_order
        for blk in st.attempts[aid].blocks.values()
        if blk.kind == "tool_ref"
    ]
    assert refs2[2].group_id == ""
    assert refs2[3].group_id == ""


def test_durable_snapshot_contains_ordered_blocks_and_full_text():
    st, _ = _state()
    st.start_attempt()
    st.append_delta(kind="text", delta="before")
    st.add_tool_ref(
        tool_call_id="call-a",
        tool_name="search",
        ref_node_id="tool-a",
        group_id="parallel-1",
    )
    st.append_delta(kind="text", delta="after")
    metadata = st.stream_metadata()
    attempts = metadata["snapshot"]["attempts"]
    blocks = attempts[0]["blocks"]
    assert [block["kind"] for block in blocks] == ["text", "tool_ref", "text"]
    assert blocks[1]["ref_node_id"] == "tool-a"
    assert "before" in blocks[0]["content"]
    assert "after" in blocks[2]["content"]


def test_durable_snapshot_omits_memory_only_and_opaque_content():
    st, _ = _state()
    st.start_attempt()
    st.start_block(kind="text", retention="memory_only")
    st.append_delta(kind="text", delta="private")
    st.start_block(kind="reasoning_summary", visibility="opaque")
    st.append_delta(kind="reasoning_summary", delta="signature")
    snap = st._snapshot_dict_unlocked(durability="durable")
    blocks = snap["attempts"][0]["blocks"]
    assert blocks[0]["content"] == ""
    assert blocks[0]["omitted_by_policy"] is True
    assert blocks[1]["content"] == ""
    assert blocks[1]["omitted_by_policy"] is True
    assert snap["preview_text"] == ""
    assert snap["preview_reasoning"] == ""


def test_durable_snapshot_does_not_replace_long_content_with_preview():
    st, _ = _state()
    st.start_attempt()
    long_text = "x" * (MAX_INLINE_PREVIEW_BYTES + 128)
    st.append_delta(kind="text", delta=long_text)
    snap = st._snapshot_dict_unlocked(durability="durable")
    block = snap["attempts"][0]["blocks"][0]
    assert block["content"] == long_text
    assert block["truncated"] is False


def test_live_snapshot_keeps_long_content_for_refresh_replacement():
    st, emitted = _state()
    st.start_attempt()
    long_text = "prefix-" + ("x" * (MAX_INLINE_PREVIEW_BYTES + 128)) + "-suffix"
    st.append_delta(kind="text", delta=long_text)
    snap = st._snapshot_dict_unlocked(durability="live")
    block = snap["attempts"][0]["blocks"][0]
    assert block["content"] == long_text
    assert block["content"].startswith("prefix-")
    assert block["content"].endswith("-suffix")
    assert block["truncated"] is False
    st.emit_snapshot(durability="live")
    streamed = [e["data"] for e in emitted if e["data"]["op"] == "snapshot"][-1]
    assert streamed["snapshot"]["attempts"][0]["blocks"][0]["content"] == long_text


def test_reused_raw_call_id_gets_distinct_occurrence_blocks():
    st, _ = _state()
    st.start_attempt()
    first = st.add_tool_ref(
        tool_call_id="call_1",
        occurrence_id="occ_round_1",
        tool_name="search",
    )
    second = st.add_tool_ref(
        tool_call_id="call_1",
        occurrence_id="occ_round_2",
        tool_name="search",
    )
    assert first != second
    blocks = st._snapshot_dict_unlocked(durability="durable")["attempts"][0]["blocks"]
    assert [block["occurrence_id"] for block in blocks] == ["occ_round_1", "occ_round_2"]
    # Replaying the exact occurrence remains idempotent.
    assert st.add_tool_ref(
        tool_call_id="call_1",
        occurrence_id="occ_round_2",
        tool_name="search",
    ) == second


def test_tool_result_continuation_stays_in_same_attempt():
    st, _ = _state()
    st.start_attempt()
    project_provider_event(st, {
        "type": "tool_use",
        "tool_call_id": "call-a",
        "tool": "search",
        "node_id": "tool-a",
        "group_id": "parallel-1",
    })
    project_provider_event(st, {
        "type": "tool_result",
        "tool_call_id": "call-a",
        "node_id": "tool-a",
    })
    project_provider_event(st, {"type": "text", "text": "continued"})
    assert len(st.attempt_order) == 1
    blocks = st._snapshot_dict_unlocked(durability="durable")["attempts"][0]["blocks"]
    assert [block["kind"] for block in blocks] == ["tool_ref", "text"]


def test_hidden_tool_events_are_not_projected_into_visible_blocks():
    st, emitted = _state()
    st.start_attempt()
    project_provider_event(st, {
        "type": "tool_use",
        "tool_call_id": "secret-call",
        "tool": "secret_tool",
        "node_id": "",
        "expose": "hidden",
    })
    project_provider_event(st, {
        "type": "tool_arguments",
        "tool_call_id": "secret-call",
        "input": '{"secret": true}',
        "expose": "hidden",
    })
    project_provider_event(st, {
        "type": "tool_result",
        "tool_call_id": "secret-call",
        "node_id": "",
        "expose": "hidden",
    })
    blocks = st._snapshot_dict_unlocked(durability="durable")["attempts"][0]["blocks"]
    assert blocks == []
    assert not any(
        e["data"].get("tool_name") == "secret_tool"
        for e in emitted
        if isinstance(e.get("data"), dict)
    )
