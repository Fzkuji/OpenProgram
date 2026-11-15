"""Sibling navigation: which version of a turn a row is, and where its neighbours lead."""
from __future__ import annotations

SINGLE = (1, 1, None, None)


def _fork_point(row: dict) -> str | None:
    """A run's fork point: its predecessor, else its caller; ROOT means none."""
    parent = row.get("predecessor") or row.get("caller") or None
    return None if parent == "ROOT" else parent


def sibling_table(chain: list[dict], all_rows: list[dict], by_id: dict) -> dict[str, tuple]:
    """``{row id: (index, total, previous leaf, next leaf)}`` for every chain row.

    A function card counts only the complete runs that are true alternatives of
    it: the other top-level entries sharing its fork point. Any other row uses
    the store's chat sibling rule.
    """
    from openprogram.interfaces.server.ws_actions.session.transcript import (
        _is_top_function_run,
        _ordered_fn_run_siblings,
    )
    from openprogram.runtime.context.git import deepest_leaf, sibling_navigation_index

    run_ids: set = set()
    runs_by_fork: dict = {}
    for position, row in enumerate(all_rows):
        if isinstance(row, dict) and row.get("role") in ("tool", "code") and _is_top_function_run(row, by_id):
            run_ids.add(row.get("id"))
            runs_by_fork.setdefault(_fork_point(row), []).append((row, position))
    chat = sibling_navigation_index(all_rows, target_ids={row.get("id") for row in chain if row.get("id")})
    table: dict[str, tuple] = {}
    for row in chain:
        row_id = row.get("id")
        if row.get("display") == "runtime" and row_id in run_ids:
            ids = _ordered_fn_run_siblings(by_id, runs_by_fork, row_id, _fork_point)
            at = ids.index(row_id) if row_id in ids else -1
            table[row_id] = (
                at + 1 if at >= 0 else 0, len(ids),
                deepest_leaf(all_rows, ids[at - 1]) if at > 0 else None,
                deepest_leaf(all_rows, ids[at + 1]) if 0 <= at < len(ids) - 1 else None,
            )
        else:
            table[row_id] = chat.get(row_id, (0, 0, None, None))
    return table


def with_navigation(row: dict, fields: tuple) -> dict:
    index, total, previous, following = fields
    return {**row, "sibling_index": index, "sibling_total": total,
            "prev_sibling_id": previous, "next_sibling_id": following}


def with_attach_stats(row: dict, session_id: str) -> dict:
    """An attach row with its embed counts, so its card needs no second read."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.session.graph.branches import _attach_embed_stats, _attach_info

    if row.get("function") != "attach":
        return row
    _reference, _manual, source = _attach_info(row)
    if not source:
        return row
    count, tokens = _attach_embed_stats(default_db(), session_id, source)
    attach = dict(row.get("attach") or {})
    attach.setdefault("source_commit_id", source)
    if count is not None:
        attach["embed_count"] = count
    if tokens is not None:
        attach["embed_tokens"] = tokens
    return {**row, "attach": attach}
