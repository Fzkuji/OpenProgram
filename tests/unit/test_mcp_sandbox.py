from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.mcp.config import MCPServerConfig


def test_forced_local_mcp_uses_shared_sandbox_invocation(tmp_path, monkeypatch):
    from openprogram import sandbox
    from openprogram.backend import local
    from openprogram.mcp.client import MCPClient

    captured = {}
    policy = sandbox.SandboxPolicy(pass_env=("MCP_MODE",))
    monkeypatch.setattr(sandbox, "resolve_policy", lambda required=False: policy)

    def fake_invocation(command, cwd=None, *, policy=None, force_sandbox=False):
        captured.update(command=command, cwd=cwd, policy=policy,
                        force_sandbox=force_sandbox)
        return ["sandbox-wrapper", "--", command], False, {"PATH": "/bin"}, True

    monkeypatch.setattr(local, "_invocation", fake_invocation)
    cfg = MCPServerConfig(
        name="probe",
        command=["python", "-m", "server with space"],
        env={"MCP_MODE": "probe", "API_SECRET": "must-drop"},
    )
    client = MCPClient(cfg, force_sandbox=True, sandbox_cwd=str(tmp_path))

    params = client._local_parameters()

    assert captured["force_sandbox"] is True
    assert captured["cwd"] == str(tmp_path)
    assert captured["command"] == "python -m 'server with space'"
    assert params.command == "sandbox-wrapper"
    assert params.args == ["--", "python -m 'server with space'"]
    assert params.env["MCP_MODE"] == "probe"
    assert "API_SECRET" not in params.env


def test_regular_owner_configured_mcp_keeps_fixed_argv_and_env(monkeypatch):
    from openprogram.mcp.client import MCPClient

    cfg = MCPServerConfig(
        name="owner-server",
        command=["python", "-m", "server"],
        env={"OWNER_SETTING": "1"},
    )
    params = MCPClient(cfg)._local_parameters()
    assert params.command == "python"
    assert params.args == ["-m", "server"]
    assert params.env["OWNER_SETTING"] == "1"


@pytest.mark.parametrize(
    ("client", "host", "origin", "site"),
    [
        ("10.0.0.5", "127.0.0.1:18100", "http://127.0.0.1:18100", "same-origin"),
        ("127.0.0.1", "127.0.0.1:18100", "http://localhost:9999", "same-site"),
    ],
)
def test_one_shot_mcp_rejects_nonlocal_or_cross_origin_context(
    client, host, origin, site,
):
    from fastapi import HTTPException
    from openprogram.webui.routes.mcp import _require_local_request

    request = SimpleNamespace(
        client=SimpleNamespace(host=client),
        headers={"host": host, "origin": origin, "sec-fetch-site": site},
    )
    with pytest.raises(HTTPException) as exc:
        _require_local_request(request)
    assert exc.value.status_code == 403


@pytest.mark.parametrize(("origin", "site"), [
    ("http://127.0.0.1:18100", "same-origin"),
    ("", ""),
])
def test_one_shot_mcp_accepts_loopback_clients(origin, site):
    from openprogram.webui.routes.mcp import _require_local_request

    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={
            "host": "127.0.0.1:18100",
            "origin": origin,
            "sec-fetch-site": site,
        },
    )
    _require_local_request(request)


def test_one_shot_client_always_forces_sandbox(tmp_path):
    from openprogram.webui.routes.mcp import _one_shot_client

    cfg = MCPServerConfig(name="probe", command=["python", "server.py"])
    client = _one_shot_client(cfg, str(tmp_path))
    assert client.force_sandbox is True
    assert client.sandbox_cwd == str(tmp_path)
