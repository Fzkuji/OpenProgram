"""Atomic conversation visibility for a rewind, without deleting recovery data.

A rewind retracts the tail of the branch it runs on, in place: the branch
keeps its record, id, name and settings, and its tip moves back. Nothing is
deleted. The retracted nodes are listed in the session meta
(``rewind_hidden_nodes``; the old tip in ``rewind_hidden_heads``), and every
reader of the conversation skips them: messages, branch lists, version
counts, search, the request's context and which compaction summary is in
effect. Undo of the rewind takes them off the list again.
"""
from __future__ import annotations


def _covers(node) -> list[str]:
    """The turns a compaction summary replaces; empty for any other node."""
    covers = (node.metadata or {}).get("covers_ids")
    return [str(item) for item in covers] if isinstance(covers, (list, tuple)) else []


def view_update(store, session_id: str, source: str | None, target: str | None) -> dict:
    """Withdraw this line's suffix while preserving existing sibling ancestry.

    The caller writes this metadata in the same transaction as HEAD. Raw
    nodes stay available to recovery; normal message and branch reads omit it.
    """
    _, index = store._open(session_id)
    from .session_store.shared import _node_conv_predecessor

    def ancestry(head):
        result = set()
        while head and head not in result:
            node = index.nodes_by_id.get(head)
            if node is None:
                break
            result.add(head)
            head = _node_conv_predecessor(node)
        return result

    hidden = set(index.meta.get("rewind_hidden_nodes") or [])
    retired = set(index.meta.get("rewind_hidden_heads") or [])
    target_chain = ancestry(target)
    protected = set(target_chain)
    active_id = index.meta.get("active_branch_id")
    other_tips = set()
    for branch in store.list_branches(session_id):
        tip = branch["head_msg_id"]
        tip_node = index.nodes_by_id.get(tip)
        if tip_node is not None and _covers(tip_node):
            continue  # a compaction summary hangs beside the chain; it is no line
        if (tip != source or (branch.get("branch_id") and branch["branch_id"] != active_id)) and tip not in retired:
            other_tips.add(tip)
            protected.update(ancestry(tip))
    withdrawn = ancestry(source) - protected
    # Calls made by withdrawn replies disappear with those replies.
    pending = list(withdrawn)
    while pending:
        parent = pending.pop()
        for child in index.children_by_caller.get(parent, ()):
            if child not in withdrawn and child not in protected:
                withdrawn.add(child)
                pending.append(child)
    # A compaction summary of withdrawn turns goes with them: no chain can
    # read it any more, and its card would describe messages that are gone.
    summaries = [node for node in index.all_nodes() if _covers(node)]
    withdrawn.update(node.id for node in summaries
                     if node.id not in protected and withdrawn.intersection(_covers(node)))
    hidden.update(withdrawn)
    restored = set(target_chain)
    # ROOT is the session anchor, not ownership of all top-level prompts.
    pending = [nid for nid in restored
               if (index.nodes_by_id[nid].metadata or {}).get("display") != "root"]
    while pending:
        for child in index.children_by_caller.get(pending.pop(), ()):
            if child not in restored:
                restored.add(child)
                pending.append(child)
    hidden.difference_update(restored)
    # Undo brings a summary back with the turns it covers.
    hidden.difference_update(node.id for node in summaries
                             if node.id in hidden and not hidden.intersection(_covers(node)))
    if source and source not in other_tips:
        retired.add(source)
    retired.discard(target)
    # The branch keeps its record (name, lock, counters) at its new tip.
    # The move is applied by the HEAD CAS to the records as they are on
    # disk inside the session lock, never as a copy of the map read here.
    moves = [[source, target]] if source and target and source != target else []
    return {"rewind_hidden_nodes": sorted(hidden), "rewind_hidden_heads": sorted(retired),
            BRANCH_RECORD_MOVES: moves}


def checkout_meta(store, session_id: str, target: str | None, branch_id: str | None = None) -> dict:
    """Metadata for an explicit branch switch, committed with its HEAD CAS.

    Returns ``{}`` when the switch changes nothing. Otherwise the update
    names the switch (``BRANCH_CHECKOUT``) instead of carrying copies of
    the branch maps: ``compare_and_set_head`` applies it with
    ``apply_checkout`` to the session record as it is on disk inside the
    session lock, so a name, an archive or another branch's record written
    meanwhile is kept. Raises ``ValueError`` for a withdrawn target or a
    branch that moved.
    """
    session = store.get_session(session_id) or {}
    _check_checkout(session, target, branch_id)
    if session.get("head_id") == target and (not branch_id or branch_id == session.get("active_branch_id")):
        return {}
    return {BRANCH_CHECKOUT: {"target": target, "branch_id": branch_id},
            "workspace_alignment": {"status": "aligned", "decision": "shared_workspace"},
            "pending_rewind_note": {"kind": "branch_kept_files"}}


def _check_checkout(meta: dict, target: str | None, branch_id: str | None) -> None:
    hidden = set(meta.get("rewind_hidden_nodes") or [])
    retired = set(meta.get("rewind_hidden_heads") or [])
    if target in hidden or target in retired:
        raise ValueError("This version was withdrawn by Rewind; use Undo rewind to recover it.")
    refs = meta.get("branch_refs") or {}
    if branch_id and branch_id != meta.get("active_branch_id") and (
            branch_id not in refs or refs[branch_id].get("head_id") != target):
        raise ValueError("The branch changed; refresh the branch list and try again.")


def apply_checkout(meta: dict, session_id: str, target: str | None, branch_id: str | None,
                   nodes: dict) -> None:
    """Switch ``meta`` (the record read inside the session lock) to ``target``.

    The branch being left keeps its record at its tip with the settings it
    has now; the branch switched to is the record at the target, or the one
    on the target's own line that fell behind its tip (it moves down, and
    its name record with it). Each branch's name stays in its record at
    its tip in ``meta["branches"]``; a parked copy is put back only for a
    tip shared with another branch or one with no record
    (``branch_config.branch_record``).
    """
    from .branch_config import (branch_identity, move_branch_record, park_record,
                                record_on_line, snapshot, unpark_record)

    _check_checkout(meta, target, branch_id)
    source = meta.get("head_id")
    refs = {key: dict(value) for key, value in (meta.get("branch_refs") or {}).items()}
    source_id = meta.get("active_branch_id") or branch_identity(session_id, source)
    refs[source_id] = {**refs.get(source_id, {}), "branch_id": source_id, "head_id": source,
                       "config": snapshot(meta)}
    if branch_id and (branch_id not in refs or refs[branch_id].get("head_id") != target):
        raise ValueError("The branch changed; refresh the branch list and try again.")
    left_chain = set()
    cursor = source
    while cursor and cursor not in left_chain:
        left_chain.add(cursor)
        node = nodes.get(cursor)
        cursor = node.predecessor if node is not None else None
    target_id = branch_id or record_on_line(
        {key: value for key, value in refs.items() if key != source_id},
        target, nodes, stop=left_chain)
    target_id = target_id or branch_identity(session_id, target)
    # The branch left parks its record (fresh, read inside the lock) in
    # case the branch switched to shares its tip.
    park_record(meta, refs, source_id)
    behind = refs.get(target_id, {}).get("head_id")
    refs[target_id] = {**refs.get(target_id, {}), "branch_id": target_id, "head_id": target,
                       "status": "active"}
    config = snapshot(refs[target_id].get("config", snapshot(meta)))
    refs[target_id]["config"] = config
    unpark_record(meta, refs, target_id)
    meta.update(config)
    meta.update(branch_refs=refs, active_branch_id=target_id)
    # A record that fell behind its tip takes its name down with it.
    move_branch_record(meta, behind, target)


#: ``compare_and_set_head`` meta-update keys that change branch records.
#: They describe the change; the CAS applies it to the on-disk records.
BRANCH_CHECKOUT = "branch_checkout"
BRANCH_RECORD_MOVES = "branch_record_moves"
