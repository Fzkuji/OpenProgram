"""The single startup recovery entry point for canonical executions."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from .outbox import ProjectionDispatchResult, ProjectionDispatcher


_log = logging.getLogger(__name__)
_PENDING_WAIT_RECOVERY_TASKS: set[asyncio.Task] = set()


def _recover_wait_outcomes(control_service) -> tuple[object, ...] | None:
    """Recover waits without nesting ``asyncio.run`` in an active loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(control_service.recover_wait_outcomes())

    task = loop.create_task(control_service.recover_wait_outcomes())
    _PENDING_WAIT_RECOVERY_TASKS.add(task)

    def _completed(done: asyncio.Task) -> None:
        _PENDING_WAIT_RECOVERY_TASKS.discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except Exception:
            _log.exception("failed to recover durable execution waits")

    task.add_done_callback(_completed)
    return None


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
    # A committed answer/decline/timeout can survive a process stop between
    # recording its outcome and leasing the continuation attempt.  Recovery
    # consumes that durable saga after expiry processing; it never reads a
    # process-local question callback.
    _recover_wait_outcomes(control_service)
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
