"""The run slots of every conversation branch, and the operations in them.

One table, ``server._run_slots``, keyed by ``(session_id, branch_key)``:
each branch of a conversation has at most one foreground operation at a
time. The selected branch's slot has ``branch_key == SELECTED`` while the
selection is one per conversation; every other recorded branch is keyed by
its ``branch_id``. ``server._running_tasks`` (session -> selected slot) and
``server._branch_running_tasks`` ((session, branch) -> slot) are views of
that table, so a reader of either sees the same entries.

A slot entry is the operation: ``op_id``, ``op_kind`` (``reply``,
``compact``, ``retry``, ``edit``, ``rewind``, ``branch``, ``function``),
``op_version`` (raised by a stop) and the turn's execution ids once it has
them. Branches never block each other. The one exception is an operation
whose ``op_scope`` is ``conversation``: a rewind that changes files on
disk. It is refused while any other branch runs or waits on an approval,
and while it runs no branch is admitted.

A request that carries a key (``op_key``) and finds its own key on the
slot is a repeat of the operation holding it, not a second operation. A
rewind, revert or reapply repeated with its idempotency key joins: it
waits for the holder to end, then goes through the store, which returns
the holder's recorded outcome. A send or function run repeated with its
``client_request_id`` is answered ``request_admitted``: one send admits
one execution. Anything else is refused as before.
"""
from __future__ import annotations

import contextlib

import threading
import time
import uuid
from collections.abc import MutableMapping
from typing import Any, Iterator

from openprogram.state.store.session.operations import OPERATION_KINDS, REPLY_KINDS

SELECTED = ""

#: Operations that run in this process without an execution; each ends
#: through its own ``finally``, never as an abandoned reservation, and its
#: release sends a clear that names it.
EXECUTIONLESS_KINDS = frozenset({"compact", "rewind", "branch"})

#: Executionless operations every client is told about while they run.
#: A branch operation (a branch delete; a switch takes no slot) ends in a moment and is
#: not shown; its refusal still names it.
ANNOUNCED_KINDS = frozenset({"compact", "rewind"})

_KIND_TEXT = {
    "reply": ("a reply is being written", "正在写回复"),
    "compact": ("context is being compacted", "正在压缩上下文"),
    "retry": ("a retried turn is running", "重试的轮次正在运行"),
    "edit": ("an edited turn is running", "编辑后的轮次正在运行"),
    "rewind": ("a rewind is in progress", "正在回退"),
    "branch": ("a branch operation is in progress", "正在进行分支操作"),
    "function": ("a function is running", "函数正在运行"),
}

_cancel_events: dict[str, threading.Event] = {}
_cancel_events_lock = threading.Lock()


class SelectedSlots(MutableMapping):
    """``session_id`` -> the selected branch's slot, a view of the one table."""

    def __init__(self, table: dict) -> None:
        self._table = table

    def __getitem__(self, session_id: str) -> dict:
        return self._table[(session_id, SELECTED)]

    def __setitem__(self, session_id: str, value: dict) -> None:
        self._table[(session_id, SELECTED)] = value

    def __delitem__(self, session_id: str) -> None:
        del self._table[(session_id, SELECTED)]

    def __iter__(self) -> Iterator[str]:
        return iter([sid for (sid, branch) in list(self._table) if branch == SELECTED])

    def __len__(self) -> int:
        return sum(1 for (_sid, branch) in list(self._table) if branch == SELECTED)


class BranchSlots(MutableMapping):
    """``(session_id, branch_id)`` -> the slot of a branch that is not selected."""

    def __init__(self, table: dict) -> None:
        self._table = table

    @staticmethod
    def _check(key) -> tuple[str, str]:
        if not (isinstance(key, tuple) and len(key) == 2 and key[1] != SELECTED):
            raise KeyError(key)
        return key

    def __getitem__(self, key) -> dict:
        return self._table[self._check(key)]

    def __setitem__(self, key, value: dict) -> None:
        self._table[self._check(key)] = value

    def __delitem__(self, key) -> None:
        del self._table[self._check(key)]

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter([key for key in list(self._table) if key[1] != SELECTED])

    def __len__(self) -> int:
        return sum(1 for key in list(self._table) if key[1] != SELECTED)


def operation_fields(kind: str = "reply", *, scope: str = "branch") -> dict[str, Any]:
    """The operation half of a new slot entry."""
    if kind not in OPERATION_KINDS:
        raise ValueError(f"unknown operation kind {kind!r}")
    op_id = "op_" + uuid.uuid4().hex
    with _cancel_events_lock:
        _cancel_events[op_id] = threading.Event()
    return {"op_id": op_id, "op_kind": kind, "op_version": 1, "op_scope": scope}


def cancel_event(op_id: str | None) -> threading.Event | None:
    """The event a Stop of operation ``op_id`` sets; the operation polls it."""
    if not op_id:
        return None
    with _cancel_events_lock:
        return _cancel_events.get(op_id)


def forget_operation(session_id: str, task: dict | None) -> None:
    """An operation's slot was released: drop its stop and its durable record."""
    op_id = (task or {}).get("op_id")
    if not op_id:
        return
    with _cancel_events_lock:
        _cancel_events.pop(op_id, None)
    if (task or {}).get("_recorded"):
        with contextlib.suppress(Exception):  # noqa: BLE001 - a stale record is swept at the next start
            from openprogram.state.store.session.operations import finish_operation
            finish_operation(session_id, op_id)


def holds_its_slot(task: dict) -> bool:
    """An executionless operation ends through its own ``finally``: its
    reservation is never taken for an abandoned one, however long it runs."""
    return task.get("op_kind") in EXECUTIONLESS_KINDS


def is_reply(task: dict | None) -> bool:
    return (task or {}).get("op_kind", "reply") in REPLY_KINDS


def kind_text(kind: str | None, *, zh: bool = False) -> str:
    english, chinese = _KIND_TEXT.get(kind or "reply", _KIND_TEXT["reply"])
    return chinese if zh else english


def busy_refusal(kind: str | None, *, branch_id: str | None = None,
                 op_id: str | None = None, other_branch: bool = False) -> dict[str, Any]:
    """The answer to an operation refused because a slot is taken; it names the kind."""
    kind = kind or "reply"
    where = "in this conversation" if other_branch else "on this branch"
    refusal: dict[str, Any] = {
        "code": "run_active",
        "kind": kind,
        "error": f"{kind_text(kind)[0].upper()}{kind_text(kind)[1:]} {where}; try again after it ends.",
    }
    if branch_id:
        refusal["branch_id"] = branch_id
    if op_id:
        refusal["op_id"] = op_id
    return refusal


def locked_refusal(waited: float | None = None, holder: str | None = None) -> dict[str, Any]:
    """The answer when the conversation's lock was not had in time.

    Nothing holds the slot: the conversation's files were being written
    (a long first load, a move, another process) past the store's wait.
    The request was not started and can be sent again; the client is
    not told a turn is running, because none may be."""
    refusal: dict[str, Any] = {
        "code": "conversation_locked",
        "kind": None,
        "retryable": True,
        "error": "This conversation's history is still being written; nothing was started. "
                 "Try again in a moment.",
    }
    if waited is not None:
        refusal["waited_seconds"] = round(waited, 1)
    if holder:
        refusal["holder"] = holder
    return refusal


def branch_running_refusal(blockers: list[dict]) -> dict[str, Any]:
    """A rewind that changes files, refused while another branch runs or waits.

    ``blocking`` carries what a client needs to stop that branch (its
    execution id and version) and then rewind again."""
    first = blockers[0]
    name = first.get("branch_name")
    waiting = first.get("reason_code") in {"wait_open", "system_access_required"}
    if first.get("selected"):
        label = "A turn on this branch"
        doing = "is waiting for an approval" if waiting else "is paused"
    else:
        label = f"Branch “{name}”" if name else "Another branch"
        doing = "is waiting for an approval" if waiting else "is running"
    return {
        "code": "branch_running",
        "kind": first.get("op_kind") or "reply",
        "error": f"{label} {doing}. Rewinding files now could undo its work; stop it, then rewind.",
        "blocking": first,
        "blocking_all": blockers,
    }


def repeat_refusal(task: dict) -> dict[str, Any]:
    """The answer to a send repeated while the send itself holds the slot.

    One send admits one execution: the repeat is not run, and it is not
    ``run_active`` either, which a client takes as "queue it and send again"."""
    refusal: dict[str, Any] = {
        "code": "request_admitted",
        "kind": task.get("op_kind") or "reply",
        "error": "This request is already running; it is not started twice.",
        "msg_id": task.get("msg_id"),
    }
    for key in ("op_id", "execution_id", "branch_id"):
        if task.get(key):
            refusal[key] = task[key]
    return refusal


def slot_with_key(session_id: str, key: str | None) -> dict | None:
    """The slot of this conversation held by the request keyed ``key``;
    caller holds the lock."""
    if not key:
        return None
    return next((task for _key, task in slot_items(session_id)
                 if task.get("op_key") == key), None)


def slot_items(session_id: str) -> list[tuple[str, dict]]:
    """Every slot of a conversation as ``(branch_key, entry)``; caller holds the lock."""
    from .. import server as state

    items: list[tuple[str, dict]] = []
    selected = state._running_tasks.get(session_id)
    if selected is not None:
        items.append((SELECTED, selected))
    items.extend((key[1], task) for key, task in list(state._branch_running_tasks.items())
                 if key[0] == session_id)
    return items


def conversation_wide_operation(session_id: str) -> dict | None:
    """The conversation-wide operation running in this process, if any; caller holds the lock."""
    return next((task for _key, task in slot_items(session_id)
                 if task.get("op_scope") == "conversation"), None)


def new_slot(owner: str, kind: str, *, scope: str = "branch",
             key: str | None = None) -> dict[str, Any]:
    """A slot entry; ``key`` is the request's own key (a send's
    ``client_request_id``, a rewind's or revert's idempotency key), so a
    repeat of the same request is recognised as that request."""
    now = time.time()
    return {
        "op_key": key or None,
        "msg_id": owner, "func_name": "_chat",
        "started_at": now, "last_event_at": now, "display_params": "",
        "loaded_func_ref": None, "stream_events": [],
        # Admission mints the canonical execution id. A provisional
        # reservation must not publish or derive one from msg_id.
        "execution_id": None, "_reserved": True,
        **operation_fields(kind, scope=scope),
    }
