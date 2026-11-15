"""Finish or roll back rewinds a crash interrupted.

A rewind moves a session's head through a journal: the intent is written
first, then the head is compared-and-set. Opening a session replays any
journal entry that has no outcome yet, before the session is shown, so a
reader never sees a half-applied rewind. Planning and applying a rewind is
the runtime's (``openprogram.runtime.conversation.rewind``); replaying its
journal only touches the store and the session's checkpoint journal.
"""
from __future__ import annotations


def head(store, session_id: str) -> str | None:
    return (store.get_session(session_id) or {}).get("head_id")


def cas_for_intent(
    store, session_id: str, intent: dict, expected, target, *, rollback: bool = False,
) -> bool:
    """Move HEAD for a rewind journal entry.

    ``rollback`` names the direction, which the heads alone cannot tell when a
    code-only rewind keeps HEAD where it is.
    """
    source_branch_id = intent.get("source_branch_id")
    target_branch_id = intent.get("target_branch_id")
    forward = (
        not rollback
        and expected == intent.get("expected_head_id")
        and target == intent.get("target_head_id")
    )
    update = None
    if source_branch_id and target_branch_id:
        update = {
            "source_branch_id": source_branch_id,
            "target_branch_id": target_branch_id,
            "active_branch_id": target_branch_id if forward else source_branch_id,
            "target_status": "active" if forward or source_branch_id == target_branch_id else "aborted",
            "preserve_target": not forward and source_branch_id != target_branch_id,
        }
    return store.compare_and_set_head(
        session_id,
        expected,
        target,
        expected_meta=({"active_branch_id": (
            intent["expected_active_branch_id"] if forward
            else target_branch_id or intent["expected_active_branch_id"]
        )} if "expected_active_branch_id" in intent else None),
        branch_update=update,
        meta_update=(
            intent.get("forward_meta_update")
            if forward else intent.get("rollback_meta_update")
        ),
    )


def recover_session_rewinds(session_id: str, *, store=None) -> list[dict]:
    from openprogram.state.store.session.session_store import default_store
    from openprogram.state.store.snapshot.checkpoint import CheckpointStore

    store = store or default_store()
    # Replay under the conversation's write lock, which a live rewind holds
    # from its intent to its outcome: a journal entry seen here is never
    # one another thread or process is still applying.
    if not session_id or session_id in {".", ".."}:
        return []
    with store._conversation_write_lock(session_id), store._rewind_recovery_scope(session_id):
        if store._open(session_id) is None:
            return []
        journal = CheckpointStore(store._session_dir(session_id))
        recovered = journal.recover_rewind_intents(
            get_head=lambda: head(store, session_id),
            compare_and_set_head=lambda intent, expected, target, rollback=False: cas_for_intent(
                store, session_id, intent, expected, target, rollback=rollback,
            ),
        )
        return recovered + journal.recover_history_intents()


def recover_all_rewinds() -> int:
    from openprogram.state.store.session.session_store import default_store

    store = default_store()
    recovered = 0
    for session in store.list_sessions(limit=100_000, include_archived=True):
        session_id = session.get("id")
        if session_id:
            recovered += len(recover_session_rewinds(session_id, store=store))
    return recovered


__all__ = ["cas_for_intent", "head", "recover_all_rewinds", "recover_session_rewinds"]
