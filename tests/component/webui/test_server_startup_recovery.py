from __future__ import annotations

import asyncio


async def _async_noop():
    return None


def test_canonical_execution_recovery_runs_before_legacy_dag_recovery(
    monkeypatch,
):
    from openprogram.webui import server

    events = []

    async def recover_execution_control():
        events.append("execution")

    def reconcile_interrupted_runs():
        events.append("dag")
        return 0

    monkeypatch.setattr(server, "_recover_execution_control", recover_execution_control)
    monkeypatch.setattr(server, "reconcile_interrupted_runs", reconcile_interrupted_runs)
    monkeypatch.setattr("openprogram.mcp.load_mcp_servers", _async_noop)
    monkeypatch.setattr("openprogram.mcp.shutdown_mcp_servers", _async_noop)
    monkeypatch.setattr("openprogram.skills.watcher.start_watcher", lambda **_: None)
    monkeypatch.setattr("openprogram.plugins.autoupdate.start", lambda: None)
    monkeypatch.setattr("openprogram.agent._rewind.recover_all_rewinds", lambda: 0)

    app = server.create_app(owner_auth=object())

    async def run_lifespan():
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(run_lifespan())

    assert events == ["execution", "dag"]
