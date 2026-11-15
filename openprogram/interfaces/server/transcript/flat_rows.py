"""The branch under the head as plain ``{id, role, content}`` rows.

Clients that only need the conversation's text read this instead of turns:
the terminal CLI, ``worker_turn`` recovering a reply by id, the self-update
rehearsal and the HTTP command route. Rows come from the same projection the
web draws, so every reader agrees on what the conversation says.
"""
from __future__ import annotations

import json
from typing import Any

from .turn_index import TranscriptView
from .turns import TurnReader

# The HTTP command route refuses replies over 4 MiB; its rows stay well under.
HTTP_ROW_BYTES = 1024 * 1024


def _text(row: dict) -> str:
    content = row.get("content")
    if isinstance(content, str) and content:
        return content
    from openprogram.state.reply_text import reply_text
    return reply_text(block for block in row.get("blocks") or () if isinstance(block, dict))


def _flat(row: dict) -> dict:
    return {"id": row.get("id"), "role": row.get("role"), "content": _text(row)}


def flat_rows(db: Any, view: TranscriptView, *, byte_limit: int | None = None) -> list[dict]:
    """The view's rows in order; with ``byte_limit``, only the newest rows that fit."""
    with view.lock:
        entries = list(reversed(view.turns))
        reader = TurnReader(db, view, entries)
        newest_first: list[dict] = []
        size = 0
        for entry in entries:
            for row in reversed(reader.project(entry)["rows"]):
                flat = _flat(row)
                if byte_limit is not None:
                    size += len(json.dumps(flat, ensure_ascii=False, default=str).encode("utf-8"))
                    if size > byte_limit:
                        return newest_first[::-1]
                newest_first.append(flat)
    return newest_first[::-1]
