"""Resource governance: the run tables its rows live in.

A Job's admission, finalization and reservations are lines of its session's
log; budget scopes are in the global log, which is read whenever the tables
are. The session of every Job is therefore always known, and a scan across
sessions can bring in each of them first.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from openprogram.models.usage.ledger import MOVED_TABLES, use_run_tables
from openprogram.runtime.execution._schema import TABLES
from openprogram.runtime.execution.store.ownership import owns_store_schema
from openprogram.runtime.execution.store.run_tables import (
    RunTables,
    TableHandle,
    log_for_path,
    tables_for,
)

_log = logging.getLogger(__name__)

_LIMIT_COLUMNS = (
    "max_total_tokens", "max_cost_microusd", "max_runtime_seconds", "idle_timeout_seconds",
)


def _tables_beside(usage_path: Path) -> RunTables:
    from openprogram.state.paths import get_run_state_path

    return tables_for(log_for_path(Path(usage_path).with_name(get_run_state_path().name)))


use_run_tables(_tables_beside)


def load_job_sessions(tables: RunTables) -> None:
    """Bring every session that has held a Job into memory."""
    sessions = {row["session_id"] for row in tables.handle.rows("budget_scopes", scope_kind="job")}
    for session_id in sessions:
        tables.ensure_session(session_id)


def _chain(scope_id: str | None, scopes: dict[str, dict]) -> list[dict]:
    chain: list[dict] = []
    seen: set[str] = set()
    while scope_id is not None and scope_id in scopes and scope_id not in seen:
        seen.add(scope_id)
        chain.append(scopes[scope_id])
        scope_id = scopes[scope_id]["parent_scope_id"]
    return chain


def _with_recoverable_barrier(row: dict) -> dict:
    """A blocked admission written before barriers had a phase can still be lifted."""
    if not row.get("terminal_blocked") or row.get("terminal_block_phase") is not None:
        return row
    prior = row.get("terminal_block_prior_dispatch_ready")
    return {
        **row,
        "terminal_block_phase": "recovery",
        "terminal_block_expires_at": time.time() + 30.0,
        "terminal_block_prior_dispatch_ready": 0 if prior is None else prior,
    }


def rows_worth_keeping(stored: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """What an older usage file still means: limits set, and work not finished."""
    scopes = {row["budget_scope_id"]: row for row in stored.get("budget_scopes", ())}
    admissions = [
        _with_recoverable_barrier(row)
        for row in stored.get("job_admissions", ()) if row["state"] != "released"
    ]
    keep: set[str] = set()
    for scope in scopes.values():
        chain = _chain(scope["budget_scope_id"], scopes)
        # A limit counts the usage of every scope under it.
        if any(item[name] is not None for item in chain for name in _LIMIT_COLUMNS):
            keep.update(item["budget_scope_id"] for item in chain)
    for admission in admissions:
        keep.update(item["budget_scope_id"] for item in _chain(admission["budget_scope_id"], scopes))
    reservations = [
        {**row, "session_id": scopes[row["budget_scope_id"]]["session_id"]}
        for row in stored.get("usage_reservations", ())
        if row["state"] in ("reserved", "started") and row["budget_scope_id"] in keep
    ]
    return {
        "budget_scopes": [scopes[scope_id] for scope_id in scopes if scope_id in keep],
        "job_admissions": admissions,
        "job_finalizations": [row for row in stored.get("job_finalizations", ()) if row["state"] == "pending"],
        "usage_reservations": reservations,
    }


class TablesAccess:
    """How the governor reaches its rows."""

    ledger: Any
    _opened_tables: RunTables | None = None
    _opening = threading.Lock()

    def _tables(self) -> RunTables:
        tables = self.ledger.run_tables()
        if self._opened_tables is not tables:
            with self._opening:
                if self._opened_tables is not tables:
                    self.ledger.open_accounting_reader()
                    try:
                        self._take_stored_rows(tables)
                    except Exception:
                        # The file keeps its tables, so the next start tries again.
                        _log.exception("job admission rows were not moved out of the usage file")
                    load_job_sessions(tables)
                    self._opened_tables = tables
        return tables

    def _take_stored_rows(self, tables: RunTables) -> None:
        """Copy rows an older usage file still holds, then remove its tables."""
        if not owns_store_schema(self.ledger._path()):  # noqa: SLF001
            return
        stored = self.ledger.stored_run_table_rows()
        if stored is None:
            return
        keep = rows_worth_keeping(stored)
        with tables.transaction() as handle:
            for table in MOVED_TABLES:
                columns = set(TABLES[table].columns)
                for row in keep[table]:
                    handle.insert(
                        table, {name: value for name, value in row.items() if name in columns},
                        on_conflict="ignore",
                    )
        self.ledger.drop_stored_run_tables()
        _log.info("job admission rows moved out of %s", self.ledger._path())  # noqa: SLF001

    @contextmanager
    def _write(self) -> Iterator[TableHandle]:
        with self._tables().transaction() as handle:
            yield handle

    def _read(self) -> TableHandle:
        return self._tables().handle

    def _admission(self, tables: TableHandle, job_id: str, **equals: Any) -> dict | None:
        self.ledger.ensure_job_session(tables, job_id)
        return tables.first("job_admissions", job_id=job_id, **equals)

    def _pending_finalization(self, tables: TableHandle, job_id: str) -> bool:
        return tables.exists("job_finalizations", job_id=job_id, state="pending")
