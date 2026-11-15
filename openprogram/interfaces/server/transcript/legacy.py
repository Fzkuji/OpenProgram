"""Every old record shape a read still meets, as a named case."""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

_log = logging.getLogger(__name__)

_NOTICE_KINDS = {"report", "reply", "idle"}
_PASTE_LIMIT = 20
_PASTE_HEAD = 32
_PASTE_LABEL = 80
# The receipt line send_message puts before a message it delivers.
_RECEIPT = re.compile(r"^\[(task|message) from (\S+?)(?: \((.*?)\))?\][^\n]*\n+")
# The header the removed handoff tool put before a message or reply it
# carried between conversations: ``📨 Message from “Title” (id):``.
_HANDOFF = re.compile(r"^(?:📨 Message from|↩️ Reply from) “[^”\n]*” \(([^()\s]+)\)[^\n]*:\n")


def parsed_extra(row: dict) -> dict | None:
    """``extra`` as a dict, from an object or a JSON string."""
    extra = row.get("extra")
    if isinstance(extra, str) and extra:
        try:
            extra = json.loads(extra)
        except (TypeError, ValueError):
            return None
    return extra if isinstance(extra, dict) else None


def read_attach(row: dict) -> dict | None:
    """An attach row's facts, hoisted to the row or left inside ``extra``."""
    attach = row.get("attach")
    if isinstance(attach, dict):
        return attach
    nested = (parsed_extra(row) or {}).get("attach")
    return nested if isinstance(nested, dict) else None


def attach_with_call(row: dict) -> dict | None:
    """An attach row's facts, with the tool call its row names."""
    attach = read_attach(row)
    call_id = row.get("tool_call_id")
    if attach is not None and call_id and not attach.get("tool_call_id"):
        return {**attach, "tool_call_id": call_id}
    return attach


def read_speaker(row: dict) -> dict | None:
    """The human a channel turn names beside its text."""
    if row.get("speaker_kind") != "human":
        return None
    speaker_id = row.get("speaker_id") if isinstance(row.get("speaker_id"), str) else ""
    display = row.get("speaker_display") if isinstance(row.get("speaker_display"), str) else ""
    if not speaker_id and not display:
        return None
    return {key: value for key, value in (("id", speaker_id), ("display", display)) if value}


def read_notice(raw: Any) -> list[dict] | None:
    """A background-agent notice's items, kept when each names its kind and line."""
    if not isinstance(raw, list):
        return None
    items = [
        {
            "kind": item["kind"],
            "name": item["name"] if isinstance(item.get("name"), str) else "",
            "failed": item.get("failed") is True,
            "address": item["address"] if isinstance(item.get("address"), str) else "",
            "line": item["line"],
        }
        for item in raw
        if isinstance(item, dict) and item.get("kind") in _NOTICE_KINDS and isinstance(item.get("line"), str)
    ]
    return items or None


def read_pasted_elements(row: dict) -> list[dict]:
    """Pasted spans of a user row; the store flattens ``extra`` onto the row."""
    raw = row.get("text_elements")
    if raw is None:
        raw = (parsed_extra(row) or {}).get("text_elements")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:_PASTE_LIMIT]:
        if not isinstance(item, dict):
            continue
        start, end, label, head = (item.get(key) for key in ("tail_start", "tail_end", "label", "head"))
        if (isinstance(start, int) and isinstance(end, int) and not isinstance(start, bool) and not isinstance(end, bool)
                and start > end >= 0 and isinstance(label, str) and isinstance(head, str)):
            out.append({"tail_start": start, "tail_end": end, "label": label[:_PASTE_LABEL], "head": head[:_PASTE_HEAD]})
    return out


def chapter_pinned_at(chapter: Any) -> float | None:
    if not isinstance(chapter, dict):
        return None
    at = chapter.get("pinned_at")
    if isinstance(at, (int, float)) and not isinstance(at, bool) and math.isfinite(at) and at > 0:
        return at
    return None


def normalize_citations(raw: Any) -> list[dict] | None:
    """Citations as stored, kept only when they name a url."""
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not item["url"].strip():
            continue
        title = item.get("title")
        start, end = item.get("start"), item.get("end")
        out.append({
            "url": item["url"].strip(),
            "title": title.strip() if isinstance(title, str) and title.strip() else None,
            "start": start if isinstance(start, (int, float)) and not isinstance(start, bool) else None,
            "end": end if isinstance(end, (int, float)) and not isinstance(end, bool) else None,
        })
    return out or None


def resolve_folded_head(head: str | None, raw_rows: list[dict], folded_rows: list[dict]) -> str | None:
    """A head aggregation folded away resolves to the row that absorbed it.

    A call row hangs off its reply by ``caller`` with an empty predecessor, so
    the walk takes either edge; a turn cut off mid-way can leave the head there.
    """
    if not head:
        return head
    shown = {row.get("id") for row in folded_rows}
    if head in shown:
        return head
    by_id = {row.get("id"): row for row in raw_rows}
    current: str | None = head
    for _ in range(100):
        if current is None or current in shown:
            break
        row = by_id.get(current) or {}
        parent = row.get("predecessor") or row.get("caller")
        current = parent if parent and parent != current else None
    return current if current in shown else head


def branch_row_ids(db: Any, session_id: str, head: str | None) -> list[str]:
    """The branch's ids under ``head``; a broken old chain yields none.

    With none, ``active_branch_chain`` walks predecessors over the same rows,
    so old data with a gap still displays and keeps its shape.
    """
    from openprogram.state.store.session.session_store import BrokenPredecessorChainError

    try:
        return [row.get("id") for row in db.get_branch(session_id, head) or []]
    except BrokenPredecessorChainError as exc:
        _log.warning("[transcript] broken predecessor chain in %s: %s", session_id, exc)
        return []


def receipt_address(content: str) -> str | None:
    """The address on a delivery's receipt line, or None when there is no receipt."""
    if not isinstance(content, str):
        return None
    match = _RECEIPT.match(content)
    if match:
        return match.group(2)
    handed = _HANDOFF.match(content)
    return handed.group(1) if handed else None
