"""Self-update's answers to the runtime's maintenance gate.

The runtime asks ``openprogram.runtime.maintenance`` at its own moments —
admitting a turn, committing a reply, claiming a Job, restarting, its
recovery pass, checking an unattended stage's turn, showing a retry for
approval. :func:`register` installs the answers below. The worker's
composition root (``operations/worker/runner.py``) and the Programs layer
(``programs/_host/lower_layers.py``) call it when they load; the
self-update package itself does not, so loading it (the CLI, the update
controller) does not load the runtime.

Each hook imports its self-update module when it is called, so a test that
replaces a self-update function is what the runtime sees.
"""
from __future__ import annotations

from typing import Any, Callable

_CONTINUATIONS = frozenset({"self_update_continue", "self_update_replan"})


def _busy(run: Callable[[], Any]) -> Any:
    """Run ``run``, reporting a briefly held update state as the runtime's
    ``StateBusy``."""
    from openprogram.runtime.maintenance import StateBusy

    from .types import ConcurrentUpdateError

    try:
        return run()
    except ConcurrentUpdateError as exc:
        raise StateBusy(str(exc)) from exc


def _turn_admission(source: str):
    from .control.maintenance import turn_admission

    return turn_admission(source)


def _hold_turn(request: Any) -> bool:
    from .control.held_turns import hold

    return hold(request, locked=True)


def _turn_committed(session_id: str, assistant_msg_id: str) -> None:
    from .control.handoff import release_prepared_update

    release_prepared_update(session_id, assistant_msg_id)


def _blocks(reason: str) -> bool:
    from .control.maintenance import maintenance_blocks

    return maintenance_blocks(reason)


def _open_state():
    from .store import SelfUpdateStore

    return SelfUpdateStore()


def _claim_job(governor, *, state, owner_instance_id, excluded_sessions, only_job_id):
    from .control.maintenance import claim_job

    return claim_job(governor, owner_instance_id=owner_instance_id,
                     excluded_sessions=excluded_sessions, only_job_id=only_job_id, store=state)


def _subscribe_changes(state, on_change):
    from openprogram.host._platform import changes

    from .control.watchdog import SELF_UPDATE_TOPIC, watch_store

    subscription = changes.subscribe(SELF_UPDATE_TOPIC, on_change)
    watch_store(state)
    return subscription


def _job_permission_snapshot(execution_store, job, current):
    if job.source == "self_update_continue":
        from .control.continuation import permission_snapshot

        return permission_snapshot(job)
    if job.source == "self_update_replan":
        from .delivery.restart import replan_permission_snapshot

        return replan_permission_snapshot(execution_store, job)
    return current


def _deliver_results() -> None:
    from .delivery.delivery import deliver_pending

    _busy(deliver_pending)


def _reconcile_jobs(runner, resume_interrupted) -> None:
    def run() -> None:
        from .control.continuation import reconcile as continue_updates
        from .control.recovery import stop_parked_stages
        from .delivery.restart import reconcile

        reconcile(runner)
        continue_updates(runner)
        resume_interrupted(runner)
        stop_parked_stages(runner)

    _busy(run)


def _require_execution(source: str, **fields: Any) -> None:
    if source in _CONTINUATIONS:
        from .control.continuation import require_execution

        require_execution(fields["request"])
        return
    if source == "self_update_verify":
        from .control.recovery import require_verifier_execution as require_stage
    elif source == "self_update_diagnose":
        from .repair.diagnosis import require_execution as require_stage
    elif source == "self_update_repair":
        from .repair.source_repair import require_execution as require_stage
    else:
        raise ValueError(f"{source} is not an unattended self-update stage")
    require_stage(**fields)


def _approval_preview(update_id, candidate_sha, request) -> dict:
    from .repair.next_candidate import approval_preview

    return approval_preview(update_id, candidate_sha, request)


def register() -> None:
    """Install self-update's answers in the runtime's maintenance gate."""
    from openprogram.runtime import maintenance

    maintenance.register(
        turn_admission=_turn_admission,
        hold_turn=_hold_turn,
        turn_committed=_turn_committed,
        blocks=_blocks,
        open_state=_open_state,
        claim_job=_claim_job,
        subscribe_changes=_subscribe_changes,
        job_permission_snapshot=_job_permission_snapshot,
        deliver_results=_deliver_results,
        reconcile_jobs=_reconcile_jobs,
        require_execution=_require_execution,
        approval_preview=_approval_preview,
    )


__all__ = ["register"]
