"""Durable hand-off between canonical executions and resource admissions.

ExecutionStore and ResourceGovernor intentionally keep separate SQLite files.
This module records an intent in the execution authority first and consumes it
later in the governor authority; it never presents the pair as one transaction.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Callable, Mapping

from .store import ExecutionStore

if TYPE_CHECKING:
    from openprogram.agent.job.types import Job
    from openprogram.agent.resource_governance import ResourceGovernor


FaultHook = Callable[[str], None]


class ResourceSaga:
    """Public primitives for idempotent resource admission, claim, and release."""

    def __init__(
        self,
        store: ExecutionStore,
        governor: "ResourceGovernor",
        *,
        owner_id: str | None = None,
        fault_hook: FaultHook | None = None,
    ) -> None:
        self.store = store
        self.governor = governor
        self.owner_id = owner_id or f"resource-saga-{uuid.uuid4().hex}"
        self._fault_hook = fault_hook

    @staticmethod
    def _fingerprint(value: Mapping[str, Any]) -> str:
        encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _fault(self, point: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)

    def admit(
        self,
        execution_id: str,
        job: "Job",
        *,
        creates_agent: bool = True,
        caller_turn_id: str | None = None,
        dispatch_ready: bool = True,
    ) -> str:
        """Durably write admission hand-off records without touching the ledger."""
        if job.id != execution_id:
            raise ValueError("job id must equal canonical execution id")
        admission_id = job.admission_id or f"adm_{uuid.uuid4().hex}"
        durable_job = replace(job, admission_id=admission_id)
        from openprogram.agent.resource_governance import _job_fingerprint

        fingerprint = _job_fingerprint(durable_job)
        payload = {
            "job": durable_job.to_dict(),
            "creates_agent": creates_agent,
            "caller_turn_id": caller_turn_id,
            "dispatch_ready": dispatch_ready,
        }
        key = f"admission:{admission_id}"
        self.store.enqueue_resource_intent(
            execution_id,
            kind="execution.admission.intent",
            idempotency_key=f"execution:{key}",
            fingerprint=fingerprint,
            admission_id=admission_id,
            payload={"admission_id": admission_id, "request_fingerprint": fingerprint},
        )
        self.store.enqueue_resource_intent(
            execution_id,
            kind="resource.admission.intent",
            idempotency_key=f"resource:{key}",
            fingerprint=self._fingerprint(payload),
            admission_id=admission_id,
            payload=payload,
        )
        self._fault("execution_written")
        return admission_id

    def request_claim(
        self,
        execution_id: str,
        *,
        admission_id: str,
        command_id: str,
        attempt_id: str | None = None,
        generation: int | None = None,
        paused: bool = True,
    ) -> str:
        """Persist a continuation claim; activation remains a separate owner step."""
        payload = {
            "command_id": command_id,
            "paused": paused,
            "owner_instance_id": self.owner_id,
        }
        claim_id = f"{admission_id}:{command_id}"
        fingerprint = self._fingerprint(payload)
        for kind, prefix in (
            ("execution.claim.intent", "execution"),
            ("resource.claim.intent", "resource"),
        ):
            self.store.enqueue_resource_intent(
                execution_id,
                kind=kind,
                idempotency_key=f"{prefix}:claim:{claim_id}",
                fingerprint=fingerprint,
                admission_id=admission_id,
                attempt_id=attempt_id,
                generation=generation,
                payload=payload,
            )
        self._fault("resume_accepted")
        return claim_id

    def request_release(
        self,
        execution_id: str,
        *,
        admission_id: str,
        reason_code: str,
        attempt_id: str | None = None,
        generation: int | None = None,
        resource_lease_generation: int | None = None,
        terminal_version: int | None = None,
    ) -> str:
        """Persist a fenced release, including checkpoint and terminal paths."""
        release_id = (
            f"{execution_id}:{terminal_version}"
            if terminal_version is not None
            else f"{execution_id}:{attempt_id}:{generation}:{reason_code}"
        )
        payload = {"reason_code": reason_code, "terminal_version": terminal_version}
        fingerprint = self._fingerprint(payload)
        for kind, prefix in (
            ("execution.release.intent", "execution"),
            ("resource.release.intent", "resource"),
        ):
            self.store.enqueue_resource_intent(
                execution_id,
                kind=kind,
                idempotency_key=f"{prefix}:release:{release_id}",
                fingerprint=fingerprint,
                admission_id=admission_id,
                attempt_id=attempt_id,
                generation=generation,
                resource_lease_generation=resource_lease_generation,
                payload=payload,
            )
        self._fault("checkpoint_paused" if terminal_version is None else "terminal_written")
        return release_id

    def reconcile(self, *, limit: int = 100) -> int:
        """Replay claimed resource intents.  A crash leaves the lease reclaimable."""
        completed = 0
        for intent in self.store.claim_resource_intents(owner_id=self.owner_id, limit=limit):
            kind = intent["kind"]
            try:
                result = self._consume(intent)
            except Exception as exc:
                self.store.retry_resource_intent(
                    intent["intent_id"], owner_id=self.owner_id, error=str(exc),
                )
                raise
            if result is None:
                self.store.retry_resource_intent(
                    intent["intent_id"], owner_id=self.owner_id, error="resource claim pending",
                )
                continue
            applied = self.store.complete_resource_intent(
                intent["intent_id"], owner_id=self.owner_id, result=result,
            )
            completed += int(applied is not None)
        return completed

    def _consume(self, intent: Mapping[str, Any]) -> dict[str, Any] | None:
        kind = str(intent["kind"])
        payload = dict(intent["payload"])
        if kind == "execution.admission.intent":
            return {"state": "recorded"}
        if kind == "resource.admission.intent":
            self._fault("governor_preparing")
            from openprogram.agent.job.types import Job

            job = Job.from_dict(dict(payload["job"]))
            decision = self.governor.reserve_admission(
                job,
                persist=lambda _job: None,
                creates_agent=bool(payload.get("creates_agent", True)),
                caller_turn_id=payload.get("caller_turn_id"),
                dispatch_ready=bool(payload.get("dispatch_ready", True)),
            )
            if not decision.accepted:
                return {"state": "rejected", "reason_code": decision.reason_code}
            return {"state": "reserved", "admission_id": job.admission_id}
        if kind == "execution.claim.intent":
            return {"state": "activation_pending"}
        if kind == "resource.claim.intent":
            command_id = str(payload["command_id"])
            if not self.governor.queue_resume(
                str(intent["execution_id"]),
                admission_id=str(intent["admission_id"]),
                command_id=command_id,
                paused=bool(payload.get("paused", True)),
            ):
                return None
            claim = self.governor.claim_execution(
                str(intent["execution_id"]),
                owner_instance_id=str(payload["owner_instance_id"]),
                admission_id=str(intent["admission_id"]),
                command_id=command_id,
            )
            if claim is None:
                return None
            self._fault("claim_obtained")
            return {
                "state": "activation_pending",
                "resource_lease_generation": claim.lease_generation,
                "session_id": claim.session_id,
            }
        if kind == "execution.release.intent":
            return {"state": "recorded"}
        if kind == "resource.release.intent":
            released = self.governor.release_execution(
                str(intent["execution_id"]),
                str(payload["reason_code"]),
                admission_id=str(intent["admission_id"]),
                owner_instance_id=self.owner_id,
                resource_lease_generation=intent["resource_lease_generation"],
            )
            return {"state": "released", "idempotent": released}
        raise ValueError(f"unsupported resource intent: {kind}")


def recover_resource_saga(store: ExecutionStore, governor: "ResourceGovernor", *, owner_id: str | None = None) -> int:
    """Startup entry point: reclaim expired hand-offs and replay them once."""
    return ResourceSaga(store, governor, owner_id=owner_id).reconcile()


__all__ = ["ResourceSaga", "recover_resource_saga"]
