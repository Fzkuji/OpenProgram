"""The single startup recovery entry point for canonical executions."""

from __future__ import annotations

from dataclasses import dataclass

from .outbox import ProjectionDispatchResult, ProjectionDispatcher


@dataclass(frozen=True)
class StartupRecoveryResult:
    canonical: tuple[object, ...]
    projections: ProjectionDispatchResult


def recover_execution_startup(
    *,
    control_service=None,
    projection_dispatcher: ProjectionDispatcher | None = None,
    projection_owner_id: str = "execution-startup",
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
    canonical = tuple(control_service.recover_startup())
    if projection_dispatcher is None:
        from .store import default_store

        projection_dispatcher = ProjectionDispatcher(default_store(), {})
    projections = projection_dispatcher.recover_startup(owner_id=projection_owner_id)
    return StartupRecoveryResult(canonical=canonical, projections=projections)
