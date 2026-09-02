"""Review scope and history data implementation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
from pathlib import Path
from typing import Any

from . import turn_files_shared as _shared
from .turn_files_shared import (
    _MAX_SCOPE_FILES, _SCOPE_PAGE_SIZE, _MAX_REVIEW_SNAPSHOTS,
    _MAX_REVIEW_SNAPSHOT_BYTES, _MAX_REVIEW_SNAPSHOT_ITEMS,
    _REVIEW_SNAPSHOT_TTL, _MAX_REVIEW_SNAPSHOT_TOMBSTONES,
    _MAX_REVIEW_CURSORS, _MAX_REVIEW_TEXT_BYTES, _REVIEW_CATEGORIES,
    _REVIEW_SORTS, _REVIEW_SCOPES, _REVIEW_SNAPSHOTS, _REVIEW_CURSORS,
    _REVIEW_SNAPSHOT_EPOCHS, _REVIEW_REGISTRY_LOCK,
    _setting, _valid_turn_id,
)
from .turn_files_diff_shared import _net_stats, _same_state


def _project_root(session_id: str) -> Path | None:
    return _shared._project_root(session_id)


class _OutputLimitError(OSError):
    pass


class _ReviewContentBudget:
    """Reserve snapshot content before reading it into retained state."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def reserve(self, size: int) -> None:
        if size < 0 or self.used + size > self.limit:
            raise _OutputLimitError("workspace snapshot content exceeds review limit")
        self.used += size

    def release(self, size: int) -> None:
        self.used = max(0, self.used - max(0, size))


def _open_session(session_id: str):
    from openprogram.store.session.session_store import default_store

    store = default_store()
    pair = store._open(session_id)
    if pair is None:
        return None
    git, index = pair
    return store, git, index, store._session_dir(session_id)


def _relative(path: str, root: Path | None) -> str:
    if root is not None:
        try:
            return str(Path(path).resolve().relative_to(root))
        except (OSError, ValueError):
            pass
    return os.path.basename(path)


def _manifest_mutations(session_dir: Path, turn_id: str) -> list[dict]:
    from openprogram.store.snapshot.checkpoint import CheckpointStore

    if not _valid_turn_id(turn_id):
        return []
    return CheckpointStore(session_dir).list_mutations(turn_id)


def _normalise_file(row: dict, root: Path | None) -> dict:
    stats = row.get("stats") or {}
    path = row.get("path") or ""
    operation = row.get("op") or row.get("operation") or "modify"
    if operation == "create":
        operation = "add"
    return {
        "path": path,
        "rel": row.get("rel") or _relative(path, root),
        "op": operation,
        "added": row.get("added", stats.get("added")),
        "removed": row.get("removed", stats.get("removed")),
        "binary": bool(row.get("binary", stats.get("binary"))),
        "diff_state": row.get("diff_state", "available"),
        "recoverability": row.get("recoverability", "exact"),
        "unavailable_reason": row.get("unavailable_reason"),
        "turn_ids": list(row.get("turn_ids") or []),
    }


def _turn_summary(index, session_dir: Path, turn_id: str, root: Path | None) -> dict:
    node = index.nodes_by_id.get(turn_id)
    metadata = getattr(node, "metadata", None) or {}
    summary = metadata.get("turn_files")
    if isinstance(summary, dict) and isinstance(summary.get("files"), list):
        files = [_normalise_file(row, root) for row in summary["files"]]
    else:
        files = [
            _normalise_file(row, root)
            for row in _manifest_mutations(session_dir, turn_id)
        ]
    for row in files:
        row["turn_ids"] = [turn_id]
    return {
        "files": files,
        "file_count": (
            int(summary.get("file_count") or len(files))
            if isinstance(summary, dict)
            else len(files)
        ),
        "reverted": bool(metadata.get("reverted")),
    }


def _active_nodes(index) -> list:
    from openprogram.store.session.session_store import _node_conv_predecessor

    nodes = []
    seen: set[str] = set()
    node = index.nodes_by_id.get(index.head_id) if index.head_id else None
    while node is not None and node.id not in seen:
        seen.add(node.id)
        nodes.append(node)
        predecessor = _node_conv_predecessor(node)
        node = index.nodes_by_id.get(predecessor) if predecessor else None
    return nodes


def _totals(files: list[dict]) -> tuple[int | None, int | None]:
    added = [row.get("added") for row in files]
    removed = [row.get("removed") for row in files]
    return (
        sum(added) if all(isinstance(value, int) for value in added) else None,
        sum(removed) if all(isinstance(value, int) for value in removed) else None,
    )

def _review_category(file: dict) -> str:
    if file.get("diff_state") and file.get("diff_state") != "available":
        return "Large"
    rel = str(file.get("rel") or file.get("path") or "")
    if re.search(r"(?:^|/)(?:tests?|specs?)(?:/|_|-)", rel, re.I):
        return "Tests"
    if re.search(r"\.(?:md|mdx|rst|txt)$", rel, re.I):
        return "Docs"
    return "Code"


def _review_filter_files(
    files: list[dict], category: str, query: str, sort: str,
) -> list[dict]:
    category = category if category in _REVIEW_CATEGORIES else "All"
    query = query.strip().casefold()
    filtered = [
        file for file in files
        if (category == "All" or _review_category(file) == category)
        and (
            not query
            or query in str(file.get("rel") or "").casefold()
            or query in str(file.get("path") or "").casefold()
        )
    ]
    if sort in {"path", "alpha"}:
        return sorted(
            filtered,
            key=lambda file: (
                str(file.get("rel") or file.get("path") or "").casefold(),
                str(file.get("rel") or file.get("path") or ""),
            ),
        )
    if sort == "category":
        return sorted(
            filtered,
            key=lambda file: (
                _review_category(file),
                str(file.get("rel") or file.get("path") or "").casefold(),
            ),
        )
    if sort == "recent":
        def recency_key(file: dict) -> tuple[int, int, str, str]:
            mutation = file.get("latest_mutation_sequence")
            turn = file.get("latest_turn_sequence")
            return (
                -(mutation if isinstance(mutation, int) else -1),
                -(turn if isinstance(turn, int) else -1),
                str(file.get("rel") or file.get("path") or "").casefold(),
                str(file.get("rel") or file.get("path") or ""),
            )
        return sorted(filtered, key=recency_key)
    return filtered


def _review_value_bytes(value: Any) -> int:
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, dict):
        return sum(_review_value_bytes(key) + _review_value_bytes(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return sum(_review_value_bytes(item) for item in value)
    return len(str(value).encode())


def _tombstone_review_snapshot(snapshot_id: str, entry: dict) -> None:
    _REVIEW_SNAPSHOT_EPOCHS[snapshot_id] = entry.get("epoch", 0)
    for token, cursor in list(_REVIEW_CURSORS.items()):
        if cursor.get("snapshot_id") == snapshot_id:
            del _REVIEW_CURSORS[token]
    while len(_REVIEW_SNAPSHOT_EPOCHS) > _setting("_MAX_REVIEW_SNAPSHOT_TOMBSTONES"):
        tombstone = next(iter(_REVIEW_SNAPSHOT_EPOCHS))
        del _REVIEW_SNAPSHOT_EPOCHS[tombstone]


def _snapshot_instance_id(basis_hash: str) -> str:
    with _REVIEW_REGISTRY_LOCK:
        _expire_review_registry()
        for snapshot_id, entry in _REVIEW_SNAPSHOTS.items():
            if entry.get("basis_hash") == basis_hash:
                return snapshot_id
        _shared._REVIEW_SNAPSHOT_NONCE += 1
        return f"sha256:{basis_hash}:{_shared._REVIEW_SNAPSHOT_NONCE}"


def _expire_review_registry(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    with _REVIEW_REGISTRY_LOCK:
        expired = [
            snapshot_id for snapshot_id, entry in _REVIEW_SNAPSHOTS.items()
            if now - entry["created_at"] >= _setting("_REVIEW_SNAPSHOT_TTL")
        ]
        for snapshot_id in expired:
            entry = _REVIEW_SNAPSHOTS.pop(snapshot_id, None)
            if entry is not None:
                _tombstone_review_snapshot(snapshot_id, entry)
        expired_cursors = [
            token for token, entry in _REVIEW_CURSORS.items()
            if now - entry["created_at"] >= _setting("_REVIEW_SNAPSHOT_TTL")
        ]
        for token in expired_cursors:
            _REVIEW_CURSORS.pop(token, None)


def _remember_review_snapshot(snapshot_id: str, entry: dict) -> bool:
    size = _review_value_bytes(entry)
    if len(entry.get("files", ())) > _setting("_MAX_REVIEW_SNAPSHOT_ITEMS") or size > _setting("_MAX_REVIEW_SNAPSHOT_BYTES"):
        return False
    now = time.monotonic()
    with _REVIEW_REGISTRY_LOCK:
        _expire_review_registry(now)
        if snapshot_id in _REVIEW_SNAPSHOTS:
            return True
        epoch = _REVIEW_SNAPSHOT_EPOCHS.get(snapshot_id, 0) + 1
        _REVIEW_SNAPSHOTS[snapshot_id] = {
            **entry, "epoch": epoch, "created_at": now, "size": size,
        }
        while (
            len(_REVIEW_SNAPSHOTS) > _setting("_MAX_REVIEW_SNAPSHOTS")
            or sum(item["size"] for item in _REVIEW_SNAPSHOTS.values()) > _setting("_MAX_REVIEW_SNAPSHOT_BYTES")
        ):
            oldest = min(_REVIEW_SNAPSHOTS, key=lambda key: _REVIEW_SNAPSHOTS[key]["created_at"])
            evicted = _REVIEW_SNAPSHOTS[oldest]
            del _REVIEW_SNAPSHOTS[oldest]
            _tombstone_review_snapshot(oldest, evicted)
        return snapshot_id in _REVIEW_SNAPSHOTS


def _get_review_snapshot(snapshot_id: str) -> dict | None:
    if not snapshot_id:
        return None
    _expire_review_registry()
    with _REVIEW_REGISTRY_LOCK:
        return _REVIEW_SNAPSHOTS.get(snapshot_id)


def _new_review_cursor(entry: dict) -> str:
    token = "rc_" + secrets.token_urlsafe(18)
    with _REVIEW_REGISTRY_LOCK:
        _expire_review_registry()
        _REVIEW_CURSORS[token] = {**entry, "created_at": time.monotonic()}
        while len(_REVIEW_CURSORS) > _setting("_MAX_REVIEW_CURSORS"):
            oldest = min(_REVIEW_CURSORS, key=lambda key: _REVIEW_CURSORS[key]["created_at"])
            del _REVIEW_CURSORS[oldest]
    return token


def _get_review_cursor(token: Any, kind: str) -> dict | None:
    if not isinstance(token, str) or not token.startswith("rc_"):
        return None
    _expire_review_registry()
    with _REVIEW_REGISTRY_LOCK:
        entry = _REVIEW_CURSORS.get(token)
        return entry if entry and entry.get("kind") == kind else None


def _scope_payload(scope: str, source: str, files: list[dict], **extra) -> dict:
    snapshot_basis = extra.pop("_snapshot_basis", files)
    snapshot_owner = extra.pop("_snapshot_owner", {})
    snapshot_store = extra.pop("_snapshot_store", None)
    category = extra.pop("category", "All")
    query = str(extra.pop("query", "") or "")
    sort = extra.pop("sort", "path")
    filtered = _review_filter_files(files, category, query, sort)
    bounded = filtered[:_setting("_MAX_SCOPE_FILES")]
    added, removed = _totals(filtered)
    payload = {
        "status": "ready",
        "scope": scope,
        "source": source,
        "files": bounded,
        "file_count": len(filtered),
        "added": added,
        "removed": removed,
        "truncated": len(filtered) > _setting("_MAX_SCOPE_FILES"),
        "category": category,
        "query": query,
        "sort": sort,
        **extra,
    }
    snapshot_value = {
        "scope": scope,
        "source": source,
        "owner": snapshot_owner,
        "basis": snapshot_basis,
        "filtered": [
            {
                key: file.get(key)
                for key in (
                    "path", "rel", "op", "added", "removed", "binary", "diff_state",
                    "recoverability", "latest_mutation_sequence", "latest_turn_sequence",
                )
            }
            for file in filtered
        ],
        "category": category,
        "query": query,
        "sort": sort,
        "head_id": extra.get("head_id"),
    }
    basis_hash = hashlib.sha256(
        json.dumps(snapshot_value, sort_keys=True, default=str).encode(),
    ).hexdigest()
    payload["snapshot_id"] = _snapshot_instance_id(basis_hash)
    snapshot_entry = {
        "scope": scope,
        "source": source,
        "owner": snapshot_owner,
        "category": category,
        "query": query,
        "sort": sort,
        "files": filtered,
        "payload": payload,
        "basis_hash": basis_hash,
        **(snapshot_store or {}),
    }
    if not _remember_review_snapshot(payload["snapshot_id"], snapshot_entry):
        payload["status"] = "unavailable"
        payload["error"] = "REVIEW_SNAPSHOT_LIMIT"
    return payload


def _page_scope(
    result: dict, cursor: Any = "", limit: int | None = None,
    snapshot_id: str = "",
) -> dict:
    if result.get("status") != "ready":
        return result
    current_id = result.get("snapshot_id") or ""
    saved = _get_review_snapshot(current_id)
    token = _get_review_cursor(cursor, "scope") if cursor else None
    if cursor and (
        token is None
        or not snapshot_id
        or token.get("snapshot_id") != snapshot_id
        or snapshot_id != current_id
        or (saved is not None and token.get("epoch") != saved.get("epoch"))
    ):
        return {
            "status": "stale",
            "scope": result.get("scope"),
            "error": "STALE_SNAPSHOT",
            "snapshot_id": snapshot_id,
            "category": result.get("category", "All"),
            "query": result.get("query", ""),
            "sort": result.get("sort", "path"),
            "files": [],
            "file_count": 0,
            "added": 0,
            "removed": 0,
        }
    if snapshot_id and snapshot_id != current_id:
        return {
            "status": "stale", "scope": result.get("scope"),
            "error": "STALE_SNAPSHOT", "snapshot_id": snapshot_id,
            "files": [], "file_count": 0, "added": 0, "removed": 0,
        }
    if saved is None:
        return {
            "status": "stale", "scope": result.get("scope"),
            "error": "STALE_SNAPSHOT", "snapshot_id": snapshot_id or current_id,
            "files": [], "file_count": 0, "added": 0, "removed": 0,
        }
    start = token["offset"] if token else 0
    size = token["limit"] if token else max(1, min(limit or _setting("_SCOPE_PAGE_SIZE"), _setting("_SCOPE_PAGE_SIZE")))
    files = saved.get("files") or []
    base = {key: value for key, value in saved["payload"].items() if key != "files"}
    next_offset = start + size
    next_cursor = (
        _new_review_cursor({
            "kind": "scope", "snapshot_id": current_id,
            "epoch": saved.get("epoch"),
            "owner": saved.get("owner"), "category": saved.get("category"),
            "query": saved.get("query"), "sort": saved.get("sort"),
            "offset": next_offset, "limit": size,
        })
        if next_offset < len(files) else None
    )
    prev_cursor = (
        _new_review_cursor({
            "kind": "scope", "snapshot_id": current_id,
            "epoch": saved.get("epoch"),
            "owner": saved.get("owner"), "category": saved.get("category"),
            "query": saved.get("query"), "sort": saved.get("sort"),
            "offset": max(0, start - size), "limit": size,
        })
        if start > 0 else None
    )
    page = files[start:start + size]
    return {
        **base,
        "files": page,
        "cursor": cursor or None,
        "next_cursor": next_cursor,
        "prev_cursor": prev_cursor,
        "page_size": size,
        "page": start // size + 1,
    }


def _history_eligibility(session_id: str, turn_id: str) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"status": "error", "action": None, "error": "unknown session"}
    _store, _git, index, session_dir = opened
    node = index.nodes_by_id.get(turn_id)
    if node is None:
        return {"status": "error", "action": None, "error": "unknown turn"}
    active = _active_nodes(index)
    if not any(candidate.id == turn_id for candidate in active):
        return {"status": "blocked", "action": None, "error": "turn is not on active branch"}
    file_turn_ids = []
    for candidate in active:
        if candidate.role != "llm":
            continue
        summary = (candidate.metadata or {}).get("turn_files") or {}
        if summary.get("file_count") or _manifest_mutations(session_dir, candidate.id):
            file_turn_ids.append(candidate.id)
    latest_file_turn = file_turn_ids[0] if file_turn_ids else None
    reverted = bool((node.metadata or {}).get("reverted"))
    direction = "reapply" if reverted else "revert"
    from openprogram.store.snapshot.checkpoint import CheckpointStore

    journal = CheckpointStore(session_dir)
    ownership = {"status": "ready", "owned_turn_ids": [], "blockers": [], "linked": []}
    from openprogram.agent.history_ownership import owned_change_set_closure

    ownership = owned_change_set_closure(session_id, [turn_id])
    if ownership["status"] != "ready":
        return {
            "status": "blocked",
            "action": None,
            "reverted": reverted,
            "latest_file_turn_id": latest_file_turn,
            "blockers": ownership["blockers"],
            "linked_impacts": ownership["linked"],
            "error": "owned_actor_running",
        }
    plan = (
        journal.plan_rewind_operation(
            ownership["owned_turn_ids"] + [turn_id], direction=direction,
        )
        if ownership["owned_turn_ids"]
        else journal.plan_history_operation(turn_id, direction)
    )
    if plan.get("status") != "ready":
        return {
            "status": plan.get("status", "unavailable"),
            "action": None,
            "reverted": reverted,
            "latest_file_turn_id": latest_file_turn,
            "conflicts": plan.get("conflicts", []),
            "unavailable": plan.get("unavailable", []),
            "error": plan.get("error"),
            "linked_impacts": ownership["linked"],
        }
    return {
        "status": "ready",
        "action": (
            "redo" if reverted and turn_id == latest_file_turn
            else "reapply" if reverted
            else "undo" if turn_id == latest_file_turn
            else "revert"
        ),
        "reverted": reverted,
        "latest_file_turn_id": latest_file_turn,
        "conflicts": [],
        "unavailable": [],
        "owned_turn_ids": ownership["owned_turn_ids"],
        "linked_impacts": ownership["linked"],
    }


def _turn_scope(
    session_id: str, turn_id: str, *, category: str = "All", query: str = "",
    sort: str = "path",
) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"status": "error", "error": f"unknown session {session_id!r}"}
    _store, _git, index, session_dir = opened
    if turn_id not in index.nodes_by_id:
        return {"status": "error", "error": f"unknown turn {turn_id!r}"}
    root = _project_root(session_id)
    from openprogram.agent.history_ownership import owned_change_set_closure

    ownership = owned_change_set_closure(session_id, [turn_id])
    producer_ids = [turn_id] + ownership["owned_turn_ids"]
    records = []
    for producer_id in producer_ids:
        mutations = _manifest_mutations(session_dir, producer_id)
        producer = index.nodes_by_id.get(producer_id)
        owner = (producer.metadata or {}).get("change_owner") if producer else None
        records.extend(
            (
                producer_id,
                (owner or {}).get("actor_id", "main"),
                (owner or {}).get("job_id"),
                getattr(producer, "seq", -1),
                mutation,
            )
            for mutation in mutations
        )
    if records and all(
        isinstance(mutation.get("mutation_sequence"), int)
        for _producer, _actor, _job, _turn_seq, mutation in records
    ):
        records.sort(key=lambda item: item[4]["mutation_sequence"])
    lineages: dict[str, dict] = {}
    for producer_id, actor_id, job_id, turn_seq, mutation in records:
        path = mutation.get("path") or ""
        if not path:
            continue
        current = lineages.get(path)
        if current is None:
            lineages[path] = {
                "path": path,
                "first_turn": producer_id,
                "last_turn": producer_id,
                "before": mutation.get("before") or {},
                "after": mutation.get("after") or {},
                "turn_ids": [producer_id],
                "actor_ids": [actor_id],
                "job_ids": [job_id] if job_id else [],
                "recoverability": mutation.get("recoverability", "exact"),
                "unavailable_reason": mutation.get("unavailable_reason"),
                "latest_mutation_sequence": mutation.get("mutation_sequence", -1),
                "latest_turn_sequence": turn_seq,
            }
            continue
        if not _same_state(current["after"], mutation.get("before") or {}):
            current["recoverability"] = "unavailable"
            current["unavailable_reason"] = "discontinuous_journal"
        current["last_turn"] = producer_id
        current["after"] = mutation.get("after") or {}
        current["turn_ids"].append(producer_id)
        current["latest_mutation_sequence"] = mutation.get("mutation_sequence", -1)
        current["latest_turn_sequence"] = turn_seq
        if actor_id not in current["actor_ids"]:
            current["actor_ids"].append(actor_id)
        if job_id and job_id not in current["job_ids"]:
            current["job_ids"].append(job_id)
    files = []
    stats_budget = [8 * 1024 * 1024]
    for lineage in lineages.values():
        added, removed, binary, diff_state = _net_stats(
            session_dir,
            lineage["first_turn"],
            lineage["before"],
            lineage["last_turn"],
            lineage["after"],
            stats_budget,
        )
        before_kind = lineage["before"].get("kind")
        after_kind = lineage["after"].get("kind")
        files.append({
            "path": lineage["path"],
            "rel": _relative(lineage["path"], root),
            "op": (
                "add" if before_kind == "absent" and after_kind == "regular"
                else "delete" if before_kind == "regular" and after_kind == "absent"
                else "modify"
            ),
            "added": added,
            "removed": removed,
            "binary": binary,
            "diff_state": diff_state,
            "recoverability": lineage["recoverability"],
            "unavailable_reason": lineage["unavailable_reason"],
            "turn_ids": lineage["turn_ids"],
            "producer_turn_id": lineage["last_turn"],
            "producer_turn_ids": lineage["turn_ids"],
            "first_turn_id": lineage["first_turn"],
            "last_turn_id": lineage["last_turn"],
            "latest_mutation_sequence": lineage["latest_mutation_sequence"],
            "latest_turn_sequence": lineage["latest_turn_sequence"],
            "origin_turn_id": turn_id,
            "actor_id": lineage["actor_ids"][-1],
            "actor_ids": lineage["actor_ids"],
            "job_ids": lineage["job_ids"],
        })
    if not records:
        files = _turn_summary(index, session_dir, turn_id, root)["files"]
        for row in files:
            row.update({
                "turn_ids": [turn_id],
                "producer_turn_id": turn_id,
                "producer_turn_ids": [turn_id],
                "first_turn_id": turn_id,
                "last_turn_id": turn_id,
                "latest_mutation_sequence": -1,
                "latest_turn_sequence": getattr(index.nodes_by_id.get(turn_id), "seq", -1),
                "origin_turn_id": turn_id,
                "actor_id": "main",
                "actor_ids": ["main"],
                "job_ids": [],
            })
    snapshot_basis = [
        {
            "path": lineage["path"],
            "before": lineage["before"],
            "after": lineage["after"],
            "turn_ids": lineage["turn_ids"],
        }
        for lineage in lineages.values()
    ] or files
    return _scope_payload(
        "turn", "mutation_journal", files,
        assistant_msg_id=turn_id,
        reverted=bool((index.nodes_by_id[turn_id].metadata or {}).get("reverted")),
        owned_turn_ids=ownership["owned_turn_ids"],
        blockers=ownership["blockers"],
        linked_impacts=ownership["linked"],
        category=category,
        query=query,
        sort=sort,
        _snapshot_owner={"session_id": session_id, "assistant_msg_id": turn_id},
        _snapshot_basis=snapshot_basis,
    )


def _branch_scope(
    session_id: str, *, category: str = "All", query: str = "", sort: str = "path",
) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"status": "error", "error": f"unknown session {session_id!r}"}
    _store, _git, index, session_dir = opened
    root = _project_root(session_id)
    lineages: dict[str, dict] = {}
    active_llm = [
        node for node in reversed(_active_nodes(index))
        if node.role == "llm" and not (node.metadata or {}).get("reverted")
    ]
    from openprogram.agent.history_ownership import owned_change_set_closure

    ownership = owned_change_set_closure(
        session_id, [node.id for node in active_llm],
    )

    producer_nodes = active_llm + [
        index.nodes_by_id[turn_id]
        for turn_id in ownership["owned_turn_ids"]
        if turn_id in index.nodes_by_id
    ]
    producer_nodes.sort(key=lambda node: node.seq)
    records = [
        (node, mutation)
        for node in producer_nodes
        if node.role == "llm"
        for mutation in _manifest_mutations(session_dir, node.id)
    ]
    if records and all(
        isinstance(mutation.get("mutation_sequence"), int)
        for _node, mutation in records
    ):
        records.sort(key=lambda item: item[1]["mutation_sequence"])
    for node, mutation in records:
        path = mutation.get("path") or ""
        before = mutation.get("before") or {}
        after = mutation.get("after") or {}
        if not path:
            continue
        current = lineages.get(path)
        if current is None:
            lineages[path] = {
                "path": path,
                "first_turn": node.id,
                "last_turn": node.id,
                "before": before,
                "after": after,
                "turn_ids": [node.id],
                "recoverability": mutation.get("recoverability", "exact"),
                "unavailable_reason": mutation.get("unavailable_reason"),
                "latest_mutation_sequence": mutation.get("mutation_sequence", -1),
                "latest_turn_sequence": getattr(node, "seq", -1),
            }
            continue
        if not _same_state(current["after"], before):
            current["recoverability"] = "unavailable"
            current["unavailable_reason"] = "discontinuous_journal"
        current["last_turn"] = node.id
        current["after"] = after
        current["turn_ids"].append(node.id)
        current["latest_mutation_sequence"] = mutation.get("mutation_sequence", -1)
        current["latest_turn_sequence"] = getattr(node, "seq", -1)
    files = []
    stats_budget = [8 * 1024 * 1024]
    for lineage in lineages.values():
        if _same_state(lineage["before"], lineage["after"]):
            continue
        added, removed, binary, diff_state = _net_stats(
            session_dir,
            lineage["first_turn"],
            lineage["before"],
            lineage["last_turn"],
            lineage["after"],
            stats_budget,
        )
        before_kind = lineage["before"].get("kind")
        after_kind = lineage["after"].get("kind")
        operation = (
            "add" if before_kind == "absent" and after_kind == "regular"
            else "delete" if before_kind == "regular" and after_kind == "absent"
            else "modify"
        )
        files.append({
            "path": lineage["path"],
            "rel": _relative(lineage["path"], root),
            "op": operation,
            "added": added,
            "removed": removed,
            "binary": binary,
            "diff_state": diff_state,
            "recoverability": lineage["recoverability"],
            "unavailable_reason": lineage["unavailable_reason"],
            "turn_ids": lineage["turn_ids"],
            "latest_mutation_sequence": lineage["latest_mutation_sequence"],
            "latest_turn_sequence": lineage["latest_turn_sequence"],
        })
    return _scope_payload(
        "branch", "mutation_journal", files,
        head_id=index.head_id,
        blockers=ownership["blockers"],
        linked_impacts=ownership["linked"],
        category=category,
        query=query,
        sort=sort,
        _snapshot_owner={"session_id": session_id},
        _snapshot_basis=[{
            "path": lineage["path"],
            "before": lineage["before"],
            "after": lineage["after"],
            "turn_ids": lineage["turn_ids"],
        } for lineage in lineages.values()],
    )


__all__ = [
    name for name in globals()
    if name.startswith("_") and not name.startswith("__")
]
