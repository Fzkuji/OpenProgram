"""Compare the copied data before and after the candidate ran on it.

A rollback changes code only, so a candidate may only add to the data it
finds. Dropped tables or columns, changed types, a raised ``min_reader_schema``
or rewritten rows in a database, or a changed line in a log, fail the trial
unless a commit carries the trailer ``Destructive-Migration: <name>``.

Run state has no database: it is the conversations' append-only logs and the
run logs beside them, so a line that was there has to stay there.

Rows are sampled. Rows the old version itself changed when it ran on the same
copy are expected to change, so they do not count.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

SAMPLE_ROWS = 100
# Key-value bookkeeping that every start of a new revision legitimately rewrites.
_BOOKKEEPING = frozenset({"store_meta", "sqlite_sequence", "sqlite_stat1"})


@dataclass
class TableSnapshot:
    columns: dict[str, str]
    key_columns: tuple[str, ...]
    rows: dict[tuple, str] = field(default_factory=dict)


@dataclass
class DatabaseSnapshot:
    user_version: int
    min_reader_schema: int | None
    tables: dict[str, TableSnapshot] = field(default_factory=dict)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    connection.text_factory = lambda raw: raw.decode("utf-8", "surrogateescape")
    return connection


def _text(value: Any) -> str:
    return str(value)


def _digest(values: tuple) -> str:
    hasher = hashlib.sha256()
    for value in values:
        raw = value if isinstance(value, bytes) else repr(value).encode("utf-8")
        hasher.update(len(raw).to_bytes(8, "big"))
        hasher.update(raw)
    return hasher.hexdigest()


def _floor(connection: sqlite3.Connection) -> int | None:
    try:
        row = connection.execute("SELECT value FROM store_meta WHERE key = 'min_reader_schema'").fetchone()
        return None if row is None else int(_text(row[0]))
    except (sqlite3.Error, ValueError):
        return None


def _rows(connection: sqlite3.Connection, table: str, columns: list[str], key: tuple[str, ...],
          keys: list[tuple] | None) -> dict[tuple, str]:
    """Digests of the first and last rows, or of exactly the rows named by ``keys``."""
    names = ",".join(_quote(name) for name in columns)
    selector = [_quote(name) for name in key] or ["rowid"]
    source = f"SELECT {','.join(selector)}, {names} FROM {_quote(table)}"
    size = len(selector)
    rows: list[tuple] = []
    if keys is None:
        ascending = ",".join(selector)
        descending = ",".join(f"{part} DESC" for part in selector)
        rows += connection.execute(f"{source} ORDER BY {ascending} LIMIT {SAMPLE_ROWS}").fetchall()
        rows += connection.execute(f"{source} ORDER BY {descending} LIMIT {SAMPLE_ROWS}").fetchall()
    else:
        where = " AND ".join(f"{part} IS ?" for part in selector)
        for wanted in keys:
            try:
                found = connection.execute(f"{source} WHERE {where}", wanted).fetchone()
            except (sqlite3.Error, UnicodeEncodeError):
                continue
            if found is not None:
                rows.append(found)
    return {tuple(row[:size]): _digest(tuple(row[size:])) for row in rows}


def _user_tables(connection: sqlite3.Connection) -> list[str]:
    names = [
        _text(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    virtual = {
        _text(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND upper(sql) LIKE 'CREATE VIRTUAL%'"
        )
    }
    return [name for name in names if name not in virtual and not any(name.startswith(f"{v}_") for v in virtual)]


def snapshot(path: Path) -> DatabaseSnapshot:
    connection = _connect(path)
    try:
        result = DatabaseSnapshot(int(connection.execute("PRAGMA user_version").fetchone()[0]), _floor(connection))
        for table in _user_tables(connection):
            info = connection.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
            columns = {_text(row[1]): _text(row[2]).upper() for row in info}
            key = tuple(_text(row[1]) for row in sorted(info, key=lambda item: item[5]) if row[5])
            entry = TableSnapshot(columns, key)
            if table not in _BOOKKEEPING:
                entry.rows = _rows(connection, table, list(columns), key, None)
            result.tables[table] = entry
        return result
    finally:
        connection.close()


def snapshot_all(state_dir: Path) -> dict[str, DatabaseSnapshot]:
    from .data_copy import is_database

    return {path.name: snapshot(path) for path in sorted(Path(state_dir).iterdir())
            if path.is_file() and is_database(path)}


def _reread(path: Path, before: DatabaseSnapshot, table: str) -> dict[tuple, str]:
    entry = before.tables[table]
    connection = _connect(path)
    try:
        return _rows(connection, table, list(entry.columns), entry.key_columns, list(entry.rows))
    finally:
        connection.close()


def compare_database(name: str, path: Path, before: DatabaseSnapshot,
                     baseline: DatabaseSnapshot | None = None) -> list[str]:
    """Plain sentences for everything the candidate did that a rollback could not undo."""
    if not Path(path).is_file():
        return [f"{name}: the database is gone"]
    after = snapshot(path)
    problems: list[str] = []
    if after.user_version < before.user_version:
        problems.append(f"{name}: user_version went from {before.user_version} to {after.user_version}")
    if (before.min_reader_schema or 0) < (after.min_reader_schema or 0):
        problems.append(f"{name}: min_reader_schema went from {before.min_reader_schema} to {after.min_reader_schema}")
    for table, entry in before.tables.items():
        present = after.tables.get(table)
        if present is None:
            problems.append(f"{name}: table {table} was dropped")
            continue
        for column, declared in entry.columns.items():
            if column not in present.columns:
                problems.append(f"{name}: column {table}.{column} was dropped")
            elif present.columns[column] != declared:
                problems.append(f"{name}: column {table}.{column} changed from {declared} to {present.columns[column]}")
        if table in _BOOKKEEPING or not entry.rows or any(column not in present.columns for column in entry.columns):
            continue
        current = _reread(path, before, table)
        expected_to_change = set()
        if baseline is not None and table in baseline.tables:
            # Same rows, compared in the snapshot taken before the old version ran.
            expected_to_change = {key for key, digest in entry.rows.items()
                                  if baseline.tables[table].rows.get(key) != digest}
        rewritten = sum(1 for key, digest in entry.rows.items()
                        if key not in expected_to_change and key in current and current[key] != digest)
        if rewritten:
            problems.append(f"{name}: {rewritten} of the sampled rows of {table} were rewritten")
    return problems


def compare_all(state_dir: Path, before: dict[str, DatabaseSnapshot],
                baseline: dict[str, DatabaseSnapshot] | None = None) -> list[str]:
    problems: list[str] = []
    for name, snapshotted in before.items():
        problems += compare_database(name, Path(state_dir) / name, snapshotted, (baseline or {}).get(name))
    return problems


@dataclass(frozen=True)
class LogSnapshot:
    size: int
    digest: str


def _hash_of(path: Path, size: int | None = None) -> str:
    hasher = hashlib.sha256()
    left = size
    with open(path, "rb") as stream:
        while left is None or left > 0:
            chunk = stream.read(1 << 20 if left is None else min(1 << 20, left))
            if not chunk:
                break
            hasher.update(chunk)
            if left is not None:
                left -= len(chunk)
    return hasher.hexdigest()


def _append_only_logs(state_dir: Path) -> list[Path]:
    """Every log run state and conversations are rebuilt from."""
    from openprogram.runtime.execution.store.run_tables import RunLogFiles

    projects = Path(state_dir) / "projects"
    found = [path for pattern in ("*/chats/*/main.jsonl", "*/chats/*/agents/*.jsonl", "*/chats/*/functions/*.jsonl")
             for path in sorted(projects.glob(pattern))]
    run_log = RunLogFiles(Path(state_dir))
    return found + [path for path in (run_log.global_path, run_log.index_path) if path.is_file()]


def snapshot_logs(state_dir: Path) -> dict[str, LogSnapshot]:
    """The copied logs, which hold what no database does."""
    found = {}
    for path in _append_only_logs(state_dir):
        found[path.relative_to(state_dir).as_posix()] = LogSnapshot(path.stat().st_size, _hash_of(path))
    return found


def compare_logs(state_dir: Path, before: dict[str, LogSnapshot],
                 baseline: dict[str, LogSnapshot] | None = None) -> list[str]:
    """A log only grows: a line the old version wrote must still be there, unchanged, after the candidate."""
    problems: list[str] = []
    for name, seen in before.items():
        earlier = (baseline or {}).get(name)
        if earlier is not None and (earlier.size > seen.size or (earlier.size == seen.size and earlier.digest != seen.digest)):
            continue  # The old version rewrote this log itself, so a rewrite proves nothing.
        path = Path(state_dir) / name
        try:
            kept = path.stat().st_size >= seen.size and _hash_of(path, seen.size) == seen.digest
        except OSError:
            kept = False
        if not kept:
            problems.append(f"{name}: lines already written were changed or removed")
    return problems


def to_evidence(snapshots: dict[str, DatabaseSnapshot]) -> str:
    return json.dumps({
        name: {"user_version": item.user_version, "min_reader_schema": item.min_reader_schema,
               "tables": {table: {"columns": entry.columns, "sampled_rows": len(entry.rows)}
                          for table, entry in item.tables.items()}}
        for name, item in snapshots.items()
    }, sort_keys=True)
