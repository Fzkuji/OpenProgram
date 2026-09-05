from __future__ import annotations

from openprogram.execution import control


def test_default_control_service_is_keyed_by_active_profile(tmp_path, monkeypatch):
    from openprogram.execution.store import ExecutionStore

    def fake_store():
        profile = control.get_active_profile() or "default"
        path = tmp_path / profile / "executions.db"
        return ExecutionStore(path)

    monkeypatch.setattr(control, "default_store", fake_store)
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
