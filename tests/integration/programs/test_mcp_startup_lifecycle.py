"""Core app readiness and cleanup with owned optional MCP connections."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
import sys

import httpx
import pytest


# Ceiling for waits that only guard against a hang; under machine load the
# awaited step can take seconds, so a tight bound fails healthy runs.
_HANG_CEILING_SECONDS = 30


async def _noop():
    return None


@pytest.fixture
def app_factory(monkeypatch):
    from openprogram.interfaces.server import server
    from openprogram.extensions.mcp import registry

    monkeypatch.setattr(server, "_recover_execution_control", _noop)
    monkeypatch.setattr(server, "reconcile_interrupted_runs", lambda: 0)
    monkeypatch.setattr("openprogram.extensions.skills.watcher.start_watcher", lambda **_: None)
    monkeypatch.setattr("openprogram.extensions.plugins.autoupdate.start", lambda: None)
    monkeypatch.setattr(registry, "_clients", {})
    monkeypatch.setattr(registry, "_registered_tool_names", {})
    monkeypatch.setattr(registry, "_loaded", False)
    started = asyncio.Event()
    original_client = registry.MCPClient

    def owned_client(config):
        client = original_client(config)
        started.set()
        return client

    monkeypatch.setattr(registry, "MCPClient", owned_client)

    def create_app():
        app = server.create_app()
        app.state.owned_mcp_started = started
        return app
    return create_app


def _remote_config():
    from openprogram.extensions.mcp.config import MCPServerConfig
    return MCPServerConfig(name="owned-pending", type="http", url="https://owned.invalid/mcp",
                           headers={"X-Key": "owned-fixture-secret"}, timeout_seconds=60)


async def _request(app, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:18100",
                               headers={"Authorization": f"Bearer {app.state.owner_auth.token}"}) as client:
        return await client.get(path)


def test_public_health_ready_while_optional_mcp_waits_then_shutdown_drains(app_factory, monkeypatch):
    from openprogram.extensions.mcp import client as client_module, registry
    monkeypatch.setattr(registry, "load_configs", lambda **_: [_remote_config()])

    async def run():
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        owned = []

        async def pending_auth(client):
            owned.append(client)
            entered.set()
            waits = [asyncio.create_task(release.wait()), asyncio.create_task(client._shutdown.wait())]
            try:
                await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                if client._shutdown.is_set():
                    raise asyncio.CancelledError
                raise RuntimeError("owned transport released during baseline cleanup")
            finally:
                for wait in waits:
                    wait.cancel()
                await asyncio.gather(*waits, return_exceptions=True)
                finished.set()

        monkeypatch.setattr(client_module.MCPClient, "_build_remote_auth", pending_auth)
        app = app_factory()
        lifecycle = app.router.lifespan_context(app)
        entering = asyncio.create_task(lifecycle.__aenter__())
        try:
            await asyncio.wait_for(entered.wait(), _HANG_CEILING_SECONDS)
            await asyncio.wait_for(asyncio.shield(entering), _HANG_CEILING_SECONDS)
            assert (await _request(app, "/healthz")).json()["status"] == "ok"
            statuses = (await _request(app, "/api/mcp/servers")).json()["servers"]
            assert len(statuses) == 1
            assert statuses[0]["ready"] is False and statuses[0]["tool_count"] == 0
            assert statuses[0]["error"] is None
            assert "owned-fixture-secret" not in str(statuses)
            assert registry.get_client("owned-pending") is owned[0]
        finally:
            # Release the owned lower transport even on RED; never leave the old
            # baseline's synchronous startup blocked after its readiness assertion.
            if not entering.done():
                release.set()
                await asyncio.wait_for(entering, _HANG_CEILING_SECONDS)
            await lifecycle.__aexit__(None, None, None)
            release.set()
            for client in owned:
                await client.stop()
        assert finished.is_set()
        assert all(client._supervisor_task.done() for client in owned)
        assert registry.list_clients() == []
        assert registry._registered_tool_names == {} and registry._loaded is False
        assert not [task for task in asyncio.all_tasks() if task.get_name() == "openprogram-mcp-startup"]

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["fatal", "needs_reauth"])
def test_public_health_remains_ready_and_mcp_failure_status_is_truthful(app_factory, monkeypatch, kind):
    from openprogram.extensions.mcp import client as client_module, registry
    monkeypatch.setattr(registry, "load_configs", lambda **_: [_remote_config()])

    async def fail_auth(_client):
        if kind == "needs_reauth":
            from mcp.client.auth import OAuthTokenError
            raise OAuthTokenError("owned fixture authorization rejection")
        raise RuntimeError("owned fixture transport failure")

    monkeypatch.setattr(client_module.MCPClient, "_build_remote_auth", fail_auth)

    async def run():
        app = app_factory()
        async with app.router.lifespan_context(app):
            assert (await _request(app, "/healthz")).status_code == 200
            await asyncio.wait_for(app.state.owned_mcp_started.wait(), _HANG_CEILING_SECONDS)
            client = registry.get_client("owned-pending")
            assert client is not None
            await asyncio.wait_for(client._ready.wait(), _HANG_CEILING_SECONDS)
            await asyncio.sleep(0)
            status = (await _request(app, "/api/mcp/servers")).json()["servers"][0]
            assert status["ready"] is False and status["tool_count"] == 0
            assert status["error"] == "mcp_server_unavailable" and status["error_kind"] == kind
            assert "owned-fixture-secret" not in str(status)
        assert registry.list_clients() == []
    asyncio.run(run())


def test_public_app_successfully_registers_real_owned_mcp_tools_and_removes_them(app_factory, monkeypatch):
    from openprogram.extensions.mcp import registry
    from openprogram.extensions.mcp.config import MCPServerConfig
    from openprogram.programs._host.runtime import _registry
    command = [sys.executable, str(Path(__file__).with_name("_mcp_fake_server.py"))]
    config = MCPServerConfig(name="owned-ready", command=command, timeout_seconds=10)
    monkeypatch.setattr(registry, "load_configs", lambda **_: [config])

    async def run():
        app = app_factory()
        async with app.router.lifespan_context(app):
            assert (await _request(app, "/healthz")).status_code == 200
            await asyncio.wait_for(app.state.owned_mcp_started.wait(), _HANG_CEILING_SECONDS)
            client = registry.get_client("owned-ready")
            assert client is not None
            await asyncio.wait_for(client._ready.wait(), _HANG_CEILING_SECONDS)
            await asyncio.sleep(0)
            status = (await _request(app, "/api/mcp/servers")).json()["servers"][0]
            assert status["ready"] is True and status["tool_count"] == 2
            names = status["registered_tool_names"]
            assert len(names) == 2
            echo = next(name for name in names if name.endswith("echo"))
            result = await _registry[echo].execute("owned-call", {"message": "owned"}, None, None)
            assert result.is_error is False and result.content[0].text == "OWNED"
        assert registry.list_clients() == []
        assert all(name not in _registry for name in names)
        assert client._supervisor_task.done()
    asyncio.run(run())


@pytest.mark.parametrize("operation", ["delete", "disable", "patch"])
def test_public_queued_server_management_stays_authoritative(app_factory, monkeypatch, operation):
    from openprogram.extensions.mcp import registry
    from openprogram.extensions.mcp.client import MCPClient
    from openprogram.extensions.mcp.config import MCPServerConfig, save_configs
    from openprogram.programs._host.runtime import _registry

    first = _remote_config()
    second = MCPServerConfig(name="owned-queued", timeout_seconds=10,
                             command=[sys.executable, str(Path(__file__).with_name("_mcp_fake_server.py"))])
    save_configs([first, second])

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        original_auth = MCPClient._build_remote_auth

        async def pending_auth(client):
            if client.config.name != first.name:
                return await original_auth(client)
            entered.set()
            await release.wait()
            raise RuntimeError("owned transport released")

        monkeypatch.setattr(MCPClient, "_build_remote_auth", pending_auth)
        app = app_factory()
        patched_client = None
        registered = []
        lifecycle = app.router.lifespan_context(app)
        await lifecycle.__aenter__()
        try:
            await asyncio.wait_for(entered.wait(), _HANG_CEILING_SECONDS)
            assert registry.get_client(second.name) is None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                         base_url="http://127.0.0.1:18100",
                                         headers={"Authorization": f"Bearer {app.state.owner_auth.token}"}) as client:
                endpoint = f"/api/mcp/servers/{second.name}"
                if operation == "delete":
                    response = await client.delete(endpoint)
                elif operation == "disable":
                    response = await client.post(endpoint + "/disable")
                else:
                    response = await client.patch(endpoint, json={"timeout_seconds": 17})
                    patched_client = registry.get_client(second.name)
                assert response.status_code == 200, response.text
                startup = next(task for task in asyncio.all_tasks()
                               if task.get_name() == "openprogram-mcp-startup")
                release.set()
                await asyncio.wait_for(asyncio.shield(startup), _HANG_CEILING_SECONDS)
                statuses = (await client.get("/api/mcp/servers")).json()["servers"]
                status = next((row for row in statuses if row["name"] == second.name), None)
                if operation == "delete":
                    assert status is None
                    assert registry.get_client(second.name) is None
                elif operation == "disable":
                    assert status["enabled"] is False and status["ready"] is False
                    assert status["tool_count"] == 0 and status["registered_tool_names"] == []
                else:
                    assert registry.get_client(second.name) is patched_client
                    assert status["timeout_seconds"] == 17 and status["ready"] is True
                    registered = status["registered_tool_names"]
                    assert len(registered) == 2
        finally:
            release.set()
            await lifecycle.__aexit__(None, None, None)
            # Also clean up the original public PATCH client on the failing
            # baseline, where stale startup replaces its registry ownership.
            if patched_client is not None:
                finished_on_exit = patched_client._supervisor_task.done()
                await patched_client.stop()
        assert registry.list_clients() == []
        assert all(name not in _registry for name in registered)
        if patched_client is not None:
            assert finished_on_exit

    asyncio.run(run())


def test_real_callback_cancel_and_close_release_executor_waiter_and_socket():
    from openprogram.extensions.mcp.oauth_flow import LocalhostCallback

    async def run():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        callback = LocalhostCallback(timeout=60)
        await callback.start()
        waiter = asyncio.create_task(callback.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with suppress(asyncio.CancelledError):
            await waiter
        try:
            await callback.close()
            # The executor has one actual worker: this public submitted job
            # cannot complete while the prior callback event wait is still alive.
            assert await asyncio.wait_for(loop.run_in_executor(None, lambda: "released"), _HANG_CEILING_SECONDS) == "released"
            assert not callback._thread.is_alive()
            async with httpx.AsyncClient(trust_env=False) as client:
                with pytest.raises(httpx.ConnectError):
                    await client.get(callback.redirect_uri)
        finally:
            # Explicit test-owned teardown also releases the failing baseline.
            callback._result_event.set()
            await callback.close()
    asyncio.run(run())


@pytest.mark.parametrize("query, outcome", [
    ({"code": "owned-code", "state": "owned-state"}, ("owned-code", "owned-state")),
    ({"state": "owned-state"}, "missing 'code'"),
    ({"error": "access_denied"}, "OAuth error"),
])
def test_owned_callback_keeps_actual_code_state_and_error_validation(query, outcome):
    from openprogram.extensions.mcp.oauth_flow import LocalhostCallback

    async def run():
        callback = LocalhostCallback(timeout=2)
        await callback.start()
        waiter = asyncio.create_task(callback.wait())
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                wrong_path = await client.get(callback.redirect_uri.replace("/callback", "/wrong"))
                assert wrong_path.status_code == 404 and not waiter.done()
                await client.get(callback.redirect_uri, params=query)
            if isinstance(outcome, tuple):
                assert await waiter == outcome
            else:
                with pytest.raises(RuntimeError, match=outcome):
                    await waiter
        finally:
            callback._result_event.set()
            await callback.close()
            with suppress(RuntimeError):
                await waiter
    asyncio.run(run())


def test_canceled_public_lifespan_drains_pending_optional_startup(app_factory, monkeypatch):
    from openprogram.extensions.mcp import client as client_module, registry
    monkeypatch.setattr(registry, "load_configs", lambda **_: [_remote_config()])

    async def run():
        entered, serving = asyncio.Event(), asyncio.Event()
        owned = []

        async def pending_auth(client):
            owned.append(client)
            entered.set()
            await client._shutdown.wait()
            raise asyncio.CancelledError

        monkeypatch.setattr(client_module.MCPClient, "_build_remote_auth", pending_auth)
        app = app_factory()

        async def serve():
            async with app.router.lifespan_context(app):
                serving.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(serve())
        try:
            await asyncio.wait_for(entered.wait(), _HANG_CEILING_SECONDS)
            await asyncio.wait_for(serving.wait(), _HANG_CEILING_SECONDS)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert registry.list_clients() == [] and registry._loaded is False
            assert all(client._supervisor_task.done() for client in owned)
            assert not [task for task in asyncio.all_tasks() if task.get_name() == "openprogram-mcp-startup"]
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            for client in owned:
                await client.stop()
    asyncio.run(run())


def test_public_health_survives_and_observes_background_loader_exception(app_factory, monkeypatch):
    from openprogram.extensions.mcp import registry
    from openprogram.interfaces.server import server

    async def run():
        observed = asyncio.Event()
        logs = []

        def bad_config(**_):
            raise RuntimeError("owned configuration fixture failure")

        def log(message):
            logs.append(message)
            if message.startswith("[mcp] startup failed:"):
                observed.set()

        monkeypatch.setattr(registry, "load_configs", bad_config)
        monkeypatch.setattr(server, "_log", log)
        app = app_factory()
        async with app.router.lifespan_context(app):
            assert (await _request(app, "/healthz")).status_code == 200
            await asyncio.wait_for(observed.wait(), _HANG_CEILING_SECONDS)
            assert any("RuntimeError" in message for message in logs)
        assert registry.list_clients() == [] and registry._loaded is False
    asyncio.run(run())


@pytest.mark.parametrize("same_state", [True, False])
def test_real_callback_preserves_sdk_state_validation(same_state):
    from urllib.parse import parse_qs, urlparse
    from mcp.client.auth import OAuthClientProvider, OAuthFlowError
    from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata
    from openprogram.extensions.mcp.oauth_flow import LocalhostCallback

    async def run():
        callback = LocalhostCallback(timeout=2)
        await callback.start()

        async def redirect(url):
            # SDK creates a real state/PKCE value, but no external URL is opened.
            state = parse_qs(urlparse(url).query)["state"][0]
            async with httpx.AsyncClient(trust_env=False) as client:
                await client.get(callback.redirect_uri, params={
                    "code": "owned-code", "state": state if same_state else "owned-mismatched-state",
                })

        provider = OAuthClientProvider(
            server_url="https://owned.invalid/mcp",
            client_metadata=OAuthClientMetadata(client_name="owned", redirect_uris=[callback.redirect_uri]),
            storage=object(), redirect_handler=redirect, callback_handler=callback.wait,
        )
        provider.context.client_info = OAuthClientInformationFull(client_id="owned-client", redirect_uris=[callback.redirect_uri])
        try:
            if same_state:
                code, _ = await provider._perform_authorization_code_grant()
                assert code == "owned-code"
            else:
                with pytest.raises(OAuthFlowError, match="[Ss]tate"):
                    await provider._perform_authorization_code_grant()
        finally:
            callback._result_event.set()
            await callback.close()
    asyncio.run(run())


def test_public_remove_pending_server_prevents_late_registry_reappearance(app_factory, monkeypatch):
    from openprogram.extensions.mcp import client as client_module, registry
    config = _remote_config()
    config.timeout_seconds = 0.1
    monkeypatch.setattr(registry, "load_configs", lambda **_: [config])

    async def run():
        entered = asyncio.Event()

        async def pending_auth(client):
            entered.set()
            await client._shutdown.wait()
            raise asyncio.CancelledError

        monkeypatch.setattr(client_module.MCPClient, "_build_remote_auth", pending_auth)
        app = app_factory()
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(entered.wait(), _HANG_CEILING_SECONDS)
            startup = next(task for task in asyncio.all_tasks() if task.get_name() == "openprogram-mcp-startup")
            assert await registry.remove_server(config.name) is True
            await asyncio.wait_for(startup, _HANG_CEILING_SECONDS)
            assert registry.get_server(config.name) is None
            assert registry._registered_tool_names == {}
            assert (await _request(app, "/healthz")).status_code == 200
    asyncio.run(run())
