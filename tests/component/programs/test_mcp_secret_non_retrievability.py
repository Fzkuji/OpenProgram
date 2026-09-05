"""MCP server credentials are storable but never retrievable.

An MCP server config holds secrets in four places: any ``env`` value
(local transport), any ``headers`` value (remote), the bearer token, and
the OAuth client secret. These tests pin the same contract the provider
credential routes already keep — a route may report that a secret exists
and show a mask, and may accept a replacement, but never returns the
stored value.

Values are masked wholesale rather than by name-matching: an MCP
server's env is free-form, so ``ENDPOINT`` can hold a signed URL just as
easily as ``API_KEY`` holds a key.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import traceback
from concurrent.futures import CancelledError as FutureCancelledError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.mcp.config import (
    MCPServerConfig,
    OAuthSettings,
    load_configs,
    save_configs,
)


BEARER = "bearer-secret-token-value"
POSIX_FILE_MODES = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows access control is not represented by POSIX mode bits",
)
CLIENT_SECRET = "oauth-client-secret-value"
ENV_SECRET = "ghp_env_secret_value_here"
HEADER_SECRET = "tenant-signed-value-here"


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Redirect ``get_state_dir`` so config I/O lands in a tmp dir."""
    from openprogram import paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    return tmp_path


def local_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="local-tools",
        type="local",
        command=["python", "server.py"],
        # Neither name looks key-shaped; both must still be masked.
        env={"ENDPOINT": ENV_SECRET, "REGION": "us-east-1"},
    )


def remote_config(auth_kind: str = "bearer") -> MCPServerConfig:
    return MCPServerConfig(
        name="remote-tools",
        type="http",
        url="https://mcp.example.com/mcp",
        headers={"X-Tenant": HEADER_SECRET},
        auth_kind=auth_kind,
        bearer_token=BEARER if auth_kind == "bearer" else None,
        oauth=(OAuthSettings(client_id="cid", client_secret=CLIENT_SECRET)
               if auth_kind == "oauth" else None),
    )


def secrets_absent(blob: str) -> None:
    for secret in (BEARER, CLIENT_SECRET, ENV_SECRET, HEADER_SECRET):
        assert secret not in blob, f"response leaked {secret!r}"


def test_restart_endpoint_does_not_disclose_runtime_exception(monkeypatch):
    """Replacing the stable error with an upstream exception leaks credentials."""
    from openprogram.webui.routes import mcp

    async def failed_restart(_name):
        raise RuntimeError("peer-secret-value restart stderr")

    monkeypatch.setattr(mcp, "restart_server", failed_restart)
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).post("/api/mcp/servers/local-tools/restart")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "mcp_runtime_restart_failed",
        "kind": "runtime",
        "action": "retry_or_restart",
    }
    assert "peer-secret-value" not in response.text


def test_public_registry_status_masks_client_error_and_stderr(capsys):
    from openprogram.mcp import registry

    secret = "peer-secret-value"
    client = type("Client", (), {})()
    client.config = local_config()
    client.is_ready = False
    client.error = f"stderr credential={secret}"
    client.error_kind = f"unknown-{secret}"
    client.tools = []
    registry._clients[client.config.name] = client
    try:
        payload = registry.server_status()
        rendered = json.dumps(payload) + capsys.readouterr().err
        assert secret not in rendered
        assert payload[0]["error"] == "mcp_server_unavailable"
        assert payload[0]["error_kind"] == "fatal"
    finally:
        registry._clients.pop(client.config.name, None)


def test_public_registry_stop_log_masks_exception(monkeypatch, capsys):
    from openprogram.mcp import registry

    secret = "peer-secret-value"
    client = type("Client", (), {})()

    async def failed_stop():
        raise RuntimeError(secret)

    client.stop = failed_stop
    registry._clients["secret-test"] = client
    registry._registered_tool_names["secret-test"] = []
    asyncio.run(registry.remove_server("secret-test"))
    assert secret not in capsys.readouterr().err


def test_restart_endpoint_masks_exception_and_breaks_chain(monkeypatch):
    from openprogram.webui.routes import mcp

    async def failed_restart(_name):
        raise RuntimeError("peer-secret-value restart stderr")

    monkeypatch.setattr(mcp, "restart_server", failed_restart)
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).post("/api/mcp/servers/local-tools/restart")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "mcp_runtime_restart_failed",
        "kind": "runtime",
        "action": "retry_or_restart",
    }
    assert "peer-secret-value" not in response.text


def test_restart_endpoint_direct_exception_chain_masks_runtime_failure(monkeypatch):
    from fastapi import HTTPException

    from openprogram.webui.routes import mcp

    secret = "peer-secret-value restart stderr"

    async def failed_restart(_name):
        raise RuntimeError(secret)

    monkeypatch.setattr(mcp, "restart_server", failed_restart)
    app = FastAPI()
    mcp.register(app)
    endpoint = next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None) == "/api/mcp/servers/{name}/restart"
        and "POST" in getattr(route, "methods", set())
    )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(endpoint("local-tools"))

    rendered = "\n".join(
        text
        for error in exception_chain(caught.value)
        for text in (str(error), repr(error), "".join(traceback.format_exception(error)))
    )
    assert secret not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_transient_supervisor_error_uses_stable_state_and_stderr(monkeypatch, capsys):
    from openprogram.mcp.client import MCPClient

    secret = "peer-secret-value transient stderr"
    client = MCPClient(local_config())
    client._ready.set()
    client._session = object()

    async def failed_transport():
        raise RuntimeError(secret)

    monkeypatch.setattr(client, "_run_local", failed_transport)

    async def exercise() -> None:
        task = asyncio.create_task(client._supervisor())
        for _ in range(100):
            if client.error_kind == "transient":
                break
            await asyncio.sleep(0)
        assert client.error_kind == "transient"
        client._shutdown.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(exercise())

    assert client.error == "mcp_connection_transient"
    assert secret not in capsys.readouterr().err


def test_await_session_failure_does_not_format_private_client_error():
    from openprogram.mcp.client import MCPClient

    secret = "peer-secret-value stored client error"
    client = MCPClient(local_config())
    client.error = secret
    client.error_kind = "fatal"

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(client._await_session_ready())

    rendered = "\n".join(
        text
        for error in exception_chain(caught.value)
        for text in (str(error), repr(error), "".join(traceback.format_exception(error)))
    )
    assert str(caught.value) == "mcp_server_unavailable:fatal"
    assert secret not in rendered


@pytest.mark.parametrize("kind", ["needs_reauth", "transient", "fatal"])
def test_public_registry_preserves_stable_ui_error_kind(kind):
    from openprogram.mcp import registry

    client = type("Client", (), {})()
    client.config = local_config()
    client.is_ready = False
    client.error = "private diagnostic"
    client.error_kind = kind
    client.tools = []
    registry._clients[client.config.name] = client
    try:
        assert registry.server_status()[0]["error_kind"] == kind
    finally:
        registry._clients.pop(client.config.name, None)


def exception_chain(exc: BaseException):
    pending = [exc]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend(
            linked
            for linked in (current.__cause__, current.__context__)
            if linked is not None
        )


# --- serialization split ---------------------------------------------


def test_storage_dict_keeps_values_and_response_dict_masks_them():
    stored = local_config().to_storage_dict()
    assert stored["env"] == {"ENDPOINT": ENV_SECRET, "REGION": "us-east-1"}

    response = local_config().to_response_dict()
    assert response["env"]["ENDPOINT"] == {
        "has_value": True,
        "masked": "ghp…here",
    }
    # A value nobody would call a secret is masked all the same — the
    # rule is the field, not the name.
    assert response["env"]["REGION"]["has_value"] is True
    assert response["env"]["REGION"]["masked"] != "us-east-1"
    secrets_absent(json.dumps(response))


def test_response_dict_masks_bearer_and_oauth_secrets():
    bearer = remote_config("bearer").to_response_dict()
    assert bearer["auth"]["has_token"] is True
    assert bearer["auth"]["masked_token"] == "bea…alue"
    assert "token" not in bearer["auth"]
    secrets_absent(json.dumps(bearer))

    oauth = remote_config("oauth").to_response_dict()
    assert oauth["auth"]["has_client_secret"] is True
    assert "client_secret" not in oauth["auth"]
    # client_id is an identifier, not a secret — it stays readable so
    # the edit dialog can prefill it.
    assert oauth["auth"]["client_id"] == "cid"
    secrets_absent(json.dumps(oauth))


def test_config_with_no_secret_reports_absence():
    cfg = MCPServerConfig(name="plain", type="http",
                          url="https://x.example/mcp", auth_kind="bearer")
    assert cfg.to_response_dict()["auth"] == {
        "kind": "bearer", "has_token": False,
    }


# --- list / detail / catalog responses --------------------------------


def registry_with(cfg: MCPServerConfig, monkeypatch):
    """Install a fake ready client for ``cfg`` in the live registry."""
    from openprogram.mcp import registry

    class FakeClient:
        def __init__(self, config):
            self.config = config
            self.tools = []
            self.error = None
            self.error_kind = None
            self.is_ready = True

        def auth_status(self):
            return {"kind": self.config.auth_kind, "authenticated": True}

    monkeypatch.setitem(registry._clients, cfg.name, FakeClient(cfg))
    return registry


def test_list_and_detail_routes_return_no_plaintext(monkeypatch, state_dir):
    from openprogram.webui.routes import mcp

    for cfg in (local_config(), remote_config("bearer"),
                remote_config("oauth")):
        registry_with(cfg, monkeypatch)
    app = FastAPI()
    mcp.register(app)
    client = TestClient(app)

    listing = client.get("/api/mcp/servers")
    assert listing.status_code == 200
    secrets_absent(listing.text)
    local = next(s for s in listing.json()["servers"]
                 if s["name"] == "local-tools")
    assert local["env"]["ENDPOINT"]["has_value"] is True

    for name in ("local-tools", "remote-tools"):
        detail = client.get(f"/api/mcp/servers/{name}")
        assert detail.status_code == 200
        secrets_absent(detail.text)


def test_catalog_route_returns_no_plaintext(monkeypatch, state_dir):
    """A catalog can ship an entry carrying a token; the echo is masked."""
    from openprogram.webui.routes import mcp

    catalog = {
        "name": "test catalog",
        "servers": [{
            "name": "remote-tools",
            "type": "http",
            "url": "https://mcp.example.com/mcp",
            "headers": {"X-Tenant": HEADER_SECRET},
            "auth": {"kind": "bearer", "token": BEARER},
        }],
    }

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        url = "https://catalog.example/c.json"

        def raise_for_status(self):
            return None

        def json(self):
            return catalog

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return FakeResponse()

    from openprogram.security import safe_http

    monkeypatch.setattr(
        safe_http,
        "configured_safe_async_client",
        lambda _consumer, _url, **_kwargs: FakeAsyncClient(),
    )
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).get(
        "/api/mcp/catalog?url=https://catalog.example/c.json")

    assert response.status_code == 200
    secrets_absent(response.text)
    entry = response.json()["servers"][0]
    assert entry["auth"]["has_token"] is True
    assert entry["headers"]["X-Tenant"]["has_value"] is True


def test_catalog_update_missing_entry_hides_signed_source_url(monkeypatch, state_dir):
    from openprogram.webui.routes import mcp

    source = "https://catalog.example/TOKEN-PATH/catalog.json?sig=QUERY-SECRET"
    cfg = remote_config()
    cfg.source_catalog_url = source
    save_configs([cfg])

    async def fake_fetch(_url):
        return {"servers": []}

    monkeypatch.setattr(mcp, "_fetch_catalog_json", fake_fetch)
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).post(
        f"/api/mcp/servers/{cfg.name}/update_from_catalog"
    )

    assert response.status_code == 502
    rendered = response.text
    assert "https://catalog.example" in rendered
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered


# --- file permissions -------------------------------------------------


@POSIX_FILE_MODES
def test_saved_config_is_owner_only(state_dir):
    path = save_configs([local_config()])

    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"config file is {oct(mode)}, expected 0o600"
    # No temp file left behind holding the same secrets.
    assert not list(state_dir.glob("*.tmp"))


@POSIX_FILE_MODES
def test_existing_world_readable_config_can_be_saved(state_dir):
    path = save_configs([local_config()])
    os.chmod(path, 0o644)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644

    save_configs([local_config()])
    assert path.is_file()


def test_save_configs_preserves_roots_block(state_dir):
    from openprogram.mcp.config import load_roots, save_roots

    save_roots([{"uri": "file:///work", "name": "work"}])
    save_configs([local_config()])

    assert load_roots() == [{"uri": "file:///work", "name": "work"}]
    assert [c.name for c in load_configs()] == ["local-tools"]


# --- preserve / replace / delete --------------------------------------


def patch_server(cfg: MCPServerConfig, body: dict, monkeypatch, *,
                 restart_fails: bool = False):
    """PATCH ``cfg`` with ``body`` and return (response, stored config)."""
    from openprogram.webui.routes import mcp

    save_configs([cfg])

    async def fake_restart(name, new_cfg=None):
        if restart_fails and new_cfg is not None and new_cfg is not cfg:
            raise RuntimeError("spawn failed")
        return {"name": name, "ready": True}

    monkeypatch.setattr(mcp, "restart_server", fake_restart)
    app = FastAPI()
    mcp.register(app)
    response = TestClient(app).patch(
        f"/api/mcp/servers/{cfg.name}", json=body)
    stored = next(c for c in load_configs(include_disabled=True)
                  if c.name == cfg.name)
    return response, stored


def test_patch_revision_conflict_resyncs_runtime_to_external_config(
    monkeypatch, state_dir
):
    from openprogram.auth.credentials import PrivateAtomicWriteError
    from openprogram.webui.routes import mcp

    cfg = local_config()
    external = local_config()
    external.timeout_seconds = 99
    restarts = []

    async def observed_restart(_name, *, new_cfg):
        restarts.append(new_cfg.timeout_seconds)
        return {"name": cfg.name, "ready": True}

    loads = iter([([cfg], "sha256:stale"), ([external], "sha256:external")])
    monkeypatch.setattr(
        mcp,
        "load_configs_with_revision",
        lambda **_kwargs: next(loads),
    )
    monkeypatch.setattr(
        mcp,
        "save_configs_revision",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PrivateAtomicWriteError("conflict", state_dir / "mcp_servers.json", committed=False)
        ),
    )
    monkeypatch.setattr(mcp, "restart_server", observed_restart)
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).patch(
        f"/api/mcp/servers/{cfg.name}", json={"timeout_seconds": 45}
    )

    assert response.status_code == 409
    assert restarts == [45, 99]


@pytest.mark.parametrize(
    ("resync_error", "remove_error", "status_code", "code", "runtime_state"),
    [
        pytest.param(
            RuntimeError("peer-secret-value resync failed"),
            None,
            503,
            "mcp_runtime_resync_failed",
            "stopped",
            id="ordinary-resync-failure-stopped",
        ),
        pytest.param(
            asyncio.CancelledError("peer-secret-value resync cancelled"),
            None,
            503,
            "mcp_runtime_resync_failed",
            "stopped",
            id="cancelled-resync-stopped",
        ),
        pytest.param(
            RuntimeError("peer-secret-value resync failed"),
            RuntimeError("peer-secret-value stop failed"),
            500,
            "mcp_runtime_state_unknown",
            "unknown",
            id="ordinary-stop-failure-unknown",
        ),
        pytest.param(
            RuntimeError("peer-secret-value resync failed"),
            asyncio.CancelledError("peer-secret-value stop cancelled"),
            500,
            "mcp_runtime_state_unknown",
            "unknown",
            id="cancelled-stop-unknown",
        ),
    ],
)
def test_patch_conflict_resync_failure_reports_sanitized_runtime_state(
    monkeypatch,
    state_dir,
    resync_error,
    remove_error,
    status_code,
    code,
    runtime_state,
):
    from openprogram.auth.credentials import PrivateAtomicWriteError
    from openprogram.webui.routes import mcp

    original = local_config()
    original.command = ["old"]
    original.env = {"TOKEN": "peer-secret-value"}
    external = local_config()
    external.command = ["old"]
    external.env = dict(original.env)
    save_configs([original])
    restarts = []
    removals = []

    async def observed_restart(_name, *, new_cfg):
        restarts.append(new_cfg.command)
        if len(restarts) == 2:
            if isinstance(resync_error, asyncio.CancelledError):
                asyncio.current_task().cancel(str(resync_error))
                await asyncio.sleep(0)
            raise resync_error
        return {"name": original.name, "ready": True}

    async def observed_remove(name):
        removals.append(name)
        if remove_error is not None:
            raise remove_error

    loads = iter([([original], "sha256:stale"), ([external], "sha256:actual")])
    monkeypatch.setattr(mcp, "load_configs_with_revision", lambda **_kwargs: next(loads))
    monkeypatch.setattr(
        mcp,
        "save_configs_revision",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PrivateAtomicWriteError(
                "conflict", state_dir / "mcp_servers.json", committed=False
            )
        ),
    )
    monkeypatch.setattr(mcp, "restart_server", observed_restart)
    monkeypatch.setattr(mcp, "remove_server", observed_remove)
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).patch(
        f"/api/mcp/servers/{original.name}", json={"command": ["new"]}
    )

    assert response.status_code == status_code
    assert response.json()["detail"] == {
        "code": code,
        "persisted_config": "unchanged",
        "runtime_state": runtime_state,
        "action": "retry_or_restart",
    }
    assert "peer-secret-value" not in response.text
    assert restarts == [["new"], ["old"]]
    assert removals == [original.name]
    stored = load_configs(include_disabled=True)[0]
    assert stored.command == ["old"]
    assert stored.env == original.env


@pytest.mark.parametrize(
    ("config_present", "restart_error", "remove_error", "status_code"),
    [
        pytest.param(
            True,
            RuntimeError("peer-secret-value resync failed"),
            None,
            503,
            id="ordinary-resync",
        ),
        pytest.param(
            True,
            asyncio.CancelledError("peer-secret-value resync cancelled"),
            None,
            503,
            id="cancelled-resync",
        ),
        pytest.param(
            True,
            RuntimeError("peer-secret-value resync failed"),
            RuntimeError("peer-secret-value stop failed"),
            500,
            id="ordinary-stop-after-resync",
        ),
        pytest.param(
            True,
            RuntimeError("peer-secret-value resync failed"),
            asyncio.CancelledError("peer-secret-value stop cancelled"),
            500,
            id="cancelled-stop-after-resync",
        ),
        pytest.param(
            False,
            None,
            RuntimeError("peer-secret-value stop failed"),
            500,
            id="ordinary-stop-after-delete",
        ),
        pytest.param(
            False,
            None,
            asyncio.CancelledError("peer-secret-value stop cancelled"),
            500,
            id="cancelled-stop-after-delete",
        ),
    ],
)
def test_restart_then_publish_failure_has_no_secret_in_recursive_exception_chain(
    monkeypatch,
    state_dir,
    config_present,
    restart_error,
    remove_error,
    status_code,
):
    from fastapi import HTTPException

    from openprogram.auth.credentials import PrivateAtomicWriteError
    from openprogram.webui.routes import mcp

    secret = "peer-secret-value"
    previous = local_config()
    previous.env = {"TOKEN": secret}
    updated = local_config()
    updated.command = ["new"]
    updated.env = dict(previous.env)
    actual = local_config()
    actual.command = ["actual"]
    actual.env = dict(previous.env)

    monkeypatch.setattr(
        mcp,
        "load_configs_with_revision",
        lambda **_kwargs: ([actual] if config_present else [], "sha256:actual"),
    )

    restarts = []

    async def failed_restart(_name, *, new_cfg):
        restarts.append(new_cfg.command)
        if len(restarts) == 2 and restart_error is not None:
            raise restart_error
        return {"name": previous.name, "ready": True}

    async def stopped(_name):
        if remove_error is not None:
            raise remove_error
        return True

    def conflict(*_args, **_kwargs):
        raise PrivateAtomicWriteError(
            "conflict",
            state_dir / f"{secret}-mcp.json",
            committed=False,
            cause=RuntimeError(f"{secret} config conflict"),
        )

    monkeypatch.setattr(mcp, "restart_server", failed_restart)
    monkeypatch.setattr(mcp, "remove_server", stopped)
    monkeypatch.setattr(mcp, "save_configs_revision", conflict)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            mcp._restart_then_publish(
                previous.name,
                previous=previous,
                updated=updated,
                configs=[updated],
                expected_revision="sha256:stale",
            )
        )

    assert caught.value.status_code == status_code
    chain = list(exception_chain(caught.value))
    rendered = "\n".join(
        representation
        for exception in chain
        for representation in (
            str(exception),
            repr(exception),
            "".join(traceback.format_exception(exception)),
        )
    )
    assert secret not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    ("rollback_error", "status_code", "runtime_state"),
    [
        (None, 500, "restored"),
        (RuntimeError("peer-secret-value rollback failed"), 500, "unknown"),
    ],
)
def test_initial_restart_failure_is_stable_and_secret_free(
    monkeypatch, rollback_error, status_code, runtime_state
):
    from fastapi import HTTPException

    from openprogram.webui.routes import mcp

    previous = local_config()
    previous.env = {"TOKEN": "peer-secret-value"}
    updated = local_config()
    updated.command = ["new"]
    calls = 0

    async def failed_restart(_name, *, new_cfg):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("peer-secret-value initial restart failed")
        if rollback_error is not None:
            raise rollback_error
        return {"ready": True}

    monkeypatch.setattr(mcp, "restart_server", failed_restart)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            mcp._restart_then_publish(
                previous.name,
                previous=previous,
                updated=updated,
                configs=[updated],
                expected_revision="sha256:current",
            )
        )

    assert caught.value.status_code == status_code
    assert caught.value.detail == {
        "code": "mcp_runtime_restart_failed",
        "persisted_config": "unchanged",
        "runtime_state": runtime_state,
        "action": "retry_or_restart",
    }
    chain = list(exception_chain(caught.value))
    rendered = "\n".join(
        text
        for error in chain
        for text in (str(error), repr(error), "".join(traceback.format_exception(error)))
    )
    assert "peer-secret-value" not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "remove_error",
    [
        pytest.param(
            RuntimeError("peer-secret-value stop failed"),
            id="ordinary-stop",
        ),
        pytest.param(
            asyncio.CancelledError("peer-secret-value stop cancelled"),
            id="cancelled-stop",
        ),
    ],
)
def test_patch_conflict_deleted_config_cleanup_failure_reports_unknown(
    monkeypatch,
    state_dir,
    remove_error,
):
    from openprogram.auth.credentials import PrivateAtomicWriteError
    from openprogram.webui.routes import mcp

    original = local_config()
    original.command = ["old"]
    original.env = {"TOKEN": "peer-secret-value"}
    save_configs([original])
    removals = []

    async def started(_name, *, new_cfg):
        return {"name": new_cfg.name, "ready": True}

    async def failed_remove(name):
        removals.append(name)
        raise remove_error

    def external_delete(*_args, **_kwargs):
        save_configs([])
        raise PrivateAtomicWriteError(
            "conflict", state_dir / "mcp_servers.json", committed=False
        )

    loads = iter([([original], "sha256:stale"), ([], "sha256:actual")])
    monkeypatch.setattr(mcp, "load_configs_with_revision", lambda **_kwargs: next(loads))
    monkeypatch.setattr(mcp, "save_configs_revision", external_delete)
    monkeypatch.setattr(mcp, "restart_server", started)
    monkeypatch.setattr(mcp, "remove_server", failed_remove)
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).patch(
        f"/api/mcp/servers/{original.name}", json={"command": ["new"]}
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "mcp_runtime_state_unknown",
        "persisted_config": "unchanged",
        "runtime_state": "unknown",
        "action": "retry_or_restart",
    }
    assert "peer-secret-value" not in response.text
    assert removals == [original.name]
    assert load_configs(include_disabled=True) == []


def test_patch_cancelled_restart_leaves_disk_unchanged(monkeypatch, state_dir):
    from openprogram.webui.routes import mcp

    original = local_config()
    original.command = ["old"]
    save_configs([original])

    async def cancelled_restart(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(mcp, "restart_server", cancelled_restart)
    app = FastAPI()
    mcp.register(app)

    with pytest.raises(FutureCancelledError):
        TestClient(app).patch(
            f"/api/mcp/servers/{original.name}", json={"command": ["new"]}
        )

    stored = load_configs(include_disabled=True)[0]
    assert stored.command == ["old"]
    assert stored.env == original.env


def test_catalog_update_cancelled_restart_leaves_disk_unchanged(
    monkeypatch, state_dir
):
    from openprogram.webui.routes import mcp

    original = local_config()
    original.command = ["old"]
    original.source_catalog_url = "https://catalog.example/mcp.json"
    save_configs([original])

    class Response:
        status_code = 200
        headers = {"content-type": "application/json"}
        url = "https://catalog.example/mcp.json"

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "servers": [
                    {
                        "name": original.name,
                        "type": "local",
                        "command": ["new"],
                    }
                ]
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            return Response()

    async def cancelled_restart(*_args, **_kwargs):
        raise asyncio.CancelledError

    from openprogram.security import safe_http

    monkeypatch.setattr(
        safe_http,
        "configured_safe_async_client",
        lambda *_args, **_kwargs: Client(),
    )
    monkeypatch.setattr(mcp, "restart_server", cancelled_restart)
    app = FastAPI()
    mcp.register(app)

    with pytest.raises(FutureCancelledError):
        TestClient(app).post(
            f"/api/mcp/servers/{original.name}/update_from_catalog"
        )

    stored = load_configs(include_disabled=True)[0]
    assert stored.command == ["old"]
    assert stored.env == original.env


def test_patch_restart_failure_restores_runtime_before_any_publication(
    monkeypatch, state_dir
):
    from openprogram.webui.routes import mcp

    original = local_config()
    saves = 0
    restarts = []

    def observed_save(*_args, **_kwargs):
        nonlocal saves
        saves += 1
        return "sha256:new"

    async def observed_restart(_name, *, new_cfg):
        restarts.append(new_cfg.timeout_seconds)
        if len(restarts) == 1:
            raise RuntimeError("new config failed")
        return {"name": original.name, "ready": True}

    monkeypatch.setattr(
        mcp,
        "load_configs_with_revision",
        lambda **_kwargs: ([original], "sha256:old"),
    )
    monkeypatch.setattr(mcp, "save_configs_revision", observed_save)
    monkeypatch.setattr(mcp, "restart_server", observed_restart)
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).patch(
        f"/api/mcp/servers/{original.name}", json={"timeout_seconds": 45}
    )

    assert response.status_code == 500
    assert restarts == [45, 30]
    assert saves == 0


def test_omitted_env_field_preserves_every_stored_value(monkeypatch, state_dir):
    response, stored = patch_server(
        local_config(), {"timeout_seconds": 45}, monkeypatch)

    assert response.status_code == 200
    assert stored.env == {"ENDPOINT": ENV_SECRET, "REGION": "us-east-1"}
    assert stored.timeout_seconds == 45


def test_omitted_env_name_preserves_that_name(monkeypatch, state_dir):
    """A patch naming one variable must not wipe its siblings."""
    response, stored = patch_server(
        local_config(), {"env": {"REGION": "eu-west-1"}}, monkeypatch)

    assert response.status_code == 200
    assert stored.env == {"ENDPOINT": ENV_SECRET, "REGION": "eu-west-1"}


def test_submitted_value_replaces_and_empty_value_deletes(monkeypatch, state_dir):
    response, stored = patch_server(
        local_config(),
        {"env": {"ENDPOINT": "replaced-value", "REGION": ""}},
        monkeypatch,
    )

    assert response.status_code == 200
    assert stored.env == {"ENDPOINT": "replaced-value"}


@pytest.mark.parametrize(
    "display_value",
    [
        {"has_value": True, "masked": "ghp…here"},
        "ghp…here",
        "•" * 8,
        "REDACTED",
        "<redacted>",
        "[redacted]",
        "***REDACTED***",
    ],
)
@pytest.mark.parametrize(
    ("config", "body"),
    [
        pytest.param(
            local_config,
            lambda value: {"env": {"ENDPOINT": value}},
            id="env",
        ),
        pytest.param(
            remote_config,
            lambda value: {"headers": {"X-Tenant": value}},
            id="header",
        ),
        pytest.param(
            remote_config,
            lambda value: {"auth": {"kind": "bearer", "token": value}},
            id="bearer",
        ),
        pytest.param(
            lambda: remote_config("oauth"),
            lambda value: {
                "auth": {"kind": "oauth", "client_secret": value}
            },
            id="oauth-client-secret",
        ),
    ],
)
def test_patch_rejects_every_display_value_without_rewriting_storage(
    monkeypatch, state_dir, config, body, display_value
):
    from openprogram.webui.routes import mcp

    cfg = config()
    path = save_configs([cfg])
    before = path.read_bytes()

    async def unexpected_restart(*_args, **_kwargs):
        raise AssertionError("invalid credential edit must not restart")

    monkeypatch.setattr(mcp, "restart_server", unexpected_restart)
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).patch(
        f"/api/mcp/servers/{cfg.name}", json=body(display_value)
    )

    assert 400 <= response.status_code < 500
    assert path.read_bytes() == before


def test_omitted_bearer_token_is_preserved(monkeypatch, state_dir):
    response, stored = patch_server(
        remote_config("bearer"),
        {"auth": {"kind": "bearer"}, "timeout_seconds": 15},
        monkeypatch,
    )

    assert response.status_code == 200
    assert stored.bearer_token == BEARER


def test_submitted_bearer_token_replaces(monkeypatch, state_dir):
    _, stored = patch_server(
        remote_config("bearer"),
        {"auth": {"kind": "bearer", "token": "fresh-token-value"}},
        monkeypatch,
    )

    assert stored.bearer_token == "fresh-token-value"


def test_omitted_client_secret_is_preserved(monkeypatch, state_dir):
    _, stored = patch_server(
        remote_config("oauth"),
        {"auth": {"kind": "oauth", "client_id": "cid", "scope": "read"}},
        monkeypatch,
    )

    assert stored.oauth is not None
    assert stored.oauth.client_secret == CLIENT_SECRET
    assert stored.oauth.scope == "read"


def test_switching_auth_kind_drops_the_other_kinds_secret(monkeypatch, state_dir):
    """Moving bearer → oauth must not leave the bearer token stored: it
    is unreachable through the UI and no longer serves any purpose."""
    _, stored = patch_server(
        remote_config("bearer"),
        {"auth": {"kind": "oauth", "client_name": "OpenProgram"}},
        monkeypatch,
    )

    assert stored.auth_kind == "oauth"
    assert stored.bearer_token is None
    assert json.dumps(stored.to_storage_dict()).count(BEARER) == 0


def test_headers_follow_the_same_preserve_rule(monkeypatch, state_dir):
    _, stored = patch_server(
        remote_config("bearer"), {"timeout_seconds": 12}, monkeypatch)

    assert stored.headers == {"X-Tenant": HEADER_SECRET}


# --- failure atomicity -------------------------------------------------


def test_failed_restart_leaves_the_stored_secret_untouched(monkeypatch, state_dir):
    """A rejected edit must not destroy a working server's credentials."""
    response, stored = patch_server(
        remote_config("bearer"),
        {"auth": {"kind": "bearer", "token": "would-be-new-token"}},
        monkeypatch,
        restart_fails=True,
    )

    assert response.status_code == 500
    assert stored.bearer_token == BEARER
    assert stored.headers == {"X-Tenant": HEADER_SECRET}
