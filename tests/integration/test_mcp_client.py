"""End-to-end MCP client + registry tests.

Spawns a tiny FastMCP server as a real subprocess and verifies:

  * client startup hits ``initialize`` + ``list_tools``
  * ``call_tool`` round-trips text args and integer args
  * the registry path wraps remote tools as AgentTool entries with
    the ``{server}__{tool}`` namespace and forwards execute() to MCP
  * ``shutdown`` releases the subprocess cleanly

The tests need the ``mcp`` Python package and Python 3.11+; both are
required by the framework itself.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import pytest


FAKE_SERVER = Path(__file__).parent / "_mcp_fake_server.py"


def _server_config() -> dict:
    return {
        "servers": {
            "fake": {
                "type": "local",
                "command": [sys.executable, str(FAKE_SERVER)],
                "enabled": True,
                "timeout_seconds": 10,
            }
        }
    }


@pytest.fixture
def temp_state_dir(monkeypatch):
    """Point ``openprogram.paths.get_state_dir`` at a fresh tmp dir
    with its own ``~/.agentic`` so the test never touches the real one.
    """
    d = Path(tempfile.mkdtemp(prefix="mcp_test_"))
    state_dir = d / ".agentic"
    state_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(d))
    yield state_dir


@pytest.fixture(autouse=True)
def _reset_mcp_loader_flag():
    """The registry's ``_loaded`` is process-global so it doesn't re-
    spawn subprocesses on accidental double calls in production. Other
    suites (FastAPI healthz tests run via TestClient) trigger the
    startup hook and leave it set, which would make every load call
    here short-circuit. Force a clean slate at the start of each test.
    """
    from openprogram.mcp import registry as _mcp_registry
    _mcp_registry._loaded = False
    _mcp_registry._clients.clear()
    _mcp_registry._registered_tool_names.clear()
    yield
    _mcp_registry._loaded = False
    _mcp_registry._clients.clear()
    _mcp_registry._registered_tool_names.clear()


def test_client_round_trip():
    async def _run():
        from openprogram.mcp.client import MCPClient
        from openprogram.mcp.config import MCPServerConfig

        cfg = MCPServerConfig(
            name="fake",
            type="local",
            command=[sys.executable, str(FAKE_SERVER)],
            enabled=True,
            timeout_seconds=10.0,
        )
        client = MCPClient(cfg)
        await client.start()
        try:
            assert client.is_ready
            assert client.error is None
            names = {t.name for t in client.tools}
            assert names == {"echo", "add"}

            r1 = await client.call_tool("echo", {"message": "hello"})
            assert r1.isError is False
            assert r1.content[0].text == "HELLO"

            r2 = await client.call_tool("add", {"a": 7, "b": 35})
            assert r2.isError is False
            assert r2.content[0].text == "42"
        finally:
            await client.stop()

    asyncio.run(_run())


def test_registry_registers_namespaced_tools(temp_state_dir):
    cfg_path = temp_state_dir / "mcp_servers.json"
    cfg_path.write_text(json.dumps(_server_config()))

    from openprogram.mcp import (
        load_mcp_servers,
        shutdown_mcp_servers,
        server_status,
    )
    from openprogram.functions._runtime import _registry

    before = set(_registry.keys())

    async def _run():
        await load_mcp_servers()
        try:
            new_names = set(_registry.keys()) - before
            assert "fake__echo" in new_names
            assert "fake__add" in new_names

            statuses = server_status()
            assert len(statuses) == 1
            assert statuses[0]["name"] == "fake"
            assert statuses[0]["ready"] is True
            assert statuses[0]["error"] is None
            assert set(statuses[0]["tools"]) == {"echo", "add"}

            result = await _registry["fake__echo"].execute(
                "call-1", {"message": "round trip"}, None, None,
            )
            assert result.content[0].text == "ROUND TRIP"
            assert result.details["mcp_server"] == "fake"
            assert result.details["mcp_tool"] == "echo"
        finally:
            await shutdown_mcp_servers()
            for name in ("fake__echo", "fake__add"):
                _registry.pop(name, None)

    asyncio.run(_run())


def test_unconfigured_loader_is_noop(monkeypatch):
    """Missing ``mcp_servers.json`` → no-op, no error."""
    d = Path(tempfile.mkdtemp(prefix="mcp_empty_"))
    (d / ".agentic").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(d))

    from openprogram.mcp import (
        load_mcp_servers,
        server_status,
        shutdown_mcp_servers,
    )

    async def _run():
        await load_mcp_servers()
        try:
            assert server_status() == []
        finally:
            await shutdown_mcp_servers()

    asyncio.run(_run())


def test_bad_command_records_error(monkeypatch):
    """Server that fails to spawn should record the error, not crash."""
    d = Path(tempfile.mkdtemp(prefix="mcp_bad_"))
    state = d / ".agentic"
    state.mkdir(parents=True)
    (state / "mcp_servers.json").write_text(json.dumps({
        "servers": {
            "broken": {
                "type": "local",
                "command": ["/does/not/exist/at/all"],
                "enabled": True,
                "timeout_seconds": 3,
            }
        }
    }))
    monkeypatch.setenv("HOME", str(d))

    from openprogram.mcp import (
        load_mcp_servers,
        server_status,
        shutdown_mcp_servers,
    )

    async def _run():
        await load_mcp_servers()
        try:
            st = server_status()
            assert len(st) == 1
            assert st[0]["ready"] is False
            assert st[0]["error"]
        finally:
            await shutdown_mcp_servers()

    asyncio.run(_run())
