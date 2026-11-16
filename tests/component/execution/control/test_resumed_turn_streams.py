"""A turn resumed by the control service streams like the turn that began it.

An approval pauses the turn; answering it resumes the work through the
service's activator. That driver once had no event sink, so the resumed
reply and tool rows never reached the transcript until the turn ended.
"""
from __future__ import annotations

import asyncio

from openprogram.runtime.execution.control import shared as control_mod


def test_resumed_activation_streams_to_clients(tmp_path, monkeypatch):
    from openprogram.runtime.conversation import production_driver
    from openprogram.runtime.events import emit_ws_frame
    from openprogram.runtime.execution import store as execution_store_mod

    monkeypatch.setattr("openprogram.state.paths.get_run_state_path", lambda: tmp_path / "executions.db")
    execution_store_mod._store_for_path.cache_clear()
    with control_mod._default_control_services_lock:
        control_mod._default_control_services.clear()

    built: list[dict] = []

    class _Driver:
        def __init__(self, executions, **kwargs):
            built.append(kwargs)

        async def activate(self, attempt, activation):
            return "activated"

    monkeypatch.setattr(production_driver, "AgentProductionDriver", _Driver)
    try:
        service = control_mod.default_control_service()
        assert asyncio.run(service.activator(object(), object())) == "activated"
    finally:
        with control_mod._default_control_services_lock:
            control_mod._default_control_services.clear()
        execution_store_mod._store_for_path.cache_clear()

    assert built and built[0]["event_sink"] is emit_ws_frame
