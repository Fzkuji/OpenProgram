"""SQLite 持久化 — context_snapshots 表的 CRUD.

只管"存这个 snapshot" / "读这个 snapshot" / "找最新 snapshot"。生成
snapshot 的算法不在这里, 在 generator.py。

存储格式:
  items_json 列里放 list[ContextItem.to_dict()] 的 JSON 串, 但每个
  item 的 ``rendered`` 字段被替成 ``rendered_hash`` (16 字符 SHA1).
  实际内容在 ``context_blobs`` 表按 hash 共享 — 详见
  ``blob_store.py``.

  好处: 老 snapshot 的 item 大多 locked, rendered 跨 snapshot 不变,
  实际只占一份 blob 行. 100 snapshot × 100 item 总 blob 数 ≈ 几百行.

refcount:
  save_snapshot 时, 每个 item.rendered_hash 调 retain(+1). 后续
  删 snapshot 时回 retain(-1), gc_zero_refcount 清掉 refcount=0
  的 blob 行.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from .types import ContextItem, Snapshot
from . import blob_store as _blob


_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_snapshots (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    parent_id       TEXT,
    created_at      REAL NOT NULL,
    head_node_id    TEXT NOT NULL,
    rules_version   TEXT NOT NULL,
    total_tokens    INTEGER NOT NULL,
    items_json      TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_snapshots_session
    ON context_snapshots(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_parent
    ON context_snapshots(parent_id);
"""


def init_schema(db_path: str | Path) -> None:
    """跑一次确保表存在。重复跑无副作用 (IF NOT EXISTS)."""
    db_path = Path(db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()
    # blob 表跟 snapshot 表平级共存, 一并初始化
    _blob.init_schema(db_path)


def save_snapshot(db_path: str | Path, snap: Snapshot) -> None:
    """写一行 snapshot, rendered 内容 intern 进 blob 表.

    Snapshot 不可变 — 写后任何字段都不再 update.
    """
    # Step 1: intern 每个 item 的 rendered 进 blob 表, 替成 hash
    items_payload: list[dict] = []
    hashes_to_retain: list[str] = []
    for item in snap.items:
        d = item.to_dict()
        rendered = d.pop("rendered", "") or ""
        if rendered:
            h = _blob.intern(db_path, rendered)
            d["rendered_hash"] = h
            hashes_to_retain.append(h)
        else:
            d["rendered_hash"] = ""
        items_payload.append(d)

    items_json = json.dumps(items_payload, ensure_ascii=False, default=str)

    # Step 2: 写 snapshot 行
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT INTO context_snapshots
               (id, session_id, parent_id, created_at, head_node_id,
                rules_version, total_tokens, items_json, summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snap.id, snap.session_id, snap.parent_id, snap.created_at,
                snap.head_node_id, snap.rules_version, snap.total_tokens,
                items_json, snap.summary,
            ),
        )
        # Step 3: refcount +1 for each blob this snapshot references.
        # 在同一 transaction 里做, 失败时 snapshot 也回滚.
        for h in hashes_to_retain:
            conn.execute(
                "UPDATE context_blobs SET refcount = refcount + 1 WHERE hash = ?",
                (h,),
            )
        conn.commit()


def load_snapshot(db_path: str | Path, snap_id: str) -> Optional[Snapshot]:
    """按 id 读一个 snapshot。None = 不存在."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM context_snapshots WHERE id = ?", (snap_id,),
        ).fetchone()
    return _row_to_snapshot_with_db(row, db_path) if row else None


def load_latest_snapshot(
    db_path: str | Path, session_id: str,
) -> Optional[Snapshot]:
    """这个 session 最新的 snapshot。None = 还没生成过."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM context_snapshots WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    return _row_to_snapshot_with_db(row, db_path) if row else None


def list_snapshots(
    db_path: str | Path, session_id: str, *, limit: int = 50,
) -> list[Snapshot]:
    """session 的 snapshot 列表, 最新在前. UI timeline 用."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM context_snapshots WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [_row_to_snapshot_with_db(r, db_path) for r in rows]


def _row_to_snapshot_with_db(row: sqlite3.Row, db_path: str | Path) -> Snapshot:
    """读 snapshot 行, 同时把 rendered_hash 回填成实际内容.

    blob 查询合并到一个 IN(...) 一次拉. 缺失的 hash (blob 被 GC 误删时)
    rendered 留空字符串, 不抛.
    """
    items_raw = json.loads(row["items_json"])
    needed = [d["rendered_hash"] for d in items_raw if d.get("rendered_hash")]
    blob_map: dict[str, str] = {}
    if needed:
        placeholders = ",".join("?" for _ in needed)
        with sqlite3.connect(str(db_path)) as conn:
            for h, content in conn.execute(
                f"SELECT hash, content FROM context_blobs WHERE hash IN ({placeholders})",
                needed,
            ):
                blob_map[h] = content
    items: list[ContextItem] = []
    for d in items_raw:
        h = d.pop("rendered_hash", "") or ""
        # 回填 rendered. blob 缺失时给空字符串 (不抛, GC race condition
        # 时可能短暂出现).
        d["rendered"] = blob_map.get(h, "")
        items.append(ContextItem.from_dict(d))
    return Snapshot(
        id=row["id"],
        session_id=row["session_id"],
        parent_id=row["parent_id"],
        created_at=row["created_at"],
        head_node_id=row["head_node_id"],
        rules_version=row["rules_version"],
        total_tokens=row["total_tokens"],
        items=items,
        summary=row["summary"] or "",
    )
