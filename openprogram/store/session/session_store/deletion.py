"""Literal node deletion with a durable, replayable history/metadata intent."""
from __future__ import annotations

import copy
import json

from ..git_session import _fsync_directory
from . import shared
from .append import history_path


def intent_path(git):
    return git.path / ".git" / "openprogram-delete-nodes.json"


def recover(git):
    """Complete an admitted deletion while its stable session lock is held."""
    path = intent_path(git)
    if not path.exists():
        return False
    receipt = json.loads(shared.read_text_with_retry(path))
    if (receipt.get("version") != 1 or not isinstance(receipt.get("meta"), dict)
            or not isinstance(receipt.get("nodes"), list)):
        raise ValueError("invalid node deletion intent")
    targets = [history_path(git, shared.Call(**identity)) for identity in receipt["nodes"]]
    try:
        for target in targets:
            target.unlink(missing_ok=True)
        _fsync_directory(git.path / "history")
        git.write_meta(receipt["meta"])
        path.unlink()
        _fsync_directory(path.parent)
    finally:
        # Metadata may be durable even if cancellation interrupts publication.
        git._synced_fingerprint = None
    return True


def delete_nodes(store, session_id, node_id, *, descendants=False):
    """Select from current durable state, persist deletion, then refresh cache."""
    with store._session_write_scope(session_id) as pair:
        if pair is None:
            return 0
        git, idx = pair
        root = idx.nodes_by_id.get(node_id)
        if root is None:
            return 0
        selected = [node_id]
        seen = {node_id}
        if descendants:
            for current in selected:
                children = (
                    idx.children_by_predecessor.get(current, [])
                    + idx.children_by_caller.get(current, [])
                )
                for child in children:
                    if child not in seen and child in idx.nodes_by_id:
                        seen.add(child)
                        selected.append(child)
        identities = []
        for identifier in selected:
            node = idx.nodes_by_id[identifier]
            history_path(git, node)  # Validate every identity before persisting intent.
            identities.append({"id": node.id, "seq": node.seq, "role": node.role})
        meta = copy.deepcopy(idx.meta)
        meta["head_id"] = idx.head_id
        branches = dict(meta.get("branches") or {})
        for identifier in selected:
            branches.pop(identifier, None)
        meta["branches"] = branches
        if idx.head_id in seen:
            fallback = shared._node_conv_predecessor(root)
            meta["head_id"] = fallback if fallback not in seen else None
        if meta.get("last_node_id") in seen:
            meta.pop("last_node_id")
        shared.atomic_write_text(intent_path(git), json.dumps(
            {"version": 1, "nodes": identities, "meta": meta},
            ensure_ascii=False, default=str,
        ))
        recover(git)
        idx.rebuild_from_paths(
            git.list_history(), git.read_meta(),
            shared._node_conv_predecessor, shared._node_caller,
        )
        return len(selected)
