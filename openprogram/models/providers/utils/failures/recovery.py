"""Invocation-local failure allowances for semantic and transport recovery.

One logical model invocation owns one ``RecoveryState``. Runtime structured
repair, provider transport retries, and agent_loop validation repairs all
``reserve()`` against that same object. Successful tool rounds do not call
``reserve`` and therefore do not consume an allowance.

Two allowances live on the state. Semantic repairs (invalid JSON, length,
incomplete output) share ``limit`` and accumulate over the invocation.
Transport retries (``reason="transport"``) have their own ``transport_limit``:
a dropped connection says nothing about the model's output, so it must not
spend the repair allowance, and a request that succeeds after transport
retries refunds them, which bounds transport recovery per request.

Compatibility:
- ``max_validation_retries`` / Runtime ``max_retries`` map to ``limit`` as
  "extra failure recoveries after the initial attempt". Explicit ``0`` means
  no automatic recovery of either kind. Default is ``2``.
- ``transport_limit`` defaults to the provider stream retry budget minus the
  first attempt (``OPENPROGRAM_PROVIDER_STREAM_RETRIES``, default 5 → 4).
- ``requests`` counts provider dispatches that called ``started()`` (best-effort
  diagnostics). It is NOT a guaranteed HTTP request counter — some adapters
  record before the socket write.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


DEFAULT_TRANSPORT_RECOVERIES = max(
    0, int(os.environ.get("OPENPROGRAM_PROVIDER_STREAM_RETRIES", "5")) - 1,
)


@dataclass
class RecoveryState:
    """Failure-recovery allowances for one logical invocation."""

    limit: int = 2
    used: int = 0
    transport_limit: int = DEFAULT_TRANSPORT_RECOVERIES
    transport_used: int = 0
    # Every transport retry ever reserved, refunds included. Call budgets
    # that count model requests credit these back (a reconnect is not a
    # new attempt at the answer).
    transport_reserves: int = 0
    requests: int = 0
    parent_id: str | None = None
    invocation_id: str | None = None
    phase: str = "active"  # active | retrying | paused | cancelled
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def reserve(self, reason: str) -> bool:
        if self.phase == "cancelled":
            return False
        if reason == "transport":
            if self.limit <= 0 or self.transport_used >= self.transport_limit:
                self.phase = "paused"
                return False
            self.transport_used += 1
            self.transport_reserves += 1
            self.phase = "retrying"
            self.attempts.append({"kind": "reserve", "reason": reason, "used": self.transport_used})
            del self.attempts[:-32]
            return True
        if self.used >= self.limit:
            self.phase = "paused"
            return False
        self.used += 1
        self.phase = "retrying"
        self.attempts.append({"kind": "reserve", "reason": reason, "used": self.used})
        del self.attempts[:-32]
        return True

    def refund(self, reason: str, count: int) -> None:
        """Return recoveries a request used once it finally succeeded.

        Transport recovery is per request: a long Agent loop makes many
        model requests under one state, and a blip that a retry rode over
        must not leave the rest of the loop with fewer recoveries.
        """
        if count <= 0 or self.phase == "cancelled":
            return
        if reason == "transport":
            self.transport_used = max(0, self.transport_used - count)
        else:
            self.used = max(0, self.used - count)
        self.phase = "active"
        self.attempts.append({"kind": "refund", "reason": reason, "count": count, "used": self.used})
        del self.attempts[:-32]

    def started(self, **metadata: Any) -> dict[str, Any]:
        self.requests += 1
        attempt = {"kind": "dispatch", "attempt": self.requests, **metadata}
        self.attempts.append(attempt)
        del self.attempts[:-32]
        return attempt

    def mark_cancelled(self) -> None:
        self.phase = "cancelled"

    def mark_paused(self) -> None:
        self.phase = "paused"

    def snapshot(self) -> dict[str, Any]:
        return {
            "used": self.used,
            "limit": self.limit,
            "transport_used": self.transport_used,
            "transport_limit": self.transport_limit,
            "transport_reserves": self.transport_reserves,
            "requests": self.requests,
            "phase": self.phase,
            "parent_id": self.parent_id,
            "invocation_id": self.invocation_id,
            "attempts": list(self.attempts),
        }


current_recovery: ContextVar[RecoveryState | None] = ContextVar(
    "provider_recovery", default=None
)


def reserve_recovery(reason: str) -> bool:
    """Consume one failure recovery when a shared state is installed.

    When no state is installed (legacy callers), return True so existing
    provider-local retry loops keep their own caps.
    """
    state = current_recovery.get()
    if state is None:
        return True
    return state.reserve(reason)


def recovery_limit_from_max_retries(max_retries: int | None) -> int:
    """Map Runtime ``max_retries`` (total attempts) to failure-recovery limit.

    ``max_retries`` counts the initial attempt plus recoveries. Explicit 0 or
    1 -> zero recoveries. None -> default 2 recoveries.
    """
    if max_retries is None:
        return 2
    return max(0, int(max_retries) - 1)
