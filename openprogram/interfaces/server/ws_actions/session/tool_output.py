"""Inline tool-output truncation for the wire, and the full-output fetch."""
from __future__ import annotations

import json

from openprogram.interfaces.server.transcript.legacy import parsed_extra
from openprogram.state.store.session.conversation_work import run_for_conversation


# Keep ordinary tool results byte-for-byte compatible while preventing one
# abnormal result from dominating the session_loaded JSON frame. The full
# persisted node remains available through get_full_tool_output.
TOOL_OUTPUT_INLINE_MAX_BYTES = 32 * 1024


def _serialized_string_prefix(text: str, limit: int = TOOL_OUTPUT_INLINE_MAX_BYTES) -> str:
    """Longest leading substring whose JSON string stays within ``limit``."""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        size = len(json.dumps(
            text[:middle], ensure_ascii=False,
        ).encode("utf-8"))
        if size <= limit:
            low = middle
        else:
            high = middle - 1
    return text[:low]


# What the call tree under a reply draws; arguments and blocks stay in the store.
CALL_ROW_FIELDS = ("id", "role", "function", "content", "status", "is_error", "duration_ms", "caller")


# One reply's whole call tree on the wire. It leaves room in the initial
# history page (128 KiB) for the reply itself and the turns around it.
CALL_TREE_MAX_BYTES = 96 * 1024


def _serialized_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def call_tree_rows(rows: list[dict]) -> list[dict]:
    """One reply's call tree, given depth-first, within ``CALL_TREE_MAX_BYTES``.

    The tree's shape is paid for first. Only a tree too wide to describe at all
    loses rows, from the end of the walk, so every kept row keeps its ancestors.
    Bodies then fill what is left in the same order, each under the inline cap,
    and rows reached after the budget is spent carry an empty body.
    """
    kept: list[tuple[dict, dict]] = []
    spent = _serialized_size([])
    for row in rows:
        shape = {field: row[field] for field in CALL_ROW_FIELDS if field in row}
        if "content" in shape:
            shape["content"] = ""
        cost = _serialized_size(shape) + len(", ")
        if spent + cost > CALL_TREE_MAX_BYTES:
            break
        spent += cost
        kept.append((row, shape))
    empty_body = _serialized_size("")
    for row, shape in kept:
        content = row.get("content")
        if "content" not in shape:
            continue
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
        limit = min(TOOL_OUTPUT_INLINE_MAX_BYTES, CALL_TREE_MAX_BYTES - spent + empty_body)
        size = _serialized_size(text)
        if size <= limit:
            shape["content"] = content
        else:
            text = _serialized_string_prefix(text, limit)
            shape["content"] = text
            size = _serialized_size(text)
        spent += size - empty_body
    return [shape for _, shape in kept]


def _truncate_tool_record(
    record: dict,
    *,
    message_id: str,
    fallback_node_id: str | None = None,
) -> dict:
    result = record.get("result")
    serialized = json.dumps(
        result, ensure_ascii=False, default=str,
    ).encode("utf-8")
    if len(serialized) <= TOOL_OUTPUT_INLINE_MAX_BYTES:
        return record
    node_id = record.get("tool_call_id") or fallback_node_id
    if not node_id:
        return record
    text = result if isinstance(result, str) else serialized.decode("utf-8")
    prefix = _serialized_string_prefix(text)
    return {
        **record,
        "result": prefix,
        "truncated": True,
        "total_bytes": len(serialized),
        "message_id": message_id,
        "node_id": node_id,
    }


def _truncate_tool_blocks(
    blocks: list,
    *,
    message_id: str,
    tool_node_ids: list[str],
) -> tuple[list, bool]:
    changed = False
    tool_index = 0
    output = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "tool":
            output.append(block)
            continue
        fallback = (
            tool_node_ids[tool_index] if tool_index < len(tool_node_ids) else None
        )
        tool_index += 1
        truncated = _truncate_tool_record(
            block, message_id=message_id, fallback_node_id=fallback,
        )
        changed = changed or truncated is not block
        output.append(truncated)
    return output, changed


def _truncate_tool_outputs_for_wire(messages: list[dict]) -> list[dict]:
    """Copy only messages whose inline tool result crosses the wire cap."""
    output = []
    for message in messages:
        message_id = str(message.get("id") or "")
        tool_calls = message.get("tool_calls")
        tool_node_ids = [
            str(call.get("tool_call_id"))
            for call in (tool_calls or [])
            if isinstance(call, dict) and call.get("tool_call_id")
        ]
        changed = False
        copied = message
        if isinstance(tool_calls, list):
            truncated_calls = [
                _truncate_tool_record(call, message_id=message_id)
                if isinstance(call, dict) else call
                for call in tool_calls
            ]
            if any(a is not b for a, b in zip(truncated_calls, tool_calls)):
                copied = {**copied, "tool_calls": truncated_calls}
                changed = True

        blocks = message.get("blocks")
        if isinstance(blocks, list):
            truncated_blocks, blocks_changed = _truncate_tool_blocks(
                blocks, message_id=message_id, tool_node_ids=tool_node_ids,
            )
            if blocks_changed:
                copied = {**copied, "blocks": truncated_blocks}
                changed = True

        extra = parsed_extra(message)
        if isinstance(extra, dict) and isinstance(extra.get("blocks"), list):
            extra_blocks, extra_changed = _truncate_tool_blocks(
                extra["blocks"],
                message_id=message_id,
                tool_node_ids=tool_node_ids,
            )
            if extra_changed:
                truncated_extra = {**extra, "blocks": extra_blocks}
                if isinstance(message.get("extra"), str):
                    truncated_extra = json.dumps(
                        truncated_extra, ensure_ascii=False, default=str,
                    )
                copied = {**copied, "extra": truncated_extra}
                changed = True
        output.append(copied if changed else message)
    return output


# The most of a saved whole output one request returns; a longer one says
# where the rest is.
FULL_OUTPUT_MAX_CHARS = 1_000_000


def _whole_output(row: dict):
    """A tool row's whole output: the file a bounded result saved
    (``details.output.full_path``, ``tool_dispatch._as_stored``), which
    holds the tool's own text without the note written for the model, or
    the row's text when it has none or the file is gone."""
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    output = details.get("output") if isinstance(details.get("output"), dict) else {}
    path = output.get("full_path")
    if isinstance(path, str) and path:
        try:
            with open(path, encoding="utf-8", errors="replace") as stream:
                text = stream.read(FULL_OUTPUT_MAX_CHARS + 1)
        except OSError:
            text = None
        if text is not None:
            if len(text) > FULL_OUTPUT_MAX_CHARS:
                text = (text[:FULL_OUTPUT_MAX_CHARS]
                        + f"\n[... the rest is in {path} ...]")
            return text
    return row.get("content", "")


async def handle_get_full_tool_output(ws, cmd: dict) -> None:
    """Return one persisted tool node's untruncated output."""
    from openprogram.runtime.context.session_db import default_db

    session_id = cmd.get("session_id")
    message_id = cmd.get("message_id")
    node_id = cmd.get("node_id")
    request_id = cmd.get("request_id")
    data = {
        "session_id": session_id,
        "message_id": message_id,
        "node_id": node_id,
        "request_id": request_id,
    }
    row = None
    if all(isinstance(value, str) and value for value in (
        session_id, message_id, node_id,
    )):
        # A block names the provider's call id, and the row is named after it
        # and its reply.
        row_ids = {node_id, f"{message_id}_t_{node_id}"}
        # The rows are read under the conversation's lock: off the loop.
        messages = await run_for_conversation(session_id, default_db().get_messages, session_id)
        row = next(
            (
                message for message in messages
                if message.get("id") in row_ids
                and message.get("role") == "tool"
                and message.get("caller") == message_id
            ),
            None,
        )
    if row is None:
        data["error"] = "tool output not found"
    else:
        data["result"] = _whole_output(row)
    await ws.send_text(json.dumps({
        "type": "full_tool_output",
        "data": data,
    }, ensure_ascii=False, default=str))
