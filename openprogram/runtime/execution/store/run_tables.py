"""Run state: held in memory, appended to each session's log.

The tables are plain Python dictionaries inside the process that owns the
profile. Every committed change is one line in the log of the session it
belongs to, and a session's tables are rebuilt from those lines the first time
something asks for them. Nothing else is stored: there is no database file.

Execution events are the live feed to the interface and stay in memory, except
the restart intents and their answers: a restart is exactly when memory goes.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.parse
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .._schema import TABLES
from ..model import TERMINAL_EXECUTION_STATUSES

#: Tables whose rows outlive the process, with their primary key columns.
LOGGED_TABLES = {
    "runs": ("run_id",),
    "executions": ("execution_id",),
    "commands": ("command_id",),
    "attempts": ("attempt_id",),
    "effects": ("effect_id",),
    "checkpoints": ("checkpoint_id",),
    "execution_inputs": ("execution_id",),
    "execution_agent_turn_inputs": ("execution_id",),
    "execution_waits": ("wait_id",),
    "execution_stop_marks": ("session_id", "client_request_id"),
    "execution_resource_intents": ("intent_id",),
    "execution_audit_events": ("sequence",),
    "execution_state_blobs": ("execution_id", "ref"),
    "execution_state_blob_refs": ("execution_id", "ref", "name"),
    "job_admissions": ("admission_id",),
    "job_finalizations": ("job_id",),
    "usage_reservations": ("reservation_id",),
}
#: Workflow revisions are not tied to a session; they share one global log.
GLOBAL_TABLES = {
    "revisions": ("revision_id",),
    "revision_artifacts": ("artifact_ref",),
    "revision_drafts": ("draft_id",),
    "revision_validations": ("validation_id",),
    "revision_approvals": ("approval_id",),
    "revision_manifests": ("manifest_id",),
    "budget_scopes": ("budget_scope_id",),
}
#: Tables that live only as long as the process, with their primary key columns.
MEMORY_TABLES = {
    "execution_events": ("sequence",),
}
#: Events a new process must still see; they keep their sequence in the log.
_RESTART_REQUESTED = "execution.restart.requested"
_RESTART_SETTLED = "execution.restart.settled"
LOGGED_EVENT_KINDS = frozenset({_RESTART_REQUESTED, _RESTART_SETTLED})
#: Lines in older logs name a removed row by these columns.
OLDER_LOG_KEYS = {"execution_state_blob_refs": ("execution_id", "ref", "name")}
_GLOBAL = "\0global"


def _encode(value: Any) -> Any:
    return {"$bytes": base64.b64encode(value).decode("ascii")} if isinstance(value, (bytes, memoryview)) else value


def _plain(value: Any) -> Any:
    return bytes(value) if isinstance(value, memoryview) else value


def _decode(value: Any) -> Any:
    return base64.b64decode(value["$bytes"]) if isinstance(value, dict) and "$bytes" in value else value


def _append_line(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(descriptor, data)
    finally:
        os.close(descriptor)


def _line_execution(line: dict) -> Any:
    """The execution a log line belongs to, when the line names one."""
    values = line.get("r")
    if values is not None:
        return values.get("execution_id") or values.get("job_id")
    table = TABLES.get(line.get("t"))
    return line["gone"][0] if table is not None and table.key[0] in {"execution_id", "job_id"} else None


def _line_run(line: dict) -> Any:
    values = line.get("r")
    return values.get("run_id") if values is not None else (line.get("gone") or [None])[0]


def _read_lines(path: Path) -> Iterator[dict]:
    try:
        raw = path.read_bytes()
    except OSError:
        return
    for line in raw.split(b"\n"):
        if line:
            try:
                yield json.loads(line)
            except ValueError:
                continue  # A writer stopped halfway through its last line.


def _file_name(session_id: str) -> str:
    return urllib.parse.quote(session_id, safe="")


class RunLogFiles:
    """One file per session under ``root``; the shape every run log has."""

    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def active_dir(self) -> Path:
        return self.root / "active"

    def function_log_directory(self, session_id: str) -> Path:
        del session_id
        return self.root / "functions"

    @property
    def index_path(self) -> Path:
        return self.root / "run_index.jsonl"

    @property
    def global_path(self) -> Path:
        return self.root / "revisions.jsonl"

    def _session_path(self, session_id: str) -> Path:
        return self.root / "sessions" / f"{_file_name(session_id)}.jsonl"

    def append(self, session_id: str, rows: list[dict]) -> None:
        _append_line(self._session_path(session_id), {"rows": rows})

    def read(self, session_id: str) -> Iterable[list[dict]]:
        for line in _read_lines(self._session_path(session_id)):
            yield line.get("rows") or []

    def append_global(self, rows: list[dict]) -> None:
        _append_line(self.global_path, {"rows": rows})

    def read_global(self) -> Iterable[list[dict]]:
        for line in _read_lines(self.global_path):
            yield line.get("rows") or []

    def read_index(self) -> dict[str, str]:
        return {str(item["e"]): str(item["s"]) for item in _read_lines(self.index_path)}

    def add_to_index(self, execution_id: str, session_id: str) -> None:
        _append_line(self.index_path, {"e": execution_id, "s": session_id})

    def set_active(self, session_id: str, active: bool) -> None:
        marker = self.active_dir / _file_name(session_id)
        if active:
            self.active_dir.mkdir(parents=True, exist_ok=True)
            marker.touch()
        else:
            marker.unlink(missing_ok=True)

    def active_sessions(self) -> list[str]:
        try:
            return [urllib.parse.unquote(path.name) for path in self.active_dir.iterdir()]
        except OSError:
            return []

    def session_gone(self, session_id: str) -> bool:
        """Whether the session's log no longer exists, so nothing of it can be unfinished."""
        return not self._session_path(session_id).exists()


class SessionRunLog(RunLogFiles):
    """The profile's run log: every session's rows live in its own log."""

    def __init__(self, root: Path):
        super().__init__(root)
        self._compared_with = None
        self._own_sessions = None

    def _sessions(self):
        """The process's session store, when it is this profile's."""
        from openprogram.state.store.session import default_store
        from openprogram.state.store.session.session_store.service import SessionStore

        process_store = default_store()
        if self._compared_with is not process_store:
            elsewhere = not process_store.root_path.resolve().is_relative_to(self.root.resolve())
            self._own_sessions = SessionStore(self.root / "projects") if elsewhere else None
            self._compared_with = process_store
        return self._own_sessions or process_store

    def function_log_directory(self, session_id: str) -> Path:
        return self._sessions()._session_dir(session_id) / "functions"  # noqa: SLF001

    def append(self, session_id: str, rows: list[dict]) -> None:
        self._sessions().append_run_rows(session_id, rows)

    def read(self, session_id: str) -> Iterable[list[dict]]:
        return self._sessions().read_run_rows(session_id)

    def session_gone(self, session_id: str) -> bool:
        """A deleted conversation: its record of the deletion outlives its log."""
        from openprogram.state.store.session.placement import is_deleted

        return is_deleted(self._sessions().root_path, session_id)


class RowConflict(Exception):
    """A row broke a table's uniqueness rule."""


def _sorted_rows(rows: list[dict], order: Any) -> list[dict]:
    """``order`` is a column, ``-column``, several of those, or a key function."""
    if order is None:
        return rows
    if callable(order):
        return sorted(rows, key=order)
    for column in reversed((order,) if isinstance(order, str) else tuple(order)):
        name, reverse = column.lstrip("-"), column.startswith("-")
        rows = sorted(rows, key=lambda row: (row[name] is not None, row[name] if row[name] is not None else 0),
                      reverse=reverse)
    return rows


def _is_many(value: Any) -> bool:
    return isinstance(value, (list, tuple, set, frozenset))


class TableHandle:
    """What the store classes read and change the tables through.

    ``equals`` filters on columns; a list, tuple or set means "any of these".
    ``where`` is a further test on the row. Rows are plain dicts: a copy, never
    the stored row. A value in ``update`` may be a function of the current row.
    """

    def __init__(self, tables: "RunTables"):
        self.tables = tables

    def rows(self, table: str, *, where=None, order=None, limit=None, offset: int = 0, **equals) -> list[dict]:
        with self.tables.lock:
            found = [dict(row) for row in self.tables.matching(table, equals, where)]
        found = _sorted_rows(found, order)[offset:]
        return found if limit is None else found[:limit]

    def first(self, table: str, *, where=None, order=None, **equals) -> dict | None:
        found = self.rows(table, where=where, order=order, limit=1, **equals)
        return found[0] if found else None

    def get(self, table: str, *key: Any) -> dict | None:
        with self.tables.lock:
            row = self.tables.stored[table].get(tuple(key))
            return None if row is None else dict(row)

    def count(self, table: str, *, where=None, **equals) -> int:
        with self.tables.lock:
            return sum(1 for _ in self.tables.matching(table, equals, where))

    def exists(self, table: str, *, where=None, **equals) -> bool:
        with self.tables.lock:
            return next(iter(self.tables.matching(table, equals, where)), None) is not None

    def insert(self, table: str, values: dict, *, on_conflict: str = "error") -> dict | None:
        """Add a row and return it as stored; None when ``ignore`` found it present."""
        if on_conflict not in {"error", "ignore", "replace"}:
            raise ValueError(on_conflict)
        with self.tables.lock:
            self.tables.require_transaction()
            return self.tables.add_row(table, values, on_conflict)

    def update(self, table: str, values: dict, *, where=None, **equals) -> int:
        with self.tables.lock:
            self.tables.require_transaction()
            chosen = [dict(row) for row in self.tables.matching(table, equals, where)]
            for row in chosen:
                self.tables.change_row(table, row, {
                    name: value(row) if callable(value) else value for name, value in values.items()
                })
            return len(chosen)

    def delete(self, table: str, *, where=None, **equals) -> int:
        with self.tables.lock:
            self.tables.require_transaction()
            chosen = [dict(row) for row in self.tables.matching(table, equals, where)]
            for row in chosen:
                self.tables.remove_row(table, row)
            return len(chosen)

    def close(self) -> None:
        return None

    def commit(self) -> None:
        return None

    def __enter__(self) -> "TableHandle":
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


class RunTables:
    """The tables, the single lock around them, and their logs."""

    def __init__(self, log: RunLogFiles):
        self.log = log
        self.lock = threading.RLock()
        self.handle = TableHandle(self)
        self.stored: dict[str, dict[tuple, dict]] = {table: {} for table in TABLES}
        self._lookup: dict[str, dict[str, dict[Any, set[tuple]]]] = {
            table: {column: {} for column in spec.lookups} for table, spec in TABLES.items()
        }
        self._unique: dict[str, dict[tuple, dict[tuple, tuple]]] = {
            table: {columns: {} for columns in spec.unique} for table, spec in TABLES.items()
        }
        # A cursor saved before a restart must never meet a reused number.
        self._next_sequence: dict[str, int] = {"execution_events": int(time.time() * 1000)}
        self._in_transaction = False
        self._undo: list[tuple[str, tuple, dict | None]] = []
        self._changed: dict[tuple[str, tuple], dict | None] = {}
        self._loaded: set[str] = set()
        # The size is taken first: a line added while reading is read next time.
        self._index_read_at = self._index_size()
        self._index = log.read_index()
        with self.lock:
            self._fold_batches(log.read_global())
            for session_id in log.active_sessions():
                self.ensure_session(session_id)
                # A marker whose session was deleted names nothing to resume.
                if log.session_gone(session_id):
                    self.forget_session(session_id)

    def require_transaction(self) -> None:
        if not self._in_transaction:
            raise RuntimeError("run state changes only inside a transaction, so that they reach the log")

    # The rows

    def matching(self, table: str, equals: dict, where) -> Iterator[dict]:
        rows = self.stored[table]
        lookups = self._lookup[table]
        candidates: Iterable[dict] = rows.values()
        for column, wanted in equals.items():
            if column in lookups:
                index = lookups[column]
                keys = (key for value in wanted for key in index.get(value, ())) if _is_many(wanted) \
                    else index.get(wanted, ())
                candidates = [rows[key] for key in keys]
                break
        tests = [(column, set(wanted) if _is_many(wanted) else None, wanted) for column, wanted in equals.items()]
        for row in candidates:
            if all(row[column] in many if many is not None else row[column] == wanted
                   if wanted is not None else row[column] is None
                   for column, many, wanted in tests) and (where is None or where(row)):
                yield row

    @staticmethod
    def _group_value(table: str, columns: tuple, row: dict) -> tuple:
        """A row's values for a unique group; holding a None means the rule does not apply."""
        condition = (TABLES[table].only_when or {}).get(columns)
        if condition is not None and row[condition[0]] != condition[1]:
            return (None,)
        return tuple(row[column] for column in columns)

    def _link(self, table: str, key: tuple, row: dict) -> None:
        self.stored[table][key] = row
        for column, index in self._lookup[table].items():
            index.setdefault(row[column], set()).add(key)
        for columns, seen in self._unique[table].items():
            value = self._group_value(table, columns, row)
            if None not in value:
                seen[value] = key

    def _unlink(self, table: str, key: tuple) -> dict | None:
        row = self.stored[table].pop(key, None)
        if row is None:
            return None
        for column, index in self._lookup[table].items():
            holders = index.get(row[column])
            if holders is not None:
                holders.discard(key)
                if not holders:
                    del index[row[column]]
        for columns, seen in self._unique[table].items():
            value = self._group_value(table, columns, row)
            if None not in value and seen.get(value) == key:
                del seen[value]
        return row

    def _conflicts(self, table: str, row: dict, ignoring: tuple | None = None) -> list[tuple]:
        spec = TABLES[table]
        key = tuple(row[column] for column in spec.key)
        found = [key] if key != ignoring and key in self.stored[table] else []
        for columns, seen in self._unique[table].items():
            value = self._group_value(table, columns, row)
            holder = None if None in value else seen.get(value)
            if holder is not None and holder != ignoring and holder not in found:
                found.append(holder)
        return found

    def _constraint_text(self, table: str, row: dict, ignoring: tuple | None = None) -> str:
        spec = TABLES[table]
        key = tuple(row[column] for column in spec.key)
        if key != ignoring and key in self.stored[table]:
            columns = spec.key
        else:
            columns = next(c for c, seen in self._unique[table].items()
                           if None not in (v := self._group_value(table, c, row)) and seen.get(v) not in (None, ignoring))
        return "UNIQUE constraint failed: " + ", ".join(f"{table}.{column}" for column in columns)

    def _complete(self, table: str, values: dict) -> dict:
        spec = TABLES[table]
        unknown = set(values) - set(spec.columns)
        if unknown:
            raise ValueError(f"{table} has no column {sorted(unknown)[0]}")
        row = {column: _plain(values[column]) if column in values else default
               for column, default in spec.columns.items()}
        if spec.generated:
            column = spec.key[0]
            if row[column] is None:
                row[column] = self._next_sequence.get(table, 1)
            self._next_sequence[table] = max(self._next_sequence.get(table, 1), row[column] + 1)
        return row

    @staticmethod
    def _check_required(table: str, row: dict) -> None:
        for column in TABLES[table].required:
            if row[column] is None:
                raise RowConflict(f"NOT NULL constraint failed: {table}.{column}")

    def _remember(self, table: str, key: tuple, before: dict | None, added: dict | None = None) -> None:
        self._undo.append((table, key, before))
        event = before if before is not None else added
        if (table in LOGGED_TABLES or table in GLOBAL_TABLES
                or (table == "execution_events" and event is not None and event["kind"] in LOGGED_EVENT_KINDS)):
            # The latest change goes last, and a removed row still names its session.
            earlier = self._changed.pop((table, key), None)
            self._changed[(table, key)] = before if before is not None else earlier

    def add_row(self, table: str, values: dict, on_conflict: str) -> dict | None:
        row = self._complete(table, values)
        self._check_required(table, row)
        conflicts = self._conflicts(table, row)
        if conflicts:
            if on_conflict == "ignore":
                return None
            if on_conflict == "error":
                raise RowConflict(self._constraint_text(table, row))
            for key in conflicts:
                self._remember(table, key, self._unlink(table, key))
        key = tuple(row[column] for column in TABLES[table].key)
        self._remember(table, key, None, added=row)
        self._link(table, key, row)
        return dict(row)

    def change_row(self, table: str, row: dict, values: dict) -> None:
        spec = TABLES[table]
        key = tuple(row[column] for column in spec.key)
        unknown = set(values) - set(spec.columns)
        if unknown:
            raise ValueError(f"{table} has no column {sorted(unknown)[0]}")
        updated = {**self.stored[table][key], **{name: _plain(value) for name, value in values.items()}}
        self._check_required(table, updated)
        if self._conflicts(table, updated, ignoring=key):
            raise RowConflict(self._constraint_text(table, updated, ignoring=key))
        new_key = tuple(updated[column] for column in spec.key)
        self._remember(table, key, self._unlink(table, key))
        if new_key != key:
            self._remember(table, new_key, None)
        self._link(table, new_key, updated)

    def remove_row(self, table: str, row: dict) -> None:
        key = tuple(row[column] for column in TABLES[table].key)
        self._remember(table, key, self._unlink(table, key))

    def _roll_back(self) -> None:
        for table, key, before in reversed(self._undo):
            self._unlink(table, key)
            if before is not None:
                self._link(table, key, before)

    # Reading a session in

    def ensure_session(self, session_id: str) -> None:
        if session_id in self._loaded:
            return
        batches = list(self.log.read(session_id))
        with self.lock:
            if session_id in self._loaded:
                return
            self._loaded.add(session_id)
            self._fold_batches(batches)

    def ensure_execution(self, execution_id: str) -> None:
        """Bring an execution into memory, wherever it was written.

        A scheduled run's own process creates its execution in a session this
        process may have loaded already, so a lookup that misses reads what
        that process added (:meth:`take_up_other_process_turns`)."""
        with self.lock:
            if (execution_id,) in self.stored["executions"]:
                return
        session_id = self._index.get(execution_id) or self._read_newer_index().get(execution_id)
        if session_id is None:
            return
        if session_id not in self._loaded:
            self.ensure_session(session_id)
        else:
            self.take_up_other_process_turns(session_id)

    def _read_newer_index(self) -> dict[str, str]:
        """The index again, when another process has added to it since it was read."""
        size = self._index_size()
        if size != self._index_read_at:
            self._index = {**self._index, **self.log.read_index()}
            self._index_read_at = size
        return self._index

    def _index_size(self) -> int:
        try:
            return self.log.index_path.stat().st_size
        except OSError:
            return 0

    def forget_session(self, session_id: str) -> None:
        """Drop a session's rows from memory; its log still has them.

        A deleted session has no log left, and whatever was unfinished in it
        went with it, so its marker goes too.
        """
        with self.lock:
            self._delete_session_rows(session_id)
            self._loaded.discard(session_id)
            if self.log.session_gone(session_id):
                self.log.set_active(session_id, False)

    def take_up_other_process_turns(self, session_id: str) -> None:
        """Read what another process wrote for its turns here since this process loaded them.

        A log is read once, so a turn that was running in a scheduled run's own
        process when its session was loaded would look unfinished from then on,
        and a turn that process started after the load would not be seen at all.
        Both are read in: the unfinished turns another process holds, and every
        execution and run the log has that this process does not.
        """
        from ..process_owner import owned_by_another_process

        batches = list(self.log.read(session_id))
        ended = {status.value for status in TERMINAL_EXECUTION_STATUSES}
        with self.lock:
            if session_id not in self._loaded:
                return
            theirs = {
                row["execution_id"] for row in self.matching("executions", {"session_id": session_id}, None)
                if row["status"] not in ended
                and owned_by_another_process(json.loads(row["owner_lease_json"] or "{}"))
            }
            # Rows this process wrote are in memory already; what is missing was written elsewhere.
            unseen = {line["r"]["execution_id"] for rows in batches for line in rows
                      if line.get("t") == "executions" and line.get("r")
                      and (line["r"]["execution_id"],) not in self.stored["executions"]}
            new_runs = {line["r"]["run_id"] for rows in batches for line in rows
                        if line.get("t") == "runs" and line.get("r")
                        and (line["r"]["run_id"],) not in self.stored["runs"]}
            if theirs or unseen or new_runs:
                for rows in batches:
                    for line in rows:
                        if (_line_execution(line) in theirs | unseen
                                or (line.get("t") == "runs" and _line_run(line) in new_runs)):
                            self._apply(line)
            for execution_id in unseen:
                self._index.setdefault(execution_id, session_id)
            self._update_active({session_id})

    def recorded_ending(self, session_id: str, execution_id: str) -> dict | None:
        """The last row of an execution's log lines in a terminal state, if any."""
        ended = {status.value for status in TERMINAL_EXECUTION_STATUSES}
        found = None
        for rows in self.log.read(session_id):
            for line in rows:
                if line.get("t") == "executions" and _line_execution(line) == execution_id \
                        and (line.get("r") or {}).get("status") in ended:
                    found = line["r"]
        return None if found is None else {name: _decode(value) for name, value in found.items()}

    def _delete_session_rows(self, session_id: str) -> None:
        owned = {row["execution_id"] for row in self.matching("executions", {"session_id": session_id}, None)}
        for table, spec in TABLES.items():
            if table in GLOBAL_TABLES:
                continue
            for column, wanted in (("session_id", session_id), ("execution_id", owned), ("job_id", owned)):
                if column in spec.columns:
                    for row in list(self.matching(table, {column: wanted}, None)):
                        self._unlink(table, tuple(row[name] for name in spec.key))
                    break

    def forget_finished_sessions(self, idle_seconds: float, *, now: float | None = None) -> int:
        """Free the memory of sessions with nothing unfinished that have been quiet this long."""
        cutoff = (time.time() if now is None else now) - idle_seconds
        ended = {status.value for status in TERMINAL_EXECUTION_STATUSES}
        with self.lock:
            if self._in_transaction:
                return 0
            newest: dict[str, float] = {}
            unfinished: set[str] = set()
            for row in self.stored["executions"].values():
                newest[row["session_id"]] = max(newest.get(row["session_id"], 0.0), row["updated_at"])
                if row["status"] not in ended:
                    unfinished.add(row["session_id"])
            quiet = [session_id for session_id, updated in newest.items()
                     if session_id not in unfinished and updated < cutoff
                     and not self._owes_a_restart_answer(session_id)]
            for session_id in quiet:
                self.forget_session(session_id)
            return len(quiet)

    def _fold_batches(self, batches: Iterable[list[dict]]) -> None:
        for rows in batches:
            for row in rows:
                self._apply(row)

    def _apply(self, line: dict) -> None:
        table = line.get("t")
        spec = TABLES.get(table)
        if spec is None:
            return
        if "gone" in line:
            columns = spec.key if len(line["gone"]) == len(spec.key) else OLDER_LOG_KEYS[table]
            for row in list(self.matching(table, dict(zip(columns, line["gone"], strict=True)), None)):
                self._unlink(table, tuple(row[name] for name in spec.key))
            return
        values = {name: _decode(value) for name, value in (line.get("r") or {}).items() if name in spec.columns}
        row = self._complete(table, values)
        for key in self._conflicts(table, row):
            self._unlink(table, key)
        self._link(table, tuple(row[column] for column in spec.key), row)

    # Writing

    @contextmanager
    def transaction(self) -> Iterator[TableHandle]:
        with self.lock:
            if self._in_transaction:
                raise RuntimeError("a transaction is already open")
            self._in_transaction = True
            self._undo, self._changed = [], {}
            try:
                yield self.handle
                touched = self._write_changes()
            except BaseException:
                self._roll_back()
                raise
            finally:
                self._in_transaction = False
                self._undo, self._changed = [], {}
            self._update_active(touched)

    def _session_of(self, table: str, key: list, values: dict | None) -> str | None:
        if values is not None and values.get("session_id"):
            return str(values["session_id"])
        if table == "runs":
            found = self.stored["runs"].get((key[0],))
            return str(found["session_id"]) if found else None
        execution_id = (values or {}).get("execution_id") or (values or {}).get("job_id") or key[0]
        found = self.stored["executions"].get((execution_id,))
        return str(found["session_id"]) if found else self._index.get(str(execution_id))

    def _write_changes(self) -> set[str]:
        if not self._changed:
            return set()
        if _process_only_reads:
            raise RuntimeError(
                "this process does not own the run tables; attempted to change "
                + ", ".join(sorted({table for table, _ in self._changed}))
            )
        by_session: dict[str, list[dict]] = {}
        touched: set[str] = set()
        new_executions: list[tuple[str, str]] = []
        for (table, key), before in self._changed.items():
            found = self.stored[table].get(key)
            values = None if found is None else {name: _encode(value) for name, value in found.items()}
            line = {"t": table, "gone": list(key)} if values is None else {"t": table, "r": values}
            session_id = _GLOBAL if table in GLOBAL_TABLES else self._session_of(table, list(key), values if values is not None else before)
            if session_id is None:
                continue
            by_session.setdefault(session_id, []).append(line)
            if table == "execution_events":
                touched.add(session_id)
            if table == "executions":
                touched.add(session_id)
                if key[0] not in self._index:
                    new_executions.append((key[0], session_id))
        for session_id, rows in by_session.items():
            if session_id == _GLOBAL:
                self.log.append_global(rows)
            else:
                self.log.append(session_id, rows)
        for execution_id, session_id in new_executions:
            self._index[execution_id] = session_id
            self.log.add_to_index(execution_id, session_id)
        return touched

    def _owes_a_restart_answer(self, session_id: str) -> bool:
        """A restart intent nothing answered; the next process must read it at start."""
        owned = {row["execution_id"] for row in self.matching("executions", {"session_id": session_id}, None)}
        events = list(self.matching("execution_events", {
            "execution_id": owned, "kind": [_RESTART_REQUESTED, _RESTART_SETTLED],
        }, None))
        answered = {json.loads(event["payload_json"]).get("request_sequence")
                    for event in events if event["kind"] == _RESTART_SETTLED}
        return any(event["kind"] == _RESTART_REQUESTED and event["sequence"] not in answered for event in events)

    def _update_active(self, sessions: set[str]) -> None:
        ended = {status.value for status in TERMINAL_EXECUTION_STATUSES}
        for session_id in sessions:
            running = any(row["status"] not in ended
                          for row in self.matching("executions", {"session_id": session_id}, None))
            active = running or self._owes_a_restart_answer(session_id)
            # Another process may still hold rows of a session deleted here.
            self.log.set_active(session_id, active and not self.log.session_gone(session_id))


def log_for_path(path: Path) -> RunLogFiles:
    """The profile's path logs into its sessions; a scratch path keeps its own files."""
    from openprogram.state.paths import get_run_state_path

    if Path(path).resolve() == get_run_state_path().resolve():
        return SessionRunLog(Path(path).parent)
    return RunLogFiles(Path(path).with_suffix(".runs"))


_process_only_reads = False


def this_process_only_reads_run_tables() -> None:
    """A subprocess has a read-only view; its progress goes in its own log."""
    global _process_only_reads
    _process_only_reads = True


def process_only_reads_run_tables() -> bool:
    return _process_only_reads


_tables_by_root: "weakref.WeakValueDictionary[str, RunTables]" = weakref.WeakValueDictionary()
_tables_lock = threading.Lock()


def tables_for(log: RunLogFiles) -> RunTables:
    """The one set of tables a process keeps for a log."""
    key = f"{type(log).__name__}:{log.root.resolve()}"
    with _tables_lock:
        tables = _tables_by_root.get(key)
        if tables is None:
            tables = RunTables(log)
            _tables_by_root[key] = tables
        return tables
