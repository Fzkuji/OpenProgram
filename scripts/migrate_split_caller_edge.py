"""One-shot migration: split the legacy `predecessor` edge into two
independent edges, `predecessor` (conversation) and `caller` (sub-call).

Background
----------
Older rows wrote sub-call relationships (assistant → tool / FunctionCall
/ sub-LLM) into the same `predecessor` column used for conversation
links (user → assistant → user). They also wrote the caller id into
`data_json.metadata.called_by` as a side-channel for downstream code,
but the SQL column itself didn't reflect it. That overload broke
`list_branches` (every tool became a leaf), `_graph_layout` (tool rows
extended trunk depth), and `sessions.last_node_id` (HEAD landed on
tool rows after worker restart mid-turn).

The new schema has an extra `caller` column. Each node sets at most
one of {predecessor, caller}. This script reads each existing row,
decides which edge it belongs on, and rewrites the columns.

Decision rule
-------------
A row is a sub-call iff its `data_json.called_by` is non-empty OR
`data_json.metadata.called_by` is non-empty. In that case we set
`caller` to that value and clear `predecessor`. Otherwise we leave
`predecessor` as-is and set `caller` to NULL.

Idempotent: re-running it on an already-migrated DB is a no-op.

Usage
-----
    python scripts/migrate_split_caller_edge.py [--db PATH] [--dry-run]

Defaults to the location returned by `openprogram.paths.get_state_dir()
/ "dag_sessions.sqlite"`.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _default_db_path() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    from openprogram.paths import get_state_dir
    return Path(get_state_dir()) / "dag_sessions.sqlite"


def migrate(db_path: Path, dry_run: bool = False) -> dict[str, int]:
    """Walk every row in `nodes` and split predecessor into caller / pred.

    Returns a stats dict (`scanned`, `to_caller`, `kept_pred`,
    `cleared`, `already_set`).
    """
    stats = {
        "scanned": 0,
        "to_caller": 0,
        "kept_pred": 0,
        "cleared": 0,
        "already_set": 0,
    }
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes)")}
        if "caller" not in cols:
            raise RuntimeError(
                f"{db_path}: `nodes.caller` column missing — open the DB "
                "through openprogram first (init_db migrates the schema)."
            )
        rows = conn.execute(
            "SELECT id, predecessor, caller, data_json FROM nodes"
        ).fetchall()
        for r in rows:
            stats["scanned"] += 1
            if r["caller"]:
                stats["already_set"] += 1
                continue
            try:
                data = json.loads(r["data_json"])
            except (json.JSONDecodeError, TypeError):
                data = {}
            meta = data.get("metadata") or {}
            caller = data.get("called_by") or meta.get("called_by") or None
            if not caller:
                stats["kept_pred"] += 1
                continue
            stats["to_caller"] += 1
            if r["predecessor"]:
                stats["cleared"] += 1
            if not dry_run:
                conn.execute(
                    "UPDATE nodes SET caller = ?, predecessor = NULL "
                    "WHERE id = ?",
                    (caller, r["id"]),
                )
        if not dry_run:
            conn.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=None,
                    help="Path to dag_sessions.sqlite "
                         "(defaults to ~/.agentic/dag_sessions.sqlite).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change without writing.")
    args = ap.parse_args()
    db_path = args.db or _default_db_path()
    if not db_path.exists():
        print(f"{db_path}: not found", file=sys.stderr)
        sys.exit(1)
    print(f"Migrating {db_path}{' (dry run)' if args.dry_run else ''}")
    stats = migrate(db_path, dry_run=args.dry_run)
    for k, v in stats.items():
        print(f"  {k:14s} {v}")


if __name__ == "__main__":
    main()
