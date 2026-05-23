"""Snapshot 持久化 — git-backed, 走 SessionStore.

每个 snapshot 一份 JSON, 落在 session repo 的 ``context/snapshots/<snap_id>.json``;
同时把"最新"那一份镜像到 ``context/snapshot.json`` (单文件入口, 跟
``context/messages.json`` 同款约定 — 工作树反映当前 LLM 视角).

跟旧 SQLite 版的差别:
  * 不再有 blob dedup 表 (``blob_store.py`` 已删).
  * git 本身就是 content-addressed (loose object 自动 dedup), 重复的
    rendered 字符串在 .git/objects/ 里只占一份, 不需要应用层做 hash 表.
  * snapshot 历史靠 git log 看 (每个 turn 一个 commit, 包含 context/snapshot.json
    的更新), 不需要单独表存 parent chain.

API 形状保持不变 (init_schema 现在 no-op, save_snapshot / load_snapshot /
load_latest_snapshot / list_snapshots 接受 SessionStore 替代 db_path).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from .types import ContextItem, Snapshot

if TYPE_CHECKING:
    from openprogram.store import SessionStore


def init_schema(store_or_path: Any) -> None:
    """No-op in the git era. Kept for back-compat callers that used to
    bootstrap SQLite tables. SessionStore lazily inits repos as needed."""
    return None


def _get_git(store: "SessionStore", session_id: str):
    """Return the GitSession for ``session_id`` or None if the session
    doesn't exist yet. Creates the repo on demand (this is called from
    write paths)."""
    pair = store._open(session_id, create_if_missing=True)
    return pair[0] if pair else None


def _snap_dir(git) -> Path:
    d = git.path / "context" / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_snapshot(store: "SessionStore", snap: Snapshot) -> None:
    """Persist a Snapshot. Writes two files:

      * ``context/snapshots/<id>.json``  — immutable per-snap record
      * ``context/snapshot.json``         — mirror of the latest snap

    Both live inside the session's git repo so a turn-end commit picks
    them up together (along with ``messages.json`` / ``meta.json``).
    """
    git = _get_git(store, snap.session_id)
    if git is None:
        return
    payload = snap.to_dict()
    # Per-snap file: durable, point of truth for that snap_id.
    snap_path = _snap_dir(git) / f"{snap.id}.json"
    snap_path.write_text(json.dumps(payload, ensure_ascii=False, default=str))
    # Mirror: latest snapshot, single file. Frontend timeline reads
    # this for "current state"; per-snap history goes through list_snapshots.
    git.write_context_file("snapshot.json", payload)


def load_snapshot(store: "SessionStore", snap_id: str, *, session_id: Optional[str] = None) -> Optional[Snapshot]:
    """Read a snapshot by id.

    ``session_id`` makes the lookup O(1). Without it we scan all sessions —
    used to keep the legacy WS action working for callers that only had
    a snap id. Avoid the scan if you can.
    """
    if session_id:
        return _load_snapshot_in_session(store, session_id, snap_id)
    for sess in store.list_sessions(limit=10**9):
        snap = _load_snapshot_in_session(store, sess["id"], snap_id)
        if snap is not None:
            return snap
    return None


def _load_snapshot_in_session(store: "SessionStore", session_id: str, snap_id: str) -> Optional[Snapshot]:
    pair = store._open(session_id)
    if not pair:
        return None
    git, _idx = pair
    p = _snap_dir(git) / f"{snap_id}.json"
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return _payload_to_snapshot(payload)


def load_latest_snapshot(store: "SessionStore", session_id: str) -> Optional[Snapshot]:
    """Read ``context/snapshot.json`` (the latest-mirror file)."""
    pair = store._open(session_id)
    if not pair:
        return None
    git, _idx = pair
    payload = git.read_context_file("snapshot.json")
    if not payload:
        return None
    return _payload_to_snapshot(payload)


def list_snapshots(store: "SessionStore", session_id: str, *, limit: int = 50) -> list[Snapshot]:
    """Snapshots for a session, newest first.

    Sort key: ``created_at`` from the snapshot payload itself (more
    reliable than file mtime when repos get copied around).
    """
    pair = store._open(session_id)
    if not pair:
        return []
    git, _idx = pair
    sdir = git.path / "context" / "snapshots"
    if not sdir.exists():
        return []
    snaps: list[Snapshot] = []
    for fpath in sdir.glob("*.json"):
        try:
            payload = json.loads(fpath.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        snap = _payload_to_snapshot(payload)
        if snap:
            snaps.append(snap)
    snaps.sort(key=lambda s: s.created_at or 0, reverse=True)
    return snaps[:limit]


def _payload_to_snapshot(payload: dict) -> Snapshot:
    items = [ContextItem.from_dict(d) for d in (payload.get("items") or [])]
    return Snapshot(
        id=payload["id"],
        session_id=payload["session_id"],
        parent_id=payload.get("parent_id"),
        created_at=float(payload.get("created_at") or 0),
        head_node_id=payload.get("head_node_id") or "",
        rules_version=payload.get("rules_version") or "",
        total_tokens=int(payload.get("total_tokens") or 0),
        items=items,
        summary=payload.get("summary") or "",
    )
