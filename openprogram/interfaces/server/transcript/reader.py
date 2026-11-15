"""Transcript reads: which turns a request gets, projected fresh from the store."""
from __future__ import annotations

import json
from typing import Any, Iterable

from .schema import Turn, Window
from .turn_index import TranscriptView, TurnEntry, _is_call_row
from .turns import TurnReader

SNIPPET_CONTEXT = 40
PAGE_TURNS = 25
PAGE_BYTES = 512 * 1024
INITIAL_PAGE_TURNS = 6
INITIAL_PAGE_BYTES = 128 * 1024
SEARCH_LIMIT = 1000


class CursorNotOnBranch(ValueError):
    """The row a read was anchored on is not on this branch."""


def _bytes(turn: Turn) -> int:
    return len(json.dumps(turn, ensure_ascii=False, default=str).encode("utf-8"))


def _window(view: TranscriptView, turns: list[Turn], start: int) -> Window:
    end = start + len(turns)
    total = len(view.turns)
    return {
        "head_id": view.head_id, "revision": view.revision,
        "before": turns[0]["id"] if turns and start > 0 else None,
        "after": turns[-1]["id"] if turns and end < total else None,
        "start": start, "end": end, "total": total,
    }


def _take(db: Any, view: TranscriptView, order: Iterable[int], byte_limit: int, empty_at: int) -> tuple[list[Turn], Window]:
    """Project turns in ``order`` until the byte limit; one whole turn always fits."""
    order = list(order)
    reader = TurnReader(db, view, [view.turns[at] for at in order])
    chosen: dict[int, Turn] = {}
    size = 0
    for at in order:
        turn = reader.project(view.turns[at])
        cost = _bytes(turn)
        if chosen and size + cost > byte_limit:
            break
        chosen[at] = turn
        size += cost
    positions = sorted(chosen)
    turns = [chosen[at] for at in positions]
    return turns, _window(view, turns, positions[0] if positions else empty_at)


def first_read(db: Any, view: TranscriptView) -> tuple[list[Turn], Window]:
    """The newest turns of a branch, as a first open gets them."""
    with view.lock:
        total = len(view.turns)
        order = range(total - 1, max(total - INITIAL_PAGE_TURNS, 0) - 1, -1)
        return _take(db, view, order, INITIAL_PAGE_BYTES, total)


def _position(view: TranscriptView, cursor: str) -> int:
    position = view.positions.get(cursor)
    if position is None:
        raise CursorNotOnBranch(cursor)
    return position


def _around(position: int, total: int, limit: int) -> list[int]:
    """The anchor, then its neighbours outward, within one page's reach."""
    start = max(0, position - limit // 2)
    stop = min(total, start + limit)
    order = [position]
    for distance in range(1, limit):
        order.extend(at for at in (position - distance, position + distance) if start <= at < stop)
    return order


def page(
    db: Any, view: TranscriptView, *, before: str | None = None, after: str | None = None, around: str | None = None,
) -> tuple[list[Turn], Window]:
    """A page beside any row of the branch, or the newest page with no cursor."""
    limit = PAGE_TURNS
    with view.lock:
        total = len(view.turns)
        if after is not None:
            position = _position(view, after)
            order = range(position + 1, min(position + 1 + limit, total))
            return _take(db, view, order, PAGE_BYTES, position + 1)
        if around is not None:
            order = _around(_position(view, around), total, limit)
            return _take(db, view, order, PAGE_BYTES, 0)
        stop = total if before is None else _position(view, before)
        order = range(stop - 1, max(stop - limit, 0) - 1, -1)
        return _take(db, view, order, PAGE_BYTES, 0)


def one_turn(db: Any, view: TranscriptView, row_id: str) -> tuple[list[Turn], Window]:
    """The one turn holding a row."""
    with view.lock:
        position = _position(view, row_id)
        return _take(db, view, [position], PAGE_BYTES, position)


def whole_read(db: Any, view: TranscriptView) -> tuple[list[Turn], Window]:
    """Every turn of the branch, unpaged."""
    with view.lock:
        reader = TurnReader(db, view, view.turns)
        turns = [reader.project(entry) for entry in view.turns]
        return turns, _window(view, turns, 0)


def _snippet(text: str, start: int, length: int, context: int = SNIPPET_CONTEXT) -> str:
    """One line of text around a match. Case folding can shift offsets, so
    the window is approximate on scripts whose folded form changes length."""
    left = max(0, start - context)
    right = min(len(text), start + length + context)
    snippet = " ".join(text[left:right].split())
    return ("…" if left else "") + snippet + ("…" if right < len(text) else "")


def _turn_matches(rows: list[dict], entry: TurnEntry, needle: str) -> list[dict]:
    by_id = {row.get("id"): row for row in rows}
    matches: list[dict] = []
    for row_id in entry.row_ids:
        row = by_id.get(row_id)
        if row is None or row.get("role") not in ("user", "assistant") or _is_call_row(row, by_id):
            continue
        if row.get("display") == "runtime" or row.get("type") == "status" or not isinstance(row.get("content"), str):
            continue
        folded = row["content"].casefold()
        count = folded.count(needle)
        if count:
            matches.append({
                "id": row_id, "message_id": row_id, "role": row.get("role"), "count": count,
                "snippet": _snippet(row["content"], folded.find(needle), len(needle)),
            })
    return matches


def search(db: Any, view: TranscriptView, query: str, limit: int | None = None) -> list[dict]:
    """Find ``query`` in the text the chat shows, in order.

    Only user and assistant rows are searched; tool rows and execution children
    nested under them are not part of the readable conversation. ``id`` is the
    matching row's id, which a page read accepts as ``around``.
    """
    needle = query.casefold()
    if not needle:
        return []
    limit = SEARCH_LIMIT if limit is None else limit
    matches: list[dict] = []
    with view.lock:
        for entry in view.turns:
            rows = db.get_messages_by_id(view.session_id, entry.raw_ids)
            matches.extend(_turn_matches(rows, entry, needle))
            if len(matches) >= limit:
                return matches[:limit]
    return matches
