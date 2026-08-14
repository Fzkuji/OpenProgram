from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _task_runner_owns_worker_lock(request, monkeypatch):
    """Unit JobRunners model code executing inside the locked worker."""
    path = Path(str(request.node.path))
    if path.name in {"test_resource_governance.py", "test_worker_lock.py"}:
        yield
        return
    monkeypatch.setattr(
        "openprogram.worker.lock.is_held_by", lambda _pid: True,
    )
    yield
