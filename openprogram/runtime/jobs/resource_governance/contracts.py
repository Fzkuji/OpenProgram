"""Resource governance: contracts."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class JobResourceView:
    job_id: str
    status: str
    resource_state: str
    reason_code: str | None
    reason_key: str | None
    retryable: bool
    limits: dict[str, Any]
    capacity: dict[str, Any]
    budget: dict[str, Any]
    execution_id: str | None = None
    capabilities: dict[str, Any] | None = None
    checkpoint_head_id: str | None = None
    event_cursor: dict[str, Any] | None = None
    resource: dict[str, Any] | None = None
    admission_id: str | None = None
    resource_lease_generation: int | None = None
    owner_instance_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.resource is None:
            value["resource"] = {
                "admission_id": self.admission_id,
                "resource_state": self.resource_state,
                "queue_wait": None,
                "resource_lease_generation": self.resource_lease_generation,
                "owner_instance_id": self.owner_instance_id,
                "limits": self.limits,
                "usage": self.budget,
                "reservation": None,
            }
        return value


@dataclass(frozen=True)
class AdmissionDecision:
    accepted: bool
    job_id: str | None
    reason_code: str | None
    retryable: bool
    effective_limits: dict[str, Any]
    capacity: dict[str, Any]
    idempotent: bool = False
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AdmissionRejected(RuntimeError):
    def __init__(self, decision: AdmissionDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason_code or "resource admission rejected")

    def to_dict(self) -> dict[str, Any]:
        return self.decision.to_dict()


@dataclass(frozen=True)
class ReservationDecision:
    accepted: bool
    reservation_id: str | None
    reason_code: str | None
    retryable: bool


@dataclass(frozen=True)
class DispatchClaim:
    job_id: str
    session_id: str
    lease_generation: int


@dataclass(frozen=True)
class ReconcileResult:
    finalized_preparing: int = 0
    rolled_back_preparing: int = 0
    released_missing: int = 0
    released_worker_lost: int = 0
    finalization_conflicts: int = 0
    completed_pending: tuple[tuple[str, str], ...] = ()
    worker_lost: tuple[tuple[str, str], ...] = ()

