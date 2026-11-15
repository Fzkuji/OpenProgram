"""Which rows make up which turn of a branch; the view holds ids, never bodies."""
from __future__ import annotations

import itertools
import threading
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .legacy import branch_row_ids, parsed_extra, resolve_folded_head

MAX_VIEWS = 16
SUB_AGENT_KEY = "#sub-agent:"

_revisions = itertools.count(1)
_registry_lock = threading.Lock()
_views: OrderedDict[str, "TranscriptView"] = OrderedDict()
_build_locks: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
# Views a socket holds, by key and how many hold them; these are never evicted.
_pinned: dict[str, int] = {}


def next_revision() -> int:
    return next(_revisions)


def sub_agent_view_key(session_id: str, agent_key: str) -> str:
    """The view of one sub-agent's turns, keyed by its first job or branch head."""
    return f"{session_id}{SUB_AGENT_KEY}{agent_key}"


def branch_tip(messages: list[dict], head_id: str) -> str:
    """Follow a sub-agent branch to its newest turn.

    Later messages to the same agent continue its branch from its last
    reply, so the branch tip moves past the head its spawn recorded.
    """
    turns: dict[str, list[dict]] = {}
    for message in messages:
        parent = message.get("predecessor")
        if (
            parent
            and message.get("role") in ("user", "assistant")
            and message.get("display") != "runtime"
            and message.get("function") != "attach"
        ):
            turns.setdefault(parent, []).append(message)
    tip, seen = head_id, set()
    while tip in turns and tip not in seen:
        seen.add(tip)
        tip = max(
            turns[tip],
            key=lambda message: message.get("created_at") or message.get("timestamp") or 0,
        )["id"]
    return tip


def _is_call_row(row: dict, by_id: dict) -> bool:
    """A row of a call tree: a tool or code node, or the leaf reply under one.

    A caller edge alone proves nothing: a spawned branch's root and an attach
    pointer carry one too, and neither is part of a call tree.
    """
    role = row.get("role")
    if role in ("tool", "code"):
        return True
    parent = by_id.get(row.get("caller"))
    return role == "assistant" and parent is not None and parent.get("role") in ("tool", "code")


def _hangs_from(row: dict, by_id: dict) -> str | None:
    """The id of the row a side row hangs from, or None when ``row`` is no
    side row. An empty id means a side row whose place is its display position."""
    if _is_call_row(row, by_id):
        return row.get("caller") or ""
    # A runtime placeholder is spliced in after the row its predecessor names.
    # A rebuilt function run has the same type and display but no predecessor,
    # and it heads its own turn.
    if (
        row.get("function") == "attach"
        or (row.get("type") == "status" and row.get("display") == "runtime" and row.get("predecessor"))
        or row.get("covers_ids") is not None
        or row.get("kind") in ("compaction", "snip", "event")
    ):
        return row.get("predecessor") or row.get("caller") or ""
    return None


def _opens_turn(row: dict, prompts: set, members: set) -> bool:
    """Whether a row that hangs from nothing starts a new turn.

    ``prompts`` holds the user rows of the current turn and ``members`` all of
    its rows. Steers and restart prompts stay in the turn they sit in, so the
    reply answering one, and with it a reply a restart cut off and the reply
    continuing it, stays there too.
    """
    role = row.get("role")
    if role == "user":
        return not row.get("steering") and row.get("trigger") != "restart"
    if role == "assistant" and row.get("display") != "runtime":
        return row.get("predecessor") not in prompts
    if role == "system":
        return row.get("predecessor") not in members
    return True  # a top-level function run, or a shape drawn as a plain row


def turn_groups(messages: list[dict]) -> list[tuple[str, list[dict]]]:
    """Cut display-ordered rows into turns: ``[(turn id, rows)]``.

    A turn is a prompt and everything it owns, so a page edge cannot separate
    a reply from its cards, steers or continuation. Rows keep their display
    order inside a turn, which holds a compaction row at its exact position.
    Rows ahead of the first opener join that turn and never become its id.
    """
    by_id = {row.get("id"): row for row in messages if row.get("id")}
    opener_ids: list[str] = []
    placed: list[int] = []  # turn number at each row's display position
    turn_of: dict[str, int] = {}
    prompts: set = set()
    members: set = set()
    for index, row in enumerate(messages):
        row_id = row.get("id")
        if _hangs_from(row, by_id) is None:
            if _opens_turn(row, prompts, members):
                opener_ids.append(str(row_id or f"hist:{index}"))
                prompts, members = set(), set()
            if row_id:
                members.add(row_id)
                turn_of[row_id] = max(len(opener_ids) - 1, 0)
                if row.get("role") == "user":
                    prompts.add(row_id)
        placed.append(len(opener_ids) - 1)
    if not messages:
        return []
    if not opener_ids:
        first = messages[0].get("id")
        opener_ids.append(str(first or "hist:0"))
    turns: list[list[dict]] = [[] for _ in opener_ids]
    for row, position in zip(messages, placed):
        parent = _hangs_from(row, by_id)
        turn = max(position, 0) if parent is None else turn_of.get(parent, max(position, 0))
        if parent is not None and row.get("id"):
            turn_of[row["id"]] = turn
        turns[turn].append(row)
    return list(zip(opener_ids, turns))


def visible_rows(rows: list[dict]) -> list[dict]:
    """The rows the transcript shows: no execution-control row, nor anything under one."""
    hidden = {row.get("id") for row in rows if row.get("execution_control") and row.get("id")}
    changed = bool(hidden)
    while changed:
        before = len(hidden)
        hidden.update(row.get("id") for row in rows if row.get("caller") in hidden)
        changed = len(hidden) != before
    return [row for row in rows if row.get("id") not in hidden] if hidden else rows


def splice_attach_pointers(chain: list[dict], all_rows: list[dict]) -> list[dict]:
    """Attach pointer rows right after the chain row they hang off."""
    chain_ids = {row.get("id") for row in chain}
    by_parent: dict[str, list[dict]] = {}
    for row in all_rows:
        # Older writes set both edges, which put a pointer on the chain too.
        if row.get("function") == "attach" and row.get("id") not in chain_ids:
            parent = row.get("predecessor") or ""
            if parent and parent in chain_ids:
                by_parent.setdefault(parent, []).append(row)
    return _spliced(chain, by_parent)


def splice_runtime_placeholders(chain: list[dict], all_rows: list[dict]) -> list[dict]:
    """Runtime placeholder rows right after the reply that called them."""
    chain_ids = {row.get("id") for row in chain}
    by_parent: dict[str, list[dict]] = {}
    for row in all_rows:
        if row.get("type") == "status" and row.get("display") == "runtime" and row.get("id") not in chain_ids:
            parent = row.get("predecessor") or ""
            if parent and parent in chain_ids:
                by_parent.setdefault(parent, []).append(row)
    return _spliced(chain, by_parent)


def _sub_agent_start(chain: list[dict], first_reply: str) -> int:
    """Where a sub-agent's own rows start on its chain.

    An agent started with ``inherit`` or from a node has the caller's rows
    above its first prompt. What hangs off those rows, its own step row and
    its siblings', belongs to the conversation that wrote them.
    """
    at = next((index for index, row in enumerate(chain) if row.get("id") == first_reply), None)
    if at is None:
        return len(chain)
    while at > 0 and (chain[at].get("role") != "user" or chain[at].get("display") == "runtime"):
        at -= 1
    return at


def _spliced(chain: list[dict], by_parent: dict[str, list[dict]]) -> list[dict]:
    if not by_parent:
        return chain
    out: list[dict] = []
    for row in chain:
        out.append(row)
        extras = by_parent.get(row.get("id"), [])
        extras.sort(key=lambda item: item.get("timestamp") or 0)
        out.extend(extras)
    return out


def _fold_parent(row: dict) -> str | None:
    """The row a stored tool row folds into; old rows name it inside ``extra``."""
    caller = row.get("caller")
    if not caller:
        caller = ((parsed_extra(row) or {}).get("tool_use") or {}).get("caller")
    return caller if caller and caller != "ROOT" else None


def _needs_graph(rows: list[dict]) -> bool:
    return any(row.get("covers_ids") or row.get("source") == "agent_spawn" or row.get("function") == "attach" for row in rows)


@dataclass(frozen=True, slots=True)
class TurnEntry:
    id: str
    row_ids: tuple[str, ...]  # drawn rows in display order, compaction rows included
    raw_ids: tuple[str, ...]  # stored rows to read, in store order


@dataclass(slots=True)
class TranscriptView:
    """One branch's turn index. Bodies are read fresh from the store."""

    session_id: str
    fingerprint: tuple
    revision: int
    head_id: str | None
    turns: list[TurnEntry]
    positions: dict[str, int]
    navigation: dict[str, tuple]
    synthetic: dict[str, dict]
    covered_by: dict[str, str]
    card_ids: frozenset[str]
    goal_executions: dict[str, str]
    spawned_from: dict[str, dict]
    spawned_branch: dict | None
    lock: threading.RLock = field(default_factory=threading.RLock)
    # For a sub-agent view: the head its tip is followed from; None on a main view.
    tip_from: str | None = None


def _raw_ids_by_turn(groups: list[tuple[str, list[dict]]], raw_rows: list[dict], card_ids: set[str]) -> list[tuple[str, ...]]:
    """For each turn, its drawn rows plus every stored row folded under them."""
    order = {row.get("id"): at for at, row in enumerate(raw_rows)}
    kids: dict[str, list[dict]] = {}
    for row in raw_rows:
        parent = _fold_parent(row)
        if parent:
            kids.setdefault(parent, []).append(row)
    drawn = {row.get("id") for _, rows in groups for row in rows}
    result: list[tuple[str, ...]] = []
    for _, rows in groups:
        found: dict[str, None] = {}
        stack: list[tuple[dict, dict | None]] = []
        for row in rows:
            row_id = row.get("id")
            if row_id in order:
                found[row_id] = None
                if row_id not in card_ids:
                    stack.extend((kid, row) for kid in kids.get(row_id, []))
        while stack:
            row, parent = stack.pop()
            row_id = row.get("id")
            is_tool = row.get("role") == "tool"
            is_leaf = row.get("role") == "assistant" and parent is not None and parent.get("role") == "tool"
            if row_id in found or row_id in drawn or not (is_tool or is_leaf):
                continue
            found[row_id] = None
            stack.extend((kid, row) for kid in kids.get(row_id, []))
        result.append(tuple(sorted(found, key=order.__getitem__)))
    return result


def card_rows(chain: list[dict], raw_by_id: dict) -> set[str]:
    """Drawn rows that are a function run's card, stored as its code node."""
    return {
        row["id"] for row in chain
        if row.get("display") == "runtime" and row.get("role") == "assistant"
        and (raw_by_id.get(row.get("id")) or {}).get("role") == "tool"
    }


def turn_entries(display: list[dict], raw_rows: list[dict], card_ids: set[str]) -> list[TurnEntry]:
    groups = turn_groups(display)
    raw_ids = _raw_ids_by_turn(groups, raw_rows, card_ids)
    return [
        TurnEntry(turn_id, tuple(row["id"] for row in rows if row.get("id")), stored)
        for (turn_id, rows), stored in zip(groups, raw_ids)
    ]


def positions_of(entries: list[TurnEntry], first: int) -> dict[str, int]:
    """Where each id of ``entries`` sits, the first entry being turn ``first``."""
    positions: dict[str, int] = {}
    for at, entry in enumerate(entries, first):
        positions[entry.id] = at
        positions.update((row_id, at) for row_id in entry.row_ids)
        positions.update((row_id, at) for row_id in entry.raw_ids)
    return positions


def build_view(
    db: Any, session_id: str, state: tuple | None, conv_head: str | None, tip_from: str | None = None,
) -> TranscriptView:
    """Index a branch from the store. Runs on a worker thread.

    With ``tip_from`` the branch is a sub-agent's: its own prompts and replies
    up to the newest turn below that head. It has no version arrows and no
    place in the session graph, because the view never moves HEAD.
    """
    from openprogram.interfaces.server.persistence import aggregate_tool_messages
    from openprogram.interfaces.server.ws_actions.session.transcript import (
        _annotate_spawn_origin,
        _rebuild_runtime_cards,
        _spawned_branch,
        splice_compaction_event_rows,
    )
    from openprogram.programs.tools.planning.goal.presentation import verification_executions
    from openprogram.runtime.context.git import active_branch_chain, head_or_tip
    from openprogram.state.store.session.graph.builder import build_session_graph

    from .navigation import SINGLE, sibling_table

    revision = next_revision()
    raw_rows = visible_rows(db.get_messages(session_id) or [])
    goal_executions = verification_executions(session_id, {row.get("id") for row in raw_rows})
    # Aggregation folds into the row dicts it is given; these copies are ours.
    all_rows = aggregate_tool_messages([dict(row) for row in raw_rows])
    if tip_from is None:
        head = state[3] if state and state[3] else head_or_tip({"head_id": conv_head}, all_rows)
        head = resolve_folded_head(head, raw_rows, all_rows)
        chain = active_branch_chain(all_rows, branch_row_ids(db, session_id, head), head)
    else:
        goal_executions = {}
        known = any(row.get("id") == tip_from for row in all_rows)
        head = branch_tip(all_rows, tip_from) if known else tip_from
        chain = [
            row for row in active_branch_chain(all_rows, branch_row_ids(db, session_id, head), head)
            if row.get("role") in ("user", "assistant") and row.get("function") != "attach"
        ] if known else []
    own_from = 0 if tip_from is None else _sub_agent_start(chain, tip_from)
    own = splice_runtime_placeholders(splice_attach_pointers(chain[own_from:], all_rows), all_rows)
    chain = chain[:own_from] + own
    chain = _rebuild_runtime_cards(chain, all_rows, session_id, with_trees=False)
    by_id = {row.get("id"): row for row in all_rows if isinstance(row, dict)}
    raw_by_id = {row.get("id"): row for row in raw_rows}
    card_ids = card_rows(chain, raw_by_id)
    navigation = {} if tip_from is not None else {
        row_id: fields for row_id, fields in sibling_table(chain, all_rows, by_id).items() if fields != SINGLE
    }
    graph: list[dict] = []
    if tip_from is None and _needs_graph(raw_rows):
        graph = build_session_graph(session_id, head, messages=raw_rows, include_layout=False)
    _annotate_spawn_origin(graph, raw_rows)
    spawned_from = {node["id"]: node["spawned_from"] for node in graph if node.get("spawned_from")}
    display = [dict(row) for row in chain]
    display = splice_compaction_event_rows(display, graph, all_rows)
    synthetic = {row["id"]: row for row in display if row.get("id") and row["id"] not in by_id}
    covered_by = {
        covered: row["id"]
        for row in display if row.get("kind") == "compaction" and row.get("slot") == "card"
        for covered in row.get("covers_ids") or []
    }
    turns = turn_entries(display, raw_rows, card_ids)
    positions = positions_of(turns, 0)
    return TranscriptView(
        session_id=session_id, fingerprint=state or (), revision=revision, head_id=head, turns=turns,
        positions=positions, navigation=navigation, synthetic=synthetic, covered_by=covered_by,
        card_ids=frozenset(card_ids), goal_executions=goal_executions, spawned_from=spawned_from,
        spawned_branch=None if tip_from is not None else _spawned_branch(head, raw_rows), tip_from=tip_from,
    )


def get_view(db: Any, session_id: str, conv_head: str | None = None) -> TranscriptView:
    """The session's view, extended when only new rows arrived and rebuilt otherwise."""
    from .view_extension import extend_view

    state = db.transcript_state(session_id)
    with _registry_lock:
        view = _views.get(session_id)
        if view is not None and state is not None and view.fingerprint == state:
            _views.move_to_end(session_id)
            return view
        build_lock = _build_locks.get(session_id)
        if build_lock is None:
            build_lock = threading.Lock()
            _build_locks[session_id] = build_lock
    with build_lock:
        state = db.transcript_state(session_id)
        with _registry_lock:
            view = _views.get(session_id)
            if view is not None and state is not None and view.fingerprint == state:
                return view
        if view is not None and state is not None and extend_view(db, view, state):
            with _registry_lock:
                if _views.get(session_id) is view:
                    _views.move_to_end(session_id)
            return view
        previous_view = view
        view = build_view(db, session_id, state, conv_head)
        if previous_view is not None and not previous_view.turns:
            # An empty chat's first rows change no position anyone holds.
            view.revision = previous_view.revision
        if state is not None:
            with _registry_lock:
                _views[session_id] = view
                _views.move_to_end(session_id)
                _evict_views()
        return view


def get_sub_agent_view(db: Any, session_id: str, agent_key: str, head_id: str) -> TranscriptView:
    """One sub-agent's branch under its own view key; a read never moves HEAD."""
    key = sub_agent_view_key(session_id, agent_key)
    state = db.transcript_state(session_id)
    with _registry_lock:
        view = _views.get(key)
        if view is not None and state is not None and view.fingerprint == state and view.tip_from == head_id:
            _views.move_to_end(key)
            return view
    view = build_view(db, session_id, state, None, tip_from=head_id)
    if state is not None:
        with _registry_lock:
            _views[key] = view
            _views.move_to_end(key)
            _evict_views()
    return view


def _evict_views() -> None:
    """Keep the views no socket holds within the cap. Takes the registry lock's owner."""
    for key in list(_views):
        if len(_views) <= MAX_VIEWS:
            return
        if not _pinned.get(key):
            del _views[key]


def pin_view(key: str) -> None:
    with _registry_lock:
        _pinned[key] = _pinned.get(key, 0) + 1


def unpin_view(key: str) -> None:
    with _registry_lock:
        left = _pinned.get(key, 0) - 1
        if left > 0:
            _pinned[key] = left
        else:
            _pinned.pop(key, None)


def cached_view(key: str) -> TranscriptView | None:
    """The view under ``key`` as it stands, with no store access."""
    with _registry_lock:
        return _views.get(key)


def forget_views() -> None:
    """Drop every view; tests that swap the store use this."""
    with _registry_lock:
        _views.clear()
        _pinned.clear()
