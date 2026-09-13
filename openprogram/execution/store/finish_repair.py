"""Execution persistence: finish repair."""
from __future__ import annotations


import json



import time



from contextlib import closing



from typing import Any



from ..model import TERMINAL_EXECUTION_STATUSES


from .shared import (
    _FINISH_REPAIR_PAGE_LIMIT,
)

class FinishRepairOperations:
    def upsert_finish_repair(
        self,
        *,
        execution_id: str,
        attempt_id: str,
        generation: int,
        expected_version: int,
        target: str,
        outcome: str,
        reason_code: str | None,
        command_id: str | None = None,
        retry_count: int = 0,
        next_attempt_at: float = 0.0,
    ) -> None:
        """Persist a terminal write for bounded retry and startup replay."""
        if type(retry_count) is not int or retry_count < 0:
            raise ValueError("finish repair retry_count must be non-negative")
        if type(next_attempt_at) not in {int, float}:
            raise ValueError("finish repair next_attempt_at must be numeric")
        now = time.time()
        with self._transaction() as connection:
            # Only terminal or stale rows may be collected. An actionable
            # repair is retained even when the bounded table is full.
            rows = connection.execute(
                """
                SELECT r.execution_id, r.attempt_id, r.generation,
                       e.status AS execution_status,
                       e.current_attempt_id AS current_attempt_id,
                       e.owner_lease_json AS owner_lease_json,
                       a.execution_id AS attempt_execution_id,
                       a.generation AS attempt_generation,
                       a.status AS attempt_status
                FROM execution_finish_repairs AS r
                LEFT JOIN executions AS e ON e.execution_id = r.execution_id
                LEFT JOIN attempts AS a ON a.attempt_id = r.attempt_id
                """
            ).fetchall()
            terminal_values = {status.value for status in TERMINAL_EXECUTION_STATUSES}
            stale_keys = []
            for row in rows:
                try:
                    owner_lease = json.loads(row["owner_lease_json"] or "{}")
                except (TypeError, ValueError):
                    owner_lease = {}
                if (
                    row["execution_status"] is None
                    or row["execution_status"] in terminal_values
                    or row["current_attempt_id"] != row["attempt_id"]
                    or row["attempt_execution_id"] != row["execution_id"]
                    or row["attempt_generation"] != row["generation"]
                    or row["attempt_status"] != "active"
                    or owner_lease.get("generation") != row["generation"]
                ):
                    stale_keys.append(
                        (row["execution_id"], row["attempt_id"], row["generation"])
                    )
            for stale_key in stale_keys:
                connection.execute(
                    "DELETE FROM execution_finish_repairs "
                    "WHERE execution_id = ? AND attempt_id = ? AND generation = ?",
                    stale_key,
                )
                execution_status = connection.execute(
                    "SELECT status FROM executions WHERE execution_id = ?",
                    (stale_key[0],),
                ).fetchone()
                if (
                    execution_status is None
                    or execution_status["status"] in terminal_values
                ):
                    connection.execute(
                        "DELETE FROM execution_finish_repair_slots WHERE execution_id = ?",
                        (stale_key[0],),
                    )
            connection.execute(
                """
                INSERT INTO execution_finish_repairs (
                    execution_id, attempt_id, generation, expected_version,
                    target, outcome, reason_code, created_at, updated_at,
                    command_id, retry_count, next_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(execution_id, attempt_id, generation) DO UPDATE SET
                    expected_version = excluded.expected_version,
                    target = excluded.target,
                    outcome = excluded.outcome,
                    reason_code = excluded.reason_code,
                    command_id = excluded.command_id,
                    retry_count = excluded.retry_count,
                    next_attempt_at = excluded.next_attempt_at,
                    updated_at = excluded.updated_at
                """,
                (
                    execution_id, attempt_id, generation, expected_version,
                    target, outcome, reason_code, now, now, command_id,
                    retry_count, next_attempt_at,
                ),
            )
            connection.execute(
                "UPDATE execution_finish_repair_slots SET state = 'repair', "
                "updated_at = ? WHERE execution_id = ?",
                (now, execution_id),
            )


    def defer_finish_repair(
        self,
        execution_id: str,
        attempt_id: str,
        generation: int,
        *,
        retry_count: int,
        next_attempt_at: float,
    ) -> None:
        """Persist repair backoff without changing its desired outcome."""
        if type(retry_count) is not int or retry_count < 0:
            raise ValueError("finish repair retry_count must be non-negative")
        if type(next_attempt_at) not in {int, float}:
            raise ValueError("finish repair next_attempt_at must be numeric")
        with self._transaction() as connection:
            connection.execute(
                "UPDATE execution_finish_repairs SET retry_count = ?, "
                "next_attempt_at = ?, updated_at = ? "
                "WHERE execution_id = ? AND attempt_id = ? AND generation = ?",
                (
                    retry_count, next_attempt_at, time.time(),
                    execution_id, attempt_id, generation,
                ),
            )


    def list_finish_repairs(
        self, *, limit: int = 256, offset: int = 0,
        include_stalled: bool = True,
        after: tuple[float, str, str, int] | None = None,
        due_before: float | None = None,
    ) -> list[dict[str, Any]]:
        if type(limit) is not int or not 0 < limit <= _FINISH_REPAIR_PAGE_LIMIT:
            raise ValueError("finish repair limit is out of bounds")
        if type(offset) is not int or offset < 0:
            raise ValueError("finish repair offset must be non-negative")
        if type(include_stalled) is not bool:
            raise ValueError("include_stalled must be a bool")
        if due_before is not None and type(due_before) not in {int, float}:
            raise ValueError("finish repair due_before must be numeric")
        if after is not None and (
            type(after) is not tuple
            or len(after) != 4
            or type(after[0]) not in {int, float}
            or not all(type(value) is str for value in after[1:3])
            or type(after[3]) is not int
        ):
            raise ValueError("finish repair cursor is invalid")
        with closing(self._connect()) as connection:
            where = "(? OR reason_code IS NULL OR reason_code != ?)"
            values: list[Any] = [include_stalled, "finish_repair_stalled"]
            if due_before is not None:
                where += " AND next_attempt_at <= ?"
                values.append(due_before)
            if after is not None:
                where += (
                    " AND (created_at > ? OR "
                    "(created_at = ? AND execution_id > ?) OR "
                    "(created_at = ? AND execution_id = ? AND attempt_id > ?) OR "
                    "(created_at = ? AND execution_id = ? AND attempt_id = ? "
                    "AND generation > ?))"
                )
                created_at, execution_id, attempt_id, generation = after
                values.extend(
                    [
                        created_at,
                        created_at,
                        execution_id,
                        created_at,
                        execution_id,
                        attempt_id,
                        created_at,
                        execution_id,
                        attempt_id,
                        generation,
                    ]
                )
            values.extend([limit, offset])
            rows = connection.execute(
                "SELECT * FROM execution_finish_repairs "
                f"WHERE {where} "
                "ORDER BY created_at, execution_id, attempt_id, generation "
                "LIMIT ? OFFSET ?",
                values,
            ).fetchall()
        return [dict(row) for row in rows]


    def list_finish_repair_slots(self, *, limit: int = 4096) -> list[dict[str, Any]]:
        if type(limit) is not int or not 0 < limit <= _FINISH_REPAIR_PAGE_LIMIT:
            raise ValueError("finish repair slot limit is out of bounds")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM execution_finish_repair_slots "
                "ORDER BY reserved_at, execution_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


    def has_stalled_finish_repairs(self) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM execution_finish_repairs "
                "WHERE reason_code = 'finish_repair_stalled' LIMIT 1"
            ).fetchone()
        return row is not None


    def delete_finish_repair(
        self, execution_id: str, attempt_id: str, generation: int,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM execution_finish_repairs "
                "WHERE execution_id = ? AND attempt_id = ? AND generation = ?",
                (execution_id, attempt_id, generation),
            )
            # Removing one stale/handled generation does not release the
            # execution's admission reservation. If no repair remains for a
            # live execution, expose the reservation as available for a new
            # repair rather than leaving it marked as an old repair.
            connection.execute(
                "UPDATE execution_finish_repair_slots SET state = 'reserved', "
                "updated_at = ? WHERE execution_id = ? "
                "AND NOT EXISTS ("
                "SELECT 1 FROM execution_finish_repairs "
                "WHERE execution_id = ?"
                ") AND EXISTS ("
                "SELECT 1 FROM executions WHERE execution_id = ? "
                "AND status NOT IN (?, ?, ?, ?)"
                ")",
                (
                    time.time(), execution_id, execution_id, execution_id,
                    *(status.value for status in TERMINAL_EXECUTION_STATUSES),
                ),
            )

