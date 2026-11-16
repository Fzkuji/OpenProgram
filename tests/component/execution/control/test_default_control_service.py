from __future__ import annotations

from openprogram.runtime.execution import control
from openprogram.runtime.execution.control import shared as control_shared
from openprogram.state import paths as state_paths


def test_default_control_service_is_keyed_by_active_profile(tmp_path, monkeypatch):
    from openprogram.runtime.execution.store import ExecutionStore

    def fake_store():
        profile = state_paths.get_active_profile() or "default"
        path = tmp_path / profile / "executions.db"
        return ExecutionStore(path)

    monkeypatch.setattr(control_shared, "default_store", fake_store)
    monkeypatch.delenv("OPENPROGRAM_PROFILE", raising=False)
    control._default_control_services.clear()

    try:
        default = control.default_control_service()
        assert control.default_control_service() is default

        monkeypatch.setenv("OPENPROGRAM_PROFILE", "alpha")
        alpha = control.default_control_service()
        assert alpha is not default
        assert control.default_control_service() is alpha
    finally:
        control._default_control_services.clear()
