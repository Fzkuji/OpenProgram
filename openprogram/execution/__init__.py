"""Canonical execution identity, lifecycle, commands, and persistence.

This package owns the control-plane data model. Runtime-specific drivers live
beside their execution engines and may not write canonical lifecycle state
directly.
"""

from .model import (
    CapabilitySet,
    CommandKind,
    CommandStatus,
    ControlCommand,
    ExecutionEvent,
    ExecutionInputRecord,
    ExecutionSnapshot,
    EventCursor,
    JobResourceDTO,
    ExecutionRecord,
    ExecutionStatus,
    RevisionRecord,
    RunRecord,
)
from .attempts import AttemptRecord, AttemptStatus, AttemptStore
from .checkpoints import (
    CheckpointFragment,
    CheckpointManifest,
    ExecutionCheckpointStore,
)
from .effects import (
    EffectClassification,
    EffectRecord,
    EffectStatus,
    EffectStore,
)
from .driver import (
    ActivationInput,
    DriverAck,
    DriverBinding,
    DriverRegistry,
    ExecutionDriver,
    RuntimeSnapshot,
    TerminationReceipt,
)
from .control import (
    AttemptCompletion,
    BranchCompletion,
    ControlDispatch,
    ReconciliationCompletion,
    RuntimeControlService,
    SafePointCompletion,
    default_control_service,
)
from .store import ExecutionStore, ProjectionConflict, default_store
from .outbox import (
    ProjectionDispatchResult,
    ProjectionDispatcher,
    ProjectionOutboxRecord,
    ProjectionOutboxState,
)
from .projections import (
    ExecutionProjectionReadModel,
    ExecutionProjectionRecord,
    ExecutionProjectionWorker,
    list_running_execution_projections,
    projection_handlers,
    start_projection_worker,
    stop_projection_worker,
    wake_projection_worker,
)
from .startup import StartupRecoveryResult, recover_execution_startup
from .resource_saga import ResourceSaga, recover_resource_saga

__all__ = [
    "CommandKind",
    "CapabilitySet",
    "CommandStatus",
    "ControlCommand",
    "ExecutionEvent",
    "ExecutionInputRecord",
    "ExecutionSnapshot",
    "EventCursor",
    "JobResourceDTO",
    "ExecutionRecord",
    "ExecutionStatus",
    "RevisionRecord",
    "RunRecord",
    "ExecutionStore",
    "AttemptRecord",
    "AttemptStatus",
    "AttemptStore",
    "CheckpointManifest",
    "CheckpointFragment",
    "ExecutionCheckpointStore",
    "EffectClassification",
    "EffectRecord",
    "EffectStatus",
    "EffectStore",
    "DriverAck",
    "ActivationInput",
    "DriverBinding",
    "DriverRegistry",
    "ExecutionDriver",
    "RuntimeSnapshot",
    "TerminationReceipt",
    "ControlDispatch",
    "RuntimeControlService",
    "SafePointCompletion",
    "AttemptCompletion",
    "BranchCompletion",
    "ReconciliationCompletion",
    "default_control_service",
    "default_store",
    "ProjectionConflict",
    "ProjectionDispatchResult",
    "ProjectionDispatcher",
    "ProjectionOutboxRecord",
    "ProjectionOutboxState",
    "ExecutionProjectionReadModel",
    "ExecutionProjectionRecord",
    "ExecutionProjectionWorker",
    "projection_handlers",
    "start_projection_worker",
    "stop_projection_worker",
    "wake_projection_worker",
    "list_running_execution_projections",
    "StartupRecoveryResult",
    "recover_execution_startup",
    "ResourceSaga",
    "recover_resource_saga",
]
