"""Store writes as numbered transcript changes for the views sockets hold.

The session log tells ``notice`` which rows a write touched; that only queues.
One thread drains everything queued, works out what each held view must learn,
and sends it, reading rows fresh from the store under the view's lock.

Every row change goes through here: open replies are sent as block items and
field patches, finished rows whole, and structure changes and head moves as
turn and window changes.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from openprogram.state.store.session import session_log

from . import turn_index
from .navigation import SINGLE
from .rows import project_rows
from .status import is_open_status
from .turn_index import SUB_AGENT_KEY, TranscriptView, _fold_parent, get_sub_agent_view, get_view, next_revision
from .turns import TurnReader

_log = logging.getLogger(__name__)

TICK_SECONDS = 0.05
FOREIGN_REBUILD_SECONDS = 2.0
MAX_UPDATE_BYTES = 512 * 1024
MAX_SOCKET_VIEWS = 16
_HEAD_WAIT_SECONDS = 2.0
_HEAD_WALK_LIMIT = 4096
_STRUCTURE_KEYS = {"rewound", "structural"}
_DERIVED_FIELDS = ("order", "steeringReplyId", "continuedBy", "continues")
# A version arrow's fields, in the order ``TranscriptView.navigation`` keeps them.
_NAVIGATION_FIELDS = ("siblingIndex", "siblingTotal", "prevSiblingId", "nextSiblingId")
_ITEM_LISTS = ("attachCards", "runtimeChildren", "callRoots")
# Text arrives by text frames; lists arrive as items; the rest is a field patch.
_NOT_PATCHED = {"id", "content", "thinking", "blocks", *_ITEM_LISTS, *_DERIVED_FIELDS}
_TEXT_BLOCKS = {"text", "thinking"}
_TREE_STATUS = {"running": "running", "completed": "done", "error": "error"}


@dataclass
class _Change:
    put: bool = False
    deleted: bool = False
    keys: set[str] = field(default_factory=set)


@dataclass
class _Dirty:
    """What was written to one session since its last tick."""

    rows: dict[str, _Change] = field(default_factory=dict)
    head_moved: bool = False
    foreign: bool = False
    urgent: bool = False
    merged: bool = False
    trees: dict[tuple[str, str], tuple[dict, str | None]] = field(default_factory=dict)


@dataclass
class _Sent:
    """What one view's holders were last sent, so only a difference goes out."""

    blocks: dict[str, dict[int, int]] = field(default_factory=dict)
    fields: dict[str, dict[str, int]] = field(default_factory=dict)
    items: dict[str, dict[str, int]] = field(default_factory=dict)
    derived: dict[str, tuple] = field(default_factory=dict)
    order: dict[str, list[str]] = field(default_factory=dict)
    navigation: dict[str, tuple] | None = None


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _crc(value: Any) -> int:
    return zlib.crc32(_dumps(value).encode("utf-8"))


def _is_single_insertion(before: list[str], after: list[str]) -> bool:
    """``after`` is ``before`` with rows added, nothing moved."""
    if len(after) < len(before):
        return False
    remaining = iter(after)
    return all(row_id in remaining for row_id in before)


def _stopping(session_id: str, row: dict) -> bool:
    """A Stop was asked of the execution this row is anchored to."""
    from openprogram.runtime.conversation.run_control.turns import cancelled_execution_ids
    from openprogram.runtime.execution import default_store

    stopped = cancelled_execution_ids(session_id)
    if not stopped:
        return False
    store = default_store()
    anchors = (store.get_execution_input(execution_id) for execution_id in stopped)
    return any(row["id"] in (anchor.user_message_id, anchor.assistant_message_id) for anchor in anchors if anchor)


def _without_default_expose(tree: dict) -> dict:
    """A tree as a read shapes it: ``expose`` appears only when it is not the default."""
    item = {key: value for key, value in tree.items() if key != "expose" or value != "full"}
    if item.get("children"):
        item["children"] = [_without_default_expose(child) for child in item["children"]]
    return item


def _item_key(name: str, item: dict) -> str:
    return f"{name}:{item.get('id') or item.get('path') or item.get('name')}"


def _view_session(view_key: str) -> str:
    return view_key.split(SUB_AGENT_KEY, 1)[0]


class TranscriptPublisher:
    def __init__(self, send: Callable[[Any, str], bool]) -> None:
        self._send = send
        # A Queue, not a SimpleQueue: its count of unfinished items is what
        # ``idle`` reads.
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.RLock()
        self._holders: dict[str, set[Any]] = {}
        self._sessions: dict[str, set[str]] = {}
        self._dirty: dict[str, _Dirty] = {}
        self._last_tick: dict[str, float] = {}
        self._last_foreign: dict[str, float] = {}
        self._waiting_for_head: dict[str, dict[str, float]] = {}
        self._waiting_for_reply: dict[str, set[str]] = {}
        self._loose_steers: dict[str, set[str]] = {}
        self._sent: dict[str, _Sent] = {}
        self._unowned_trees: set[tuple[str, str]] = set()
        self._busy = False
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    # The observer's side: only queues.

    def notice(self, session_id: str, record_type: str, node_ids: tuple, keys: frozenset) -> None:
        self._queue.put((session_id, record_type, node_ids, keys))

    def notice_foreign(self, session_id: str) -> None:
        self._queue.put((session_id, "foreign", (), frozenset()))

    def notice_merged(self, session_id: str) -> None:
        self._queue.put((session_id, "merged", (), frozenset()))

    def notice_tree(self, session_id: str, msg_id: str, tree: dict, function: str | None) -> None:
        self._queue.put((session_id, "tree", (msg_id, tree, function), frozenset()))

    # Holders

    def hold(self, ws: Any, session_id: str, view: str | None = None) -> None:
        """Join ``ws`` to a view. Runs on the event loop, before the read captures."""
        key = view or session_id
        closed: str | None = None
        with self._lock:
            held = getattr(ws, "_transcript_views", None)
            if held is None:
                held = OrderedDict()
                ws._transcript_views = held
            if key not in held:
                turn_index.pin_view(key)
                self._holders.setdefault(key, set()).add(ws)
                self._sessions.setdefault(session_id, set()).add(key)
            held[key] = session_id
            held.move_to_end(key)
            if len(held) > MAX_SOCKET_VIEWS:
                closed = next(iter(held))
                self._release_locked(ws, closed)
        if closed is not None:
            self._send(ws, _dumps({"type": "view_closed", "data": {"session_id": _view_session(closed), "view": closed}}))

    def read_view(self, view_key: str, view: TranscriptView) -> None:
        """Note the version arrows a holder is about to read.

        Arrow patches are counted from the oldest read no step has answered,
        so a later read that already shows a moved arrow does not hide the
        move from a holder that read before it. A view's arrows never change
        in place, so these are the ones the read's rows carry.
        """
        with self._lock:
            if view_key not in self._holders:
                return
            sent = self._sent.setdefault(view_key, _Sent())
            if sent.navigation is None:
                sent.navigation = dict(view.navigation)

    def close_view(self, ws: Any, view: str) -> None:
        with self._lock:
            self._release_locked(ws, view)

    def release(self, ws: Any) -> None:
        """A socket stopped: everything it held is free."""
        with self._lock:
            for view in list(getattr(ws, "_transcript_views", None) or ()):
                self._release_locked(ws, view)

    def _release_locked(self, ws: Any, view: str) -> None:
        held = getattr(ws, "_transcript_views", None)
        if not held or view not in held:
            return
        session_id = held.pop(view)
        turn_index.unpin_view(view)
        holders = self._holders.get(view)
        if holders is not None:
            holders.discard(ws)
            if not holders:
                del self._holders[view]
                self._waiting_for_reply.pop(view, None)
                self._loose_steers.pop(view, None)
                self._sent.pop(view, None)
                views = self._sessions.get(session_id)
                if views is not None:
                    views.discard(view)
                    if not views:
                        del self._sessions[session_id]

    def _view_keys(self, session_id: str) -> list[str]:
        with self._lock:
            return list(self._sessions.get(session_id, ()))

    def _deliver(self, view_key: str, payload: str) -> None:
        with self._lock:
            sockets = list(self._holders.get(view_key, ()))
        for ws in sockets:
            self._send(ws, payload)

    # The thread

    def start(self) -> None:
        session_log.set_record_observer(self.notice)
        session_log.set_foreign_change_observer(self.notice_foreign)
        self._thread = threading.Thread(target=self._run, name="openprogram-transcript-publisher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        session_log.set_record_observer(None)
        session_log.set_foreign_change_observer(None)
        self._stopping.set()
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(5)

    def _run(self) -> None:
        while not self._stopping.is_set():
            self._wait_and_drain()
            for session_id in self._due_sessions():
                # Busy before the pop, so ``idle`` never sees neither.
                self._busy = True
                dirty = self._dirty.pop(session_id)
                self._last_tick[session_id] = time.monotonic()
                try:
                    self._publish_session(session_id, dirty)
                except Exception:  # noqa: BLE001  # one bad session must not stop the rest
                    _log.exception("transcript update for session %s failed", session_id)
                    self._send_gap(session_id)
                finally:
                    self._busy = False

    def _wait_and_drain(self) -> None:
        timeout = self._seconds_to_next_due()
        try:
            self._take(self._queue.get(timeout=timeout))
        except queue.Empty:
            return
        while True:
            try:
                self._take(self._queue.get_nowait())
            except queue.Empty:
                return

    def _take(self, item: tuple | None) -> None:
        try:
            if item is not None:
                self._mark_dirty(item)
        finally:
            self._queue.task_done()

    def _mark_dirty(self, item: tuple) -> None:
        session_id, record_type, node_ids, keys = item
        if session_id not in self._sessions:
            return
        dirty = self._dirty.setdefault(session_id, _Dirty())
        if record_type == "foreign":
            dirty.foreign = True
            return
        if record_type == "merged":
            dirty.merged = dirty.urgent = True
            return
        if record_type == "steer_tick":
            return  # only makes the session due again
        if record_type == "tree":
            msg_id, tree, function = node_ids
            dirty.trees[(msg_id, str(tree.get("path") or function or tree.get("name")))] = (tree, function)
            dirty.urgent = dirty.urgent or tree.get("status") != "running"
            return
        if record_type == "meta.patch":
            if "head_id" in keys:
                dirty.head_moved = dirty.urgent = True
            return
        for node_id in node_ids:
            change = dirty.rows.setdefault(node_id, _Change())
            change.put = change.put or record_type == "node.put"
            change.deleted = change.deleted or record_type == "node.delete"
            change.keys.update(keys)
        if "status" in keys:
            dirty.urgent = True

    def _next_tick_at(self, session_id: str, dirty: _Dirty) -> float:
        if dirty.urgent:
            return 0.0
        at = self._last_tick.get(session_id, 0.0) + TICK_SECONDS
        if dirty.foreign:
            at = max(at, self._last_foreign.get(session_id, 0.0) + FOREIGN_REBUILD_SECONDS)
        return at

    def _seconds_to_next_due(self) -> float | None:
        if not self._dirty:
            return None
        now = time.monotonic()
        return max(min(self._next_tick_at(sid, dirty) for sid, dirty in self._dirty.items()) - now, 0.0)

    def _due_sessions(self) -> list[str]:
        now = time.monotonic()
        return [sid for sid, dirty in self._dirty.items() if now >= self._next_tick_at(sid, dirty)]

    @property
    def idle(self) -> bool:
        """Nothing queued, nothing due and nothing being sent. Exact: a
        notice counts as unfinished from its put until it is in ``_dirty``
        (an item the worker has dequeued is in neither the queue nor
        ``_dirty``), and a tick is busy from before it takes its session."""
        return not self._queue.unfinished_tasks and not self._dirty and not self._busy

    # What a tick sends

    def _publish_session(self, session_id: str, dirty: _Dirty) -> None:
        from openprogram.runtime.context.session_db import default_db

        db = default_db()
        if dirty.foreign:
            self._take_foreign_rows(db, session_id, dirty)
        branches_changed = False
        for view_key in self._view_keys(session_id):
            with db.reading_cached_index():
                branches_changed = self._publish_view(db, session_id, view_key, dirty) or branches_changed
        if branches_changed:
            self._deliver(session_id, _dumps({"type": "branches_changed", "data": {"session_id": session_id}}))

    def _take_foreign_rows(self, db: Any, session_id: str, dirty: _Dirty) -> None:
        """Rows another process appended count as new ids, at most once per rebuild window."""
        self._last_foreign[session_id] = time.monotonic()
        view = turn_index.cached_view(session_id)
        before = view.fingerprint[1] if view is not None and view.fingerprint else 0
        state = db.transcript_state(session_id)  # this read may rebuild the index from disk
        if state is None or state[1] <= before:
            return
        for row in db.transcript_appended(session_id, before, state[1]):
            dirty.rows.setdefault(row["id"], _Change(put=True))

    def _publish_view(self, db: Any, session_id: str, view_key: str, dirty: _Dirty) -> bool:
        """Send what one view must learn. True when the branch list changed."""
        view = turn_index.cached_view(view_key)
        if view is None:
            return False
        with self._lock:
            sent = self._sent.setdefault(view_key, _Sent())
            if sent.navigation is None:
                # No holder has read yet; its read can only be newer than this.
                sent.navigation = dict(view.navigation)
        rows = {row["id"]: row for row in db.transcript_rows(session_id, list(dirty.rows))}
        branches = dirty.head_moved or any(change.deleted for change in dirty.rows.values()) or any(
            row.get("function") == "attach" for row in rows.values())
        stale = self._stale_reason(db, session_id, view, dirty, rows)
        if stale is not None:
            head = (db.get_session(session_id) or {}).get("head_id")
            sent.navigation = None  # the holders read again; the first of those reads is noted
            self._send_changes(view, view_key, [{"kind": "window_stale", "head_id": head, "reason": stale}])
            return branches
        targets = self._targets(db, session_id, view, view_key, dirty, rows)
        if targets or dirty.trees:
            view = self._current_view(db, session_id, view_key, view)
            with view.lock:
                changes = self._changes(db, view, view_key, targets, rows) if targets else []
                changes += self._tree_changes(db, session_id, view, view_key, dirty.trees)
                changes += self._navigation_changes(sent, view)
                self._send_changes(view, view_key, changes)
        return branches

    def _stale_reason(self, db: Any, session_id: str, view: TranscriptView, dirty: _Dirty, rows: dict[str, dict]) -> str | None:
        if dirty.merged and view.tip_from is None:
            return "branch"  # the head is usually a descendant, and its peers are consumed
        last_turn = {*view.turns[-1].row_ids, *view.turns[-1].raw_ids} if view.turns else set()
        for row_id, change in dirty.rows.items():
            if change.deleted:
                return "structure"
            row = rows.get(row_id)
            # A summary's rows are drawn from it, on its insert and on every patch.
            if change.keys & _STRUCTURE_KEYS or (row is not None and row.get("covers_ids") is not None):
                return "structure"
            if "predecessor" in change.keys and not change.put and row_id in view.positions and row_id not in last_turn:
                return "structure"
        if dirty.head_moved and view.tip_from is None and not self._head_is_descendant(db, session_id, view):
            return "branch"
        return None

    def _head_is_descendant(self, db: Any, session_id: str, view: TranscriptView) -> bool:
        head = (db.get_session(session_id) or {}).get("head_id")
        if head == view.head_id:
            return True
        if view.head_id is None or not view.turns:
            return True  # an empty chat: whatever the head becomes is new
        current, steps = head, 0
        while current and steps < _HEAD_WALK_LIMIT:
            if current == view.head_id:
                return True
            found = db.get_messages_by_id(session_id, [current])
            if not found:
                return False
            current, steps = found[0].get("predecessor") or found[0].get("caller"), steps + 1
        return False

    def _targets(
        self, db: Any, session_id: str, view: TranscriptView, view_key: str, dirty: _Dirty, rows: dict[str, dict],
    ) -> dict[str, _Change]:
        """The rows this step sends, by id. A new row waits for its head patch."""
        waiting = self._waiting_for_head.setdefault(view_key, {})
        now = time.monotonic()
        for row_id in [row_id for row_id, deadline in waiting.items() if deadline < now]:
            del waiting[row_id]
        held_over = self._waiting_for_reply.pop(view_key, set())
        targets = {
            row_id: change for row_id, change in dirty.rows.items()
            if (row := rows.get(row_id)) is not None and not row.get("execution_control")
            and self._publishes(row, change, view)
            and not self._held_back(session_id, view_key, row, change, rows, held_over)
        }
        if dirty.head_moved and waiting:
            for row in db.transcript_rows(session_id, list(waiting)):
                rows[row["id"]] = row
                targets.setdefault(row["id"], _Change(put=True))
            waiting.clear()
        loose = self._loose_steers.get(view_key, ())
        for row in db.transcript_rows(session_id, [*held_over, *loose]):
            rows[row["id"]] = row
            change = targets.get(row["id"], _Change())
            targets[row["id"]] = replace(change, put=change.put or bool(row.get("steering")))
        return targets

    def _held_back(
        self, session_id: str, view_key: str, row: dict, change: _Change, rows: dict[str, dict], held_over: set[str],
    ) -> bool:
        """Stop rule and the steer's wait for its reparent: this tick sends neither."""
        if is_open_status(row.get("status")) and _stopping(session_id, row):
            return True  # the terminal row covers it
        if not (change.put and row.get("steering")) or row["id"] in held_over:
            return False
        if any(other.get("predecessor") == row["id"] for other in rows.values()):
            return False  # its reply was reparented in this same drain
        self._waiting_for_reply.setdefault(view_key, set()).add(row["id"])
        self._queue.put((session_id, "steer_tick", (), frozenset()))
        self._dirty.setdefault(session_id, _Dirty())
        return True

    def _publishes(self, row: dict, change: _Change, view: TranscriptView) -> bool:
        if view.tip_from is None:
            return True
        return row["id"] in view.positions or self._extends_sub_agent(row, change, view) or row.get("function") == "attach"

    def _extends_sub_agent(self, row: dict, change: _Change, view: TranscriptView) -> bool:
        """A new row at a sub-agent view's tip, under the same filter as its reads."""
        return bool(
            change.put and row.get("predecessor") == view.head_id and row.get("role") in ("user", "assistant")
            and row.get("display") != "runtime" and row.get("function") != "attach"
        )

    def _current_view(self, db: Any, session_id: str, view_key: str, view: TranscriptView) -> TranscriptView:
        """The index brought up to the store, the number kept: this step tells holders what changed."""
        if view.tip_from is None:
            rebuilt = get_view(db, session_id, view.head_id)
        else:
            agent_key = view_key.split(SUB_AGENT_KEY, 1)[1]
            rebuilt = get_sub_agent_view(db, session_id, agent_key, view.tip_from)
        if rebuilt is not view:
            rebuilt.revision = view.revision
        return rebuilt

    def _changes(
        self, db: Any, view: TranscriptView, view_key: str, targets: dict[str, _Change], rows: dict[str, dict],
    ) -> list[dict]:
        """Project each touched turn once and say what changed in it."""
        waiting = self._waiting_for_head.setdefault(view_key, {})
        wanted: dict[int, list[str]] = {}
        loose = self._loose_steers.setdefault(view_key, set())
        changes: list[dict] = []
        for row_id, change in targets.items():
            owner = self._owner_row(rows[row_id])
            position = view.positions.get(owner)
            if position is not None:
                loose.discard(row_id)
                wanted.setdefault(position, []).append(owner)
            elif change.put and rows[row_id].get("steering") and rows[row_id].get("predecessor") in view.positions:
                if row_id not in loose:
                    loose.add(row_id)
                    changes.append(self._steer_as_it_stands(view, rows[row_id]))
            elif change.put and owner == row_id:
                waiting[row_id] = time.monotonic() + _HEAD_WAIT_SECONDS
        for position in sorted(wanted):
            changes += self._turn_changes(db, view, view_key, position, wanted[position], rows, targets)
        return changes

    @staticmethod
    def _steer_as_it_stands(view: TranscriptView, row: dict) -> dict:
        """A steer whose reparent has not come, drawn after the row it hangs from."""
        anchor = row["predecessor"]
        return {
            "kind": "row_upsert", "turnId": view.turns[view.positions[anchor]].id, "afterRowId": anchor,
            "row": project_rows([row], session_id=view.session_id)[0],
        }

    def _owner_row(self, row: dict) -> str:
        """The row whose drawing changes: a late tool row's reply, otherwise the row."""
        if row.get("role") in ("tool", "code"):
            return _fold_parent(row) or row.get("predecessor") or row["id"]
        return row["id"]

    def _turn_changes(
        self, db: Any, view: TranscriptView, view_key: str, position: int, owners: list[str],
        rows: dict[str, dict], targets: dict[str, _Change],
    ) -> list[dict]:
        entry = view.turns[position]
        turn = TurnReader(db, view, [entry]).project(entry)
        if entry.id in owners and targets.get(entry.id, _Change()).put:
            previous = view.turns[position - 1].id if position > 0 else None
            self._record_turn(self._sent.setdefault(view_key, _Sent()), turn)
            return [{"kind": "turn_upsert", "turn": turn, "afterTurnId": previous}]
        return self._live_turn_changes(view_key, entry.id, position, view, turn, owners, rows, targets)

    def _live_turn_changes(
        self, view_key: str, turn_id: str, position: int, view: TranscriptView, turn: dict, owners: list[str],
        rows: dict[str, dict], targets: dict[str, _Change],
    ) -> list[dict]:
        """One projected turn as changes: open rows piecewise, finished rows whole."""
        sent = self._sent.setdefault(view_key, _Sent())
        order = [row["id"] for row in turn["rows"]]
        previous_order = sent.order.get(turn_id)
        sent.order[turn_id] = order
        if previous_order is not None and not _is_single_insertion(previous_order, order):
            after = view.turns[position - 1].id if position > 0 else None
            self._record_turn(sent, turn)
            return [{"kind": "turn_upsert", "turn": turn, "afterTurnId": after}]
        by_id = {row["id"]: (at, row) for at, row in enumerate(turn["rows"])}
        cards = {card["id"]: row["id"] for row in turn["rows"] for card in row.get("attachCards") or []}
        out: list[dict] = []
        whole: set[str] = set()
        for owner in dict.fromkeys(owners):
            if owner in cards:
                card = next(c for c in by_id[cards[owner]][1]["attachCards"] if c["id"] == owner)
                out.append({"kind": "item_upsert", "turnId": turn_id, "rowId": cards[owner], "list": "attachCards", "item": card})
                continue
            if owner not in by_id:
                continue
            at, row = by_id[owner]
            if targets.get(owner, _Change()).put or not is_open_status(row.get("status")):
                whole.add(owner)
                out.append({
                    "kind": "row_upsert", "turnId": turn_id,
                    "afterRowId": turn["rows"][at - 1]["id"] if at else None, "row": row,
                })
                self._record_whole(sent, row)
                continue
            out += self._open_row_changes(sent, turn_id, row)
        out += self._derived_changes(sent, turn_id, turn["rows"], whole)
        return out

    @classmethod
    def _record_turn(cls, sent: _Sent, turn: dict) -> None:
        for row in turn["rows"]:
            cls._record_whole(sent, row)

    @staticmethod
    def _record_whole(sent: _Sent, row: dict) -> None:
        """A row went out whole: later differences of an open row start from it."""
        row_id = row["id"]
        for kept in (sent.blocks, sent.fields, sent.items):
            kept.pop(row_id, None)
        if not is_open_status(row.get("status")):
            return
        sent.blocks[row_id] = {
            index: _crc(block) for index, block in enumerate(row.get("blocks") or [])
            if block.get("type") not in _TEXT_BLOCKS
        }
        sent.items[row_id] = {_item_key(name, item): _crc(item) for name in _ITEM_LISTS for item in row.get(name) or []}
        sent.fields[row_id] = {name: _crc(value) for name, value in row.items() if name not in _NOT_PATCHED}

    @staticmethod
    def _open_row_changes(sent: _Sent, turn_id: str, row: dict) -> list[dict]:
        row_id = row["id"]
        out: list[dict] = []
        blocks = sent.blocks.setdefault(row_id, {})
        for index, block in enumerate(row.get("blocks") or []):
            if block.get("type") in _TEXT_BLOCKS or blocks.get(index) == (crc := _crc(block)):
                continue
            blocks[index] = crc
            out.append({"kind": "item_upsert", "turnId": turn_id, "rowId": row_id, "list": "blocks", "item": block, "index": index})
        items = sent.items.setdefault(row_id, {})
        for name in _ITEM_LISTS:
            for item in row.get(name) or []:
                key = _item_key(name, item)
                if items.get(key) != (crc := _crc(item)):
                    items[key] = crc
                    out.append({"kind": "item_upsert", "turnId": turn_id, "rowId": row_id, "list": name, "item": item})
        fields = sent.fields.setdefault(row_id, {})
        changed = {}
        for name, value in row.items():
            if name not in _NOT_PATCHED and fields.get(name) != (crc := _crc(value)):
                fields[name] = crc
                changed[name] = value
        for name in [name for name in fields if name not in row]:
            del fields[name]
            changed[name] = None  # a read leaves out an empty field: null takes it off the row
        if changed:
            out.append({"kind": "row_patch", "turnId": turn_id, "rowId": row_id, "fields": changed})
        return out

    @staticmethod
    def _derived_changes(sent: _Sent, turn_id: str, projected: list[dict], whole: set[str]) -> list[dict]:
        """A row joining a turn can change its neighbours' links."""
        out: list[dict] = []
        for row in projected:
            values = tuple(row.get(name) for name in _DERIVED_FIELDS)
            before = sent.derived.get(row["id"])
            sent.derived[row["id"]] = values
            if before is None or before == values or row["id"] in whole:
                continue
            fields = {name: value for name, old, value in zip(_DERIVED_FIELDS, before, values) if old != value}
            out.append({"kind": "row_patch", "turnId": turn_id, "rowId": row["id"], "fields": fields})
        return out

    @staticmethod
    def _navigation_changes(sent: _Sent, view: TranscriptView) -> list[dict]:
        """Version arrows that lead somewhere new.

        A row written on a branch this view does not draw (a turn running on
        another branch) becomes the leaf an arrow of a drawn row leads to.
        No drawn row changed, so the arrow is patched on its own.
        """
        before, sent.navigation = sent.navigation or {}, dict(view.navigation)
        out: list[dict] = []
        for row_id in {**before, **view.navigation}:
            old, new = before.get(row_id, SINGLE), view.navigation.get(row_id, SINGLE)
            position = view.positions.get(row_id)
            if old == new or position is None:
                continue
            fields = {name: value for name, was, value in zip(_NAVIGATION_FIELDS, old, new) if was != value}
            out.append({"kind": "row_patch", "turnId": view.turns[position].id, "rowId": row_id, "fields": fields})
        return out

    def _tree_changes(
        self, db: Any, session_id: str, view: TranscriptView, view_key: str,
        trees: dict[tuple[str, str], tuple[dict, str | None]],
    ) -> list[dict]:
        """A function run's live tree, on the row it belongs to."""
        out: list[dict] = []
        for (msg_id, _root), (tree, function) in trees.items():
            found = db.transcript_rows(session_id, [msg_id, f"{msg_id}_reply"])
            row = next((r for r in found if r["id"] == msg_id), None) or (found[0] if found else None)
            if row is None or (position := view.positions.get(self._tree_owner(row))) is None:
                if (session_id, msg_id) not in self._unowned_trees:
                    self._unowned_trees.add((session_id, msg_id))
                    self.notice_foreign(session_id)
                continue
            status = _TREE_STATUS.get(tree.get("status"))
            if status == "running" and (not is_open_status(row.get("status")) or _stopping(session_id, row)):
                continue  # a running tree never changes a row the view holds as ended
            entry = view.turns[position]
            out += self._tree_change(db, view, entry, row, tree, function, status)
        return out

    @staticmethod
    def _tree_owner(row: dict) -> str:
        return row.get("caller") or row.get("predecessor") or row["id"] if row.get("role") == "code" else row["id"]

    @staticmethod
    def _tree_change(
        db: Any, view: TranscriptView, entry: Any, row: dict, tree: dict, function: str | None, status: str | None,
    ) -> list[dict]:
        settled = {"status": status} if status else {}
        if row.get("role") == "assistant" and row.get("display") != "runtime":
            if not tree.get("children"):
                return []  # a flat call is already the reply's tool step; a read draws no tree for it
            item = _without_default_expose(tree if tree.get("name") else {**tree, "name": function})
            return [{"kind": "item_upsert", "turnId": entry.id, "rowId": row["id"], "list": "callRoots", "item": item}]
        fields = {"contextTree": tree, "function": function, **settled}
        for drawn in TurnReader(db, view, [entry]).project(entry)["rows"]:
            if any(child["id"] == row["id"] for child in drawn.get("runtimeChildren") or []):
                return [{
                    "kind": "item_upsert", "turnId": entry.id, "rowId": drawn["id"], "list": "runtimeChildren",
                    "item": {"id": row["id"], **fields},
                }]
        return [{"kind": "row_patch", "turnId": entry.id, "rowId": row["id"], "fields": fields}]

    # Sending

    def _send_changes(self, view: TranscriptView, view_key: str, changes: list[dict]) -> None:
        if not changes:
            return
        with view.lock:
            previous, view.revision = view.revision, next_revision()
            payload = _dumps({"type": "transcript_update", "data": {
                "session_id": view.session_id, "view": view_key, "previous": previous,
                "revision": view.revision, "changes": changes,
            }})
            if len(payload.encode("utf-8")) > MAX_UPDATE_BYTES:
                self._send_gap(view.session_id)
                return
            self._deliver(view_key, payload)

    def _send_gap(self, session_id: str) -> None:
        payload = _dumps({"type": "transcript_gap", "data": {"session_id": session_id}})
        for view_key in self._view_keys(session_id):
            self._deliver(view_key, payload)


_publisher: TranscriptPublisher | None = None


def current() -> TranscriptPublisher | None:
    return _publisher


def start(send: Callable[[Any, str], bool]) -> TranscriptPublisher:
    """Called once by the Server on load, and by tests with their own sockets."""
    global _publisher
    stop()
    _publisher = TranscriptPublisher(send)
    _publisher.start()
    return _publisher


def stop() -> None:
    global _publisher
    if _publisher is not None:
        _publisher.stop()
        _publisher = None


def hold(ws: Any, session_id: str, view: str | None = None) -> None:
    if _publisher is not None:
        _publisher.hold(ws, session_id, view)


def notice_merged(session_id: str) -> None:
    """A merge finished: every holder of the conversation reads once at latest."""
    if _publisher is not None:
        _publisher.notice_merged(session_id)


def notice_tree(session_id: str, msg_id: str, tree: dict, function: str | None) -> None:
    """A live tree from the Server's fan-out: the input for function-run cards."""
    if _publisher is not None:
        _publisher.notice_tree(session_id, msg_id, tree, function)


def read_view(view_key: str, view: TranscriptView) -> None:
    if _publisher is not None:
        _publisher.read_view(view_key, view)


def close_view(ws: Any, view: str) -> None:
    if _publisher is not None:
        _publisher.close_view(ws, view)


def release(ws: Any) -> None:
    if _publisher is not None:
        _publisher.release(ws)
