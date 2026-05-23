"""Context blob store — 内容寻址的 rendered 文本缓存.

每个 ContextItem 的 rendered 字符串可能很长 (整段 tool result), 但
跨 snapshot 大量重复 (老节点 state lock 之后内容不变, 每个新 snapshot
都引用同一份). 直接把所有 rendered 序列化进 items_json 会让 100 个
snapshot 占好几百 MB.

方案: rendered 抽到独立表 ``context_blobs``, 按 SHA1(rendered) 共享,
ContextItem 只存 rendered_hash. 90%+ 的 item 在相邻 snapshot 间能
dedup, 实际 blob 行数 ≈ session 内 DAG 节点 + summary 数, 跟
snapshot 数无关.

refcount 维护:
  - save_snapshot 时, 增量计算: 比对新 snapshot vs 旧 snapshot 的
    hash 集合, 新增的 +1, 移除的 -1.
  - 删 session 级联清 blob (cascade by foreign key + 周期性 vacuum).

接口故意小, 集中在两个函数:
  - ``intern(db_path, content)`` -> hash, 写表 refcount=0.
  - ``release(db_path, hash)`` -> refcount -1, =0 时删行.

调用方 (store.py save_snapshot) 负责管 refcount.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_blobs (
    hash      TEXT PRIMARY KEY,
    content   TEXT NOT NULL,
    refcount  INTEGER NOT NULL DEFAULT 0
);
"""


def init_schema(db_path: str | Path) -> None:
    db_path = Path(db_path).expanduser()
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def hash_content(content: str) -> str:
    """SHA1 截 16 字符 — 跟 git 风格一致, 碰撞概率可忽略."""
    return hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]


def intern(db_path: str | Path, content: str) -> str:
    """Write content if not present, return its hash.

    Caller 负责后续 refcount 调整 (调 retain).
    """
    h = hash_content(content)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO context_blobs (hash, content, refcount) "
            "VALUES (?, ?, 0)",
            (h, content),
        )
        conn.commit()
    return h


def get(db_path: str | Path, blob_hash: str) -> Optional[str]:
    """读 blob 内容, 不存在返回 None."""
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT content FROM context_blobs WHERE hash = ?", (blob_hash,),
        ).fetchone()
    return row[0] if row else None


def retain(db_path: str | Path, blob_hash: str, delta: int = 1) -> None:
    """refcount += delta. delta=+1 一般在 snapshot 创建时,
    delta=-1 在 snapshot 删除 / 老化时."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE context_blobs SET refcount = refcount + ? WHERE hash = ?",
            (delta, blob_hash),
        )
        conn.commit()


def gc_zero_refcount(db_path: str | Path) -> int:
    """删 refcount <= 0 的 blob. 返回删了多少行.

    周期性调用 (e.g. 启动时 + 删 session 后). 不阻塞主路径.
    """
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute(
            "DELETE FROM context_blobs WHERE refcount <= 0"
        )
        conn.commit()
        return cur.rowcount
