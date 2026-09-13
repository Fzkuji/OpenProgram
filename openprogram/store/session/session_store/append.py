"""Serialized node append and recovery of its history/metadata write pair."""
from __future__ import annotations

import copy
import json
import time

from . import shared


def intent_path(git):
    return git.path / ".git" / "openprogram-append.json"


def history_path(git, node):
    if (not isinstance(node.id, str) or not node.id or node.id in {".", ".."}
            or "/" in node.id or "\\" in node.id
            or not isinstance(node.role, str) or "/" in node.role or "\\" in node.role
            or not isinstance(node.seq, int) or node.seq < 0):
        raise ValueError("invalid append history identity")
    return git.path / "history" / f"{node.seq:04d}-{(node.role or 'x')[0]}-{node.id}.json"


def recover(store, git):
    """Complete a prepared append under the stable session lock, if present."""
    path = intent_path(git)
    if not path.exists():
        return False
    receipt = json.loads(shared.read_text_with_retry(path))
    if receipt.get("version") != 1 or not isinstance(receipt.get("meta"), dict):
        raise ValueError("invalid append intent")
    node = shared.Call(**receipt["node"])
    target = history_path(git, node)
    if not target.exists():
        git.write_history(node.seq, node.role, node.id, node.to_dict())
    git.write_meta(receipt["meta"])
    fields = {"updated_at": receipt["meta"]["updated_at"]}
    if node.role == shared.ROLE_USER and node.output:
        text = str(node.output).strip().replace("\n", " ")
        fields["preview"] = (text[:77] + "…") if len(text) > 80 else text
    store._update_index_entry(git.path.name, **fields)
    store._schedule_index_flush()
    path.unlink()
    # The writes above sync the GitSession fingerprint, but its caller's
    # index still precedes recovery and must be rebuilt before use.
    git._synced_fingerprint = None
    return True


def append_node(store, session_id, node, *, create_if_missing=True, advance_head=True, tip_only=False):
    """Persist a detached node, then publish it to the session index."""
    with store._session_write_scope(session_id, create_if_missing=create_if_missing) as pair:
        if pair is None:
            return
        git, idx = pair
        if node.id in idx.nodes_by_id:
            return
        pending = copy.deepcopy(node)
        predecessor = shared._node_conv_predecessor(pending)
        caller = shared._node_caller(pending)
        shared._check_append_invariant(session_id, idx, pending, predecessor, caller)
        if pending.seq < 0:
            pending.seq = idx.next_seq
        if pending.seq in idx._taken_seqs:
            raise ValueError("append sequence already exists")
        target = history_path(git, pending)
        store.spill_large_node(session_id, pending)
        meta = copy.deepcopy(idx.meta)
        meta["head_id"] = idx.head_id
        if (not caller and advance_head
                and (not tip_only or idx.head_id is None or predecessor == idx.head_id)):
            meta["head_id"] = pending.id
        meta["updated_at"] = time.time()
        git._ensure_init()
        path = intent_path(git)
        shared.atomic_write_text(path, json.dumps(
            {"version": 1, "node": pending.to_dict(), "meta": meta},
            ensure_ascii=False, default=str,
        ))
        try:
            git.write_history(pending.seq, pending.role, pending.id, pending.to_dict())
        except BaseException as failure:
            # No committed history means the failed append can be cancelled.
            # If replacement completed before the exception, retain recovery.
            if not target.exists():
                try:
                    path.unlink()
                except OSError as cleanup_error:
                    failure.add_note(f"append intent cleanup failed: {cleanup_error}")
            raise
        recover(store, git)
        idx.append(pending, predecessor=predecessor, caller=caller)
        with idx._lock:
            idx.meta.clear()
            idx.meta.update(meta)
            idx.head_id = meta["head_id"]
        node.seq = pending.seq
        node.metadata = copy.deepcopy(pending.metadata)
        git.mark_synced()
