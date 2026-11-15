"""Display rows from stored rows: the one place a transcript row is shaped."""
from __future__ import annotations

import json
from typing import Any

from .blocks import assign_block_ids, project_blocks
from .children import assign_block_owners, is_reference_card
from .legacy import (
    attach_with_call,
    chapter_pinned_at,
    read_notice,
    read_pasted_elements,
    read_speaker,
    receipt_address,
)
from .status import stored_row_status

_SYSTEM_EVENT_KINDS = {"compaction", "snip", "event"}
_GOAL_SPAWN_LABELS = {"goal 判定", "goal 完善"}


def _present(row: dict) -> dict:
    return {key: value for key, value in row.items() if value is not None}


def _truthy(value: Any) -> Any:
    """``value || undefined``: an empty object or list counts as present."""
    if value is None or value is False or value == "" or (isinstance(value, (int, float)) and value == 0):
        return None
    return value


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _number(value: Any) -> Any:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _sibling_fields(row: dict) -> dict:
    return {
        "siblingIndex": row.get("sibling_index"), "siblingTotal": row.get("sibling_total"),
        "prevSiblingId": row.get("prev_sibling_id"), "nextSiblingId": row.get("next_sibling_id"),
    }


class _CallTrees:
    """The rows folded into a reply's call tree, by the row that called them."""

    def __init__(self, rows: list[dict]):
        by_id = {row["id"]: row for row in rows if row.get("id")}
        self.kids: dict[str, list[dict]] = {}
        self.folded: set[str] = set()
        for row in rows:
            caller, row_id = row.get("caller"), row.get("id")
            if not caller or caller == "ROOT" or not row_id or caller not in by_id:
                continue
            is_tool = row.get("role") == "tool" and bool(row.get("function"))
            is_leaf = row.get("role") == "assistant" and by_id[caller].get("role") == "tool"
            if is_tool or is_leaf:
                self.kids.setdefault(caller, []).append(row)
                self.folded.add(row_id)

    def node(self, row: dict) -> dict:
        kids = [self.node(kid) for kid in self.kids.get(row.get("id") or "", [])]
        content = row.get("content") if isinstance(row.get("content"), str) else None
        failed = row.get("status") == "error" or row.get("is_error") is True
        return _present({
            "path": row.get("id"), "tool_call_id": row.get("tool_call_id") or None,
            "name": "LLM" if row.get("role") == "assistant" else (row.get("function") or "call"),
            "status": row.get("status"), "output": content,
            "error": (content or "error") if failed else None,
            "duration_ms": row.get("duration_ms"), "children": kids or None,
        })


def _sender_exists(db: Any, address: str) -> bool:
    """Whether the session a receipt names exists, so a typed look-alike stays the person's own message."""
    session_id = address.split(":", 1)[0]
    if db is None:
        from openprogram.runtime.context.session_db import default_db

        db = default_db()
    try:
        return db.get_session(session_id) is not None
    except Exception:  # noqa: BLE001 - an unreadable store names no sender
        return False


def _user_row(row: dict, row_id: str, timestamp: Any, db: Any) -> dict:
    spawned = row.get("spawned_from") or {}
    origin = row.get("origin")
    drawn = _present({
        "id": row_id, "role": "user", "content": row.get("content") or "",
        "display": "runtime" if row.get("display") == "runtime" else None,
        "status": "done", "timestamp": timestamp, "agentId": row.get("agent_id") or None,
        "source": _string(row.get("source")), "notice": read_notice(row.get("notice")),
        "trigger": "restart" if row.get("trigger") == "restart" else None,
        "startsGoal": True if row.get("starts_goal") is True else None,
        "speaker": read_speaker(row), "steering": row.get("steering") is True,
        "calledBy": _string(row.get("predecessor")), "pastedElements": read_pasted_elements(row),
        "spawnedFrom": _present({"callerId": spawned["caller_id"], "label": spawned.get("label") or None})
        if spawned.get("caller_id") else None,
        "chapterPinnedAt": chapter_pinned_at(row.get("chapter")),
        **_sibling_fields(row),
    })
    if origin not in ("person", "agent_delivery"):
        # An old row with no origin: a receipt line counts only when the session it names exists.
        address = receipt_address(row.get("content") or "")
        origin = "agent_delivery" if address and _sender_exists(db, address) else "person"
    drawn["origin"] = origin
    request = row.get("command_id") if row.get("steering") is True else row.get("client_request_id")
    if request:
        drawn["clientRequestId"] = request
    return drawn


def _runtime_child(row: dict, row_id: str, timestamp: Any, called_by: str, running: bool) -> dict:
    return _present({
        "id": row_id, "role": "assistant", "content": row.get("content") or "",
        "function": row.get("function") or None, "display": "runtime",
        "status": stored_row_status("running" if running else row.get("status"), row.get("type")),
        "rawType": row.get("type"), "timestamp": timestamp, "contextTree": _truthy(row.get("context_tree")),
        "usage": row.get("usage"), "toolCallId": row.get("tool_call_id") or None,
        "calledBy": called_by, "agentId": row.get("agent_id") or None,
        "rowKind": "function_run",
    })


def _assistant_row(row: dict, row_id: str, timestamp: Any, running: bool) -> dict:
    stored = row.get("blocks") or [{"type": "tool", **call} for call in row.get("tool_calls") or [] if isinstance(call, dict)]
    blocks, tools, thinking = project_blocks(row_id, stored)
    failed = row.get("status") == "error" or row.get("type") == "error"
    detail = row.get("error_detail")
    error_detail = detail if detail is not None else (row.get("content") if failed else None)
    if failed and (not detail or row.get("content") == detail):
        from openprogram.state.reply_text import reply_text
        content = reply_text(blocks)
    else:
        content = row.get("content") or ""
    return _present({
        "id": row_id, "role": "assistant", "content": content, "errorDetail": error_detail,
        "errorReason": row.get("error_reason"), "errorRetryable": row.get("error_retryable"),
        "errorRetryAfterS": row.get("error_retry_after_s"),
        # Which limit or refusal it was (``errors.failure_cause``): the chat's
        # spent-quota card reads the provider, limit and reset from it.
        "errorCause": row.get("error_cause") if failed and isinstance(row.get("error_cause"), dict) else None,
        "streamSeq": _number(row.get("stream_seq")),
        "thinking": thinking, "tools": tools or None, "blocks": blocks or None,
        "function": row.get("function") or None,
        "display": "runtime" if row.get("display") == "runtime" else None,
        "status": stored_row_status("running" if running else row.get("status"), row.get("type")),
        "rawType": row.get("type"), "goalVerification": row.get("goal_verification"), "timestamp": timestamp,
        "contextTree": _truthy(row.get("context_tree")), "usage": row.get("usage"), "turnFiles": row.get("turn_files"),
        "reverted": bool(_truthy(row.get("reverted"))), "attempts": row.get("attempts"),
        "current_attempt": row.get("current_attempt"), "attach": attach_with_call(row),
        "calledBy": _string(row.get("predecessor")), "agentId": row.get("agent_id") or None,
        **_report_fields(row),
        **_sibling_fields(row),
    })


def _report_fields(row: dict) -> dict:
    """An always-on report posted as a message: the chat labels it by these."""
    if not row.get("always_on_report"):
        return {}
    return {"reportId": row.get("always_on_report"), "reportKind": _string(row.get("kind")),
            "reportSource": _string(row.get("report_source")), "reportTitle": _string(row.get("report_title"))}


def _system_row(row: dict, row_id: str, timestamp: Any) -> dict:
    return _present({
        "id": row_id, "role": "system", "content": row.get("content") or "", "status": "done", "timestamp": timestamp,
        "kind": row.get("kind") if row.get("kind") in _SYSTEM_EVENT_KINDS else None,
        "slot": row.get("slot") if row.get("slot") in ("card", "event") else None,
        "summarisedCount": _number(row.get("summarised_count")),
        "tokensBefore": _number(row.get("tokens_before")), "tokensAfter": _number(row.get("tokens_after")),
    })


def _move_top_level_cards(out: list[dict]) -> None:
    """A card anchored on a row that is no reply draws straight after that row."""
    i = 0
    while i < len(out):
        card = out[i]
        if card["role"] == "assistant" and card.get("function") == "attach" and card.get("calledBy"):
            anchor = next((at for at, row in enumerate(out) if row["id"] == card["calledBy"]), -1)
            if anchor >= 0 and anchor != i - 1:
                if anchor < i:
                    del out[i]
                    out.insert(anchor + 1, card)
                else:
                    out.insert(anchor + 1, card)
                    del out[i]
                    i -= 1
        i += 1


def _stamp_steering(out: list[dict], raw_by_id: dict[str, dict]) -> None:
    """A steer stays its own row; the reply it steered draws it at its boundary."""
    by_id = {row["id"]: row for row in out}
    for reply in out:
        if reply["role"] != "assistant":
            continue
        marked = {block.get("message_id") for block in reply.get("blocks") or [] if block.get("type") == "steering"}
        for message_id in marked:
            user = by_id.get(message_id) if message_id else None
            if user and user.get("steering"):
                user["steeringReplyId"] = reply["id"]
        legacy: list[dict] = []
        predecessor = raw_by_id.get(reply["id"], {}).get("predecessor")
        visited: set = set()
        while predecessor and predecessor not in visited:
            visited.add(predecessor)
            user = by_id.get(predecessor)
            if not user or not user.get("steering"):
                break
            user["steeringReplyId"] = reply["id"]
            if user["id"] not in marked:
                legacy.insert(0, _present({"type": "steering", "message_id": user["id"], "text": user["content"],
                                           "timestamp": user.get("timestamp")}))
            predecessor = user.get("calledBy")
        if legacy:
            for at, block in enumerate(legacy):
                block["index"] = at - len(legacy)
            held = reply.get("blocks") or ([{"index": 0, "type": "text", "text": reply["content"]}] if reply["content"] else [])
            reply["blocks"] = [*legacy, *held]


def _prompt_of(by_id: dict, reply: dict) -> dict | None:
    prompt_id = reply.get("calledBy")
    if prompt_id is None and reply["id"].endswith("_reply"):
        prompt_id = reply["id"][: -len("_reply")]
    return by_id.get(prompt_id) if prompt_id else None


def _stamp_continuations(out: list[dict]) -> None:
    """A reply a restart cut off is drawn by the reply that continues it."""
    by_id = {row["id"]: row for row in out}
    for reply in out:
        if reply["role"] != "assistant" or not reply.get("calledBy"):
            continue
        prompt = by_id.get(reply["calledBy"])
        cut = by_id.get(prompt["calledBy"]) if prompt and prompt.get("trigger") == "restart" and prompt.get("calledBy") else None
        if cut and cut["role"] == "assistant":
            cut["continuedBy"] = reply["id"]
    for reply in out:
        if reply["role"] != "assistant":
            continue
        cut_ids: list[str] = []
        seen = {reply["id"]}
        prompt = _prompt_of(by_id, reply)
        while prompt and prompt.get("trigger") == "restart" and prompt.get("calledBy") and prompt["calledBy"] not in seen:
            cut = by_id.get(prompt["calledBy"])
            if not cut or cut["role"] != "assistant":
                break
            seen.add(cut["id"])
            cut_ids.insert(0, cut["id"])
            prompt = _prompt_of(by_id, cut)
        if cut_ids:
            reply["continues"] = cut_ids


def _row_kind(row: dict, by_id: dict, session_id: str | None, spawn_labels: dict) -> str:
    """One value per branch of the transcript's dispatch."""
    role = row["role"]
    if role == "system":
        if row.get("kind") == "compaction" and row.get("slot") == "card":
            return "compaction_card"
        return "system_event" if row.get("kind") in _SYSTEM_EVENT_KINDS else "system_text"
    if role == "assistant" and row.get("function") == "attach":
        return "external_reference" if is_reference_card(row.get("attach"), session_id) else "sub_agent_step"
    if role == "user" and row.get("source") == "job_followup" and row.get("notice"):
        return "agent_notice"
    if role == "user" and row.get("display") != "runtime" and not row.get("spawnedFrom") and row.get("origin") == "agent_delivery":
        return "received_message"
    if role == "user" and row.get("spawnedFrom"):
        return "spawned_root"
    if row.get("display") == "runtime":
        return "silent" if role == "user" else "function_run"
    if role == "user":
        return "user"
    called_by = row.get("calledBy")
    parent_label = ((by_id.get(called_by) or {}).get("spawnedFrom") or {}).get("label") or spawn_labels.get(called_by)
    return "silent" if row.get("goalVerification") or parent_label in _GOAL_SPAWN_LABELS else "assistant"


def _no_id_key(row: dict, position: int) -> str:
    """A key for a row an old store wrote with no id, the same wherever it is read."""
    return f"hist_{row.get('role') or 'row'}_{row.get('timestamp') or row.get('created_at') or 0}_{position}"


def project_rows(
    rows: list[dict],
    *,
    session_id: str | None = None,
    covered_by: dict[str, str] | None = None,
    spawn_labels: dict[str, str] | None = None,
    db: Any = None,
) -> list[dict]:
    """One turn's stored rows, shaped as the chat draws them."""
    trees = _CallTrees(rows)
    raw_by_id = {row["id"]: row for row in rows if row.get("id")}
    out: list[dict] = []
    replies: dict[str, dict] = {}
    for position, row in enumerate(rows):
        if row.get("id") and row["id"] in trees.folded:
            continue
        placeholder = row.get("type") == "status" and row.get("display") == "runtime"
        running = placeholder and row.get("status") == "running"
        if row.get("type") == "status" and not placeholder:
            continue
        row_id = row.get("id") or _no_id_key(row, position)
        timestamp = row.get("timestamp") or row.get("created_at")
        role = row.get("role")
        if role == "user":
            out.append(_user_row(row, row_id, timestamp, db))
            continue
        if placeholder and role == "assistant":
            caller = row.get("caller")
            called_by = caller if isinstance(caller, str) and caller and caller != "ROOT" else None
            parent = replies.get(called_by) if called_by else None
            if parent:
                parent.setdefault("runtimeChildren", []).append(_runtime_child(row, row_id, timestamp, called_by, running))
                continue
        if role == "assistant":
            reply = _assistant_row(row, row_id, timestamp, running)
            if row.get("function") == "attach" and reply.get("calledBy"):
                parent = replies.get(reply["calledBy"])
                if parent:
                    reply["rowKind"] = "external_reference" if is_reference_card(reply.get("attach"), session_id) else "sub_agent_step"
                    parent.setdefault("attachCards", []).append(reply)
                    continue
            roots = [kid for kid in trees.kids.get(row_id, []) if kid.get("role") == "tool" and kid.get("function")]
            if roots:
                reply["callRoots"] = [trees.node(kid) for kid in roots]
            out.append(reply)
            replies[row_id] = reply
            continue
        if role == "tool" and row.get("function"):
            content = row.get("content")
            out.append(_present({
                "id": row_id, "role": "assistant",
                "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, separators=(",", ":"), default=str),
                "function": row.get("function") or None, "display": "runtime",
                "status": stored_row_status(row.get("status"), row.get("type")),
                "rawType": row.get("type"), "timestamp": timestamp, "contextTree": _truthy(row.get("context_tree")),
                "agentId": row.get("agent_id") or None, **_sibling_fields(row),
            }))
            continue
        out.append(_system_row(row, row_id, timestamp))
    _move_top_level_cards(out)
    _stamp_steering(out, raw_by_id)
    _stamp_continuations(out)
    by_id = {row["id"]: row for row in out}
    for row in out:
        row["rowKind"] = _row_kind(row, by_id, session_id, spawn_labels or {})
        if covered_by and row["id"] in covered_by:
            row["coveredBy"] = covered_by[row["id"]]
        if row["role"] == "assistant":
            if row.get("blocks"):
                assign_block_ids(row["blocks"])
            assign_block_owners(row, session_id)
    return out
