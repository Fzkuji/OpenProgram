"""Durable projection delivery for canonical execution events."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

from ._schema import PROJECTION_KINDS


class ProjectionOutboxState(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DELIVERED = "delivered"


@dataclass(frozen=True)
class ProjectionOutboxRecord:
    outbox_id: str
    event_sequence: int
    execution_id: str
    projection_kind: str
    dedupe_key: str
    payload_ref: str
    state: ProjectionOutboxState
    claim_owner: str | None
    claim_expires_at: float | None
    attempts: int
    available_at: float
    delivered_at: float | None
    last_error: str | None


@dataclass(frozen=True)
class ProjectionDispatchResult:
    claimed: int
    delivered: int
    failed: int


ProjectionHandler = Callable[[ProjectionOutboxRecord], object]


class ProjectionDispatcher:
    """Claim and deliver projections without changing canonical execution state."""

    def __init__(self, store, handlers: Mapping[str, ProjectionHandler]):
        unknown = set(handlers) - set(PROJECTION_KINDS)
        if unknown:
            raise ValueError(f"unsupported projection kinds: {sorted(unknown)}")
        self.store = store
        self.handlers = dict(handlers)

    def dispatch_once(
        self,
        *,
        owner_id: str,
        limit: int = 100,
        lease_ttl_seconds: float = 30.0,
    ) -> ProjectionDispatchResult:
        claimed = self.store.claim_projection_outbox(
            owner_id=owner_id,
            limit=limit,
            lease_ttl_seconds=lease_ttl_seconds,
            allowed_kinds=self.handlers,
        )
        delivered = 0
        failed = 0
        for item in claimed:
            handler = self.handlers.get(item.projection_kind)
            if handler is None:
                continue
            try:
                handler(item)
                self.store.ack_projection_outbox(item.outbox_id, owner_id=owner_id)
            except Exception as exc:  # noqa: BLE001 - delivery must be retryable
                from .store import ProjectionConflict

                if isinstance(exc, ProjectionConflict):
                    # The lease may have expired while a handler was running.
                    # A later reclaim will make the item available again.
                    failed += 1
                    continue
                try:
                    self.store.fail_projection_outbox(
                        item.outbox_id,
                        owner_id=owner_id,
                        error=str(exc) or type(exc).__name__,
                    )
                except ProjectionConflict:
                    # Failure reporting is also fenced; a lost claim is not a
                    # startup-fatal dispatcher error.
                    pass
                failed += 1
            else:
                delivered += 1
        return ProjectionDispatchResult(
            claimed=len(claimed), delivered=delivered, failed=failed
        )

    def recover_startup(
        self,
        *,
        owner_id: str,
        limit: int = 100,
        lease_ttl_seconds: float = 30.0,
        max_batches: int = 10,
        max_seconds: float = 1.0,
    ) -> ProjectionDispatchResult:
        """Reclaim abandoned leases, then replay ready projections."""
        self.store.reclaim_projection_outbox()
        # No consumer is an explicit pending state.  Never claim an item that
        # cannot be acknowledged by a real projection handler.
        if not self.handlers:
            return ProjectionDispatchResult(claimed=0, delivered=0, failed=0)
        return self.drain(
            owner_id=owner_id,
            limit=limit,
            lease_ttl_seconds=lease_ttl_seconds,
            max_batches=max_batches,
            max_seconds=max_seconds,
        )

    def drain(
        self,
        *,
        owner_id: str,
        limit: int = 100,
        lease_ttl_seconds: float = 30.0,
        max_batches: int | None = None,
        max_seconds: float | None = None,
    ) -> ProjectionDispatchResult:
        """Deliver bounded batches until caught up or one batch fails.

        A failed delivery stays pending.  Stopping at that batch prevents a
        permanently failing consumer from spinning its own retry loop.
        """
        if max_batches is not None and max_batches <= 0:
            raise ValueError("max_batches must be positive")
        if max_seconds is not None and max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        claimed = delivered = failed = batches = 0
        deadline = time.monotonic() + max_seconds if max_seconds is not None else None
        while True:
            result = self.dispatch_once(
                owner_id=owner_id,
                limit=limit,
                lease_ttl_seconds=lease_ttl_seconds,
            )
            claimed += result.claimed
            delivered += result.delivered
            failed += result.failed
            batches += 1
            if (
                result.claimed < limit
                or result.failed
                or (max_batches is not None and batches >= max_batches)
                or (deadline is not None and time.monotonic() >= deadline)
            ):
                return ProjectionDispatchResult(
                    claimed=claimed, delivered=delivered, failed=failed
                )
