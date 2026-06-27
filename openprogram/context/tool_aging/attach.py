"""Attach tool sub-call rows to their owning assistant.

``DagSessionDB.get_branch()`` walks the conversation chain (user ↔
assistant ↔ user) by ``metadata.called_by`` only — tools live off the
``caller`` edge and are NOT on that chain, so they're missing from
the history dict the assembler gets.

This module fills the gap: given the conv-chain history and the
session id, it loads the matching tool rows from the DB and attaches
them to each assistant's ``tool_calls`` field. The result is a
single linear list where each assistant carries its own tool calls
inline — ready for the aging / assembly stages downstream.
"""
from __future__ import annotations

import json
from typing import Any


def _load_tool_rows_for_session(session_id: str) -> dict[str, list[dict]]:
    """Group every tool row of ``session_id`` by caller (assistant id).

    One DB read per assembly call — cheap because the rows are tiny
    and SQLite is in-process.
    """
    from openprogram.agent.session_db import default_db
    db = default_db()
    by_caller: dict[str, list[dict]] = {}
    try:
        all_msgs = db.get_messages(session_id) or []
    except Exception:
        return by_caller
    for m in all_msgs:
        if m.get("role") != "tool":
            continue
        caller = m.get("caller") or m.get("called_by")
        if not caller:
            continue
        by_caller.setdefault(caller, []).append(m)
    # Sort each group chronologically so the model sees them in
    # the order the assistant emitted them.
    for v in by_caller.values():
        v.sort(key=lambda r: r.get("timestamp") or r.get("created_at") or 0)
    return by_caller


def _tool_row_to_call(row: dict) -> dict:
    """Normalize a DB tool row into the
    ``{tool, input, result, tool_call_id, is_error}`` shape the
    aging + assembly stages expect.

    Handles both shapes we see in the wild:
      * tool_use blob inside ``extra`` JSON (legacy + dispatcher path)
      * top-level ``function`` + ``content`` (older rows)
    """
    extra = row.get("extra")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except (TypeError, ValueError):
            extra = {}
    if not isinstance(extra, dict):
        extra = {}
    tu = extra.get("tool_use") or {}
    name = (
        tu.get("name")
        or row.get("function")
        or row.get("name")
        or ""
    )
    args = tu.get("arguments") or tu.get("input") or row.get("input") or {}
    result = row.get("content")
    if result is None:
        result = row.get("output") or ""
    return {
        "tool": name,
        "input": args,
        "result": result,
        "tool_call_id": row.get("id"),
        "is_error": bool(row.get("is_error")),
    }


def enrich_with_tools(
    history: list[dict], session_id: str,
) -> list[dict]:
    """Mutate-then-return: attach tool_calls to each assistant in history.

    For every assistant message with no existing ``tool_calls``, we
    look up the caller-children from the DB. If the assistant already
    has ``tool_calls`` (e.g. dispatcher's step 5 inline blob), we
    leave it alone — that path already has the canonical list.
    """
    by_caller = _load_tool_rows_for_session(session_id)
    for m in history:
        if m.get("role") != "assistant":
            continue
        if m.get("tool_calls"):
            continue
        mid = m.get("id")
        if not mid or mid not in by_caller:
            continue
        m["tool_calls"] = [_tool_row_to_call(r) for r in by_caller[mid]]
    return history
