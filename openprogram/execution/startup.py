"""The single startup recovery entry point for canonical executions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .outbox import ProjectionDispatchResult, ProjectionDispatcher


@dataclass(frozen=True)
class StartupRecoveryResult:
    canonical: tuple[object, ...]
    projections: ProjectionDispatchResult
    waits_reclaimed: int = 0
    waits_expired: int = 0


def recover_execution_startup(
    *,
    control_service=None,
    projection_dispatcher: ProjectionDispatcher | None = None,
    projection_owner_id: str | None = None,
) -> StartupRecoveryResult:
    """Recover canonical state first, then reclaim and replay projections.

    The canonical recovery step is authoritative. Projection delivery is
    independent and is never allowed to mutate canonical execution state.
    """
    if control_service is None:
        # Resolve through the package export so application startup can
        # replace the single canonical service in tests and deployments.
        from openprogram.execution import default_control_service

        control_service = default_control_service()
    # Recovery first fences or closes stale execution owners.  Only then can
    # a claimed wait be judged orphaned and returned to an authorized client.
    from .waits import DurableWaitStore

    waits = DurableWaitStore(control_service.executions)
    canonical = tuple(control_service.recover_startup())
    waits_reclaimed = (
        waits.reclaim_expired_claims()
        + waits.reclaim_orphaned_claims()
    )
    waits_expired = waits.expire_due()
    if projection_dispatcher is None:
        from .store import default_store
        from .projections import projection_handlers

        store = default_store()
        projection_dispatcher = ProjectionDispatcher(store, projection_handlers(store))
    owner_id = projection_owner_id or f"execution-startup-{uuid.uuid4().hex}"
    projections = projection_dispatcher.recover_startup(owner_id=owner_id)
    return StartupRecoveryResult(
        canonical=canonical, projections=projections,
        waits_reclaimed=waits_reclaimed, waits_expired=waits_expired,
    )
