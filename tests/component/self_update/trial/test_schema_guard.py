"""A trial fails only for what a rollback could not undo."""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from openprogram.operations.self_update.trial import schema_guard


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "store.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO store_meta VALUES ('min_reader_schema', '3');
        CREATE TABLE items (id TEXT PRIMARY KEY, body TEXT NOT NULL, score INTEGER);
        INSERT INTO items VALUES ('a', 'one', 1), ('b', 'two', 2);
        PRAGMA user_version = 5;
    """)
    connection.close()
    return path


def _execute(path: Path, script: str) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(script)
    connection.close()


def _problems(path: Path, change: str | None = None, baseline=None) -> list[str]:
    before = schema_guard.snapshot(path)
    if change:
        _execute(path, change)
    return schema_guard.compare_database("store.db", path, before, baseline)


def test_an_additive_migration_passes(database):
    assert _problems(database, """
        ALTER TABLE items ADD COLUMN extra TEXT;
        CREATE TABLE more (id INTEGER PRIMARY KEY);
        INSERT INTO items VALUES ('c', 'three', 3, NULL);
        PRAGMA user_version = 6;
    """) == []


@pytest.mark.parametrize(("change", "expected"), [
    ("DROP TABLE items", "table items was dropped"),
    ("ALTER TABLE items DROP COLUMN score", "column items.score was dropped"),
    ("ALTER TABLE items RENAME COLUMN body TO text", "column items.body was dropped"),
    ("UPDATE store_meta SET value = '6' WHERE key = 'min_reader_schema'", "min_reader_schema went from 3 to 6"),
    ("UPDATE items SET body = 'changed' WHERE id = 'a'", "rows of items were rewritten"),
    ("PRAGMA user_version = 4", "user_version went from 5 to 4"),
])
def test_what_a_rollback_cannot_undo_fails(database, change, expected):
    problems = _problems(database, change)
    assert any(expected in problem for problem in problems), problems


def test_a_changed_type_fails(database):
    problems = _problems(database, """
        CREATE TABLE items_new (id TEXT PRIMARY KEY, body BLOB NOT NULL, score INTEGER);
        INSERT INTO items_new SELECT * FROM items;
        DROP TABLE items;
        ALTER TABLE items_new RENAME TO items;
    """)
    assert any("items.body changed from TEXT to BLOB" in problem for problem in problems), problems


def test_rows_the_old_version_also_changed_do_not_count(database):
    baseline = schema_guard.snapshot(database)
    _execute(database, "UPDATE items SET score = 10 WHERE id = 'a'")  # the old version's own run
    assert _problems(database, "UPDATE items SET score = 11 WHERE id = 'a'", baseline) == []
    assert _problems(database, "UPDATE items SET score = 11 WHERE id = 'b'", baseline) != []


def test_a_new_revision_may_rewrite_bookkeeping(database):
    assert _problems(database, "INSERT INTO store_meta VALUES ('normalised', 'abc')") == []


def test_a_missing_database_fails(database, tmp_path):
    before = schema_guard.snapshot(database)
    database.unlink()
    assert "gone" in schema_guard.compare_database("store.db", database, before)[0]


def _log(state: Path, text: str) -> Path:
    path = state / "projects" / "p" / "chats" / "s" / "main.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_a_conversation_log_may_only_grow(tmp_path):
    log = _log(tmp_path, '{"a":1}\n')
    before = schema_guard.snapshot_logs(tmp_path)
    log.write_text('{"a":1}\n{"b":2}\n')
    assert schema_guard.compare_logs(tmp_path, before) == []
    log.write_text('{"a":"rewritten"}\n{"b":2}\n')
    assert "main.jsonl" in schema_guard.compare_logs(tmp_path, before)[0]
    log.unlink()
    assert schema_guard.compare_logs(tmp_path, before)


def test_a_log_the_old_version_rewrote_proves_nothing(tmp_path):
    log = _log(tmp_path, '{"long":"line one"}\n')
    baseline = schema_guard.snapshot_logs(tmp_path)
    log.write_text('{"a":1}\n')
    before = schema_guard.snapshot_logs(tmp_path)
    log.write_text('{"b":2}\n')
    assert schema_guard.compare_logs(tmp_path, before, baseline) == []
