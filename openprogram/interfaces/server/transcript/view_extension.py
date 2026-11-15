"""Carry a branch's turn index over the rows appended since it was built.

A read during a running turn finds a few new rows on a branch whose earlier
turns cannot have changed. Only the last turn is worked out again, by the same
steps a full build runs, over that turn's rows and the new ones. Anything that
could reach further back is left to a full build.
"""
from __future__ import annotations

from typing import Any

from .legacy import resolve_folded_head
from .navigation import SINGLE
from .turn_index import (
    TranscriptView,
    TurnEntry,
    _fold_parent,
    card_rows,
    positions_of,
    splice_attach_pointers,
    splice_runtime_placeholders,
    turn_entries,
)


def _appended_range(view: TranscriptView, state: tuple) -> tuple[int, int] | None:
    """Which stored positions are new, when appending is all that happened."""
    before = view.fingerprint
    if not before or state[0] is not before[0] or state[4] != before[4] or not state[3]:
        return None
    added = state[1] - before[1]
    if added <= 0 or added != state[2] - before[2]:
        return None  # a removal, or seqs handed out of order
    return before[1], state[1]


def _needs_full_build(row: dict) -> bool:
    """Rows that change more of the view than the turn they join."""
    is_top_level_run = row.get("role") in ("tool", "code") and not _fold_parent(row)
    return bool(
        is_top_level_run or row.get("execution_control") or row.get("covers_ids") is not None
        or row.get("kind") in ("compaction", "snip", "event")
    )


def _parents(row: dict) -> set[str]:
    edges = {row.get("predecessor"), row.get("caller")}
    if row.get("role") == "tool":
        edges.add(_fold_parent(row))
    return {edge for edge in edges if edge and edge != "ROOT"}


def _rows_joining_last_turn(view: TranscriptView, appended: list[dict], tail: TurnEntry) -> dict[str, dict] | None:
    """The appended rows that belong to the last turn or after it, by id.

    A row with no link to this branch changes nothing here, except the leaf a
    version arrow leads to, or where a sub-agent branch's way back points.
    """
    known = {*tail.row_ids, *tail.raw_ids}
    joining: dict[str, dict] = {}
    unrelated = False
    for row in appended:
        if _needs_full_build(row):
            return None
        parents = _parents(row)
        if not parents:
            return None  # another version of the first turn, or a new root
        inside = {parent for parent in parents if parent in known or parent in joining}
        if inside == parents:
            joining[row["id"]] = row
        elif inside or any(parent in view.positions for parent in parents):
            return None  # it hangs from an earlier turn
        else:
            unrelated = True
    return None if unrelated and view.navigation else joining


def _branch_rows_after_head(view: TranscriptView, joining: dict[str, dict], head: str | None) -> list[str] | None:
    """The new branch rows, oldest first; None when ``head`` is not below the old head."""
    rows: list[str] = []
    current = head
    while current != view.head_id:
        row = joining.get(current)
        if row is None or len(rows) > len(joining):
            return None
        rows.append(current)
        current = row.get("predecessor")
    rows.reverse()
    return rows


def _is_placed_card(view: TranscriptView, row: dict) -> bool:
    """An attach row hanging from a row this branch draws: it joins that row's turn."""
    parent = row.get("predecessor")
    return row.get("function") == "attach" and row.get("id") not in view.positions and parent in view.positions and (
        parent in view.turns[view.positions[parent]].row_ids
    )


def _place_card(db: Any, view: TranscriptView, row: dict) -> None:
    """Add an attach row to its parent's turn, after the side rows it already has."""
    parent, row_id = row["predecessor"], row["id"]
    at = view.positions[parent]
    entry = view.turns[at]
    place = entry.row_ids.index(parent) + 1
    following = {sibling["id"]: sibling for sibling in db.get_messages_by_id(view.session_id, entry.row_ids[place:])}
    while place < len(entry.row_ids):
        sibling = following.get(entry.row_ids[place]) or {}
        is_side_row = sibling.get("function") == "attach" or (sibling.get("type") == "status" and sibling.get("display") == "runtime")
        if sibling.get("predecessor") != parent or not is_side_row:
            break
        place += 1
    view.turns[at] = TurnEntry(entry.id, (*entry.row_ids[:place], row_id, *entry.row_ids[place:]), (*entry.raw_ids, row_id))
    view.positions[row_id] = at


def _adds_version(db: Any, session_id: str, row: dict) -> bool:
    """Whether a new row is another version of something already there."""
    from openprogram.runtime.context.git import sibling_navigation_index

    if row.get("role") in ("tool", "code"):
        return False
    parent = row.get("predecessor") or row.get("caller")
    children = db.transcript_children(session_id, parent)
    return sibling_navigation_index(children, target_ids={row["id"]}).get(row["id"], SINGLE) != SINGLE


def extend_view(db: Any, view: TranscriptView, state: tuple) -> bool:
    """Bring ``view`` up to ``state`` in place. False means a full build is needed."""
    from openprogram.interfaces.server.persistence import aggregate_tool_messages
    from openprogram.interfaces.server.ws_actions.session.transcript import _rebuild_runtime_cards
    from openprogram.programs.tools.planning.goal.presentation import verification_executions

    span = _appended_range(view, state)
    tail = view.turns[-1] if view.turns else None
    if (
        span is None or tail is None or view.spawned_branch is not None or view.head_id not in tail.row_ids
        or any(row_id in view.synthetic for row_id in tail.row_ids)
    ):
        return False
    session_id = view.session_id
    appended = db.transcript_appended(session_id, *span)
    cards = [row for row in appended if _is_placed_card(view, row)]
    joining = _rows_joining_last_turn(view, [row for row in appended if row not in cards], tail)
    if joining is None or any(_adds_version(db, session_id, row) for row in joining.values()):
        return False
    with view.lock:
        for card in cards:
            _place_card(db, view, card)
    tail = view.turns[-1]
    if not joining and state[3] == view.head_id:
        with view.lock:
            view.fingerprint = state
        return True
    raw_rows = [*db.get_messages_by_id(session_id, tail.raw_ids), *joining.values()]
    all_rows = aggregate_tool_messages([dict(row) for row in raw_rows])
    head = resolve_folded_head(state[3], raw_rows, all_rows)
    new_on_branch = _branch_rows_after_head(view, joining, head)
    if new_on_branch is None or any(joining[row_id].get("source") == "agent_spawn" for row_id in new_on_branch):
        return False
    by_id = {row.get("id"): row for row in all_rows}
    chain = [by_id[row_id] for row_id in (*tail.row_ids, *new_on_branch) if row_id in by_id]
    chain = splice_attach_pointers(chain, all_rows)
    chain = splice_runtime_placeholders(chain, all_rows)
    chain = _rebuild_runtime_cards(chain, all_rows, session_id, with_trees=False)
    tail_cards = card_rows(chain, {row.get("id"): row for row in raw_rows})
    entries = turn_entries([dict(row) for row in chain], raw_rows, tail_cards)
    if not entries or entries[0].id != tail.id or not (
        set(tail.row_ids) <= set(entries[0].row_ids) and set(tail.raw_ids) <= set(entries[0].raw_ids)
    ):
        return False
    replies = {row_id for row_id, row in joining.items() if row.get("role") == "assistant"}
    verifiers = verification_executions(session_id, replies) if replies else {}
    with view.lock:
        first = len(view.turns) - 1
        view.turns[first:] = entries
        view.positions.update(positions_of(entries, first))
        view.goal_executions.update(verifiers)
        view.card_ids = (view.card_ids - set(tail.row_ids)) | tail_cards
        view.head_id = head
        view.fingerprint = state
    return True
