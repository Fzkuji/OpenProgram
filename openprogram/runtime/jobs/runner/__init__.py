"""JobRunner — ThreadPoolExecutor-backed worker pool."""
from openprogram.runtime.jobs.runner.service import JobRunner
from openprogram.runtime.jobs.runner.shared import (
    JobGovernanceContext,
    NonPreemptibleOperation,
    _broadcast,
    _current_job_governance,
    _current_job_id,
    _current_job_runner,
    _mirror_linked_job_to_caller,
    current_runner,
    current_job_operation_timeout,
    current_job_operation_timeout_reason,
    current_job_resource_context,
    get_runner,
    record_current_job_activity,
    runner_for_execution_store,
    shutdown_runner,
)

__all__ = [
    "JobGovernanceContext",
    "JobRunner",
    "NonPreemptibleOperation",
    "_broadcast",
    "_current_job_governance",
    "_current_job_id",
    "_current_job_runner",
    "_mirror_linked_job_to_caller",
    "current_job_operation_timeout",
    "current_job_operation_timeout_reason",
    "current_job_resource_context",
    "current_runner",
    "get_runner",
    "record_current_job_activity",
    "runner_for_execution_store",
    "shutdown_runner",
]
