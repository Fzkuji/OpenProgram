"""What a rewind leaves behind: Undo and Redo marks and the note to the model.

A rewind that moves the conversation records an edge in the session meta.
Undo and Redo move HEAD along that edge by compare-and-set and touch messages
only; files stay as they are. The conversation's next turn carries one note
beside its message saying how the conversation and the files relate: it is
taken onto that turn's message when the message is stored
(``turn_runtime.prep``), so a turn that fails and is sent again still
carries it, and later requests render it from there.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

_log = logging.getLogger(__name__)

MAX_EDGES = 50

NOTES = {
    "branch_kept_files": (
        "The user selected another conversation version. All versions share the current "
        "workspace; files were not restored. Read current files before editing and do not "
        "repeat changes merely because they are missing from this conversation history."
    ),
    "conversation_only": (
        "The user rewound this conversation to an earlier message and left the "
        "files alone, so edits from the removed messages may still be on disk. "
        "Check the current files or git status before relying on them."
    ),
    "restored": (
        "The user rewound this conversation and the files you edited to an "
        "earlier message. Files you did not edit through your file tools were "
        "not changed."
    ),
    "partial": (
        "The user rewound this conversation to an earlier message, but some "
        "files could not be restored, so files may disagree with this "
        "conversation. Check them before relying on them."
    ),
    "code_only": (
        "The user put files back to an earlier point and kept this "
        "conversation, so edits described above may be gone. Do not redo them "
        "unless asked."
    ),
    "undo_kept_files": (
        "The user brought back messages from a rewind. The files were not "
        "brought back, so edits described in those messages may be gone. Do "
        "not redo them unless asked."
    ),
}


def forget_read_baselines(session_id: str) -> None:
    """What the model read before a rewind no longer proves what it knows."""
    from openprogram.state.store.snapshot import read_tracking

    read_tracking.forget_session(session_id)


# Notes that still warn the model about files on disk.
_FILE_WARNINGS = {"conversation_only", "partial"}


def _note_for_rewind(mode: str, plan: dict[str, Any], pending: dict[str, Any]) -> dict[str, Any]:
    """The note after this rewind, given the one no turn has seen yet.

    A new rewind replaces the Undo note. A rewind with nothing to say keeps
    an unsent warning, and a clean restore cannot retract one, because the
    files that warning is about are outside this rewind.
    """
    if mode == "conversation":
        kind = "conversation_only" if plan.get("has_changes") or plan.get("skipped") else None
    elif plan.get("skipped"):
        kind = "partial"
    else:
        kind = "restored" if plan.get("has_changes") else None
    earlier = pending.get("kind")
    if kind is None:
        return {} if earlier == "undo_kept_files" else dict(pending)
    if kind == "restored" and earlier in _FILE_WARNINGS:
        kind = "partial"
    return {"kind": kind}


def rewind_meta_updates(
    session: dict[str, Any],
    plan: dict[str, Any],
    *,
    mode: str,
    target_branch_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Meta written with the HEAD move and the values that undo that write."""
    edges = list(session.get("rewind_edges") or [])
    pending = session.get("pending_rewind_note") or {}
    if mode == "code":
        code_note = bool(session.get("pending_code_note"))
        return (
            {"pending_code_note": code_note or bool(plan.get("has_changes"))},
            {"pending_code_note": code_note},
        )
    rollback = {"rewind_edges": edges, "pending_rewind_note": pending}
    forward = {"pending_rewind_note": _note_for_rewind(mode, plan, pending)}
    restored = mode != "conversation" and bool(plan.get("has_changes"))
    forward["rewind_edges"] = (edges + [{
        "id": f"rewind_{uuid.uuid4().hex[:16]}",
        "target_msg_id": plan["target_msg_id"],
        "source_head_id": plan["source_head_id"],
        "target_head_id": plan["target_head_id"],
        "source_branch_id": plan["source_branch_id"],
        "target_branch_id": target_branch_id,
        "mode": mode,
        "files_restored": restored,
        "note_before": pending,
        "at": time.time(),
        "status": "rewound",
        "sent": False,
    }])[-MAX_EDGES:]
    return forward, rollback


def _on_active_branch(edge: dict, session: dict) -> bool:
    active = session.get("active_branch_id")
    owners = {edge.get("source_branch_id"), edge.get("target_branch_id")} - {None}
    return not active or not owners or active in owners


def take_rewind_note(session_id: str) -> str:
    """The note for the turn whose message is being stored now, or "".

    Marks unsent rewinds as sent: the note is on that message from here on.
    """
    try:
        from openprogram.state.store.session.session_store import default_store

        store = default_store()
        session = store.get_session(session_id) or {}
        note = session.get("pending_rewind_note") or {}
        code_note = bool(session.get("pending_code_note"))
        edges = session.get("rewind_edges") or []
        unsent = any(not edge.get("sent") and edge.get("status") == "rewound"
                     and _on_active_branch(edge, session) for edge in edges)
        if not note and not code_note and not unsent:
            return ""
        store.update_session(
            session_id,
            pending_rewind_note={},
            pending_code_note=False,
            rewind_edges=[
                {**edge, "sent": True} if edge.get("status") == "rewound" and _on_active_branch(edge, session) else edge
                for edge in edges
            ],
        )
        kinds = [note.get("earlier"), note.get("kind")]
        if code_note and note.get("kind") != "undo_kept_files":
            kinds.append("code_only")
        texts = [NOTES[str(kind)] for kind in kinds if str(kind) in NOTES]
        # The rewound history is gone from the next request.
        from openprogram.models.usage.prefix_fingerprint import expect_break
        expect_break(session_id, "rewind")
        return "\n\n".join(texts)
    except Exception:
        _log.warning("rewind note unavailable for session %s", session_id, exc_info=True)
        return ""


def _chain_ids(index) -> list[str]:
    from .rewind import _active_chain

    return [node.id for node in _active_chain(index)]


def _node_text(node) -> str:
    from .rewind import _node_text as text_of

    return text_of(node)


def list_rewind_marks(session_id: str) -> list[dict[str, Any]]:
    """Marks for the transcript at the current HEAD, oldest first.

    ``anchor_head_id`` is the message the mark follows; a ``pending`` mark is
    the transcript's last row.
    """
    from openprogram.state.store.session.session_store import default_store

    store = default_store()
    pair = store._open(session_id)
    if pair is None:
        return []
    _log_handle, index = pair
    chain = set(_chain_ids(index))
    head = index.head_id
    marks: list[dict[str, Any]] = []
    session = store.get_session(session_id) or {}
    for edge in session.get("rewind_edges") or []:
        if not _on_active_branch(edge, session):
            continue
        variant = None
        destination = None
        if edge.get("status") == "rewound" and edge.get("target_head_id") in chain:
            destination = edge.get("source_head_id")
            if edge.get("source_head_id") not in chain:
                if edge.get("sent"):
                    variant = "back"
                elif edge.get("target_head_id") == head:
                    variant = "pending"
        elif (
            edge.get("status") == "undone"
            and edge.get("source_head_id") in chain
            and edge.get("target_head_id") in chain
        ):
            destination = edge.get("undone_from_head_id") or edge.get("target_head_id")
            variant = "forward"
        if variant is None:
            continue
        available = destination in index.nodes_by_id
        marks.append({
            "edge_id": edge.get("id"),
            "variant": variant,
            "anchor_head_id": edge.get("target_head_id"),
            "target_msg_id": edge.get("target_msg_id"),
            "at": edge.get("at"),
            "mode": edge.get("mode"),
            "files_restored": bool(edge.get("files_restored")),
            "available": available,
            "reason": None if available else "gone",
        })
    return marks


def _refused(session_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "status": "refused", "session_id": session_id, "code": code,
        "error": message, "head_changed": False, "new_head_id": None,
    }


def undo_rewind(
    session_id: str,
    *,
    edge_id: str | None = None,
    expected_head_id: str | None = None,
) -> dict[str, Any]:
    """Bring back the messages a rewind left; files stay as they are."""
    return _switch(session_id, "rewound", edge_id, expected_head_id)


def redo_rewind(
    session_id: str,
    *,
    edge_id: str | None = None,
    expected_head_id: str | None = None,
) -> dict[str, Any]:
    """Go back to the rewound conversation; files stay as they are."""
    return _switch(session_id, "undone", edge_id, expected_head_id)


def _switch(
    session_id: str, status: str, edge_id: str | None, expected_head_id: str | None,
) -> dict[str, Any]:
    from openprogram.state.store.session.session_store import default_store

    store = default_store()
    pair = store._open(session_id)
    if pair is None:
        return _refused(session_id, "unknown_session", "This conversation no longer exists.")
    _log_handle, index = pair
    session = store.get_session(session_id) or {}
    if session.get("archived"):
        return _refused(session_id, "archived", "An archived conversation can't be rewound.")
    edges = [dict(edge) for edge in session.get("rewind_edges") or []]
    candidates = [
        edge for edge in edges
        if edge.get("status") == status and _on_active_branch(edge, session)
        and (not edge_id or edge.get("id") == edge_id)
    ]
    if not candidates:
        return _refused(session_id, "retired", "This rewind can no longer be switched.")
    edge = candidates[-1]
    head = index.head_id
    if expected_head_id is not None and expected_head_id != head:
        return _refused(session_id, "stale", "The conversation moved on. Reload and try again.")
    if any(
        row.get("id") != edge.get("id")
        and row.get("status") == "rewound"
        and _on_active_branch(row, session)
        and not row.get("sent")
        and row.get("target_head_id") == head
        for row in edges
    ):
        return _refused(session_id, "unsent", "Send a message, then try again.")
    chain = set(_chain_ids(index))
    undoing = status == "rewound"
    required = edge.get("target_head_id") if undoing else edge.get("source_head_id")
    if required not in chain:
        return _refused(session_id, "stale", "The conversation moved on. Reload and try again.")
    destination = (
        edge.get("source_head_id") if undoing
        else edge.get("undone_from_head_id") or edge.get("target_head_id")
    )
    if destination not in index.nodes_by_id:
        return _refused(session_id, "gone", "Those messages are no longer in this conversation.")
    pending_note = session.get("pending_rewind_note") or {}
    prompt_text = ""
    if undoing:
        unsent = not edge.get("sent")
        if unsent:
            # A rewind nothing was sent after leaves no mark once undone.
            target = index.nodes_by_id.get(edge.get("target_msg_id"))
            prompt_text = _node_text(target) if target is not None else ""
            edge.update(status="retired")
            pending_note = edge.get("note_before") or {}
        else:
            edge.update(status="undone", undone_from_head_id=head)
        if edge.get("files_restored"):
            earlier = pending_note.get("kind")
            pending_note = {
                "kind": "undo_kept_files",
                **({"earlier": earlier} if earlier in _FILE_WARNINGS else {}),
            }
        from_branch, to_branch = edge.get("target_branch_id"), edge.get("source_branch_id")
    else:
        edge.update(status="rewound")
        if pending_note.get("kind") == "undo_kept_files":
            pending_note = {"kind": pending_note["earlier"]} if pending_note.get("earlier") else {}
        from_branch, to_branch = edge.get("source_branch_id"), edge.get("target_branch_id")
    updated = [edge if row.get("id") == edge.get("id") else row for row in edges]
    branch_update = (
        {
            "source_branch_id": from_branch,
            "target_branch_id": to_branch,
            "active_branch_id": to_branch,
            "target_status": "active",
        }
        if from_branch and to_branch else None
    )
    from openprogram.state.store.session.rewind_view import view_update
    visibility = view_update(store, session_id, head, destination)
    try:
        moved = store.compare_and_set_head(
            session_id, head, destination,
            branch_update=branch_update,
            meta_update={"rewind_edges": updated, "pending_rewind_note": pending_note, **visibility},
        )
    except ValueError as exc:
        return _refused(session_id, "unreadable", str(exc))
    if not moved:
        return _refused(session_id, "stale", "The conversation moved on. Reload and try again.")
    forget_read_baselines(session_id)
    return {
        "status": "committed",
        "session_id": session_id,
        "edge_id": edge.get("id"),
        "new_head_id": destination,
        "previous_head_id": head,
        "head_changed": True,
        "files_changed": False,
        "user_text": prompt_text,
        "target_msg_id": edge.get("target_msg_id"),
        "marks": list_rewind_marks(session_id),
    }
