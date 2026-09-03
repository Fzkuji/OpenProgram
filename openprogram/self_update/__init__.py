"""Conversational self-update state protocol.

App activation and rollback deliberately live outside the worker process. This
package exposes the durable request/state contract and the dispatcher handoff
that releases a prepared request only after its origin turn is durable.
"""

from .handoff import release_prepared_update
from .store import SelfUpdateStore
from .types import (
    SCHEMA_VERSION,
    ActiveUpdateError,
    ConcurrentUpdateError,
    CorruptUpdateStateError,
    InvalidTransitionError,
    IterationMode,
    IterationPolicy,
    SelfUpdateError,
    UpdateExistsError,
    UpdateNotFoundError,
    UpdatePhase,
    UpdateRecord,
    UpdateRequest,
    UpdateState,
    VerifierClaim,
    VerifierDispatch,
    can_transition,
    is_terminal,
    mint_update_id,
)

__all__ = [
    "SCHEMA_VERSION",
    "ActiveUpdateError",
    "ConcurrentUpdateError",
    "CorruptUpdateStateError",
    "InvalidTransitionError",
    "IterationMode",
    "IterationPolicy",
    "SelfUpdateError",
    "SelfUpdateStore",
    "UpdateExistsError",
    "UpdateNotFoundError",
    "UpdatePhase",
    "UpdateRecord",
    "UpdateRequest",
    "UpdateState",
    "VerifierClaim",
    "VerifierDispatch",
    "can_transition",
    "is_terminal",
    "mint_update_id",
    "release_prepared_update",
]
