"""Resource governance: finalization."""
from __future__ import annotations
import json
import time
from typing import Any, Callable, Mapping
from openprogram.runtime.jobs.types import JobStatus, is_terminal
from openprogram.runtime.jobs.resource_governance.limits import (
    TERMINAL_FIELD_NAMES,
    _CANONICAL_PROJECTION_OWNER,
    _NO_TERMINAL_BLOCK,
)


def _first_set(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _barrier_may_lift(row: dict[str, Any], now: float) -> bool:
    """A recovery barrier lifts at once; a prepared one only after it expires."""
    if row["terminal_block_phase"] == "recovery":
        return True
    expires_at = row["terminal_block_expires_at"]
    return (
        row["terminal_block_phase"] == "prepared"
        and expires_at is not None
        and expires_at <= now
    )


class FinalizationOperations:
    def finalize_stopping_job(
        self,
        job_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        reason_code: str,
        terminal_fields: Mapping[str, Any],
        mutate: Callable[[dict[str, Any]], Any],
    ) -> bool:
        """Finalize this exact stopping claim."""
        if terminal_fields.get("reason_code") != reason_code:
            raise ValueError("terminal_fields reason_code must match reason_code")
        return self._finalize_with_intent(
            job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            terminal_fields=terminal_fields,
            eligible_states=("stopping",),
            use_admission_reason=True,
            mutate=mutate,
        )


    def abandon_stopping_job(
        self, job_id: str, *, owner_instance_id: str, lease_generation: int,
    ) -> bool:
        """Revoke a failed stopping claim so reconciliation can finish it."""
        with self._write() as tables:
            self.ledger.ensure_job_session(tables, job_id)
            if self._pending_finalization(tables, job_id):
                return False
            return tables.update(
                "job_admissions",
                {
                    "owner_instance_id": None,
                    "lease_generation": lambda row: row["lease_generation"] + 1,
                },
                job_id=job_id, state="stopping",
                owner_instance_id=owner_instance_id, lease_generation=lease_generation,
            ) == 1


    def request_stop(self, job_id: str, reason_code: str) -> None:
        now = time.time()
        with self._write() as tables:
            row = self._admission(
                tables, job_id, state=("preparing", "queued", "live", "stopping"),
            )
            if row is None:
                return
            state = row["state"]
            not_started = state in ("preparing", "queued")
            borrowed_and_held = (
                state == "queued"
                and row["borrowed_parent_job_id"] is not None
                and row["owner_instance_id"] is not None
            )
            changes: dict[str, Any] = {"reason_code": reason_code}
            if state == "live":
                changes["state"] = "stopping"
            elif not_started and not borrowed_and_held:
                changes["state"] = "released"
            if not_started:
                changes.update(_NO_TERMINAL_BLOCK)
            if not_started and not borrowed_and_held:
                changes["released_at"] = now
            tables.update("job_admissions", changes, job_id=job_id)


    def release_job(
        self,
        job_id: str,
        reason_code: str | None = None,
        *,
        owner_instance_id: str | None = None,
        lease_generation: int | None = None,
    ) -> bool:
        with self._write() as tables:
            self.ledger.ensure_job_session(tables, job_id)
            if self._pending_finalization(tables, job_id):
                return False
            return tables.update(
                "job_admissions",
                {
                    "state": "released", "queue_state": "released", **_NO_TERMINAL_BLOCK,
                    "released_at": time.time(), "resume_parent_msg_id": None,
                    "reason_code": lambda row: _first_set(reason_code, row["reason_code"]),
                },
                job_id=job_id,
                where=lambda row: row["state"] != "released" and (
                    row["state"] in ("preparing", "queued")
                    or (
                        owner_instance_id is not None
                        and lease_generation is not None
                        and row["owner_instance_id"] == owner_instance_id
                        and row["lease_generation"] == lease_generation
                    )
                ),
            ) == 1


    @staticmethod
    def _terminal_fields_json(terminal_fields: Mapping[str, Any]) -> str:
        fields = dict(terminal_fields)
        if fields.keys() != TERMINAL_FIELD_NAMES:
            raise ValueError("terminal_fields must contain only terminal Job fields")
        try:
            status = JobStatus(fields["status"])
        except (TypeError, ValueError) as exc:
            raise ValueError("terminal_fields status must be terminal") from exc
        if not is_terminal(status):
            raise ValueError("terminal_fields status must be terminal")
        fields["status"] = status.value
        for name in TERMINAL_FIELD_NAMES - {"status"}:
            if fields[name] is not None and not isinstance(fields[name], str):
                raise ValueError(f"terminal_fields {name} must be a string or null")
        return json.dumps(
            {"version": 1, "fields": fields},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )


    @classmethod
    def _terminal_fields(cls, fields_json: str) -> dict[str, Any]:
        try:
            payload = json.loads(fields_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid terminal fields JSON") from exc
        if (
            not isinstance(payload, dict)
            or payload.keys() != {"version", "fields"}
            or payload["version"] != 1
            or not isinstance(payload["fields"], dict)
            or cls._terminal_fields_json(payload["fields"]) != fields_json
        ):
            raise ValueError("invalid terminal fields JSON")
        return payload["fields"]


    def _stage_finalization(
        self,
        job_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        terminal_fields: Mapping[str, Any],
        eligible_states: tuple[str, ...],
        borrowed_parent_job_id: str | None = None,
        require_parent_fence: bool = False,
        use_admission_reason: bool = False,
    ) -> tuple[str, str, dict[str, Any]] | None:
        with self._write() as tables:
            admission = self._admission(
                tables, job_id,
                owner_instance_id=owner_instance_id, lease_generation=lease_generation,
            )
            if (
                admission is None
                or admission["state"] not in eligible_states
                or admission["borrowed_parent_job_id"] != borrowed_parent_job_id
            ):
                return None
            if require_parent_fence and not self._parent_holds_claim(
                tables, borrowed_parent_job_id, owner_instance_id, lease_generation,
            ):
                return None
            fields = dict(terminal_fields)
            if use_admission_reason and admission["reason_code"] is not None:
                fields["reason_code"] = admission["reason_code"]
            fields_json = self._terminal_fields_json(fields)
            existing = tables.get("job_finalizations", job_id)
            if existing is not None:
                if (
                    existing["owner_instance_id"] != owner_instance_id
                    or existing["lease_generation"] != lease_generation
                    or existing["fields_json"] != fields_json
                ):
                    return None
                if existing["state"] == "pending":
                    # Someone else already staged this intent and owns the
                    # terminal write; a pending intent left by a crash is
                    # completed by reconcile, never by a second writer.
                    return None
                return str(existing["state"]), fields_json, fields
            tables.insert("job_finalizations", {
                "job_id": job_id, "session_id": admission["session_id"],
                "owner_instance_id": owner_instance_id,
                "lease_generation": lease_generation, "fields_json": fields_json,
                "state": "pending", "created_at": time.time(),
            })
            return "pending", fields_json, fields


    def _complete_finalization(
        self,
        job_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        fields_json: str,
        reason_code: str | None,
        eligible_states: tuple[str, ...],
    ) -> bool:
        with self._write() as tables:
            self.ledger.ensure_job_session(tables, job_id)
            intent = tables.first(
                "job_finalizations", job_id=job_id, owner_instance_id=owner_instance_id,
                lease_generation=lease_generation, fields_json=fields_json,
            )
            if intent is None:
                return False
            if intent["state"] == "completed":
                return True
            changed = tables.update(
                "job_admissions",
                {
                    "state": "released", **_NO_TERMINAL_BLOCK,
                    "released_at": time.time(), "resume_parent_msg_id": None,
                    "reason_code": lambda row: _first_set(reason_code, row["reason_code"]),
                },
                job_id=job_id, owner_instance_id=owner_instance_id,
                lease_generation=lease_generation, state=eligible_states,
            )
            if changed != 1:
                return False
            tables.update(
                "job_finalizations", {"state": "completed", "completed_at": time.time()},
                job_id=job_id, state="pending",
            )
            return True


    def enqueue_terminal_projection(
        self, job_id: str, terminal_fields: Mapping[str, Any],
    ) -> bool:
        """Durably queue a canonical terminal projection for retry.

        This ownerless intent is used only when the JobStore write is
        temporarily unavailable.  It preserves the exact terminal fields;
        reconciliation performs the projection and admission release later.
        """
        fields_json = self._terminal_fields_json(terminal_fields)
        expires_at = time.time() + 30.0
        with self._write() as tables:
            admission = self._admission(tables, job_id)
            if admission is None:
                return False
            # The dispatch fence and the projection intent must commit
            # together.  This closes the queued-admission race with
            # claim_next while the canonical database remains separate.
            tables.update(
                "job_admissions",
                {
                    "dispatch_ready": 0, "terminal_blocked": 1,
                    "terminal_block_phase": lambda row: _first_set(
                        row["terminal_block_phase"], "projection",
                    ),
                    "terminal_block_expires_at": lambda row: _first_set(
                        row["terminal_block_expires_at"], expires_at,
                    ),
                    "terminal_block_prior_dispatch_ready": lambda row: _first_set(
                        row["terminal_block_prior_dispatch_ready"], row["dispatch_ready"],
                    ),
                },
                job_id=job_id, where=lambda row: row["state"] != "released",
            )
            existing = tables.get("job_finalizations", job_id)
            if existing is not None:
                return existing["fields_json"] == fields_json
            tables.insert("job_finalizations", {
                "job_id": job_id, "session_id": admission["session_id"],
                "owner_instance_id": _CANONICAL_PROJECTION_OWNER,
                "lease_generation": admission["lease_generation"],
                "fields_json": fields_json, "state": "pending", "created_at": time.time(),
            })
        return True


    def block_dispatch(
        self,
        job_id: str,
        *,
        command_id: str | None = None,
        phase: str = "recovery",
    ) -> bool:
        """Fence an admission before canonical terminal transition."""
        if phase not in {"prepared", "recovery", "projection"}:
            raise ValueError("invalid terminal barrier phase")
        expires_at = time.time() + 30.0
        with self._write() as tables:
            self.ledger.ensure_job_session(tables, job_id)
            return tables.update(
                "job_admissions",
                {
                    "dispatch_ready": 0, "terminal_blocked": 1,
                    "terminal_block_command_id": lambda row: _first_set(
                        row["terminal_block_command_id"], command_id,
                    ),
                    "terminal_block_phase": lambda row: (
                        _first_set(row["terminal_block_phase"], phase)
                        if row["terminal_blocked"] == 1 else phase
                    ),
                    "terminal_block_expires_at": lambda row: (
                        _first_set(row["terminal_block_expires_at"], expires_at)
                        if row["terminal_blocked"] == 1 else expires_at
                    ),
                    "terminal_block_prior_dispatch_ready": lambda row: _first_set(
                        row["terminal_block_prior_dispatch_ready"],
                        0 if row["terminal_blocked"] == 1 else row["dispatch_ready"],
                    ),
                },
                job_id=job_id, where=lambda row: row["state"] != "released",
            ) == 1


    def mark_terminal_dispatch_recovery(
        self, job_id: str, *, command_id: str,
    ) -> bool:
        """Record that the canonical cancel CAS failed after preparation."""
        with self._write() as tables:
            self.ledger.ensure_job_session(tables, job_id)
            return tables.update(
                "job_admissions",
                {
                    "terminal_block_phase": "recovery",
                    "terminal_block_expires_at": time.time() + 30.0,
                },
                job_id=job_id, terminal_blocked=1, terminal_block_command_id=command_id,
            ) == 1


    def unblock_terminal_dispatch(
        self,
        job_id: str,
        *,
        expected_state: str = "queued",
        expected_owner_instance_id: str | None = None,
        expected_lease_generation: int | None = None,
    ) -> bool:
        """Restore a queued admission under an exact recovery fence."""
        fence: dict[str, Any] = {
            "state": expected_state, "terminal_blocked": 1,
            "owner_instance_id": expected_owner_instance_id,
        }
        if expected_lease_generation is not None:
            fence["lease_generation"] = expected_lease_generation
        now = time.time()
        with self._write() as tables:
            self.ledger.ensure_job_session(tables, job_id)
            if self._pending_finalization(tables, job_id):
                return False
            return tables.update(
                "job_admissions",
                {
                    **_NO_TERMINAL_BLOCK,
                    "dispatch_ready": lambda row: _first_set(
                        row["terminal_block_prior_dispatch_ready"], row["dispatch_ready"],
                    ),
                },
                job_id=job_id, where=lambda row: _barrier_may_lift(row, now), **fence,
            ) == 1


    def restore_terminal_dispatch_claim(
        self,
        job_id: str,
        *,
        expected_state: str,
        expected_owner_instance_id: str | None,
        expected_lease_generation: int,
        target_state: str,
        owner_instance_id: str | None = None,
        lease_generation: int | None = None,
        reason_code: str | None = None,
    ) -> bool:
        """Reconcile a pre-cancel fence to a canonical owner/state exactly."""
        if target_state not in {"live", "stopping", "released"}:
            raise ValueError("target_state must be live, stopping, or released")
        if target_state != "released" and (
            owner_instance_id is None or lease_generation is None
        ):
            raise ValueError("an active target requires an owner fence")
        now = time.time()
        reason = {"reason_code": lambda row: _first_set(reason_code, row["reason_code"])}
        if target_state == "released":
            changes: dict[str, Any] = {
                "state": "released", **_NO_TERMINAL_BLOCK, "dispatch_ready": 0,
                "owner_instance_id": None, "released_at": now,
                "resume_parent_msg_id": None, **reason,
            }
        else:
            changes = {
                "state": target_state, **_NO_TERMINAL_BLOCK, "dispatch_ready": 0,
                "owner_instance_id": owner_instance_id, "lease_generation": lease_generation,
                "started_at": lambda row: _first_set(row["started_at"], now),
                "last_activity_at": now, **reason,
            }
        with self._write() as tables:
            self.ledger.ensure_job_session(tables, job_id)
            if self._pending_finalization(tables, job_id):
                return False
            return tables.update(
                "job_admissions", changes,
                job_id=job_id, state=expected_state, terminal_blocked=1,
                owner_instance_id=expected_owner_instance_id,
                lease_generation=expected_lease_generation,
                where=lambda row: _barrier_may_lift(row, now),
            ) == 1


    def _complete_ownerless_projection(
        self, job_id: str, *, lease_generation: int,
        fields_json: str, reason_code: str | None,
    ) -> bool:
        """Complete an ownerless projection intent under its admission fence."""
        with self._write() as tables:
            admission = self._admission(tables, job_id)
            intent = tables.first(
                "job_finalizations", job_id=job_id,
                owner_instance_id=_CANONICAL_PROJECTION_OWNER,
                lease_generation=lease_generation, fields_json=fields_json,
            )
            if intent is None:
                return False
            if intent["state"] == "completed":
                return True
            if admission is None:
                return False
            if admission["state"] != "released":
                changed = tables.update(
                    "job_admissions",
                    {
                        "state": "released", **_NO_TERMINAL_BLOCK,
                        "released_at": time.time(), "resume_parent_msg_id": None,
                        "reason_code": lambda row: _first_set(reason_code, row["reason_code"]),
                    },
                    job_id=job_id, state=("preparing", "queued", "live", "stopping"),
                    lease_generation=lease_generation,
                )
                if changed != 1:
                    return False
            return tables.update(
                "job_finalizations", {"state": "completed", "completed_at": time.time()},
                job_id=job_id, owner_instance_id=_CANONICAL_PROJECTION_OWNER,
                lease_generation=lease_generation, fields_json=fields_json, state="pending",
            ) == 1


    @staticmethod
    def _intent_tuple(row: dict[str, Any]) -> tuple[str, str, str, int, str, str]:
        return (
            row["job_id"], row["session_id"], row["owner_instance_id"],
            row["lease_generation"], row["fields_json"], row["state"],
        )


    def pending_finalization(self, job_id: str):
        """Return the immutable pending terminal intent, if one exists."""
        tables = self._read()
        self.ledger.ensure_job_session(tables, job_id)
        row = tables.first("job_finalizations", job_id=job_id, state="pending")
        return None if row is None else self._intent_tuple(row)


    def pending_finalizations(self) -> list[tuple[str, str, str, int, str, str]]:
        """Return all pending terminal intents for projection recovery."""
        rows = self._read().rows("job_finalizations", state="pending", order="created_at")
        return [self._intent_tuple(row) for row in rows]


    def admission_fence(self, job_id: str) -> tuple[str | None, int] | None:
        """Return the current admission owner fence for a projection."""
        row = self._admission(self._read(), job_id)
        return None if row is None else (row["owner_instance_id"], int(row["lease_generation"]))


    def admission_state(
        self, job_id: str,
    ) -> tuple[str, str | None, int, bool, str | None, float | None, str | None, int | None] | None:
        """Return state and fence fields needed for canonical recovery."""
        row = self._admission(self._read(), job_id)
        if row is None:
            return None
        return (
            str(row["state"]), row["owner_instance_id"], int(row["lease_generation"]),
            bool(row["terminal_blocked"]), row["terminal_block_phase"],
            row["terminal_block_expires_at"], row["terminal_block_command_id"],
            row["terminal_block_prior_dispatch_ready"],
        )


    def complete_pending_finalization(
        self,
        job_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        fields_json: str,
        reason_code: str | None,
    ) -> bool:
        """Release exactly the admission fenced by a pending intent."""
        if owner_instance_id == _CANONICAL_PROJECTION_OWNER:
            return self._complete_ownerless_projection(
                job_id,
                lease_generation=lease_generation,
                fields_json=fields_json,
                reason_code=reason_code,
            )
        return self._complete_finalization(
            job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            fields_json=fields_json,
            reason_code=reason_code,
            eligible_states=('preparing', 'queued', 'live', 'stopping'),
        )


    def _finalize_with_intent(
        self,
        job_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        terminal_fields: Mapping[str, Any],
        eligible_states: tuple[str, ...],
        mutate: Callable[[dict[str, Any]], Any],
        borrowed_parent_job_id: str | None = None,
        require_parent_fence: bool = False,
        use_admission_reason: bool = False,
    ) -> bool:
        staged = self._stage_finalization(
            job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            terminal_fields=terminal_fields,
            eligible_states=eligible_states,
            borrowed_parent_job_id=borrowed_parent_job_id,
            require_parent_fence=require_parent_fence,
            use_admission_reason=use_admission_reason,
        )
        if staged is None:
            return False
        intent_state, fields_json, fields = staged
        if intent_state == "completed":
            return True
        mutate(fields)
        return self._complete_finalization(
            job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            fields_json=fields_json,
            reason_code=fields["reason_code"],
            eligible_states=eligible_states,
        )


    def finalize_job(
        self,
        job_id: str,
        reason_code: str | None,
        *,
        owner_instance_id: str,
        lease_generation: int,
        terminal_fields: Mapping[str, Any],
        mutate: Callable[[dict[str, Any]], Any],
    ) -> bool:
        """Persist terminal intent, write JobStore, then release this lease."""
        if terminal_fields.get("reason_code") != reason_code:
            raise ValueError("terminal_fields reason_code must match reason_code")
        return self._finalize_with_intent(
            job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            terminal_fields=terminal_fields,
            eligible_states=("live", "stopping"),
            mutate=mutate,
        )
