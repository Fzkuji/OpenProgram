from __future__ import annotations

import pytest

from openprogram.runtime.jobs.resource_governance import (
    ResourceLimits,
    ResourceGovernor,
    resolve_resource_limits,
)
from openprogram.runtime.jobs.types import Job
from openprogram.models.usage.ledger import UsageLedger

from tests.component.agent import async_job_support as _async_job_support

# Shared pytest fixtures, bound at module level so pytest collects them here.
store_fixture = _async_job_support.store_fixture


@pytest.fixture(autouse=True)
def _worker_lock_is_held(monkeypatch):
    monkeypatch.setattr(
        "openprogram.operations.worker.lock.is_held_by", lambda _pid: True,
        raising=False,
    )
