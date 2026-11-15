"""A reply's blocks as a read shows them: stored order, with ids."""
from __future__ import annotations

from typing import Any

from .legacy import normalize_citations

AGENTIC_TOOL_NAMES = {"gui_agent"}


def js_string(value: Any) -> str:
    """``String(value)`` as the web client would have written it."""
    if isinstance(value, str):
        return value
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ",".join("" if item is None else js_string(item) for item in value)
    if value is None:
        return ""
    return "[object Object]"


def _tool_status(block: dict) -> str:
    outcome = block.get("outcome")
    if outcome == "cancelled":
        return "cancelled"
    return "error" if outcome in ("not_started", "unknown") or block.get("is_error") else "done"


def _result(block: dict) -> str | None:
    result = block.get("result")
    return None if result is None else js_string(result)


def _known(fields: dict) -> dict:
    """A long turn repeats these keys thousands of times; an empty one is not sent."""
    return {key: value for key, value in fields.items() if value is not None}


def project_blocks(row_id: str, stored: list) -> tuple[list[dict], list[dict], str | None]:
    """``(blocks, tools, thinking)`` for one reply.

    Empty text and thinking are dropped, ``index`` stays the stored position,
    and a tool block whose store names no call keeps a generated call id.
    Each entry of ``tools`` is its block's facts without the input and result.
    """
    blocks: list[dict] = []
    tools: list[dict] = []
    thinking: str | None = None
    for index, block in enumerate(stored):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "thinking" and block.get("text"):
            thinking = (thinking or "") + block["text"]
            blocks.append({"index": index, "type": "thinking", "text": block["text"]})
        elif kind == "text" and block.get("text"):
            citations = normalize_citations(block.get("citations"))
            blocks.append({"index": index, "type": "text", "text": block["text"],
                           **({"citations": citations} if citations else {})})
        elif kind == "steering" and block.get("message_id"):
            blocks.append({"index": index, "type": "steering", "message_id": block["message_id"],
                           "text": block.get("text"), "timestamp": block.get("timestamp")})
        elif kind == "tool":
            call_id = block.get("tool_call_id") or f"{row_id}_t{index}"
            tool = block.get("tool") or "?"
            full_output = block.get("full_output") if isinstance(block.get("full_output"), dict) else None
            drawn: dict = _known({
                "index": index, "type": "tool", "tool": tool, "tool_call_id": call_id,
                "input": block.get("input") or "", "result": _result(block),
                "truncated": block.get("truncated") is True, "total_bytes": block.get("total_bytes"),
                "full_output": full_output,
                # A bounded result's card reads its whole output by these.
                "message_id": block.get("message_id") or (row_id if full_output else None),
                "node_id": block.get("node_id") or (call_id if full_output else None),
                "is_error": bool(block.get("is_error")), "outcome": block.get("outcome"),
            })
            if not block.get("tool_call_id"):
                drawn["id"] = f"tool:{index}"
            if block.get("provider_executed") is True:
                drawn["provider_executed"] = True
            blocks.append(drawn)
            if tool in AGENTIC_TOOL_NAMES:
                continue
            # The body travels once, on the block; the client copies it back (turn-rows.ts).
            tools.append(_known({
                "id": call_id, "tool": tool,
                "truncated": block.get("truncated") is True, "totalBytes": block.get("total_bytes"),
                "messageId": block.get("message_id"), "nodeId": block.get("node_id"),
                "isError": bool(block.get("is_error")),
                **({"providerExecuted": True} if block.get("provider_executed") is True else {}),
                "status": _tool_status(block),
            }))
    return blocks, tools, thinking


def assign_block_ids(blocks: list[dict]) -> None:
    """Give every block its stable id.

    Text and thinking count runs of their type, so a run the final write
    splits keeps its first id and an open step keeps its place.
    """
    runs: dict[str, int] = {}
    seen: dict[str, int] = {}
    in_run: dict | None = None

    def unique(value: str) -> str:
        count = seen.get(value, 0)
        seen[value] = count + 1
        return f"{value}#{count}" if count else value

    for block in blocks:
        kind = block.get("type")
        if kind == "tool" and block.get("id"):
            in_run = None  # a stored tool block that named no call
        elif kind == "tool" and block.get("tool_call_id"):
            in_run = None
            block["id"] = unique(block["tool_call_id"])
        elif kind == "steering" and block.get("message_id"):
            in_run = None
            block["id"] = unique(f"steering:{block['message_id']}")
        elif in_run is not None and in_run["type"] == kind and kind in ("text", "thinking"):
            in_run["parts"] += 1
            block["id"] = f"{in_run['id']}.{in_run['parts']}"
        else:
            number = runs.get(kind, 0)
            runs[kind] = number + 1
            in_run = {"type": kind, "id": f"{kind}:{number}", "parts": 0}
            block["id"] = in_run["id"]
