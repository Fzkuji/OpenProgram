"""Durable release of a prepared update after its origin turn commits."""

from __future__ import annotations

from openprogram.operations.self_update.store import SelfUpdateStore
from openprogram.operations.self_update.types import UpdatePhase, UpdateState


def release_prepared_update(
    session_id: str,
    assistant_id: str,
    *,
    store: SelfUpdateStore | None = None,
) -> UpdateState | None:
    """Release only the exact prepared request owned by this durable turn."""
    target = store or SelfUpdateStore()
    if not target.root.exists():
        return None
    record = target.load_active()
    if record is None or record.state.phase is not UpdatePhase.PREPARING:
        return None
    if record.request.origin_assistant_id != assistant_id:
        return None
    return target.transition(
        record.request.update_id,
        # An undo to a retained version, or a prebuilt release, has nothing to build.
        UpdatePhase.TRIAL if record.request.candidate_version else UpdatePhase.BUILDING,
        expected_phase=UpdatePhase.PREPARING,
        detail={
            "turn_released": True,
            "session_id": session_id,
            "assistant_id": assistant_id,
        },
    )


__all__ = ["release_prepared_update"]
